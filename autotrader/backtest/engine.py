"""Intraday backtesting engine — replays 5-minute bars through the full trading system.

Simulates exactly what the live system sees at each point in time:
- 5-minute bar replay with no look-ahead bias
- Same indicators, patterns, key levels, VWAP from 5-min data
- Same Claude prompts with time-of-day phase context
- Same risk manager checks (regime, confidence, position limits, sector correlation)
- Same PositionManager logic: scale-outs at 1R/2R, trailing stops, time exits
- Fills at next bar's open + slippage ($0.02/share)
- Force close everything at 3:50 PM simulated time
"""

import hashlib
import json
import logging
import math
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, time
from pathlib import Path

import numpy as np
import pandas as pd

from autotrader.config import BASE_DIR, RISK, PHASE_CONFIG

logger = logging.getLogger(__name__)

SLIPPAGE_PER_SHARE = 0.02
CLAUDE_CACHE_DIR = BASE_DIR / "data" / "claude_cache"
CLAUDE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _tz_aware_timestamp(dt, index):
    """Create a Timestamp compatible with a DataFrame's index timezone."""
    ts = pd.Timestamp(dt.date() if hasattr(dt, 'date') else dt)
    if hasattr(index, 'tz') and index.tz is not None:
        ts = ts.tz_localize(index.tz)
    return ts

# Default analysis times (ET) — more frequent = more trade opportunities
DEFAULT_ANALYSIS_TIMES = [
    time(9, 35),   # Open (ORB forming)
    time(9, 50),   # Late open
    time(10, 0),   # Prime start (10 AM reversal zone)
    time(10, 15),  # Prime
    time(10, 30),  # Prime mid
    time(10, 45),  # Prime late
    time(11, 15),  # Late morning
    time(11, 45),  # Lunch
    time(13, 0),   # Mid-day
    time(14, 0),   # Afternoon
    time(14, 30),  # Afternoon mid
    time(15, 0),   # Power hour start
    time(15, 15),  # Power hour mid
    time(15, 30),  # Power hour late
]

# Backtest-specific risk parameters (more aggressive than live for discovery)
BACKTEST_RISK = {
    "max_risk_per_trade_pct": 0.20,       # 20% max ($20k on $100k) — scaled by confidence
    "max_position_pct": 0.25,             # 25% max in one stock
    "max_total_exposure_pct": 0.80,       # 80% deployed at once
    "max_trades_per_day": 25,             # Room for 20+ trades
    "min_risk_reward_ratio": 1.5,         # Slightly relaxed from 2:1
    "min_confidence_to_trade": 0.50,      # Lower floor, confidence scaling handles the rest
    "analyze_count": 10,                  # Symbols per cycle (filtered by pre-filter)
}


def _confidence_risk_scale(confidence: float) -> float:
    """Scale risk amount by confidence. Higher confidence = bigger bet.

    0.50 confidence → 15% of max risk
    0.60 confidence → 30% of max risk
    0.70 confidence → 50% of max risk
    0.80 confidence → 75% of max risk
    0.90 confidence → 95% of max risk
    1.00 confidence → 100% of max risk
    """
    # Quadratic scaling: starts small, ramps up fast at high confidence
    # Clamp to [0.50, 1.0] range
    c = max(0.50, min(1.0, confidence))
    # Map 0.50-1.0 → 0.0-1.0, then square it and scale to 0.15-1.0
    normalized = (c - 0.50) / 0.50
    return 0.15 + 0.85 * (normalized ** 1.5)


# ═══════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════


@dataclass
class SimulatedFill:
    symbol: str
    side: str
    quantity: int
    price: float
    raw_price: float
    slippage: float
    timestamp: datetime
    pattern: str = ""
    confidence: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reasoning: str = ""


@dataclass
class SimulatedPosition:
    symbol: str
    entry_price: float
    quantity: int
    stop_loss: float
    take_profit: float
    entry_time: datetime
    pattern: str = ""
    confidence: float = 0.0
    risk_per_share: float = 0.0
    highest_price: float = 0.0
    scale_out_stage: int = 0
    shares_remaining: int = 0
    realized_pnl: float = 0.0
    r_target_1: float = 0.0
    r_target_2: float = 0.0
    current_stop: float = 0.0

    def __post_init__(self):
        self.risk_per_share = abs(self.entry_price - self.stop_loss)
        self.highest_price = self.entry_price
        self.shares_remaining = self.quantity
        self.r_target_1 = self.entry_price + self.risk_per_share
        self.r_target_2 = self.entry_price + self.risk_per_share * 2
        self.current_stop = self.stop_loss


@dataclass
class BacktestTrade:
    symbol: str
    pattern: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    r_multiple: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: str
    confidence: float = 0.0
    market_phase: str = ""
    slippage_cost: float = 0.0


@dataclass
class BacktestResult:
    start_date: str
    end_date: str
    trading_days: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_r: float = 0.0
    total_slippage: float = 0.0
    max_drawdown_pct: float = 0.0
    starting_equity: float = 100_000.0
    ending_equity: float = 100_000.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    daily_returns: list = field(default_factory=list)
    api_calls: int = 0
    cache_hits: int = 0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0

    @property
    def expectancy(self) -> float:
        return self.total_pnl / self.total_trades if self.total_trades > 0 else 0

    @property
    def profit_factor(self) -> float:
        gross_wins = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_losses = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_losses > 0:
            return gross_wins / gross_losses
        return float("inf") if gross_wins > 0 else 0

    @property
    def avg_r(self) -> float:
        return self.total_r / self.total_trades if self.total_trades > 0 else 0

    @property
    def return_pct(self) -> float:
        if self.starting_equity > 0:
            return (self.ending_equity - self.starting_equity) / self.starting_equity * 100
        return 0

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        return sum(wins) / len(wins) if wins else 0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.trades if t.pnl < 0]
        return sum(losses) / len(losses) if losses else 0

    @property
    def sharpe_ratio(self) -> float:
        if not self.daily_returns or len(self.daily_returns) < 5:
            return 0.0
        rets = pd.Series(self.daily_returns)
        if rets.std() == 0:
            return 0.0
        return float((rets.mean() / rets.std()) * math.sqrt(252))

    @property
    def passes_minimum_bar(self) -> bool:
        return (
            self.total_trades >= 200
            and self.profit_factor > 1.3
            and self.max_drawdown_pct < 8.0
            and self.sharpe_ratio > 0.75
        )


# ═══════════════════════════════════════════════════════════
# ENGINE
# ═══════════════════════════════════════════════════════════


