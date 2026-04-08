"""Live trading engine — mirrors backtest/engine.py exactly.

Same SignalEngine, same risk parameters, same position management,
same analysis times, same phase blocking. The only difference is
real-time Alpaca data and real order execution instead of simulated bars.
"""

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from autotrader.config import (
    PHASE_CONFIG, RISK, LOG_DIR, LOG_LEVEL,
    DAY_TRADE_MODE, CLOSE_ALL_EOD, EOD_CLOSE_MINUTE,
    SCANNER, WATCHLIST_FALLBACK,
    ENABLE_LONG, ENABLE_SHORT,
)
from autotrader.signals.engine import SignalEngine
from autotrader.signals.short_engine import ShortSignalEngine
from autotrader.execution.broker import AlpacaBroker
from autotrader.data.scanner import MarketScanner
from autotrader.data.market import get_current_price, get_stock_data, get_intraday_data
from autotrader.data.indicators import calculate_indicators, calculate_intraday_indicators, calculate_dual_thrust_range
from autotrader.data.patterns import (
    detect_all_patterns, format_patterns_for_prompt,
    get_key_levels, format_levels_for_prompt,
)
from autotrader.data.regime import MarketRegime
from autotrader.alerts.telegram import TelegramAlerts

logger = logging.getLogger("autotrader")

SLIPPAGE_PER_SHARE = 0.01


# ═══════════════════════════════════════════════════════════
# RISK PARAMETERS — IDENTICAL TO BACKTEST_RISK
# ═══════════════════════════════════════════════════════════

LIVE_RISK = {
    "max_risk_per_trade_pct": 0.05,
    "max_position_pct": 0.15,
    "max_total_exposure_pct": 0.80,
    "max_trades_per_day": 25,
    "min_risk_reward_ratio": 1.5,
    "min_confidence_to_trade": 0.65,
    "analyze_count": 10,
}


# ═══════════════════════════════════════════════════════════
# ANALYSIS TIMES — IDENTICAL TO BACKTEST DEFAULT_ANALYSIS_TIMES
# ═══════════════════════════════════════════════════════════

ANALYSIS_TIMES = [
    time(9, 35),   # Open (ORB forming)
    time(10, 0),   # Prime start (10 AM reversal zone)
    time(10, 30),  # Prime mid
    time(10, 50),  # Prime late
    time(11, 0),   # Late morning (lunch blocked by signal engine)
    time(11, 45),  # Lunch (blocked by signal engine)
    time(13, 0),   # Mid-day (blocked by signal engine)
    time(14, 0),   # Afternoon (blocked by signal engine)
    time(14, 45),  # Afternoon late (blocked by signal engine)
    time(15, 15),  # Power hour
]


# ═══════════════════════════════════════════════════════════
# CONFIDENCE → RISK SCALING — IDENTICAL TO BACKTEST
# ═══════════════════════════════════════════════════════════

def _confidence_risk_scale(confidence: float) -> float:
    """Scale risk amount by confidence. Higher confidence = bigger bet."""
    c = max(0.65, min(1.0, confidence))
    normalized = (c - 0.65) / 0.35
    return 0.30 + 0.70 * (normalized ** 1.2)


# ═══════════════════════════════════════════════════════════
# SECTOR CONCENTRATION — IDENTICAL TO BACKTEST
# ═══════════════════════════════════════════════════════════

_CORRELATED_GROUPS = {
    "mega_tech": {"AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN"},
    "semis": {"NVDA", "AMD", "INTC", "AVGO", "QCOM", "MU", "TSM", "ASML"},
    "ev_auto": {"TSLA", "RIVN", "LCID", "NIO", "GM", "F"},
    "banking": {"JPM", "BAC", "GS", "MS", "WFC", "C"},
    "energy": {"XOM", "CVX", "COP", "SLB", "OXY", "EOG"},
    "biotech": {"MRNA", "PFE", "JNJ", "ABBV", "LLY", "BMY"},
}
_MAX_SECTOR_CONCENTRATION = 2


# ═══════════════════════════════════════════════════════════
# LIVE POSITION — MIRRORS SimulatedPosition FROM BACKTEST
# ═══════════════════════════════════════════════════════════

