"""Standalone SHORT-only backtest — completely independent from the long system.

Tests the ShortSignalEngine in isolation before integration.
Reuses existing data infrastructure but has its own position management
with correct short P&L logic.

Usage:
    python -m autotrader.backtest.short_backtest --start 2024-01-02 --end 2024-12-31
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time
from pathlib import Path

import numpy as np
import pandas as pd

from autotrader.config import BASE_DIR, RISK, PHASE_CONFIG

logger = logging.getLogger(__name__)

SLIPPAGE_PER_SHARE = 0.01
CACHE_DIR = BASE_DIR / "data" / "backtest_cache"

# Reuse the same analysis times as the long system
ANALYSIS_TIMES = [
    time(9, 35), time(10, 0), time(10, 30), time(10, 50),
    time(11, 0), time(11, 45), time(13, 0), time(14, 0),
    time(14, 45), time(15, 15),
]

RISK_PARAMS = {
    "max_risk_per_trade_pct": 0.15,
    "max_position_pct": 0.20,
    "max_total_exposure_pct": 0.80,
    "max_trades_per_day": 25,
    "min_risk_reward_ratio": 1.5,
    "min_confidence_to_trade": 0.65,
    "analyze_count": 10,
}


# ═══════════════════════════════════════════════════
# COST MODEL (same as long system)
# ═══════════════════════════════════════════════════

class CostModel:
    SEC_FEE_RATE = 0.0000278
    TAF_FEE_PER_SHARE = 0.000166
    FINRA_FEE_PER_SHARE = 0.0000130
    MIN_COST = 0.02

    @classmethod
    def round_trip_cost(cls, entry_price, exit_price, shares):
        sell_notional = exit_price * shares
        sec_fee = sell_notional * cls.SEC_FEE_RATE
        taf_fee = shares * cls.TAF_FEE_PER_SHARE
        finra_fee = shares * cls.FINRA_FEE_PER_SHARE
        total = sec_fee + taf_fee + finra_fee
        return max(cls.MIN_COST, round(total, 4))


# ═══════════════════════════════════════════════════
# SHORT POSITION
# ═══════════════════════════════════════════════════

@dataclass
class ShortPosition:
    symbol: str
    entry_price: float      # Price we sold short at
    quantity: int
    stop_loss: float        # ABOVE entry (buy back at loss if price rises)
    take_profit: float      # BELOW entry (buy back at profit if price falls)
    entry_time: datetime
    pattern: str = ""
    confidence: float = 0.0
    risk_per_share: float = 0.0
    lowest_price: float = 0.0    # Best price for short (lowest)
    highest_price: float = 0.0   # Worst price for short (highest)
    scale_out_stage: int = 0
    shares_remaining: int = 0
    realized_pnl: float = 0.0
    r_target_1: float = 0.0     # First cover target (BELOW entry)
    r_target_2: float = 0.0     # Second cover target (BELOW entry)
    current_stop: float = 0.0   # Trailing stop (ABOVE current price)
    breakeven_locked: bool = False
    mae: float = 0.0            # Max adverse excursion (price going UP)
    mfe: float = 0.0            # Max favorable excursion (price going DOWN)

    def __post_init__(self):
        self.risk_per_share = abs(self.stop_loss - self.entry_price)
        self.lowest_price = self.entry_price
        self.highest_price = self.entry_price
        self.shares_remaining = self.quantity
        # Targets are BELOW entry for shorts
        self.r_target_1 = self.entry_price - self.risk_per_share * 0.5
        self.r_target_2 = self.entry_price - self.risk_per_share * 1.5
        self.current_stop = self.stop_loss


@dataclass
class ShortTrade:
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
    trading_costs: float = 0.0
    mae: float = 0.0
    mfe: float = 0.0


# ═══════════════════════════════════════════════════
# SHORT BACKTEST ENGINE
# ═══════════════════════════════════════════════════

class ShortBacktest:
    def __init__(self, start: str, end: str, starting_equity: float = 100_000.0,
                 signal_params: dict | None = None):
        self.start_date = start
        self.end_date = end
        self.equity = starting_equity
        self.starting_equity = starting_equity

        from autotrader.signals.short_engine import ShortSignalEngine
        self.signal_engine = ShortSignalEngine(params=signal_params)

        self.positions: dict[str, ShortPosition] = {}
        self.completed_trades: list[ShortTrade] = []
        self.equity_curve: list[tuple[str, float]] = []
        self.daily_returns: list[float] = []

        self.peak_equity = starting_equity
        self.consecutive_losses = 0
        self.cooldown_until = None
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.pattern_performance: dict[str, list[float]] = {}

        # Sector concentration
        self._CORRELATED_GROUPS = {
            "mega_tech": {"AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "NFLX"},
            "semis": {"NVDA", "AMD", "INTC", "MU", "AVGO", "QCOM", "TSM", "MRVL", "LRCX", "KLAC", "AMAT"},
            "ev_auto": {"TSLA", "RIVN", "LCID", "NIO", "F", "GM", "LI", "XPEV"},
            "banking": {"JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW"},
            "energy": {"XOM", "CVX", "COP", "SLB", "OXY", "MPC", "VLO", "PSX"},
            "biotech": {"MRNA", "BNTX", "PFE", "JNJ", "ABBV", "BMY", "LLY", "MRK"},
        }

    def run(self) -> dict:
        from autotrader.backtest.data_fetcher import (
            fetch_5m_bars, fetch_daily_bars, fetch_spy_daily,
            fetch_vix_daily, get_trading_days,
        )
        from autotrader.backtest.engine import BacktestEngine, _tz_aware_timestamp

        # 1. Get trading days
        trading_days = get_trading_days(self.start_date, self.end_date)
        if not trading_days:
            return self._build_result(0)

        # 2. SPY/VIX for regime
        spy_daily = fetch_spy_daily(self.start_date, self.end_date)
        vix_daily = fetch_vix_daily(self.start_date, self.end_date)

        # 3. Build universe (reuse the long engine's universe builder)
        temp_engine = BacktestEngine(start=self.start_date, end=self.end_date, deterministic=True)
        broad_universe = temp_engine._build_broad_universe()

        # 4. Daily bars
        daily_bars = {}
        for sym in broad_universe:
            daily_bars[sym] = fetch_daily_bars(sym, self.start_date, self.end_date)
        daily_bars = {s: d for s, d in daily_bars.items() if not d.empty}

        # 5. Scanner + 5m bars
        bars_5m = {}
        daily_hot_lists = {}
        for day_dt in trading_days:
            day_str = day_dt.strftime("%Y-%m-%d")
            hot_list = temp_engine._scan_for_day(day_dt, daily_bars)
            daily_hot_lists[day_str] = hot_list
            for sym in hot_list:
                if sym not in bars_5m:
                    bars_5m[sym] = fetch_5m_bars(sym, self.start_date, self.end_date)
            bars_5m = {s: d for s, d in bars_5m.items() if not d.empty}

        # 6. Run each day
        for day_idx, day_dt in enumerate(trading_days):
            day_str = day_dt.strftime("%Y-%m-%d")
            self.daily_trades = 0
            self.daily_pnl = 0.0
            day_start_equity = self.equity

            candidates = daily_hot_lists.get(day_str, [])
            if not candidates:
                continue

            regime = self._get_regime(day_dt, spy_daily, vix_daily, _tz_aware_timestamp)
            regime_label = regime.get("regime", "unknown")
            regime_mult = self._get_regime_multiplier(regime_label)

            self._replay_day_short(
                day_dt, candidates, bars_5m, daily_bars, regime, regime_mult, _tz_aware_timestamp
            )

            daily_return = (self.equity - day_start_equity) / day_start_equity if day_start_equity > 0 else 0
            self.daily_returns.append(daily_return)
            self.equity_curve.append((day_str, self.equity))

            if self.equity > self.peak_equity:
                self.peak_equity = self.equity

            if day_idx % 20 == 0:
                logger.info(f"Day {day_idx+1}/{len(trading_days)} | {day_str} | Equity: ${self.equity:,.0f} | Trades: {len(self.completed_trades)}")

        return self._build_result(len(trading_days))

    def _replay_day_short(self, day_dt, candidates, bars_5m, daily_bars, regime, regime_mult, _tz_aware_timestamp):
        from autotrader.data.indicators import (
            calculate_indicators, get_signal_summary,
            calculate_intraday_indicators, get_intraday_signal_summary,
        )
        from autotrader.data.patterns import (
            detect_all_patterns, get_key_levels,
            format_patterns_for_prompt, format_levels_for_prompt,
        )

        day_str = day_dt.strftime("%Y-%m-%d")

        # Build day bars
        all_timestamps = set()
        sym_day_bars = {}
        for sym in candidates:
            df = bars_5m.get(sym)
            if df is None or df.empty:
                continue
            if hasattr(df.index, 'date'):
                mask = df.index.date == day_dt.date()
            else:
                continue
            day_df = df[mask]
            if day_df.empty:
                continue
            sym_day_bars[sym] = day_df
            all_timestamps.update(day_df.index)

        if not all_timestamps:
            return

        sorted_times = sorted(all_timestamps)
        analysis_indices = self._pick_analysis_bar_indices(sorted_times)

        pending_shorts = {}

        for bar_idx, current_time in enumerate(sorted_times):
            if not hasattr(current_time, 'hour'):
                continue
            hour, minute = current_time.hour, current_time.minute

            try:
                from zoneinfo import ZoneInfo
            except ImportError:
                from backports.zoneinfo import ZoneInfo
            if current_time.tzinfo and str(current_time.tzinfo) != "US/Eastern":
                current_et = current_time.astimezone(ZoneInfo("US/Eastern"))
                hour, minute = current_et.hour, current_et.minute

            if hour < 9 or (hour == 9 and minute < 30):
                continue
            if hour == 15 and minute >= 50:
                self._force_close_all(current_time, sym_day_bars)
                break
            if hour >= 16:
                self._force_close_all(current_time, sym_day_bars)
                break

            # Fill pending short orders
            for sym in list(pending_shorts.keys()):
                if sym in sym_day_bars and sym not in self.positions:
                    today_df = sym_day_bars[sym]
                    if current_time in today_df.index:
                        bar = today_df.loc[current_time]
                        # Short entry: sell at open MINUS slippage (slightly worse fill)
                        fill_price = float(bar["Open"]) - SLIPPAGE_PER_SHARE
                        order = pending_shorts.pop(sym)

                        # Widen stop by 15%
                        risk_dist = abs(order["stop_loss"] - fill_price)
                        buffered_stop = order["stop_loss"] + risk_dist * 0.15

                        if fill_price >= buffered_stop:
                            continue

                        self.positions[sym] = ShortPosition(
                            symbol=sym,
                            entry_price=fill_price,
                            quantity=order["qty"],
                            stop_loss=buffered_stop,
                            take_profit=order["take_profit"],
                            entry_time=current_time,
                            pattern=order["pattern"],
                            confidence=order["confidence"],
                        )
                        self.daily_trades += 1

            # Manage existing short positions
            self._manage_short_positions(current_time, sym_day_bars, hour, minute)

            # Run analysis at scheduled times
            if bar_idx in analysis_indices:
                phase = self._get_phase(hour, minute)
                phase_config = PHASE_CONFIG.get(phase, {})

                if phase_config.get("size_multiplier", 1.0) <= 0:
                    continue
                if phase == "lunch":
                    continue
                if hour >= 15 or (hour == 14 and minute >= 30):
                    continue

                # Daily loss halt
                if self.equity > 0 and self.daily_pnl < 0:
                    if abs(self.daily_pnl) / self.equity >= RISK["max_daily_loss_pct"]:
                        break

                # Cooldown
                if self.cooldown_until and current_time < self.cooldown_until:
                    continue
                self.cooldown_until = None

                analyze_count = min(RISK_PARAMS["analyze_count"], len(candidates))
                for sym in candidates[:analyze_count]:
                    if self.daily_trades >= RISK_PARAMS["max_trades_per_day"]:
                        break
                    if sym in self.positions or sym in pending_shorts:
                        continue
                    if sym not in sym_day_bars:
                        continue

                    today_df = sym_day_bars[sym]
                    visible_bars = today_df[today_df.index <= current_time]
                    if len(visible_bars) < 3:
                        continue

                    daily = daily_bars.get(sym)
                    if daily is None or daily.empty:
                        continue
                    cutoff = _tz_aware_timestamp(day_dt, daily.index)
                    daily_to_date = daily[daily.index < cutoff]
                    if len(daily_to_date) < 20:
                        continue

                    # Calculate indicators
                    indicators = calculate_indicators(daily_to_date)
                    intraday_indicators = calculate_intraday_indicators(visible_bars)
                    indicators.update(intraday_indicators)

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

                    # Score for SHORT
                    sig = self.signal_engine.score(
                        symbol=sym,
                        price_data=price_data,
                        indicators=indicators,
                        intraday_indicators=intraday_indicators,
                        patterns_text=patterns_text,
                        levels=key_levels,
                        phase=phase,
                        regime=regime.get("regime", "unknown"),
                    )

                    if sig.action != "SHORT":
                        continue

                    if sig.confidence < RISK_PARAMS["min_confidence_to_trade"]:
                        continue

                    # R:R check
                    risk = abs(sig.stop_loss - price) + SLIPPAGE_PER_SHARE * 2
                    reward = abs(price - sig.take_profit) - SLIPPAGE_PER_SHARE * 2
                    if risk <= 0 or reward <= 0 or reward / risk < RISK_PARAMS["min_risk_reward_ratio"]:
                        continue

                    # Exposure check
                    total_pos_value = sum(p.entry_price * p.shares_remaining for p in self.positions.values())
                    if self.equity > 0 and total_pos_value / self.equity >= RISK_PARAMS["max_total_exposure_pct"]:
                        continue

                    # Sector check
                    if not self._check_sector_ok(sym):
                        continue

                    # Position sizing
                    phase_mult = max(phase_config.get("size_multiplier", 1.0), 0.40)
                    if phase == "open":
                        phase_mult = 1.5
                    risk_amount = (
                        self.equity * RISK_PARAMS["max_risk_per_trade_pct"]
                        * phase_mult * regime_mult
                    )
                    stop_dist = abs(sig.stop_loss - price) + SLIPPAGE_PER_SHARE * 2
                    if stop_dist <= 0:
                        continue
                    shares = int(risk_amount / (price * (stop_dist / price)))
                    if shares <= 0:
                        continue
                    max_shares = int(self.equity * RISK_PARAMS["max_position_pct"] / price)
                    cash = self.equity - total_pos_value
                    max_by_cash = int(cash / price)
                    shares = min(shares, max_shares, max_by_cash)
                    if shares <= 0:
                        continue

                    pending_shorts[sym] = {
                        "qty": shares,
                        "stop_loss": sig.stop_loss,
                        "take_profit": sig.take_profit,
                        "pattern": sig.pattern,
                        "confidence": sig.confidence,
                        "reasoning": sig.reasoning,
                        "phase": phase,
                    }

        # EOD close
        if self.positions:
            last_time = sorted_times[-1] if sorted_times else day_dt
            self._force_close_all(last_time, sym_day_bars)

    def _manage_short_positions(self, current_time, sym_day_bars, hour, minute):
        """Manage short positions — INVERTED P&L logic."""
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

            # Track extremes (inverted for shorts)
            if bar_low < pos.lowest_price:
                pos.lowest_price = bar_low
            if bar_high > pos.highest_price:
                pos.highest_price = bar_high

            # MAE/MFE (inverted for shorts)
            if pos.entry_price > 0:
                # Adverse = price going UP (bad for short)
                adverse = (bar_high - pos.entry_price) / pos.entry_price * 100
                # Favorable = price going DOWN (good for short)
                favorable = (pos.entry_price - bar_low) / pos.entry_price * 100
                if adverse > pos.mae:
                    pos.mae = adverse
                if favorable > pos.mfe:
                    pos.mfe = favorable

            # Stop loss check — for shorts, stop triggers when price goes ABOVE stop
            if bar_close >= pos.current_stop:
                # Cover at stop + slippage (buying back = price goes up = worse)
                exit_price = pos.current_stop + SLIPPAGE_PER_SHARE
                self._close_short(sym, exit_price, current_time, "Stop loss hit")
                continue

            # R calculation for shorts (inverted)
            current_r = (pos.entry_price - bar_close) / pos.risk_per_share if pos.risk_per_share > 0 else 0
            hold_minutes = (current_time - pos.entry_time).total_seconds() / 60

            # Breakeven lock at +0.3R
            if not pos.breakeven_locked and current_r >= 0.3:
                pos.current_stop = min(pos.entry_price, pos.current_stop)  # Move stop DOWN to breakeven
                pos.breakeven_locked = True

            # Scale out at 0.5R (cover 1/3)
            if (pos.scale_out_stage == 0
                    and bar_low <= pos.r_target_1  # Price dropped to target
                    and pos.shares_remaining > 1):
                sell_qty = max(1, int(pos.quantity / 3))
                actual_sell = min(sell_qty, pos.shares_remaining - 1)
                if actual_sell > 0:
                    exit_price = pos.r_target_1 + SLIPPAGE_PER_SHARE
                    costs = CostModel.round_trip_cost(pos.entry_price, exit_price, actual_sell)
                    pnl = (pos.entry_price - exit_price) * actual_sell - costs
                    r_mult = (pos.entry_price - exit_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

                    self.completed_trades.append(ShortTrade(
                        symbol=sym, pattern=pos.pattern,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        quantity=actual_sell, pnl=pnl, r_multiple=r_mult,
                        entry_time=pos.entry_time, exit_time=current_time,
                        exit_reason="Scale out 0.5R (1/3)",
                        confidence=pos.confidence, trading_costs=costs,
                        mae=pos.mae, mfe=pos.mfe,
                    ))
                    self.equity += pnl
                    self.daily_pnl += pnl
                    pos.shares_remaining -= actual_sell
                    pos.realized_pnl += pnl
                    pos.scale_out_stage = 1
                    pos.current_stop = pos.entry_price  # Move to breakeven
                    pos.breakeven_locked = True

            # Scale out at 1.5R (cover another 1/3)
            if (pos.scale_out_stage == 1
                    and bar_low <= pos.r_target_2
                    and pos.shares_remaining > 1):
                sell_qty = max(1, int(pos.quantity / 3))
                actual_sell = min(sell_qty, pos.shares_remaining - 1)
                if actual_sell > 0:
                    exit_price = pos.r_target_2 + SLIPPAGE_PER_SHARE
                    costs = CostModel.round_trip_cost(pos.entry_price, exit_price, actual_sell)
                    pnl = (pos.entry_price - exit_price) * actual_sell - costs
                    r_mult = (pos.entry_price - exit_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

                    self.completed_trades.append(ShortTrade(
                        symbol=sym, pattern=pos.pattern,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        quantity=actual_sell, pnl=pnl, r_multiple=r_mult,
                        entry_time=pos.entry_time, exit_time=current_time,
                        exit_reason="Scale out 1.5R (1/3)",
                        confidence=pos.confidence, trading_costs=costs,
                        mae=pos.mae, mfe=pos.mfe,
                    ))
                    self.equity += pnl
                    self.daily_pnl += pnl
                    pos.shares_remaining -= actual_sell
                    pos.realized_pnl += pnl
                    pos.scale_out_stage = 2

            # Time stops (same as longs)
            if hold_minutes >= 45:
                exit_price = bar_close + SLIPPAGE_PER_SHARE
                self._close_short(sym, exit_price, current_time, "Time stop (45min)")
                continue
            elif hold_minutes >= 20 and current_r <= 0:
                exit_price = bar_close + SLIPPAGE_PER_SHARE
                self._close_short(sym, exit_price, current_time, "Time stop (20min loser)")
                continue

            # Trailing stop (inverted — trail DOWN from lowest price)
            if pos.breakeven_locked:
                trail_distance = 0.5 * pos.risk_per_share
                trail_stop = pos.lowest_price + trail_distance  # Trails UP from lowest
                trail_stop = min(trail_stop, pos.entry_price)   # Ceiling at breakeven
                if trail_stop < pos.current_stop:
                    pos.current_stop = trail_stop

    def _close_short(self, symbol, exit_price, exit_time, reason):
        if symbol not in self.positions:
            return
        pos = self.positions[symbol]
        qty = pos.shares_remaining
        if qty <= 0:
            del self.positions[symbol]
            return

        costs = CostModel.round_trip_cost(pos.entry_price, exit_price, qty)
        # SHORT P&L = (entry - exit) * shares - costs
        pnl = (pos.entry_price - exit_price) * qty - costs
        r_mult = (pos.entry_price - exit_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

        self.completed_trades.append(ShortTrade(
            symbol=symbol, pattern=pos.pattern,
            entry_price=pos.entry_price, exit_price=exit_price,
            quantity=qty, pnl=pnl, r_multiple=r_mult,
            entry_time=pos.entry_time, exit_time=exit_time,
            exit_reason=reason,
            confidence=pos.confidence, trading_costs=costs,
            mae=pos.mae, mfe=pos.mfe,
        ))
        self.equity += pnl
        self.daily_pnl += pnl

        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= RISK["max_consecutive_losses"]:
                self.cooldown_until = exit_time + timedelta(minutes=RISK["cooldown_after_losses_minutes"])
        else:
            self.consecutive_losses = 0

        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        del self.positions[symbol]

    def _force_close_all(self, close_time, sym_day_bars):
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            if sym in sym_day_bars:
                today_df = sym_day_bars[sym]
                visible = today_df[today_df.index <= close_time]
                if not visible.empty:
                    # Cover at close + slippage (buying back)
                    exit_price = float(visible.iloc[-1]["Close"]) + SLIPPAGE_PER_SHARE
                else:
                    exit_price = pos.entry_price
            else:
                exit_price = pos.entry_price
            self._close_short(sym, exit_price, close_time, "EOD force close")

    # ═══════════════════════════════════════════
    # HELPERS (borrowed from long engine)
    # ═══════════════════════════════════════════

    def _pick_analysis_bar_indices(self, sorted_times):
        indices = set()
        for target in ANALYSIS_TIMES:
            try:
                from zoneinfo import ZoneInfo
            except ImportError:
                from backports.zoneinfo import ZoneInfo
            best_idx = None
            best_diff = float("inf")
            for i, t in enumerate(sorted_times):
                et = t.astimezone(ZoneInfo("US/Eastern")) if t.tzinfo else t
                target_minutes = target.hour * 60 + target.minute
                bar_minutes = et.hour * 60 + et.minute
                diff = abs(bar_minutes - target_minutes)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
            if best_idx is not None and best_diff <= 10:
                indices.add(best_idx)
        return indices

    def _get_phase(self, hour, minute):
        total_min = hour * 60 + minute
        if total_min < 570:
            return "premarket"
        if total_min < 600:
            return "open"
        if total_min < 660:
            return "prime"
        if total_min < 810:
            return "lunch"
        if total_min < 900:
            return "afternoon"
        if total_min < 950:
            return "power_hour"
        return "close"

    def _get_regime(self, day_dt, spy_daily, vix_daily, _tz_aware_timestamp):
        from autotrader.backtest.engine import BacktestEngine
        temp = BacktestEngine.__new__(BacktestEngine)
        return BacktestEngine._get_regime(temp, day_dt, spy_daily, vix_daily)

    def _get_regime_multiplier(self, regime_label):
        mult_map = {
            "bull_quiet": 0.80,      # Harder to short in bull
            "bull_volatile": 1.0,    # Volatility helps shorts
            "bear_quiet": 1.0,       # Bear market = short heaven
            "bear_volatile": 1.10,   # Best environment for shorts
        }
        return mult_map.get(regime_label, 0.90)

    def _check_sector_ok(self, sym):
        count_in_group = {}
        for s in self.positions:
            for group_name, members in self._CORRELATED_GROUPS.items():
                if s in members:
                    count_in_group[group_name] = count_in_group.get(group_name, 0) + 1
        for group_name, members in self._CORRELATED_GROUPS.items():
            if sym in members and count_in_group.get(group_name, 0) >= 2:
                return False
        return True

    def _build_result(self, num_days):
        trades = self.completed_trades
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = sum(1 for t in trades if t.pnl <= 0)
        total_pnl = sum(t.pnl for t in trades)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else 0

        # Max drawdown
        max_dd = 0
        peak = self.starting_equity
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Sharpe
        if self.daily_returns:
            avg_return = np.mean(self.daily_returns)
            std_return = np.std(self.daily_returns)
            sharpe = (avg_return / std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe = 0

        # Pattern breakdown
        pattern_stats = {}
        for t in trades:
            pat = t.pattern or "unknown"
            if pat not in pattern_stats:
                pattern_stats[pat] = {"trades": 0, "wins": 0, "pnl": 0.0}
            pattern_stats[pat]["trades"] += 1
            if t.pnl > 0:
                pattern_stats[pat]["wins"] += 1
            pattern_stats[pat]["pnl"] += t.pnl

        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "profit_factor": pf,
            "max_drawdown_pct": max_dd,
            "sharpe_ratio": sharpe,
            "pattern_stats": pattern_stats,
            "equity_curve": self.equity_curve,
            "num_days": num_days,
        }


def main():
    parser = argparse.ArgumentParser(description="Short-only backtest")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    for lib in ("httpx", "httpcore", "urllib3", "yfinance", "alpaca"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    bt = ShortBacktest(start=args.start, end=args.end)
    result = bt.run()

    print(f"\n{'='*50}")
    print(f"SHORT BACKTEST: {args.start} → {args.end}")
    print(f"{'='*50}")
    print(f"Trades:        {result['total_trades']}")
    print(f"Win Rate:      {result['win_rate']:.1f}%")
    print(f"P&L:           ${result['total_pnl']:,.2f}")
    print(f"Profit Factor: {result['profit_factor']:.2f}")
    print(f"Max Drawdown:  {result['max_drawdown_pct']:.1f}%")
    print(f"Sharpe:        {result['sharpe_ratio']:.2f}")

    if result["pattern_stats"]:
        print(f"\nPattern Breakdown:")
        for pat, stats in sorted(result["pattern_stats"].items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr = (stats["wins"] / stats["trades"] * 100) if stats["trades"] > 0 else 0
            print(f"  {pat:25s} | {stats['trades']:3d} trades | {wr:5.1f}% WR | ${stats['pnl']:>8,.2f}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