class BacktestEngine:
    """Replay 5-minute bars through the full trading system.

    Usage:
        engine = BacktestEngine(start="2025-01-02", end="2025-03-28")
        result = engine.run()
    """

    def __init__(
        self,
        start: str,
        end: str,
        starting_equity: float = 100_000.0,
        model: str = "claude-haiku-4-5-20251001",
        max_cycles_per_day: int = 14,
        max_trades_per_day: int = 25,
    ):
        self.start_date = start
        self.end_date = end
        self.equity = starting_equity
        self.starting_equity = starting_equity
        self.model = model
        self.max_cycles_per_day = max_cycles_per_day
        self.max_trades_per_day = max_trades_per_day

        self.positions: dict[str, SimulatedPosition] = {}
        self.completed_trades: list[BacktestTrade] = []
        self.equity_curve: list[tuple[str, float]] = []
        self.daily_returns: list[float] = []
        self.api_calls = 0
        self.cache_hits = 0

        # Risk tracking (mirrors RiskManager)
        self.peak_equity = starting_equity
        self.consecutive_losses = 0
        self.cooldown_until: datetime | None = None
        self.daily_trades = 0
        self.daily_pnl = 0.0

        # Analysis times (trimmed to max_cycles_per_day)
        self.analysis_times = DEFAULT_ANALYSIS_TIMES[:max_cycles_per_day]

    def run(self) -> BacktestResult:
        """Run the full intraday backtest."""
        from autotrader.backtest.data_fetcher import (
            fetch_5m_bars, fetch_daily_bars, fetch_spy_daily,
            fetch_vix_daily, get_trading_days,
        )

        logger.info(
            f"Backtest: {self.start_date} to {self.end_date} | "
            f"Model: {self.model} | Cycles/day: {self.max_cycles_per_day}"
        )

        # 1. Get trading days
        trading_days = get_trading_days(self.start_date, self.end_date)
        if not trading_days:
            logger.error("No trading days found in range")
            return self._build_result(0)

        logger.info(f"Found {len(trading_days)} trading days")

        # 2. Prefetch SPY/VIX daily for regime
        spy_daily = fetch_spy_daily(self.start_date, self.end_date)
        vix_daily = fetch_vix_daily(self.start_date, self.end_date)

        # 3. Build universe candidates — use scanner scoring on daily data
        # For backtest, we use a fixed universe of high-liquidity names plus
        # dynamically scored movers per day
        universe = self._build_backtest_universe(trading_days)

        # 4. Prefetch all 5m data for the universe
        logger.info(f"Fetching 5-minute data for {len(universe)} symbols...")
        bars_5m: dict[str, pd.DataFrame] = {}
        daily_bars: dict[str, pd.DataFrame] = {}
        for sym in universe:
            bars_5m[sym] = fetch_5m_bars(sym, self.start_date, self.end_date)
            daily_bars[sym] = fetch_daily_bars(sym, self.start_date, self.end_date)
            if not bars_5m[sym].empty:
                logger.debug(f"  {sym}: {len(bars_5m[sym])} 5m bars")

        # Filter to symbols with actual data
        active_symbols = [s for s in universe if not bars_5m[s].empty and not daily_bars[s].empty]
        logger.info(f"Active symbols with data: {len(active_symbols)}")

        # 5. Replay each trading day
        prev_equity = self.equity
        for day_idx, day_dt in enumerate(trading_days):
            day_str = day_dt.strftime("%Y-%m-%d")
            self.daily_trades = 0
            self.daily_pnl = 0.0

            # Snapshot equity at start of day
            self.equity_curve.append((day_str, round(self.equity, 2)))

            # Determine regime for this day
            regime = self._get_regime(day_dt, spy_daily, vix_daily)
            regime_multiplier = self._regime_size_multiplier(regime)
            regime_context = self._regime_context_str(regime)

            # Find today's top candidates using scanner-like scoring
            todays_candidates = self._score_candidates_for_day(
                day_dt, active_symbols, daily_bars, bars_5m
            )

            if not todays_candidates:
                # Track daily return
                daily_ret = (self.equity - prev_equity) / prev_equity if prev_equity > 0 else 0
                self.daily_returns.append(daily_ret)
                prev_equity = self.equity
                continue

            # Replay the day bar by bar
            self._replay_day(
                day_dt=day_dt,
                candidates=todays_candidates,
                bars_5m=bars_5m,
                daily_bars=daily_bars,
                regime=regime,
                regime_multiplier=regime_multiplier,
                regime_context=regime_context,
            )

            # Track daily return
            daily_ret = (self.equity - prev_equity) / prev_equity if prev_equity > 0 else 0
            self.daily_returns.append(daily_ret)
            prev_equity = self.equity

            # Log progress
            if (day_idx + 1) % 5 == 0 or day_idx == len(trading_days) - 1:
                logger.info(
                    f"Day {day_idx+1}/{len(trading_days)} | {day_str} | "
                    f"Equity: ${self.equity:,.0f} | Trades: {len(self.completed_trades)} | "
                    f"API: {self.api_calls} | Cache: {self.cache_hits}"
                )

        return self._build_result(len(trading_days))

    def _replay_day(
        self,
        day_dt: datetime,
        candidates: list[str],
        bars_5m: dict[str, pd.DataFrame],
        daily_bars: dict[str, pd.DataFrame],
        regime: dict,
        regime_multiplier: float,
        regime_context: str,
    ):
        """Replay one trading day through 5-minute bars."""
        from autotrader.data.indicators import (
            calculate_indicators, get_signal_summary,
            calculate_intraday_indicators, get_intraday_signal_summary,
        )
        from autotrader.data.patterns import (
            detect_all_patterns, get_key_levels,
            format_patterns_for_prompt, format_levels_for_prompt,
        )

        day_str = day_dt.strftime("%Y-%m-%d")

        # Get all 5m bars for today, across all candidates
        # Build a time-sorted list of all timestamps for today
        all_timestamps = set()
        sym_day_bars: dict[str, pd.DataFrame] = {}

        for sym in candidates:
            df = bars_5m.get(sym)
            if df is None or df.empty:
                continue
            # Filter to this day's bars
            if hasattr(df.index, 'date'):
                mask = df.index.date == day_dt.date()
            else:
                mask = pd.Series([False] * len(df))
            day_df = df[mask]
            if day_df.empty:
                continue
            sym_day_bars[sym] = day_df
            all_timestamps.update(day_df.index)

        if not all_timestamps:
            return

        sorted_times = sorted(all_timestamps)

        # Determine which timestamps to run analysis cycles at
        analysis_indices = self._pick_analysis_bar_indices(sorted_times)

        # Pending orders: {symbol: {side, qty, stop_loss, take_profit, pattern, confidence, reasoning}}
        pending_buys: dict[str, dict] = {}

        for bar_idx, current_time in enumerate(sorted_times):
            current_et = current_time
            if hasattr(current_time, 'hour'):
                hour, minute = current_time.hour, current_time.minute
            else:
                continue

            # Convert to ET if needed (Alpaca returns UTC)
            try:
                from zoneinfo import ZoneInfo
            except ImportError:
                from backports.zoneinfo import ZoneInfo
            if current_time.tzinfo and str(current_time.tzinfo) != "US/Eastern":
                current_et = current_time.astimezone(ZoneInfo("US/Eastern"))
                hour, minute = current_et.hour, current_et.minute

            # Skip pre-market bars
            if hour < 9 or (hour == 9 and minute < 30):
                continue

            # Force close at 3:50 PM ET
            if hour == 15 and minute >= 50:
                self._force_close_all(current_time, sym_day_bars, bar_idx, sorted_times)
                break

            if hour >= 16:
                self._force_close_all(current_time, sym_day_bars, bar_idx, sorted_times)
                break

            # ── Fill pending buy orders at this bar's open ──
            for sym in list(pending_buys.keys()):
                if sym in sym_day_bars and sym not in self.positions:
                    today_df = sym_day_bars[sym]
                    if current_time in today_df.index:
                        bar = today_df.loc[current_time]
                        fill_price = float(bar["Open"]) + SLIPPAGE_PER_SHARE
                        order = pending_buys.pop(sym)

                        # Verify stop still valid after slippage
                        if fill_price <= order["stop_loss"]:
                            continue

                        self.positions[sym] = SimulatedPosition(
                            symbol=sym,
                            entry_price=fill_price,
                            quantity=order["qty"],
                            stop_loss=order["stop_loss"],
                            take_profit=order["take_profit"],
                            entry_time=current_time,
                            pattern=order["pattern"],
                            confidence=order["confidence"],
                        )
                        self.daily_trades += 1

            # ── Manage existing positions (check stops, scale-outs) ──
            self._manage_positions_at_bar(current_time, sym_day_bars, hour, minute)

            # ── Run Claude analysis at scheduled times ──
            if bar_idx in analysis_indices:
                phase = self._get_phase(hour, minute)
                phase_config = PHASE_CONFIG.get(phase, {})

                # Skip phases that block trading
                if phase_config.get("size_multiplier", 1.0) <= 0:
                    continue

                # Check daily loss halt
                if self.equity > 0 and self.daily_pnl < 0:
                    daily_loss_pct = abs(self.daily_pnl) / self.equity
                    if daily_loss_pct >= RISK["max_daily_loss_pct"]:
                        logger.debug(f"  Daily loss halt on {day_str}")
                        break

                # Check cooldown
                if self.cooldown_until and current_time < self.cooldown_until:
                    continue
                self.cooldown_until = None

                # Analyze top candidates
                analyze_count = min(BACKTEST_RISK["analyze_count"], len(candidates))
                for sym in candidates[:analyze_count]:
                    if self.daily_trades >= self.max_trades_per_day:
                        break
                    if sym in self.positions:
                        continue  # Already in this stock
                    if sym in pending_buys:
                        continue
                    if sym not in sym_day_bars:
                        continue

                    today_df = sym_day_bars[sym]
                    # Only use bars up to current_time (no look-ahead)
                    visible_bars = today_df[today_df.index <= current_time]
                    if len(visible_bars) < 3:
                        continue

                    # Get daily data up to prior day (no look-ahead)
                    daily = daily_bars.get(sym)
                    if daily is None or daily.empty:
                        continue
                    cutoff = _tz_aware_timestamp(day_dt, daily.index)
                    daily_to_date = daily[daily.index < cutoff]
                    if len(daily_to_date) < 20:
                        continue

                    # Calculate indicators
                    indicators = calculate_indicators(daily_to_date)
                    signal_summary = get_signal_summary(indicators)

                    intraday_indicators = calculate_intraday_indicators(visible_bars)
                    intraday_summary = get_intraday_signal_summary(intraday_indicators)
                    indicators.update(intraday_indicators)

                    # Current bar data
                    current_bar = visible_bars.iloc[-1]
                    price = float(current_bar["Close"])
                    today_open = float(today_df.iloc[0]["Open"]) if len(today_df) > 0 else price
                    prev_close = float(daily_to_date["Close"].iloc[-1]) if len(daily_to_date) > 0 else price

                    price_data = {
                        "price": price,
                        "open": today_open,
                        "high": float(visible_bars["High"].max()),
                        "low": float(visible_bars["Low"].min()),
                        "volume": int(visible_bars["Volume"].sum()),
                        "prev_close": prev_close,
                        "change_pct": ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0,
                    }

                    # Patterns
                    vwap = indicators.get("vwap") or indicators.get("vwap_5m")
                    prior_day_high = float(daily_to_date["High"].iloc[-1]) if len(daily_to_date) > 0 else None
                    prior_day_low = float(daily_to_date["Low"].iloc[-1]) if len(daily_to_date) > 0 else None

                    detected_patterns = detect_all_patterns(
                        df_daily=daily_to_date,
                        df_5m=visible_bars if len(visible_bars) >= 10 else None,
                        prior_day_high=prior_day_high,
                        prior_day_low=prior_day_low,
                        prior_day_close=prev_close,
                        vwap=vwap,
                    )
                    patterns_text = format_patterns_for_prompt(detected_patterns)

                    key_levels = get_key_levels(
                        df_daily=daily_to_date,
                        df_5m=visible_bars if len(visible_bars) >= 6 else None,
                        vwap=vwap,
                    )
                    levels_text = format_levels_for_prompt(key_levels)

                    # Scanner flags (simplified for backtest)
                    vol_avg = indicators.get("volume_sma_20", 1) or 1
                    rvol = price_data["volume"] / vol_avg if vol_avg > 0 else 0
                    gap_pct = ((today_open - prev_close) / prev_close * 100) if prev_close > 0 else 0
                    flags = []
                    if rvol >= 2.0:
                        flags.append(f"RVOL: {rvol:.1f}x")
                    if abs(gap_pct) >= 2.0:
                        flags.append(f"Gap: {gap_pct:+.1f}%")
                    scanner_flags = " | ".join(flags) if flags else "Watchlist stock"

                    # Build portfolio context
                    positions_list = []
                    total_position_value = 0
                    for ps, pos in self.positions.items():
                        pv = pos.entry_price * pos.shares_remaining
                        positions_list.append({
                            "symbol": ps,
                            "qty": pos.shares_remaining,
                            "market_value": pv,
                            "unrealized_pnl": 0,
                        })
                        total_position_value += pv

                    portfolio = {
                        "equity": self.equity,
                        "cash": self.equity - total_position_value,
                        "buying_power": self.equity - total_position_value,
                        "positions": positions_list,
                        "daily_pnl": self.daily_pnl,
                    }

                    # Check total exposure
                    if self.equity > 0 and total_position_value / self.equity >= BACKTEST_RISK["max_total_exposure_pct"]:
                        continue

                    # Check sector concentration
                    if not self._check_sector_ok(sym):
                        continue

                    # Technical pre-filter: skip boring setups before paying for Claude
                    if not self._should_call_claude(price_data, indicators, patterns_text):
                        continue

                    # Ask Claude
                    decision = self._ask_claude(
                        symbol=sym,
                        price_data=price_data,
                        indicators=indicators,
                        signal_summary=signal_summary,
                        intraday_summary=intraday_summary,
                        portfolio=portfolio,
                        scanner_flags=scanner_flags,
                        patterns_text=patterns_text,
                        levels_text=levels_text,
                        phase=phase,
                        regime_context=regime_context,
                        day_str=day_str,
                        time_str=f"{hour:02d}{minute:02d}",
                    )

                    if not decision:
                        continue

                    action = decision.get("action", "HOLD").upper()
                    confidence = float(decision.get("confidence", 0))
                    stop_loss = decision.get("stop_loss")
                    take_profit = decision.get("take_profit")

                    # Validate BUY
                    if action != "BUY" or not stop_loss or not take_profit:
                        continue

                    stop_loss = float(stop_loss)
                    take_profit = float(take_profit)

                    # Confidence threshold
                    if confidence < BACKTEST_RISK["min_confidence_to_trade"]:
                        continue

                    # R:R check with slippage
                    risk = abs(price - stop_loss) + SLIPPAGE_PER_SHARE * 2
                    reward = abs(take_profit - price) - SLIPPAGE_PER_SHARE * 2
                    if risk <= 0 or reward <= 0 or reward / risk < BACKTEST_RISK["min_risk_reward_ratio"]:
                        continue

                    # Confidence-based position sizing
                    # Max risk = 20% of equity ($20k on $100k), scaled by confidence
                    conf_scale = _confidence_risk_scale(confidence)
                    phase_mult = phase_config.get("size_multiplier", 1.0)
                    # Ensure lunch/open still trade, just smaller
                    phase_mult = max(phase_mult, 0.40)
                    risk_amount = (
                        self.equity
                        * BACKTEST_RISK["max_risk_per_trade_pct"]
                        * conf_scale
                        * phase_mult
                        * regime_multiplier
                    )

                    stop_dist = abs(price - stop_loss) + SLIPPAGE_PER_SHARE * 2
                    if stop_dist <= 0:
                        continue

                    shares = int(risk_amount / (price * (stop_dist / price)))
                    if shares <= 0:
                        continue

                    # Cap by max position and buying power
                    max_shares = int(self.equity * BACKTEST_RISK["max_position_pct"] / price)
                    max_by_cash = int(portfolio["cash"] / price)
                    shares = min(shares, max_shares, max_by_cash)
                    if shares <= 0:
                        continue

                    # Queue order to fill at next bar's open
                    pending_buys[sym] = {
                        "qty": shares,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "pattern": decision.get("pattern", "unknown"),
                        "confidence": confidence,
                        "reasoning": decision.get("reasoning", ""),
                        "phase": phase,
                    }

        # End of day: force close anything remaining
        if self.positions:
            last_time = sorted_times[-1] if sorted_times else day_dt
            self._force_close_all(last_time, sym_day_bars, len(sorted_times) - 1, sorted_times)

    def _manage_positions_at_bar(
        self, current_time: datetime, sym_day_bars: dict, hour: int, minute: int
    ):
        """Check stops, scale-outs, trailing stops, and time exits for all positions."""
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            if sym not in sym_day_bars:
                continue

            today_df = sym_day_bars[sym]
            if current_time not in today_df.index:
                continue

            bar = today_df.loc[current_time]
            bar_low = float(bar["Low"])
            bar_high = float(bar["High"])
            bar_close = float(bar["Close"])

            # Update highest price
            if bar_high > pos.highest_price:
                pos.highest_price = bar_high

            # Check stop loss hit (bar low touched stop)
            if bar_low <= pos.current_stop:
                exit_price = pos.current_stop - SLIPPAGE_PER_SHARE
                self._close_position(sym, exit_price, current_time, "Stop loss hit")
                continue

            # Scale out at 1R
            if (pos.scale_out_stage == 0
                    and bar_high >= pos.r_target_1
                    and pos.shares_remaining > 1):
                sell_qty = max(1, int(pos.quantity / 3))
                actual_sell = min(sell_qty, pos.shares_remaining - 1)
                if actual_sell > 0:
                    exit_price = pos.r_target_1 - SLIPPAGE_PER_SHARE
                    pnl = (exit_price - pos.entry_price) * actual_sell
                    slippage = SLIPPAGE_PER_SHARE * 2 * actual_sell
                    r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

                    self.completed_trades.append(BacktestTrade(
                        symbol=sym, pattern=pos.pattern,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        quantity=actual_sell, pnl=pnl, r_multiple=r_mult,
                        entry_time=pos.entry_time, exit_time=current_time,
                        exit_reason="Scale out 1R (1/3)",
                        confidence=pos.confidence, slippage_cost=slippage,
                    ))
                    self.equity += pnl
                    self.daily_pnl += pnl
                    pos.shares_remaining -= actual_sell
                    pos.realized_pnl += pnl
                    pos.scale_out_stage = 1
                    pos.current_stop = pos.entry_price  # Move to breakeven

            # Scale out at 2R
            elif (pos.scale_out_stage == 1
                  and bar_high >= pos.r_target_2
                  and pos.shares_remaining > 1):
                sell_qty = max(1, int(pos.quantity / 3))
                actual_sell = min(sell_qty, pos.shares_remaining - 1)
                if actual_sell > 0:
                    exit_price = pos.r_target_2 - SLIPPAGE_PER_SHARE
                    pnl = (exit_price - pos.entry_price) * actual_sell
                    slippage = SLIPPAGE_PER_SHARE * 2 * actual_sell
                    r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

                    self.completed_trades.append(BacktestTrade(
                        symbol=sym, pattern=pos.pattern,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        quantity=actual_sell, pnl=pnl, r_multiple=r_mult,
                        entry_time=pos.entry_time, exit_time=current_time,
                        exit_reason="Scale out 2R (2/3)",
                        confidence=pos.confidence, slippage_cost=slippage,
                    ))
                    self.equity += pnl
                    self.daily_pnl += pnl
                    pos.shares_remaining -= actual_sell
                    pos.realized_pnl += pnl
                    pos.scale_out_stage = 2

            # Trailing stop (after first scale-out)
            if pos.scale_out_stage >= 1:
                # Use 2% fixed trailing (ATR not available per bar in backtest)
                trail_stop = pos.highest_price * 0.98
                if trail_stop > pos.current_stop:
                    pos.current_stop = trail_stop

            # Time exit: 30 min to close → sell half
            minutes_to_close = (16 * 60) - (hour * 60 + minute)
            if minutes_to_close <= 30 and minutes_to_close > 15 and pos.shares_remaining > 1:
                sell_qty = max(1, int(pos.shares_remaining / 2))
                exit_price = bar_close - SLIPPAGE_PER_SHARE
                pnl = (exit_price - pos.entry_price) * sell_qty
                slippage = SLIPPAGE_PER_SHARE * 2 * sell_qty
                r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

                self.completed_trades.append(BacktestTrade(
                    symbol=sym, pattern=pos.pattern,
                    entry_price=pos.entry_price, exit_price=exit_price,
                    quantity=sell_qty, pnl=pnl, r_multiple=r_mult,
                    entry_time=pos.entry_time, exit_time=current_time,
                    exit_reason=f"Time exit ({minutes_to_close}min to close)",
                    confidence=pos.confidence, slippage_cost=slippage,
                ))
                self.equity += pnl
                self.daily_pnl += pnl
                pos.shares_remaining -= sell_qty
                pos.realized_pnl += pnl

            # Time exit: 15 min to close → close all
            if minutes_to_close <= 15:
                exit_price = bar_close - SLIPPAGE_PER_SHARE
                self._close_position(sym, exit_price, current_time, f"EOD close ({minutes_to_close}min)")

    def _force_close_all(self, close_time, sym_day_bars, bar_idx, sorted_times):
        """Force close all positions at EOD."""
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            if sym in sym_day_bars:
                today_df = sym_day_bars[sym]
                # Use the last available bar's close
                visible = today_df[today_df.index <= close_time]
                if not visible.empty:
                    exit_price = float(visible.iloc[-1]["Close"]) - SLIPPAGE_PER_SHARE
                else:
                    exit_price = pos.entry_price  # Fallback
            else:
                exit_price = pos.entry_price

            self._close_position(sym, exit_price, close_time, "EOD force close")

    def _close_position(self, symbol: str, exit_price: float, exit_time, reason: str):
        """Close a position and record the trade."""
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        qty = pos.shares_remaining
        if qty <= 0:
            del self.positions[symbol]
            return

        pnl = (exit_price - pos.entry_price) * qty
        slippage = SLIPPAGE_PER_SHARE * 2 * qty
        r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

        self.completed_trades.append(BacktestTrade(
            symbol=symbol, pattern=pos.pattern,
            entry_price=pos.entry_price, exit_price=exit_price,
            quantity=qty, pnl=pnl, r_multiple=r_mult,
            entry_time=pos.entry_time, exit_time=exit_time,
            exit_reason=reason,
            confidence=pos.confidence, slippage_cost=slippage,
        ))
        self.equity += pnl
        self.daily_pnl += pnl

        # Track consecutive losses
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= RISK["max_consecutive_losses"]:
                self.cooldown_until = exit_time + timedelta(minutes=RISK["cooldown_after_losses_minutes"])
        else:
            self.consecutive_losses = 0

        # Track drawdown
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        del self.positions[symbol]

    def _should_call_claude(
        self, price_data: dict, indicators: dict, patterns_text: str
    ) -> bool:
        """Technical pre-filter: only call Claude on genuinely interesting setups.

        Uses data already computed (free). Eliminates ~60% of boring-stock calls
        that would return HOLD anyway.

        Scoring:
          RVOL ≥ 3x       → 40pts   RVOL ≥ 2x     → 25pts
          RVOL ≥ 1.5x     → 15pts   RVOL ≥ 1.2x   →  5pts
          Change ≥ 4%     → 25pts   Change ≥ 2%   → 15pts   Change ≥ 1% → 8pts
          Intraday ≥ 3%   → 20pts   Intraday ≥1.5% → 10pts  Intraday ≥0.8% → 5pts
          Pattern detected → 20pts
          RSI ≥70 or ≤30  → 15pts   RSI ≥65 or ≤35 →  8pts
          At VWAP (≤0.4%) → 15pts   Far VWAP (≥2%) → 10pts

        Threshold: 25 pts required to call Claude.
        Examples that PASS:
          RVOL 1.5x (15) + gap 1% (8) + any pattern (20) = 43 ✓
          RVOL 2x (25) alone = 25 ✓
          Gap 2% (15) + pattern (20) = 35 ✓
        Examples that FAIL (saved API call):
          Flat stock, RVOL 1.1x, no gap, no patterns = 0 ✗
          Minor 0.8% move, no RVOL, no pattern = 5 ✗
        """
        score = 0

        # 1. Relative volume — most important day-trading signal
        rvol = float(indicators.get("relative_volume") or 0)
        if rvol >= 3.0:
            score += 40
        elif rvol >= 2.0:
            score += 25
        elif rvol >= 1.5:
            score += 15
        elif rvol >= 1.2:
            score += 5

        # 2. Daily change % (gap + intraday combined)
        change_pct = abs(float(price_data.get("change_pct", 0)))
        if change_pct >= 4.0:
            score += 25
        elif change_pct >= 2.0:
            score += 15
        elif change_pct >= 1.0:
            score += 8

        # 3. Intraday move from today's open
        price = float(price_data.get("price", 0))
        today_open = float(price_data.get("open", price) or price)
        if today_open > 0 and price > 0:
            intraday_move = abs((price - today_open) / today_open * 100)
            if intraday_move >= 3.0:
                score += 20
            elif intraday_move >= 1.5:
                score += 10
            elif intraday_move >= 0.8:
                score += 5

        # 4. Detected patterns (already computed — free signal)
        if patterns_text and len(patterns_text.strip()) > 15 and "No patterns" not in patterns_text:
            score += 20

        # 5. RSI at extremes (momentum continuation or oversold bounce)
        rsi = float(indicators.get("rsi") or 50)
        if rsi >= 70 or rsi <= 30:
            score += 15
        elif rsi >= 65 or rsi <= 35:
            score += 8

        # 6. VWAP proximity — at VWAP = high-probability reclaim/rejection zone
        vwap = indicators.get("vwap") or indicators.get("vwap_5m")
        if vwap and price > 0:
            vwap_dist_pct = abs((price - float(vwap)) / float(vwap) * 100)
            if vwap_dist_pct <= 0.4:   # Right at VWAP = prime setup zone
                score += 15
            elif vwap_dist_pct >= 2.0:  # Extended from VWAP = potential reclaim trade
                score += 10

        return score >= 25

    def _ask_claude(
        self, symbol, price_data, indicators, signal_summary,
        intraday_summary, portfolio, scanner_flags, patterns_text,
        levels_text, phase, regime_context, day_str, time_str,
    ) -> dict | None:
        """Ask Claude for a trading decision, with caching."""
        import anthropic
        from autotrader.config import ANTHROPIC_API_KEY, CLAUDE_MAX_TOKENS
        from autotrader.brain.prompts import SYSTEM_PROMPT

        # Build cache key from all inputs
        cache_data = f"{symbol}_{day_str}_{time_str}_{price_data.get('price')}_{price_data.get('volume')}_{phase}"
        cache_key = hashlib.md5(cache_data.encode()).hexdigest()
        cache_file = CLAUDE_CACHE_DIR / f"{cache_key}.json"

        # Check cache
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    self.cache_hits += 1
                    return json.load(f)
            except Exception:
                pass

        # Build the prompt (simplified version of build_analysis_prompt for backtest)
        prompt = self._build_backtest_prompt(
            symbol=symbol,
            price_data=price_data,
            indicators=indicators,
            signal_summary=signal_summary,
            intraday_summary=intraday_summary,
            portfolio=portfolio,
            scanner_flags=scanner_flags,
            patterns_text=patterns_text,
            levels_text=levels_text,
            phase=phase,
            regime_context=regime_context,
        )

        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=self.model,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            self.api_calls += 1
            raw_text = response.content[0].text.strip()

            # Parse JSON
            text = raw_text
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0].strip()

            decision = json.loads(text)

            # Save to cache
            try:
                with open(cache_file, "w") as f:
                    json.dump(decision, f)
            except Exception:
                pass

            # Rate limit (be gentle with API)
            time_module.sleep(0.3)
            return decision

        except json.JSONDecodeError as e:
            logger.debug(f"JSON parse error for {symbol}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Claude API error for {symbol}: {e}")
            time_module.sleep(1)
            return None

    def _build_backtest_prompt(
        self, symbol, price_data, indicators, signal_summary,
        intraday_summary, portfolio, scanner_flags, patterns_text,
        levels_text, phase, regime_context,
    ) -> str:
        """Build analysis prompt for backtest (mirrors live prompt structure)."""
        vol = indicators.get("volume", 0)
        vol_avg = indicators.get("volume_sma_20", 1) or 1
        vol_vs_avg = f"{vol/vol_avg:.1f}x average" if vol_avg > 0 else "N/A"

        positions = portfolio.get("positions", [])
        current_pos = "None"
        open_pos_strs = []
        for pos in positions:
            ps = f"{pos['symbol']}: {pos['qty']} shares (${pos.get('market_value', 0):,.0f})"
            open_pos_strs.append(ps)
            if pos["symbol"] == symbol:
                current_pos = ps
        open_positions = ", ".join(open_pos_strs) if open_pos_strs else "None"

        prev_close = price_data.get("prev_close", 0)
        today_open = price_data.get("open", 0)
        gap_pct = ((today_open - prev_close) / prev_close * 100) if prev_close else 0

        macd_cross = "None recently"
        if indicators.get("macd_bullish_cross"):
            macd_cross = "BULLISH cross"
        elif indicators.get("macd_bearish_cross"):
            macd_cross = "BEARISH cross"

        phase_desc = {
            "open": "OPENING DRIVE — Gap & Go, ORB, Red-to-Green only. Size 50%.",
            "prime": "PRIME TIME — First Pullback, Flags, VWAP Reclaim. Full size.",
            "lunch": "LUNCH DEAD ZONE — Mean reversion only, 35% size, 70%+ confidence required.",
            "afternoon": "AFTERNOON — Continuations, VWAP tests. 85% size.",
            "power_hour": "POWER HOUR — Momentum, HOD/LOD breaks. Full size, quick targets.",
            "close": "CLOSING — EXIT ONLY.",
        }.get(phase, phase)

        return f"""Analyze {symbol} for a day trade.

═══ MARKET REGIME ═══
{regime_context}

═══ WHY IS THIS STOCK ON YOUR RADAR? ═══
{scanner_flags}

═══ PRICE ACTION ═══
Price: ${price_data['price']} | Change: {price_data.get('change_pct', 0):+.2f}% | Gap: {gap_pct:+.2f}%
Open: ${today_open} | High: ${price_data.get('high', 0)} | Low: ${price_data.get('low', 0)} | Prev Close: ${prev_close}
Volume: {price_data.get('volume', 0):,} ({vol_vs_avg})

═══ PATTERNS DETECTED ═══
{patterns_text}

═══ KEY PRICE LEVELS ═══
{levels_text}

═══ TECHNICAL INDICATORS ═══
Trend: SMA(20)={indicators.get('sma_20', 'N/A')} | SMA(50)={indicators.get('sma_50', 'N/A')} | SMA(200)={indicators.get('sma_200', 'N/A')}
Short-term: EMA(9)={indicators.get('ema_9', 'N/A')} | EMA(21)={indicators.get('ema_21', 'N/A')} | EMA bullish: {indicators.get('ema_bullish', 'N/A')}
Momentum: RSI(14)={indicators.get('rsi', 'N/A')} | MACD hist={indicators.get('macd_histogram', 'N/A')} | MACD cross: {macd_cross}
Stochastic: K={indicators.get('stoch_k', 'N/A')} D={indicators.get('stoch_d', 'N/A')}
Volatility: BB upper={indicators.get('bb_upper', 'N/A')} mid={indicators.get('bb_middle', 'N/A')} lower={indicators.get('bb_lower', 'N/A')} width={indicators.get('bb_width_pct', 'N/A')}%
Range: ATR={indicators.get('atr', 'N/A')} ({indicators.get('atr_pct', 'N/A')}% of price) | VWAP={indicators.get('vwap', 'N/A')}
Volume: OBV {indicators.get('obv_trend', 'N/A')} | Relative volume: {indicators.get('relative_volume', 'N/A')}x avg

═══ SIGNAL SUMMARY ═══
{signal_summary}

═══ INTRADAY (5-min chart) ═══
{intraday_summary}

═══ CATALYST ═══
Backtest mode — historical catalyst data not available

═══ YOUR PORTFOLIO ═══
Equity: ${portfolio['equity']:,.2f} | Cash: ${portfolio['cash']:,.2f}
Position in {symbol}: {current_pos}
All positions: {open_positions}
Today's P&L: ${portfolio.get('daily_pnl', 0):,.2f} | Trade budget: {self.daily_trades}/8 used

═══ CURRENT TIME ═══
{phase_desc}

What do you see? What's the trade?

JSON only:
{{"action": "BUY|SELL|HOLD", "symbol": "{symbol}", "confidence": 0.0, "entry_price": 0.0, "quantity": 0, "stop_loss": 0.0, "take_profit": 0.0, "pattern": "name", "reasoning": "your thinking"}}"""

    def _pick_analysis_bar_indices(self, sorted_times: list) -> set[int]:
        """Pick which bar indices to run analysis at, matching analysis_times."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        indices = set()
        for target_time in self.analysis_times:
            best_idx = None
            best_diff = timedelta(hours=24)
            for i, ts in enumerate(sorted_times):
                ts_et = ts.astimezone(ZoneInfo("US/Eastern")) if ts.tzinfo else ts
                ts_time = ts_et.time()
                # Compare times
                diff = abs(
                    timedelta(hours=ts_time.hour, minutes=ts_time.minute)
                    - timedelta(hours=target_time.hour, minutes=target_time.minute)
                )
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
            if best_idx is not None and best_diff < timedelta(minutes=15):
                indices.add(best_idx)

        return indices

    def _get_phase(self, hour: int, minute: int) -> str:
        """Determine market phase from ET time."""
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

    def _get_regime(self, day_dt: datetime, spy_daily: pd.DataFrame, vix_daily: pd.DataFrame) -> dict:
        """Determine market regime for a given day (no look-ahead)."""
        regime = {"spy_trend": "unknown", "vix_level": "elevated", "regime": "bear_quiet",
                  "spy_price": 0, "spy_sma_50": 0, "vix_price": 18}

        if spy_daily is not None and not spy_daily.empty:
            spy_to_date = spy_daily[spy_daily.index < _tz_aware_timestamp(day_dt, spy_daily.index)]
            if len(spy_to_date) >= 50:
                spy_price = float(spy_to_date["Close"].iloc[-1])
                spy_sma_50 = float(spy_to_date["Close"].tail(50).mean())
                regime["spy_price"] = spy_price
                regime["spy_sma_50"] = spy_sma_50
                regime["spy_trend"] = "bullish" if spy_price >= spy_sma_50 else "bearish"

        if vix_daily is not None and not vix_daily.empty:
            vix_to_date = vix_daily[vix_daily.index < _tz_aware_timestamp(day_dt, vix_daily.index)]
            if len(vix_to_date) >= 1:
                vix_price = float(vix_to_date["Close"].iloc[-1])
                regime["vix_price"] = vix_price
                if vix_price < 16:
                    regime["vix_level"] = "quiet"
                elif vix_price < 22:
                    regime["vix_level"] = "elevated"
                elif vix_price < 30:
                    regime["vix_level"] = "volatile"
                else:
                    regime["vix_level"] = "extreme"

        spy_trend = regime["spy_trend"]
        vix_level = regime["vix_level"]
        if spy_trend == "bullish" and vix_level in ("quiet", "elevated"):
            regime["regime"] = "bull_quiet"
        elif spy_trend == "bullish":
            regime["regime"] = "bull_volatile"
        elif vix_level in ("quiet", "elevated"):
            regime["regime"] = "bear_quiet"
        else:
            regime["regime"] = "bear_volatile"

        return regime

    def _regime_size_multiplier(self, regime: dict) -> float:
        return {
            "bull_quiet": 1.0,
            "bull_volatile": 0.70,
            "bear_quiet": 0.50,
            "bear_volatile": 0.25,
        }.get(regime["regime"], 0.5)

    def _regime_context_str(self, regime: dict) -> str:
        desc = {
            "bull_quiet": "BULLISH + LOW VOL — Best conditions. Full aggression.",
            "bull_volatile": "BULLISH + HIGH VOL — Tighter stops, favor pullbacks.",
            "bear_quiet": "BEARISH + LOW VOL — Favor mean reversion, reduce size.",
            "bear_volatile": "BEARISH + HIGH VOL — Minimal size, A+ only.",
        }.get(regime["regime"], "Unknown")
        trend = "UP" if regime["spy_trend"] == "bullish" else "DOWN"
        return (
            f"SPY: ${regime['spy_price']:.2f} | Trend: {trend} (50SMA=${regime['spy_sma_50']:.2f})\n"
            f"VIX: {regime['vix_price']:.1f} ({regime['vix_level']})\n"
            f"REGIME: {regime['regime'].upper().replace('_', ' ')} — {desc}"
        )

    def _build_backtest_universe(self, trading_days: list) -> list[str]:
        """Build universe of symbols to test.

        Uses high-liquidity names that are likely to show up in the scanner.
        """
        # Core liquid names that would regularly appear in the scanner
        core = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD",
            "JPM", "BA", "NFLX", "DIS", "CRM", "PYPL", "SQ", "UBER",
            "COIN", "PLTR", "SOFI", "RIVN", "NIO", "SNAP", "ROKU", "SHOP",
            "ABNB", "DKNG", "MARA", "RIOT", "SMCI", "ARM", "MU", "AVGO",
        ]
        return core

    def _score_candidates_for_day(
        self, day_dt, symbols, daily_bars, bars_5m
    ) -> list[str]:
        """Score and rank candidates for a specific day using prior-day data."""
        scores = {}
        for sym in symbols:
            daily = daily_bars.get(sym)
            if daily is None or daily.empty:
                continue

            # Only use data up to prior day
            to_date = daily[daily.index < _tz_aware_timestamp(day_dt, daily.index)]
            if len(to_date) < 10:
                continue

            try:
                close = to_date["Close"]
                volume = to_date["Volume"]
                price = float(close.iloc[-1])
                prev_price = float(close.iloc[-2])
                avg_vol = float(volume.tail(20).mean())

                change_pct = (price - prev_price) / prev_price * 100

                # Score based on recent activity
                score = 0
                # Volatility (bigger moves = more opportunity)
                if abs(change_pct) >= 3:
                    score += 20
                elif abs(change_pct) >= 1.5:
                    score += 10

                # 5-day trend
                if len(close) >= 5:
                    five_day = (price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100
                    if abs(five_day) >= 8:
                        score += 15

                # Near 20-day high/low
                if len(to_date) >= 20:
                    high_20 = float(to_date["High"].tail(20).max())
                    low_20 = float(to_date["Low"].tail(20).min())
                    if price >= high_20 * 0.97:
                        score += 10
                    if price <= low_20 * 1.03:
                        score += 10

                # Volume spike
                if avg_vol > 0:
                    last_vol = float(volume.iloc[-1])
                    if last_vol / avg_vol >= 2.0:
                        score += 15

                scores[sym] = score
            except Exception:
                continue

        # Return top 20 by score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [sym for sym, _ in ranked[:20]]

    def _check_sector_ok(self, symbol: str) -> bool:
        """Check sector concentration against current positions."""
        from autotrader.risk.manager import CORRELATED_GROUPS, MAX_SECTOR_CONCENTRATION

        target_group = None
        for group, members in CORRELATED_GROUPS.items():
            if symbol in members:
                target_group = group
                break

        if target_group is None:
            return True

        held = [s for s in self.positions if s in CORRELATED_GROUPS.get(target_group, [])]
        return len(held) < MAX_SECTOR_CONCENTRATION

    def _build_result(self, trading_days: int) -> BacktestResult:
        """Build the final result object."""
        result = BacktestResult(
            start_date=self.start_date,
            end_date=self.end_date,
            trading_days=trading_days,
            total_trades=len(self.completed_trades),
            wins=sum(1 for t in self.completed_trades if t.pnl > 0),
            losses=sum(1 for t in self.completed_trades if t.pnl <= 0),
            total_pnl=sum(t.pnl for t in self.completed_trades),
            total_r=sum(t.r_multiple for t in self.completed_trades),
            total_slippage=sum(t.slippage_cost for t in self.completed_trades),
            starting_equity=self.starting_equity,
            ending_equity=self.equity,
            trades=self.completed_trades,
            equity_curve=self.equity_curve,
            daily_returns=self.daily_returns,
            api_calls=self.api_calls,
            cache_hits=self.cache_hits,
        )

        # Max drawdown from equity curve
        peak = self.starting_equity
        max_dd = 0.0
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = round(max_dd, 2)

        return result


# ═══════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════


def format_backtest_result(result: BacktestResult) -> str:
    """Format backtest results for display."""
    lines = [
        "",
        "═══════════════════════════════════════════════════════════",
        "              INTRADAY BACKTEST RESULTS",
        "═══════════════════════════════════════════════════════════",
        f"Period: {result.start_date} → {result.end_date} ({result.trading_days} trading days)",
        "",
        "─── PERFORMANCE ───",
        f"  Total Trades:    {result.total_trades}",
        f"  Win Rate:        {result.win_rate:.1f}% ({result.wins}W / {result.losses}L)",
        f"  Avg Win:         ${result.avg_win:,.2f}",
        f"  Avg Loss:        ${result.avg_loss:,.2f}",
        f"  Profit Factor:   {result.profit_factor:.2f}",
        f"  Total P&L:       ${result.total_pnl:,.2f}",
        f"  Return:          {result.return_pct:+.2f}%",
        f"  Max Drawdown:    {result.max_drawdown_pct:.1f}%",
        f"  Sharpe Ratio:    {result.sharpe_ratio:.2f}",
        f"  Expectancy:      ${result.expectancy:,.2f}/trade",
        f"  Total R:         {result.total_r:+.1f}",
        f"  Avg R/Trade:     {result.avg_r:+.2f}",
        f"  Total Slippage:  ${result.total_slippage:,.2f}",
        f"  Equity:          ${result.starting_equity:,.0f} → ${result.ending_equity:,.0f}",
        f"  API Calls:       {result.api_calls} | Cache Hits: {result.cache_hits}",
    ]

    # PASS/FAIL thresholds
    lines.append("")
    lines.append("─── PASS/FAIL CRITERIA ───")
    checks = [
        (result.total_trades >= 200, f"  200+ trades:     {result.total_trades} {'PASS' if result.total_trades >= 200 else 'FAIL'}"),
        (result.profit_factor > 1.3, f"  PF > 1.3:        {result.profit_factor:.2f} {'PASS' if result.profit_factor > 1.3 else 'FAIL'}"),
        (result.max_drawdown_pct < 8.0, f"  Max DD < 8%:     {result.max_drawdown_pct:.1f}% {'PASS' if result.max_drawdown_pct < 8.0 else 'FAIL'}"),
        (result.sharpe_ratio > 0.75, f"  Sharpe > 0.75:   {result.sharpe_ratio:.2f} {'PASS' if result.sharpe_ratio > 0.75 else 'FAIL'}"),
    ]
    all_pass = all(c[0] for c in checks)
    for _, text in checks:
        lines.append(text)
    lines.append("")
    if all_pass:
        lines.append("  >>> OVERALL: PASS <<<")
    else:
        lines.append("  >>> OVERALL: FAIL <<<")

    # Pattern breakdown
    pattern_perf: dict[str, dict] = {}
    for t in result.trades:
        p = t.pattern or "unknown"
        if p not in pattern_perf:
            pattern_perf[p] = {"trades": 0, "wins": 0, "pnl": 0.0, "r": 0.0}
        pattern_perf[p]["trades"] += 1
        pattern_perf[p]["pnl"] += t.pnl
        pattern_perf[p]["r"] += t.r_multiple
        if t.pnl > 0:
            pattern_perf[p]["wins"] += 1

    if pattern_perf:
        lines.append("")
        lines.append("─── PATTERN BREAKDOWN ───")
        for p, s in sorted(pattern_perf.items(), key=lambda x: x[1]["trades"], reverse=True):
            wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
            avg_r = s["r"] / s["trades"] if s["trades"] > 0 else 0
            lines.append(
                f"  {p:25s} {s['trades']:3d} trades | WR: {wr:5.1f}% | "
                f"Avg R: {avg_r:+.2f} | P&L: ${s['pnl']:>9,.2f}"
            )

    # Phase breakdown
    phase_perf: dict[str, dict] = {}
    for t in result.trades:
        p = t.market_phase or "unknown"
        if not p or p == "unknown":
            # Infer phase from exit time
            if hasattr(t.entry_time, 'hour'):
                h = t.entry_time.hour
                if h < 10:
                    p = "open"
                elif h < 11:
                    p = "prime"
                elif h < 14:
                    p = "lunch"
                elif h < 15:
                    p = "afternoon"
                else:
                    p = "power_hour"
        if p not in phase_perf:
            phase_perf[p] = {"trades": 0, "wins": 0, "pnl": 0.0, "r": 0.0}
        phase_perf[p]["trades"] += 1
        phase_perf[p]["pnl"] += t.pnl
        phase_perf[p]["r"] += t.r_multiple
        if t.pnl > 0:
            phase_perf[p]["wins"] += 1

    if phase_perf:
        lines.append("")
        lines.append("─── PHASE BREAKDOWN ───")
        phase_order = ["open", "prime", "lunch", "afternoon", "power_hour", "unknown"]
        for p in phase_order:
            if p not in phase_perf:
                continue
            s = phase_perf[p]
            wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
            avg_r = s["r"] / s["trades"] if s["trades"] > 0 else 0
            lines.append(
                f"  {p:15s} {s['trades']:3d} trades | WR: {wr:5.1f}% | "
                f"Avg R: {avg_r:+.2f} | P&L: ${s['pnl']:>9,.2f}"
            )

    lines.append("")
    lines.append("═══════════════════════════════════════════════════════════")
    return "\n".join(lines)