@dataclass
class LivePosition:
    symbol: str
    entry_price: float
    quantity: int
    stop_loss: float
    take_profit: float
    entry_time: datetime
    direction: str = "long"       # "long" or "short"
    pattern: str = ""
    confidence: float = 0.0
    risk_per_share: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    scale_out_stage: int = 0
    shares_remaining: int = 0
    realized_pnl: float = 0.0
    r_target_1: float = 0.0
    r_target_2: float = 0.0
    current_stop: float = 0.0
    breakeven_locked: bool = False
    broker_stop_order_id: str = ""

    def __post_init__(self):
        self.risk_per_share = abs(self.entry_price - self.stop_loss)
        self.highest_price = self.entry_price
        self.lowest_price = self.entry_price
        self.shares_remaining = self.quantity
        if self.direction == "short":
            self.r_target_1 = self.entry_price - self.risk_per_share * 1.0   # 1.0R below for shorts
            self.r_target_2 = self.entry_price - self.risk_per_share * 2.0   # 2.0R below for shorts
        else:
            self.r_target_1 = self.entry_price + self.risk_per_share * 1.0   # 1.0R first target
            self.r_target_2 = self.entry_price + self.risk_per_share * 2.0   # 2.0R second target
        self.current_stop = self.stop_loss


# ═══════════════════════════════════════════════════════════
# MAIN AUTOTRADER CLASS
# ═══════════════════════════════════════════════════════════

class AutoTrader:
    """Live trading engine — mirrors backtest/engine.py logic exactly."""

    def __init__(self):
        self.signal_engine = SignalEngine()
        self.short_signal_engine = ShortSignalEngine()
        self.broker = AlpacaBroker()
        self.scanner = MarketScanner()
        self.telegram = TelegramAlerts()
        self.regime = MarketRegime()
        self.scheduler = AsyncIOScheduler()
        self._running = False

        # Position tracking — mirrors backtest
        self.positions: dict[str, LivePosition] = {}

        # Daily counters — mirrors backtest
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.cooldown_until: datetime | None = None
        self.peak_equity = 0.0
        self._daily_r = 0.0

    async def start(self):
        """Start the trading system."""
        logger.info("=" * 60)
        logger.info("  AutoTrader v4.0 — Deterministic Signal Engine")
        logger.info("  Same logic as backtest — no differences")
        logger.info("=" * 60)

        account = self.broker.get_account()
        if not account:
            logger.error("Cannot connect to Alpaca — aborting")
            return
        self.peak_equity = account.get("equity", 100_000)

        await self.telegram.start()

        logger.info(f"Account equity: ${account.get('equity', 0):,.2f}")
        logger.info(f"Day trade mode: {DAY_TRADE_MODE}")

        # Build trading universe
        logger.info("Building trading universe...")
        self.scanner.build_universe()
        logger.info(f"Universe: {len(self.scanner.universe)} liquid stocks")

        # Initial scan
        self.scanner.scan_for_movers()
        logger.info(f"Hot list: {len(self.scanner.hot_list)} candidates")

        # Reconcile existing positions
        await self._reconcile_positions()

        # ── Schedule jobs ──────────────────────────────

        # Main trading loop — runs every 2 min, checks if it's an analysis time
        self.scheduler.add_job(
            self._trading_loop,
            IntervalTrigger(minutes=2),
            id="trading_loop",
            name="Main Trading Loop",
            max_instances=1,
        )

        # Position management — every 1 min (mirrors backtest checking every bar)
        self.scheduler.add_job(
            self._manage_positions,
            IntervalTrigger(minutes=1),
            id="position_mgmt",
            name="Position Management",
        )

        # Refresh hot list every 15 min
        self.scheduler.add_job(
            self._refresh_hot_list,
            IntervalTrigger(minutes=SCANNER["hot_list_refresh_minutes"]),
            id="hot_list_refresh",
            name="Hot List Refresh",
        )

        # Daily universe rebuild at 9:00 AM ET
        self.scheduler.add_job(
            self._rebuild_universe,
            CronTrigger(hour=9, minute=0, timezone="US/Eastern"),
            id="universe_rebuild",
            name="Daily Universe Rebuild",
        )

        # EOD close all positions at 3:50 PM ET
        if CLOSE_ALL_EOD:
            self.scheduler.add_job(
                self._eod_close_all,
                CronTrigger(hour=15, minute=EOD_CLOSE_MINUTE, timezone="US/Eastern"),
                id="eod_close",
                name="EOD Close All",
            )

        # Daily summary at 4:30 PM ET + auto shutdown
        self.scheduler.add_job(
            self._daily_summary,
            CronTrigger(hour=16, minute=30, timezone="US/Eastern"),
            id="daily_summary",
            name="Daily Summary",
        )

        self.scheduler.start()
        self._running = True

        # Initial regime check
        self.regime.update()
        regime_label = self.regime.state.regime if self.regime.state else "unknown"

        logger.info("AutoTrader is LIVE.")
        await self.telegram.send_message(
            f"*AutoTrader v4.0 LIVE — Deterministic Engine*\n"
            f"Equity: ${account.get('equity', 0):,.2f}\n"
            f"Universe: {len(self.scanner.universe)} stocks\n"
            f"Hot list: {len(self.scanner.hot_list)} movers\n"
            f"Regime: {regime_label}\n"
            f"Risk: {LIVE_RISK['max_risk_per_trade_pct']*100:.0f}% per trade"
        )

        # Run initial cycle
        await self._trading_loop()

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Gracefully shut down."""
        logger.info("Shutting down AutoTrader...")
        self._running = False
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        await self.telegram.send_message("AutoTrader shutting down.")
        await self.telegram.stop()
        logger.info("AutoTrader stopped.")

    # ══════════════════════════════════════════════════════
    # CORE TRADING LOOP — mirrors backtest analysis cycle
    # ══════════════════════════════════════════════════════

    async def _trading_loop(self):
        """Scan → score with SignalEngine → risk check → execute.

        Runs every 2 min. Only analyzes at scheduled ANALYSIS_TIMES (±5 min).
        Mirrors the backtest engine's analysis cycle exactly.
        """
        if not self._is_market_hours():
            return

        now_et = datetime.now(timezone.utc).astimezone(
            __import__('zoneinfo', fromlist=['ZoneInfo']).ZoneInfo("US/Eastern")
        )
        hour, minute = now_et.hour, now_et.minute

        # Only run analysis at scheduled times (±5 min window)
        if not self._is_analysis_time(hour, minute):
            return

        phase = self._get_phase(hour, minute)

        # Block lunch — identical to backtest engine
        if phase == "lunch":
            return

        # Block late entries — identical to backtest engine
        if hour >= 15 or (hour == 14 and minute >= 30):
            return

        # Update market regime
        self.regime.update()
        regime_label = self.regime.state.regime if self.regime.state else "unknown"
        regime_multiplier = self.regime.get_size_multiplier() if hasattr(self.regime, 'get_size_multiplier') else 1.0

        # Check daily loss halt — identical to backtest
        account = self.broker.get_account()
        equity = account.get("equity", 100_000)
        if equity > 0 and self.daily_pnl < 0:
            daily_loss_pct = abs(self.daily_pnl) / equity
            if daily_loss_pct >= RISK["max_daily_loss_pct"]:
                logger.warning(f"Daily loss halt: {daily_loss_pct:.1%}")
                return

        # Check cooldown — identical to backtest
        if self.cooldown_until and now_et < self.cooldown_until:
            return
        self.cooldown_until = None

        # Check daily R limit
        if self._daily_r <= -3.0:
            logger.warning(f"Daily R limit hit ({self._daily_r:.1f}R)")
            return

        # Refresh hot list if stale
        if self.scanner.needs_hot_list_refresh():
            self.scanner.scan_for_movers()

        # Get candidates — mirrors backtest
        candidates = self.scanner.get_top_candidates()
        if not candidates:
            return

        phase_config = PHASE_CONFIG.get(phase, {})
        if phase_config.get("size_multiplier", 1.0) <= 0:
            return

        logger.info(
            f"--- Analysis cycle | Phase: {phase} | Regime: {regime_label} | "
            f"Candidates: {len(candidates)} | Trades: {self.daily_trades} | "
            f"R: {self._daily_r:+.1f} ---"
        )

        # Analyze top candidates — identical count to backtest
        analyze_count = min(LIVE_RISK["analyze_count"], len(candidates))
        symbols = [c.symbol for c in candidates[:analyze_count]]

        for sym in symbols:
            if self.daily_trades >= LIVE_RISK["max_trades_per_day"]:
                break
            if sym in self.positions:
                continue

            # Check sector concentration — identical to backtest
            if not self._check_sector_ok(sym):
                continue

            # Check total exposure — identical to backtest
            total_position_value = sum(
                pos.entry_price * pos.shares_remaining
                for pos in self.positions.values()
            )
            if equity > 0 and total_position_value / equity >= LIVE_RISK["max_total_exposure_pct"]:
                continue

            try:
                await self._analyze_and_trade(sym, equity, phase, phase_config, regime_multiplier)
            except Exception as e:
                logger.error(f"Error processing {sym}: {e}")

            await asyncio.sleep(0.5)

        logger.info(f"--- Cycle complete | Trades: {self.daily_trades} | R: {self._daily_r:+.1f} ---")

    async def _analyze_and_trade(
        self, symbol: str, equity: float, phase: str,
        phase_config: dict, regime_multiplier: float,
    ):
        """Analyze one symbol with SignalEngine and potentially trade.

        Data gathering and signal engine call mirrors backtest/engine.py exactly.
        """
        # Get real-time price
        price_data_raw = get_current_price(symbol)
        if not price_data_raw:
            return
        price = price_data_raw["price"]

        # Get daily data for indicators (same as backtest: calculate_indicators on daily)
        daily = get_stock_data(symbol, period="3mo", interval="1d")
        if daily is None or daily.empty or len(daily) < 20:
            return

        indicators = calculate_indicators(daily)

        # Get intraday 5m data (same as backtest: calculate_intraday_indicators on 5m)
        intraday = get_intraday_data(symbol, period="1d", interval="5m")
        if intraday is not None and not intraday.empty and len(intraday) >= 3:
            intraday_indicators = calculate_intraday_indicators(intraday)
            indicators.update(intraday_indicators)

            # Build price_data dict — mirrors backtest
            today_open = float(intraday.iloc[0]["Open"])
            prev_close = float(daily["Close"].iloc[-1])

            price_data = {
                "price": price,
                "open": today_open,
                "high": float(intraday["High"].max()),
                "low": float(intraday["Low"].min()),
                "volume": int(intraday["Volume"].sum()),
                "prev_close": prev_close,
                "change_pct": ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0,
            }
        else:
            prev_close = float(daily["Close"].iloc[-1])
            price_data = {
                "price": price,
                "open": price,
                "high": price,
                "low": price,
                "volume": 0,
                "prev_close": prev_close,
                "change_pct": ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0,
            }

        # Patterns — same as backtest
        vwap = indicators.get("vwap") or indicators.get("vwap_5m")
        prior_day_high = float(daily["High"].iloc[-1])
        prior_day_low = float(daily["Low"].iloc[-1])

        detected_patterns = detect_all_patterns(
            df_daily=daily,
            df_5m=intraday if intraday is not None and len(intraday) >= 10 else None,
            prior_day_high=prior_day_high,
            prior_day_low=prior_day_low,
            prior_day_close=prev_close,
            vwap=vwap,
        )
        patterns_text = format_patterns_for_prompt(detected_patterns)

        # Key levels — same as backtest
        key_levels = get_key_levels(
            df_daily=daily,
            df_5m=intraday if intraday is not None and len(intraday) >= 6 else None,
            vwap=vwap,
        )

        # Dual Thrust dynamic range — adaptive ORB thresholds
        today_open = price_data.get("open", price_data.get("price", 0))
        dt_levels = calculate_dual_thrust_range(daily, today_open)
        key_levels.update(dt_levels)

        # ── Call both signal engines — mirrors backtest ──
        regime_str = self.regime.state.regime if self.regime.state else "unknown"
        score_kwargs = dict(
            symbol=symbol,
            price_data=price_data,
            indicators=indicators,
            intraday_indicators=indicators,  # Already merged above
            patterns_text=patterns_text,
            levels=key_levels,
            phase=phase,
            regime=regime_str,
        )

        decision = None
        direction = "long"

        if ENABLE_LONG:
            long_decision = self.signal_engine.score(**score_kwargs)
        else:
            long_decision = None

        if ENABLE_SHORT:
            short_decision = self.short_signal_engine.score(**score_kwargs)
        else:
            short_decision = None

        # Pick the best signal — higher confidence wins
        long_ok = long_decision and long_decision.action == "BUY"
        short_ok = short_decision and short_decision.action == "SHORT"

        if long_ok and short_ok:
            if long_decision.confidence >= short_decision.confidence:
                decision = long_decision
                direction = "long"
            else:
                decision = short_decision
                direction = "short"
        elif long_ok:
            decision = long_decision
            direction = "long"
        elif short_ok:
            decision = short_decision
            direction = "short"
        else:
            return

        confidence = decision.confidence
        stop_loss = decision.stop_loss
        take_profit = decision.take_profit

        # Confidence threshold — identical to backtest
        if confidence < LIVE_RISK["min_confidence_to_trade"]:
            return

        # Require a detected pattern — block NO_SETUP trades
        pattern_name = decision.pattern if hasattr(decision, 'pattern') else ""
        if not pattern_name or pattern_name.lower() in ("unknown", "no setup", "no_setup", "none", ""):
            return

        # R:R check with slippage — identical to backtest
        risk = abs(price - stop_loss) + SLIPPAGE_PER_SHARE * 2
        reward = abs(take_profit - price) - SLIPPAGE_PER_SHARE * 2
        if risk <= 0 or reward <= 0 or reward / risk < LIVE_RISK["min_risk_reward_ratio"]:
            return

        # Position sizing — IDENTICAL to backtest
        conf_scale = _confidence_risk_scale(confidence)
        phase_mult = phase_config.get("size_multiplier", 1.0)
        if phase == "open":
            phase_mult = 1.5
        else:
            phase_mult = max(phase_mult, 0.40)

        # All patterns get equal sizing — no backtest-mined bias
        pattern = decision.pattern if hasattr(decision, 'pattern') else ""
        pattern_mult = 1.0

        # Confidence death zone 0.80-0.85:
        # Longs: 28% WR, -$1,592 across 53 trades → block entirely
        # Shorts: 58% WR, +$19 → keep but reduce size 50%
        if 0.80 <= confidence < 0.85:
            if direction == "long":
                return  # Block long trades in death zone
            else:
                pattern_mult *= 0.50

        risk_amount = (
            equity
            * LIVE_RISK["max_risk_per_trade_pct"]
            * conf_scale
            * phase_mult
            * regime_multiplier
        )

        stop_dist = abs(price - stop_loss) + SLIPPAGE_PER_SHARE * 2
        if stop_dist <= 0:
            return

        shares = int(risk_amount / (price * (stop_dist / price)))
        if shares <= 0:
            return

        # Cap position size — identical to backtest
        max_position_value = equity * LIVE_RISK["max_position_pct"]
        if shares * price > max_position_value:
            shares = int(max_position_value / price)
        if shares <= 0:
            return

        # Apply pattern sizing AFTER caps — identical to backtest
        shares = max(1, int(shares * pattern_mult))

        # ── EXECUTE — direction-aware ──
        action_label = "SHORT" if direction == "short" else "BUY"
        logger.info(
            f"{action_label} SIGNAL: {symbol} | {decision.pattern} | "
            f"conf={confidence:.2f} | score={decision.score:.0f} | "
            f"${price:.2f} | SL=${stop_loss:.2f} | TP=${take_profit:.2f} | "
            f"{shares} shares | {decision.reasoning}"
        )

        if direction == "short":
            order_id = self.broker.short_shares(symbol, shares)
        else:
            order_id = self.broker.buy_shares(symbol, shares)

        if not order_id:
            logger.error(f"Failed to execute {action_label.lower()} for {symbol}")
            return

        # Register position — mirrors SimulatedPosition from backtest
        now = datetime.now(timezone.utc)
        self.positions[symbol] = LivePosition(
            symbol=symbol,
            entry_price=price,
            quantity=shares,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=now,
            direction=direction,
            pattern=decision.pattern,
            confidence=confidence,
        )

        # Place broker-side stop loss for crash protection (direction-aware)
        stop_id = self.broker.place_stop_loss(
            symbol, shares, stop_loss,
            side="SHORT" if direction == "short" else "LONG",
        )
        if stop_id:
            self.positions[symbol].broker_stop_order_id = stop_id

        self.daily_trades += 1

        await self.telegram.send_message(
            f"*{action_label}*: {symbol} | {shares} shares @ ${price:.2f}\n"
            f"Pattern: {decision.pattern} | Conf: {confidence:.2f}\n"
            f"Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f}\n"
            f"{decision.reasoning}"
        )

    # ══════════════════════════════════════════════════════
    # POSITION MANAGEMENT — mirrors backtest _manage_positions_at_bar()
    # ══════════════════════════════════════════════════════

    async def _manage_positions(self):
        """Check stops, scale-outs, breakeven locks, time exits.

        Identical logic to backtest/engine.py _manage_positions_at_bar().
        """
        if not self.positions:
            return
        if not self._is_market_hours():
            return

        now = datetime.now(timezone.utc)

        for sym in list(self.positions.keys()):
            pos = self.positions[sym]

            try:
                price_data = get_current_price(sym)
                if not price_data:
                    continue
                current_price = price_data["price"]

                is_short = pos.direction == "short"
                side_label = "SHORT" if is_short else "LONG"

                # Update highest/lowest — identical to backtest
                if current_price > pos.highest_price:
                    pos.highest_price = current_price
                if current_price < pos.lowest_price:
                    pos.lowest_price = current_price

                # Check stop loss — direction-aware (identical to backtest)
                if is_short:
                    stop_hit = current_price >= pos.current_stop
                else:
                    stop_hit = current_price <= pos.current_stop

                if stop_hit:
                    logger.info(f"STOP HIT: {sym} ({side_label}) @ ${current_price:.2f} (stop=${pos.current_stop:.2f})")
                    await self._close_live_position(sym, "Stop loss hit")
                    continue

                # Calculate current R — direction-aware (identical to backtest)
                if is_short:
                    current_r = (pos.entry_price - current_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
                else:
                    current_r = (current_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
                hold_minutes = (now - pos.entry_time).total_seconds() / 60

                # 1. Breakeven lock at +1.0R — identical to backtest
                if not pos.breakeven_locked and current_r >= 1.0:
                    if is_short:
                        new_stop = min(pos.entry_price, pos.current_stop)
                        improved = new_stop < pos.current_stop
                    else:
                        new_stop = max(pos.entry_price, pos.current_stop)
                        improved = new_stop > pos.current_stop

                    if improved:
                        pos.current_stop = new_stop
                        pos.breakeven_locked = True
                        if pos.broker_stop_order_id:
                            new_id = self.broker.replace_stop_loss(
                                pos.broker_stop_order_id, sym,
                                pos.shares_remaining, pos.current_stop,
                                side="SHORT" if is_short else "LONG",
                            )
                            pos.broker_stop_order_id = new_id or ""
                        logger.info(f"BREAKEVEN LOCK: {sym} ({side_label}) stop → ${pos.current_stop:.2f}")

                # 2. Scale out at 1.0R — direction-aware
                target_1_hit = (current_price <= pos.r_target_1) if is_short else (current_price >= pos.r_target_1)
                if (pos.scale_out_stage == 0
                        and target_1_hit
                        and pos.shares_remaining > 1):
                    sell_qty = max(1, int(pos.quantity / 3))
                    actual_sell = min(sell_qty, pos.shares_remaining - 1)
                    if actual_sell > 0:
                        if is_short:
                            success = self.broker.buy_to_cover(sym, actual_sell)
                            pnl = (pos.entry_price - current_price) * actual_sell
                        else:
                            success = self.broker.sell_shares(sym, actual_sell)
                            pnl = (current_price - pos.entry_price) * actual_sell
                        if success:
                            r_mult = current_r
                            pos.shares_remaining -= actual_sell
                            pos.realized_pnl += pnl
                            pos.scale_out_stage = 1
                            pos.current_stop = pos.entry_price
                            pos.breakeven_locked = True
                            self.daily_pnl += pnl
                            self._daily_r += r_mult * (actual_sell / pos.quantity)
                            if pos.broker_stop_order_id:
                                new_id = self.broker.replace_stop_loss(
                                    pos.broker_stop_order_id, sym,
                                    pos.shares_remaining, pos.current_stop,
                                    side="SHORT" if is_short else "LONG",
                                )
                                pos.broker_stop_order_id = new_id or ""
                            logger.info(
                                f"SCALE OUT 1.0R: {sym} ({side_label}) {actual_sell} shares @ ${current_price:.2f} "
                                f"(+${pnl:.2f})"
                            )
                            await self.telegram.send_message(
                                f"*Scale Out 1.0R*: {sym} ({side_label}) | {actual_sell} shares @ ${current_price:.2f}\n"
                                f"P&L: ${pnl:+.2f} | Remaining: {pos.shares_remaining}"
                            )

                # 3. Scale out at 2.0R — direction-aware
                target_2_hit = (current_price <= pos.r_target_2) if is_short else (current_price >= pos.r_target_2)
                if (pos.scale_out_stage == 1
                        and target_2_hit
                        and pos.shares_remaining > 1):
                    sell_qty = max(1, int(pos.quantity / 3))
                    actual_sell = min(sell_qty, pos.shares_remaining - 1)
                    if actual_sell > 0:
                        if is_short:
                            success = self.broker.buy_to_cover(sym, actual_sell)
                            pnl = (pos.entry_price - current_price) * actual_sell
                        else:
                            success = self.broker.sell_shares(sym, actual_sell)
                            pnl = (current_price - pos.entry_price) * actual_sell
                        if success:
                            r_mult = current_r
                            pos.shares_remaining -= actual_sell
                            pos.realized_pnl += pnl
                            pos.scale_out_stage = 2
                            self.daily_pnl += pnl
                            self._daily_r += r_mult * (actual_sell / pos.quantity)
                            if pos.broker_stop_order_id:
                                new_id = self.broker.replace_stop_loss(
                                    pos.broker_stop_order_id, sym,
                                    pos.shares_remaining, pos.current_stop,
                                    side="SHORT" if is_short else "LONG",
                                )
                                pos.broker_stop_order_id = new_id or ""
                            logger.info(
                                f"SCALE OUT 2.0R: {sym} ({side_label}) {actual_sell} shares @ ${current_price:.2f} "
                                f"(+${pnl:.2f})"
                            )
                            await self.telegram.send_message(
                                f"*Scale Out 2.0R*: {sym} ({side_label}) | {actual_sell} shares @ ${current_price:.2f}\n"
                                f"P&L: ${pnl:+.2f} | Remaining: {pos.shares_remaining}"
                            )

                # 4. Time management — identical to backtest
                if hold_minutes >= 90:
                    logger.info(f"TIME STOP 90min: {sym} ({side_label})")
                    await self._close_live_position(sym, "Time stop (90min)")
                    continue
                elif hold_minutes >= 45 and current_r <= 0:
                    logger.info(f"TIME STOP 45min loser: {sym} ({side_label})")
                    await self._close_live_position(sym, "Time stop (45min loser)")
                    continue

                # 5. Trailing stop — direction-aware (identical to backtest)
                if pos.breakeven_locked:
                    trail_distance = 1.0 * pos.risk_per_share
                    if is_short:
                        trail_stop = pos.lowest_price + trail_distance
                        trail_stop = min(trail_stop, pos.entry_price)
                        improved = trail_stop < pos.current_stop
                    else:
                        trail_stop = pos.highest_price - trail_distance
                        trail_stop = max(trail_stop, pos.entry_price)
                        improved = trail_stop > pos.current_stop

                    if improved:
                        pos.current_stop = trail_stop
                        if pos.broker_stop_order_id:
                            new_id = self.broker.replace_stop_loss(
                                pos.broker_stop_order_id, sym,
                                pos.shares_remaining, pos.current_stop,
                                side="SHORT" if is_short else "LONG",
                            )
                            pos.broker_stop_order_id = new_id or ""

            except Exception as e:
                logger.error(f"Position management error for {sym}: {e}")

    async def _close_live_position(self, symbol: str, reason: str):
        """Close a position via broker and update tracking. Direction-aware."""
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        is_short = pos.direction == "short"
        price_data = get_current_price(symbol)
        current_price = price_data["price"] if price_data else pos.entry_price

        # Alpaca close_position works for both long and short
        success = self.broker.close_position(symbol)
        if not success:
            logger.error(f"Failed to close {symbol}")
            return

        # Direction-aware P&L
        if is_short:
            pnl = (pos.entry_price - current_price) * pos.shares_remaining
            r_mult = (pos.entry_price - current_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
        else:
            pnl = (current_price - pos.entry_price) * pos.shares_remaining
            r_mult = (current_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
        self.daily_pnl += pnl
        self._daily_r += r_mult

        # Track consecutive losses — identical to backtest
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= RISK["max_consecutive_losses"]:
                self.cooldown_until = datetime.now(timezone.utc) + timedelta(
                    minutes=RISK["cooldown_after_losses_minutes"]
                )
                logger.warning(f"Cooldown activated: {RISK['cooldown_after_losses_minutes']}min")
        else:
            self.consecutive_losses = 0

        side_label = "SHORT" if is_short else "LONG"
        del self.positions[symbol]

        logger.info(f"CLOSED {symbol} ({side_label}): P&L ${pnl:+,.2f} | R: {r_mult:+.2f} | {reason}")
        await self.telegram.send_message(
            f"*Position Closed*: {symbol} ({side_label})\n"
            f"P&L: ${pnl:+,.2f} | R: {r_mult:+.2f}\n"
            f"Reason: {reason}"
        )

    # ══════════════════════════════════════════════════════
    # SCHEDULED JOBS
    # ══════════════════════════════════════════════════════

    async def _refresh_hot_list(self):
        if not self._is_market_hours():
            return
        logger.info("Refreshing hot list...")
        self.scanner.scan_for_movers()

    async def _rebuild_universe(self):
        logger.info("Daily universe rebuild...")
        self.scanner.build_universe()
        self.scanner.scan_for_movers()

        # Reset daily counters
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self._daily_r = 0.0
        self.consecutive_losses = 0
        self.cooldown_until = None

        await self.telegram.send_message(
            f"*Pre-Market Scan*\n"
            f"Universe: {len(self.scanner.universe)} stocks\n"
            f"Hot list: {len(self.scanner.hot_list)} movers\n"
            f"{self.scanner.get_scan_summary()}"
        )

    async def _eod_close_all(self):
        """Close all positions at 3:50 PM — identical to backtest force close."""
        if not self.positions:
            logger.info("EOD: No positions to close")
            return

        logger.warning(f"EOD: Closing {len(self.positions)} positions")
        await self.telegram.send_message(f"*EOD CLOSE*: Closing {len(self.positions)} positions")

        for sym in list(self.positions.keys()):
            await self._close_live_position(sym, "EOD force close")
            await asyncio.sleep(0.5)

    async def _daily_summary(self):
        """End-of-day summary and shutdown."""
        account = self.broker.get_account()
        equity = account.get("equity", 0)

        await self.telegram.send_message(
            f"*Daily Summary*\n"
            f"Trades: {self.daily_trades}\n"
            f"P&L: ${self.daily_pnl:+,.2f}\n"
            f"R earned: {self._daily_r:+.1f}\n"
            f"Equity: ${equity:,.2f}"
        )

        # Auto-shutdown
        logger.info("Daily summary complete — shutting down")
        self._running = False

    async def _reconcile_positions(self):
        """On startup, register any existing broker positions. Detects direction."""
        broker_positions = self.broker.get_positions()
        for bp in broker_positions:
            sym = bp["symbol"]
            if sym not in self.positions:
                entry = bp["avg_entry_price"]
                qty = abs(int(bp["qty"]))
                # Detect direction from Alpaca side field
                direction = "short" if bp.get("side", "").lower() == "short" else "long"
                # Conservative stop: 3% from entry in the losing direction
                if direction == "short":
                    stop = entry * 1.03
                    target = entry * 0.95
                else:
                    stop = entry * 0.97
                    target = entry * 1.05
                self.positions[sym] = LivePosition(
                    symbol=sym,
                    entry_price=entry,
                    quantity=qty,
                    stop_loss=stop,
                    take_profit=target,
                    entry_time=datetime.now(timezone.utc),
                    direction=direction,
                    pattern="reconciled",
                )
                # Place broker stop if none exists (direction-aware)
                stop_id = self.broker.place_stop_loss(
                    sym, qty, stop,
                    side="SHORT" if direction == "short" else "LONG",
                )
                if stop_id:
                    self.positions[sym].broker_stop_order_id = stop_id
                logger.info(f"Reconciled {direction.upper()} position: {sym} {qty} shares @ ${entry:.2f}")

    # ══════════════════════════════════════════════════════
    # HELPERS — identical to backtest engine
    # ══════════════════════════════════════════════════════

    def _get_phase(self, hour: int, minute: int) -> str:
        """Determine market phase from ET time — identical to backtest."""
        t = hour * 60 + minute
        if t < 9 * 60 + 30:
            return "premarket"
        elif t < 10 * 60:
            return "open"
        elif t < 11 * 60:
            return "prime"
        elif t < 13 * 60 + 30:
            return "lunch"
        elif t < 15 * 60:
            return "afternoon"
        elif t < 15 * 60 + 50:
            return "power_hour"
        else:
            return "close"

    def _is_analysis_time(self, hour: int, minute: int) -> bool:
        """Check if current time is within ±5 min of a scheduled analysis time."""
        current = hour * 60 + minute
        for t in ANALYSIS_TIMES:
            target = t.hour * 60 + t.minute
            if abs(current - target) <= 5:
                return True
        return False

    def _is_market_hours(self) -> bool:
        """Check if market is open."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("US/Eastern"))
        if now.weekday() >= 5:
            return False
        market_open = now.replace(hour=9, minute=30, second=0)
        market_close = now.replace(hour=16, minute=0, second=0)
        return market_open <= now <= market_close

    def _check_sector_ok(self, symbol: str) -> bool:
        """Check sector concentration — identical to backtest."""
        target_group = None
        for group, members in _CORRELATED_GROUPS.items():
            if symbol in members:
                target_group = group
                break
        if target_group is None:
            return True
        held = [s for s in self.positions if s in _CORRELATED_GROUPS.get(target_group, set())]
        return len(held) < _MAX_SECTOR_CONCENTRATION


# ═══════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════

async def run():
    """Start the trading system."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for lib in ("httpx", "httpcore", "urllib3", "yfinance", "alpaca"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    trader = AutoTrader()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(trader.stop()))

    await trader.start()
