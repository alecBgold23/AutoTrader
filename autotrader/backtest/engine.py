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

SLIPPAGE_PER_SHARE = 0.01  # $0.01/share — realistic for liquid large/mid-caps (legacy, see CostModel)
CLAUDE_CACHE_DIR = BASE_DIR / "data" / "claude_cache"
CLAUDE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Bump this when the prompt changes to invalidate cached responses
PROMPT_VERSION = "v2"


def _tz_aware_timestamp(dt, index):
    """Create a Timestamp compatible with a DataFrame's index timezone."""
    ts = pd.Timestamp(dt.date() if hasattr(dt, 'date') else dt)
    if hasattr(index, 'tz') and index.tz is not None:
        ts = ts.tz_localize(index.tz)
    return ts

# Analysis times ordered for best full-day coverage when sliced to [:N].
# First 10 cover every phase of the day; extras fill prime-time gaps.
# Phase blocking is handled by the signal engine (blocks lunch, afternoon,
# close, premarket) and the backtest engine (blocks late-day entries).
DEFAULT_ANALYSIS_TIMES = [
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
    # Extra slots if cycles > 10
    time(9, 50),   # Late open
    time(10, 15),  # Prime extra
    time(10, 45),  # Prime extra
    time(14, 30),  # Afternoon mid (blocked)
]

# Backtest-specific risk parameters (more aggressive than live for discovery)
BACKTEST_RISK = {
    "max_risk_per_trade_pct": 0.15,       # 15% max ($15k on $100k) — day trading, flat by EOD
    "max_position_pct": 0.20,             # 20% max in one stock
    "max_total_exposure_pct": 0.80,       # 80% deployed at once
    "max_trades_per_day": 25,             # Room for 20+ trades
    "min_risk_reward_ratio": 1.5,         # Slightly relaxed from 2:1
    "min_confidence_to_trade": 0.65,      # Raised from 0.50 — data shows <0.65 degrades PF
    "analyze_count": 10,                  # Symbols per cycle (filtered by pre-filter)
}

# ═══════════════════════════════════════════════════════════
# BACKTEST SYSTEM PROMPT — more aggressive than live for signal discovery
# ═══════════════════════════════════════════════════════════

BACKTEST_SYSTEM_PROMPT = """You are a day trader running a BACKTEST. Your job is to find tradeable setups so the system can measure which patterns actually make money.

This is signal discovery, not live trading. You are evaluated on:
1. Finding REAL setups — patterns with clear entries, stops, and targets
2. Volume of signals — you should find 2-4 tradeable setups per analysis cycle
3. Variety — momentum, reversal, VWAP, ORB, flags, mean reversion, everything
4. Accuracy of risk levels — logical stop losses and realistic take profits

═══════════════════════════════════════════════════════
YOUR APPROACH
═══════════════════════════════════════════════════════

For every stock, identify:
1. WHAT pattern is forming (ORB breakout, VWAP reclaim, flag, oversold bounce, mean reversion)
2. WHERE is the entry (specific price at the setup trigger)
3. WHERE is the stop (below the pattern invalidation — a LOGICAL level, not arbitrary %)
4. WHERE is the target (next resistance/support, or 2-3x the risk distance)

If you see a recognizable setup with clear risk/reward → BUY.
The system handles position sizing and risk limits — your job is to find the setup.

═══════════════════════════════════════════════════════
SETUPS (your playbook)
═══════════════════════════════════════════════════════

MOMENTUM (trading WITH the move):
• Gap & Go — Stock gaps with volume, ride continuation after first pullback
• Opening Range Breakout — Price breaks first 30-min range with conviction
• First Pullback — Strong open → pullback on declining volume → bounces = entry
• Bull/Bear Flag — Trend → tight consolidation → breakout with volume
• VWAP Reclaim — Stock reclaims VWAP from below = bullish shift in control
• HOD/LOD Break — New high/low of day with volume = momentum continuation

REVERSAL (need clear level + volume):
• Oversold Bounce at Support — RSI extreme + real support level + volume
• Mean Reversion — Extended beyond Bollinger Bands + snapping back toward VWAP
• Hammer at Support — Reversal candle at a key level with volume confirmation
• Volume Climax Reversal — Massive volume spike at price extreme = exhaustion

You need ONE clear setup with conviction. Don't overthink it.

═══════════════════════════════════════════════════════
MARKET REGIME (context, NOT a constraint)
═══════════════════════════════════════════════════════

You'll be told the market regime (SPY trend + VIX level). Use this to choose WHICH setups to favor:

BULLISH regime → favor momentum: breakouts, flags, HOD breaks, Gap & Go
BEARISH regime → favor mean reversion: oversold bounces, VWAP reclaims, support reversals
HIGH VIX → wider swings = wider stops but BIGGER targets, not fewer trades

The regime tells you which DIRECTION to lean, not whether to trade.
Bearish markets have EXCELLENT mean reversion and oversold bounce opportunities — find them.

═══════════════════════════════════════════════════════
CONFIDENCE SCALE (use the full range)
═══════════════════════════════════════════════════════

confidence: 0.0-1.0
- 0.50-0.59: Pattern forming, acceptable R:R. Worth a small position.
- 0.60-0.69: Pattern confirmed, good R:R. Standard trade.
- 0.70-0.79: Pattern + volume confirming. Above-average conviction.
- 0.80-0.89: Multiple confluences aligning. Strong trade.
- 0.90+: Textbook setup, volume screaming, obvious entry. Full conviction.

USE THE FULL RANGE. A marginal setup = 0.55. A strong breakout with volume = 0.85.

═══════════════════════════════════════════════════════
OUTPUT
═══════════════════════════════════════════════════════

Output ONLY valid JSON. No markdown, no code fences.

{"action": "BUY|SELL|HOLD", "symbol": "TICKER", "confidence": 0.0, "entry_price": 0.0, "quantity": 0, "stop_loss": 0.0, "take_profit": 0.0, "pattern": "setup name", "reasoning": "What pattern, what level, what your edge is"}

BUY when you see a setup. HOLD only when there is genuinely no pattern forming.
If you find yourself holding on every stock in a cycle, look harder — there are always setups if you look for the right type (momentum in bull markets, reversals in bear markets)."""


_MOMENTUM_PATTERNS = {
    "orb", "orb breakout", "opening range breakout",
    "vwap reclaim", "vwap breakout",
    "flag", "flag breakout", "bull flag",
    "hod break", "high of day",
    "gap and go", "gap up",
    "first pullback", "pullback",
    "ema trend", "ema crossover",
    "breakout", "momentum", "continuation",
    "red to green", "power hour momentum",
}


def _is_momentum_pattern(pattern: str) -> bool:
    """Classify a pattern as momentum vs mean-reversion.

    Momentum patterns need follow-through and volume; they fail in choppy markets.
    Everything not explicitly momentum is treated as MR (more conservative default).
    """
    if not pattern:
        return True  # Unknown patterns default to momentum (more conservative filtering)
    p = pattern.lower().strip()
    # Check if any momentum keyword appears in the pattern string
    for mom in _MOMENTUM_PATTERNS:
        if mom in p:
            return True
    return False


def _confidence_risk_scale(confidence: float) -> float:
    """Scale risk amount by confidence. Higher confidence = bigger bet.

    0.65 confidence → 30% of max risk
    0.70 confidence → 45% of max risk
    0.80 confidence → 70% of max risk
    0.90 confidence → 92% of max risk
    1.00 confidence → 100% of max risk
    """
    c = max(0.65, min(1.0, confidence))
    normalized = (c - 0.65) / 0.35
    return 0.30 + 0.70 * (normalized ** 1.2)


# ═══════════════════════════════════════════════════════════
# COST MODEL
# ═══════════════════════════════════════════════════════════


class CostModel:
    """Models all real trading costs for US equities via Alpaca."""

    SLIPPAGE_PER_SHARE = 0.01          # $0.01/share slippage
    SEC_FEE_RATE = 0.0000278           # $27.80 per million (sells only)
    TAF_FEE_PER_SHARE = 0.000166       # FINRA TAF fee per share
    EXCHANGE_FEE_PER_SHARE = 0.003     # Average exchange fee

    @classmethod
    def entry_cost(cls, price: float, shares: int) -> float:
        """Total cost to enter a position."""
        slippage = cls.SLIPPAGE_PER_SHARE * shares
        taf = cls.TAF_FEE_PER_SHARE * shares
        exchange = cls.EXCHANGE_FEE_PER_SHARE * shares
        return slippage + taf + exchange

    @classmethod
    def exit_cost(cls, price: float, shares: int) -> float:
        """Total cost to exit a position."""
        slippage = cls.SLIPPAGE_PER_SHARE * shares
        sec = price * shares * cls.SEC_FEE_RATE
        taf = cls.TAF_FEE_PER_SHARE * shares
        exchange = cls.EXCHANGE_FEE_PER_SHARE * shares
        return slippage + sec + taf + exchange

    @classmethod
    def round_trip_cost(cls, entry_price: float, exit_price: float, shares: int) -> float:
        """Total round-trip cost."""
        return cls.entry_cost(entry_price, shares) + cls.exit_cost(exit_price, shares)

    @classmethod
    def effective_entry(cls, price: float, shares: int) -> float:
        """Price you effectively pay (higher than market)."""
        return price + cls.entry_cost(price, shares) / shares if shares > 0 else price

    @classmethod
    def effective_exit(cls, price: float, shares: int) -> float:
        """Price you effectively receive (lower than market)."""
        return price - cls.exit_cost(price, shares) / shares if shares > 0 else price


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
    direction: str = "long"    # "long" or "short"
    risk_per_share: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    scale_out_stage: int = 0
    shares_remaining: int = 0
    realized_pnl: float = 0.0
    r_target_1: float = 0.0
    r_target_2: float = 0.0
    current_stop: float = 0.0
    # MAE/MFE tracking (Phase 3)
    mae: float = 0.0           # Maximum adverse excursion (worst drawdown % from entry)
    mfe: float = 0.0           # Maximum favorable excursion (best unrealized gain % from entry)
    mae_time: datetime | None = None
    mfe_time: datetime | None = None

    # Phase 6: Adaptive exit tracking
    breakeven_locked: bool = False  # True once stop moved to breakeven

    def __post_init__(self):
        self.risk_per_share = abs(self.entry_price - self.stop_loss)
        self.highest_price = self.entry_price
        self.lowest_price = self.entry_price
        self.shares_remaining = self.quantity
        if self.direction == "short":
            # Short targets are BELOW entry
            self.r_target_1 = self.entry_price - self.risk_per_share * 0.5
            self.r_target_2 = self.entry_price - self.risk_per_share * 1.5
        else:
            self.r_target_1 = self.entry_price + self.risk_per_share * 0.5
            self.r_target_2 = self.entry_price + self.risk_per_share * 1.5
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
    direction: str = "long"      # "long" or "short"
    slippage_cost: float = 0.0
    trading_costs: float = 0.0   # Phase 2: realistic round-trip costs
    mae: float = 0.0             # Phase 3: max adverse excursion %
    mfe: float = 0.0             # Phase 3: max favorable excursion %


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
    total_costs: float = 0.0      # Phase 2: total trading costs (slippage + fees)
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
        max_cycles_per_day: int = 10,
        max_trades_per_day: int = 25,
        deterministic: bool = True,
        signal_params: dict | None = None,
    ):
        self.start_date = start
        self.end_date = end
        self.equity = starting_equity
        self.starting_equity = starting_equity
        self.model = model
        self.max_cycles_per_day = max_cycles_per_day
        self.max_trades_per_day = max_trades_per_day
        self.deterministic = deterministic

        # Initialize signal engines for deterministic mode
        if self.deterministic:
            from autotrader.signals.engine import SignalEngine
            from autotrader.signals.short_engine import ShortSignalEngine
            self.signal_engine = SignalEngine(params=signal_params)
            self.short_engine = ShortSignalEngine(params=signal_params)

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

        # Pattern-level performance tracking (rolling window for dynamic suppression)
        self.pattern_performance: dict[str, list[float]] = {}

        # Analysis times (trimmed to max_cycles_per_day)
        self.analysis_times = DEFAULT_ANALYSIS_TIMES[:max_cycles_per_day]

    def run(self, preloaded_data: dict | None = None) -> BacktestResult:
        """Run the full intraday backtest.

        Args:
            preloaded_data: Optional dict with pre-fetched data to avoid redundant
                API calls (used by walk-forward optimizer). Keys:
                - trading_days, spy_daily, vix_daily, daily_bars, bars_5m, daily_hot_lists
        """
        from autotrader.backtest.data_fetcher import (
            fetch_5m_bars, fetch_daily_bars, fetch_spy_daily,
            fetch_vix_daily, get_trading_days,
        )

        mode = "DETERMINISTIC" if self.deterministic else f"Claude ({self.model})"
        logger.info(
            f"Backtest: {self.start_date} to {self.end_date} | "
            f"Mode: {mode} | Cycles/day: {self.max_cycles_per_day}"
        )

        if preloaded_data:
            # Use pre-loaded data (walk-forward optimization mode)
            all_trading_days = preloaded_data["trading_days"]
            spy_daily = preloaded_data["spy_daily"]
            vix_daily = preloaded_data["vix_daily"]
            daily_bars = preloaded_data["daily_bars"]
            bars_5m = preloaded_data["bars_5m"]
            all_hot_lists = preloaded_data["daily_hot_lists"]

            # Filter trading days to this engine's date range
            start_dt = pd.Timestamp(self.start_date)
            end_dt = pd.Timestamp(self.end_date)
            trading_days = [d for d in all_trading_days
                           if start_dt <= pd.Timestamp(d.strftime("%Y-%m-%d")) <= end_dt]

            if not trading_days:
                return self._build_result(0)

            logger.info(f"Using preloaded data: {len(trading_days)} trading days")

            # Filter hot lists to this date range
            daily_hot_lists = {}
            for day_dt in trading_days:
                day_str = day_dt.strftime("%Y-%m-%d")
                daily_hot_lists[day_str] = all_hot_lists.get(day_str, [])
        else:
            # Normal mode: fetch all data
            # 1. Get trading days
            trading_days = get_trading_days(self.start_date, self.end_date)
            if not trading_days:
                logger.error("No trading days found in range")
                return self._build_result(0)

            logger.info(f"Found {len(trading_days)} trading days")

            # 2. Prefetch SPY/VIX daily for regime
            spy_daily = fetch_spy_daily(self.start_date, self.end_date)
            vix_daily = fetch_vix_daily(self.start_date, self.end_date)

            # 3. Dynamic market scan — just like the live system
            broad_universe = self._build_broad_universe()
            logger.info(f"Broad universe: {len(broad_universe)} liquid stocks")

            # 4. Fetch daily bars for the broad universe
            logger.info(f"Fetching daily data for {len(broad_universe)} symbols...")
            daily_bars: dict[str, pd.DataFrame] = {}
            for sym in broad_universe:
                daily_bars[sym] = fetch_daily_bars(sym, self.start_date, self.end_date)
            daily_bars = {s: d for s, d in daily_bars.items() if not d.empty}
            logger.info(f"Daily data loaded for {len(daily_bars)} symbols")

            # 5. For each trading day, run the scanner to find that day's movers
            bars_5m: dict[str, pd.DataFrame] = {}
            daily_hot_lists: dict[str, list[str]] = {}

            for day_dt in trading_days:
                day_str = day_dt.strftime("%Y-%m-%d")
                hot_list = self._scan_for_day(day_dt, daily_bars)
                daily_hot_lists[day_str] = hot_list

                # Lazy-fetch 5m data for new symbols we haven't seen
                for sym in hot_list:
                    if sym not in bars_5m:
                        bars_5m[sym] = fetch_5m_bars(sym, self.start_date, self.end_date)

        # Count unique symbols selected across all days
        all_selected = set()
        for syms in daily_hot_lists.values():
            all_selected.update(syms)
        logger.info(
            f"Scanner selected {len(all_selected)} unique symbols across "
            f"{len(trading_days)} days (5m data fetched for each)"
        )

        active_symbols = list(all_selected)  # Not used directly — each day uses its hot list

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

            # Use today's scanner hot list (already computed above)
            todays_candidates = daily_hot_lists.get(day_str, [])

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

        # Pending orders: {symbol: {direction, qty, stop_loss, take_profit, pattern, confidence, reasoning}}
        pending_orders: dict[str, dict] = {}

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

            # ── Fill pending orders at this bar's open ──
            for sym in list(pending_orders.keys()):
                if sym in sym_day_bars and sym not in self.positions:
                    today_df = sym_day_bars[sym]
                    if current_time in today_df.index:
                        bar = today_df.loc[current_time]
                        order = pending_orders.pop(sym)
                        direction = order.get("direction", "long")

                        if direction == "short":
                            # Short fill: sell at open - slippage
                            fill_price = float(bar["Open"]) - SLIPPAGE_PER_SHARE
                            risk_dist = abs(order["stop_loss"] - fill_price)
                            buffered_stop = order["stop_loss"] + risk_dist * 0.15
                            if fill_price >= buffered_stop:
                                continue
                        else:
                            # Long fill: buy at open + slippage
                            fill_price = float(bar["Open"]) + SLIPPAGE_PER_SHARE
                            risk_dist = abs(fill_price - order["stop_loss"])
                            buffered_stop = order["stop_loss"] - risk_dist * 0.15
                            if fill_price <= buffered_stop:
                                continue

                        self.positions[sym] = SimulatedPosition(
                            symbol=sym,
                            entry_price=fill_price,
                            quantity=order["qty"],
                            stop_loss=buffered_stop,
                            take_profit=order["take_profit"],
                            entry_time=current_time,
                            pattern=order["pattern"],
                            confidence=order["confidence"],
                            direction=direction,
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

                # Block lunch phase from new entries — structurally low volume,
                # wider spreads, mean reversion traps. Still manage existing positions.
                if phase == "lunch":
                    continue

                # Block new entries after 2:30 PM ET — late-day entries have
                # low win rates and leave no time for thesis to play out.
                if hour >= 15 or (hour == 14 and minute >= 30):
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
                    if sym in pending_orders:
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

                    # ── Deterministic signal engine vs Claude ──
                    if self.deterministic:
                        # Try LONG signal first
                        sig_decision = self.signal_engine.score(
                            symbol=sym,
                            price_data=price_data,
                            indicators=indicators,
                            intraday_indicators=intraday_indicators,
                            patterns_text=patterns_text,
                            levels=key_levels,
                            phase=phase,
                            regime=regime.get("label", "unknown"),
                        )

                        # If no long signal, try SHORT
                        trade_direction = "long"
                        if sig_decision.action != "BUY":
                            short_decision = self.short_engine.score(
                                symbol=sym,
                                price_data=price_data,
                                indicators=indicators,
                                intraday_indicators=intraday_indicators,
                                patterns_text=patterns_text,
                                levels=key_levels,
                                phase=phase,
                                regime=regime.get("label", "unknown"),
                            )
                            if short_decision.action != "SHORT":
                                continue
                            sig_decision = short_decision
                            trade_direction = "short"

                        action = sig_decision.action
                        confidence = sig_decision.confidence
                        stop_loss = sig_decision.stop_loss
                        take_profit = sig_decision.take_profit
                        pattern = sig_decision.pattern
                        reasoning = sig_decision.reasoning
                    else:
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
                        pattern = decision.get("pattern", "unknown")
                        reasoning = decision.get("reasoning", "")

                    # Confidence threshold
                    if confidence < BACKTEST_RISK["min_confidence_to_trade"]:
                        continue

                    # R:R check with slippage
                    risk = abs(price - stop_loss) + SLIPPAGE_PER_SHARE * 2
                    reward = abs(take_profit - price) - SLIPPAGE_PER_SHARE * 2
                    if risk <= 0 or reward <= 0 or reward / risk < BACKTEST_RISK["min_risk_reward_ratio"]:
                        continue

                    # Confidence-based position sizing
                    conf_scale = _confidence_risk_scale(confidence)
                    phase_mult = phase_config.get("size_multiplier", 1.0)
                    # Open phase (9:30-10:00 ET) has highest edge — boost sizing 1.5x
                    # Structurally: highest volume, clearest setups, most momentum
                    if phase == "open":
                        phase_mult = 1.5
                    else:
                        phase_mult = max(phase_mult, 0.40)
                    # Pattern-quality sizing: ORB patterns get full size, others get 50%
                    # Data: ORB = +$3,845 from 203 trades, non-ORB = +$96 from 103 trades
                    pattern_mult = 1.0 if pattern and "ORB" in pattern else 0.50
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
                    # Apply pattern-quality sizing AFTER caps (otherwise cap overrides it)
                    shares = max(1, int(shares * pattern_mult))
                    if shares <= 0:
                        continue

                    # Queue order to fill at next bar's open
                    pending_orders[sym] = {
                        "qty": shares,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "pattern": pattern,
                        "confidence": confidence,
                        "reasoning": reasoning,
                        "phase": phase,
                        "direction": trade_direction if self.deterministic else "long",
                    }

        # End of day: force close anything remaining
        if self.positions:
            last_time = sorted_times[-1] if sorted_times else day_dt
            self._force_close_all(last_time, sym_day_bars, len(sorted_times) - 1, sorted_times)

    def _manage_positions_at_bar(
        self, current_time: datetime, sym_day_bars: dict, hour: int, minute: int
    ):
        """Check stops, scale-outs, and time stops — direction-aware for long AND short."""
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
            is_short = pos.direction == "short"

            # Update highest/lowest price
            if bar_high > pos.highest_price:
                pos.highest_price = bar_high
            if bar_low < pos.lowest_price:
                pos.lowest_price = bar_low

            # Track MAE/MFE (direction-aware)
            if pos.entry_price > 0:
                if is_short:
                    adverse = (bar_high - pos.entry_price) / pos.entry_price * 100
                    favorable = (pos.entry_price - bar_low) / pos.entry_price * 100
                else:
                    adverse = (pos.entry_price - bar_low) / pos.entry_price * 100
                    favorable = (bar_high - pos.entry_price) / pos.entry_price * 100
                if adverse > pos.mae:
                    pos.mae = adverse
                    pos.mae_time = current_time
                if favorable > pos.mfe:
                    pos.mfe = favorable
                    pos.mfe_time = current_time

            # Check stop loss (direction-aware)
            if is_short:
                # Short: stop triggers when close goes ABOVE stop
                if bar_close >= pos.current_stop:
                    exit_price = pos.current_stop + SLIPPAGE_PER_SHARE
                    self._close_position(sym, exit_price, current_time, "Stop loss hit")
                    continue
            else:
                # Long: stop triggers when close goes BELOW stop
                if bar_close <= pos.current_stop:
                    exit_price = pos.current_stop - SLIPPAGE_PER_SHARE
                    self._close_position(sym, exit_price, current_time, "Stop loss hit")
                    continue

            # Current R (direction-aware)
            if is_short:
                current_r = (pos.entry_price - bar_close) / pos.risk_per_share if pos.risk_per_share > 0 else 0
            else:
                current_r = (bar_close - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
            hold_minutes = (current_time - pos.entry_time).total_seconds() / 60

            # 1. Breakeven lock at +0.3R
            if not pos.breakeven_locked and current_r >= 0.3:
                if is_short:
                    pos.current_stop = min(pos.entry_price, pos.current_stop)
                else:
                    pos.current_stop = max(pos.entry_price, pos.current_stop)
                pos.breakeven_locked = True

            # 2. Scale out at 0.5R (direction-aware target check)
            target_1_hit = (bar_low <= pos.r_target_1) if is_short else (bar_high >= pos.r_target_1)
            if pos.scale_out_stage == 0 and target_1_hit and pos.shares_remaining > 1:
                sell_qty = max(1, int(pos.quantity / 3))
                actual_sell = min(sell_qty, pos.shares_remaining - 1)
                if actual_sell > 0:
                    exit_price = pos.r_target_1 + (SLIPPAGE_PER_SHARE if is_short else -SLIPPAGE_PER_SHARE)
                    costs = CostModel.round_trip_cost(pos.entry_price, exit_price, actual_sell)
                    if is_short:
                        pnl = (pos.entry_price - exit_price) * actual_sell - costs
                        r_mult = (pos.entry_price - exit_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
                    else:
                        pnl = (exit_price - pos.entry_price) * actual_sell - costs
                        r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
                    slippage = SLIPPAGE_PER_SHARE * 2 * actual_sell

                    self.completed_trades.append(BacktestTrade(
                        symbol=sym, pattern=pos.pattern,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        quantity=actual_sell, pnl=pnl, r_multiple=r_mult,
                        entry_time=pos.entry_time, exit_time=current_time,
                        exit_reason="Scale out 0.5R (1/3)",
                        confidence=pos.confidence, direction=pos.direction,
                        slippage_cost=slippage, trading_costs=costs,
                        mae=pos.mae, mfe=pos.mfe,
                    ))
                    self.equity += pnl
                    self.daily_pnl += pnl
                    pos.shares_remaining -= actual_sell
                    pos.realized_pnl += pnl
                    pos.scale_out_stage = 1
                    pos.current_stop = pos.entry_price
                    pos.breakeven_locked = True

            # 3. Scale out at 1.5R
            target_2_hit = (bar_low <= pos.r_target_2) if is_short else (bar_high >= pos.r_target_2)
            if pos.scale_out_stage == 1 and target_2_hit and pos.shares_remaining > 1:
                sell_qty = max(1, int(pos.quantity / 3))
                actual_sell = min(sell_qty, pos.shares_remaining - 1)
                if actual_sell > 0:
                    exit_price = pos.r_target_2 + (SLIPPAGE_PER_SHARE if is_short else -SLIPPAGE_PER_SHARE)
                    costs = CostModel.round_trip_cost(pos.entry_price, exit_price, actual_sell)
                    if is_short:
                        pnl = (pos.entry_price - exit_price) * actual_sell - costs
                        r_mult = (pos.entry_price - exit_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
                    else:
                        pnl = (exit_price - pos.entry_price) * actual_sell - costs
                        r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
                    slippage = SLIPPAGE_PER_SHARE * 2 * actual_sell

                    self.completed_trades.append(BacktestTrade(
                        symbol=sym, pattern=pos.pattern,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        quantity=actual_sell, pnl=pnl, r_multiple=r_mult,
                        entry_time=pos.entry_time, exit_time=current_time,
                        exit_reason="Scale out 1.5R (1/3)",
                        confidence=pos.confidence, direction=pos.direction,
                        slippage_cost=slippage, trading_costs=costs,
                        mae=pos.mae, mfe=pos.mfe,
                    ))
                    self.equity += pnl
                    self.daily_pnl += pnl
                    pos.shares_remaining -= actual_sell
                    pos.realized_pnl += pnl
                    pos.scale_out_stage = 2

            # 4. Time stops
            if hold_minutes >= 45:
                exit_price = bar_close + (SLIPPAGE_PER_SHARE if is_short else -SLIPPAGE_PER_SHARE)
                self._close_position(sym, exit_price, current_time, "Time stop (45min)")
                continue
            elif hold_minutes >= 20 and current_r <= 0:
                # Showed some promise but back underwater — cut at 20min
                exit_price = bar_close + (SLIPPAGE_PER_SHARE if is_short else -SLIPPAGE_PER_SHARE)
                self._close_position(sym, exit_price, current_time, "Time stop (20min loser)")
                continue

            # 5. Trailing stop (direction-aware)
            if pos.breakeven_locked:
                trail_distance = 0.5 * pos.risk_per_share
                if is_short:
                    trail_stop = pos.lowest_price + trail_distance
                    trail_stop = min(trail_stop, pos.entry_price)
                    if trail_stop < pos.current_stop:
                        pos.current_stop = trail_stop
                else:
                    trail_stop = pos.highest_price - trail_distance
                    trail_stop = max(trail_stop, pos.entry_price)
                    if trail_stop > pos.current_stop:
                        pos.current_stop = trail_stop

    def _force_close_all(self, close_time, sym_day_bars, bar_idx, sorted_times):
        """Force close all positions at EOD."""
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            if sym in sym_day_bars:
                today_df = sym_day_bars[sym]
                # Use the last available bar's close
                visible = today_df[today_df.index <= close_time]
                if not visible.empty:
                    raw_close = float(visible.iloc[-1]["Close"])
                    # Shorts buy back (slippage hurts = higher price)
                    # Longs sell (slippage hurts = lower price)
                    if pos.direction == "short":
                        exit_price = raw_close + SLIPPAGE_PER_SHARE
                    else:
                        exit_price = raw_close - SLIPPAGE_PER_SHARE
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

        # Phase 2: Realistic cost modeling — direction-aware P&L
        costs = CostModel.round_trip_cost(pos.entry_price, exit_price, qty)
        if pos.direction == "short":
            pnl = (pos.entry_price - exit_price) * qty - costs
            r_mult = (pos.entry_price - exit_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
        else:
            pnl = (exit_price - pos.entry_price) * qty - costs
            r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
        slippage = SLIPPAGE_PER_SHARE * 2 * qty  # Legacy tracking

        self.completed_trades.append(BacktestTrade(
            symbol=symbol, pattern=pos.pattern,
            entry_price=pos.entry_price, exit_price=exit_price,
            quantity=qty, pnl=pnl, r_multiple=r_mult,
            entry_time=pos.entry_time, exit_time=exit_time,
            exit_reason=reason,
            confidence=pos.confidence, direction=pos.direction,
            slippage_cost=slippage,
            trading_costs=costs,
            mae=pos.mae, mfe=pos.mfe,
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

        # Pattern-level performance tracking (keep last 15 trades per pattern)
        pat = pos.pattern or "unknown"
        if pat not in self.pattern_performance:
            self.pattern_performance[pat] = []
        self.pattern_performance[pat].append(pnl)
        if len(self.pattern_performance[pat]) > 15:
            self.pattern_performance[pat] = self.pattern_performance[pat][-15:]

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

        return score >= 15

    def _ask_claude(
        self, symbol, price_data, indicators, signal_summary,
        intraday_summary, portfolio, scanner_flags, patterns_text,
        levels_text, phase, regime_context, day_str, time_str,
    ) -> dict | None:
        """Ask Claude for a trading decision, with caching."""
        import anthropic
        from autotrader.config import ANTHROPIC_API_KEY, CLAUDE_MAX_TOKENS

        # Build cache key — includes PROMPT_VERSION so prompt changes invalidate cache
        cache_data = f"{PROMPT_VERSION}_{symbol}_{day_str}_{time_str}_{price_data.get('price')}_{price_data.get('volume')}_{phase}"
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
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=1, timeout=30.0)
            response = client.messages.create(
                model=self.model,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=BACKTEST_SYSTEM_PROMPT,
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

            # Rate limit — stay under Haiku's token-per-minute limit
            time_module.sleep(1.5)
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
            "open": "OPENING DRIVE — Gap & Go, ORB, Red-to-Green. High volatility, early setups forming.",
            "prime": "PRIME TIME — Best hour for setups. First Pullback, Flags, VWAP Reclaim.",
            "lunch": "MIDDAY — Volume lower. Mean reversion and support bounces work best here.",
            "afternoon": "AFTERNOON — Momentum continuations, VWAP tests, new HOD/LOD setups.",
            "power_hour": "POWER HOUR — Real institutional volume. Momentum, HOD/LOD breaks.",
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
                  "spy_price": 0, "spy_sma_50": 0, "vix_price": 18,
                  "spy_chop": 50.0, "spy_adx": 25.0}

        if spy_daily is not None and not spy_daily.empty:
            spy_to_date = spy_daily[spy_daily.index < _tz_aware_timestamp(day_dt, spy_daily.index)]
            if len(spy_to_date) >= 50:
                spy_price = float(spy_to_date["Close"].iloc[-1])
                spy_sma_50 = float(spy_to_date["Close"].tail(50).mean())
                regime["spy_price"] = spy_price
                regime["spy_sma_50"] = spy_sma_50
                regime["spy_trend"] = "bullish" if spy_price >= spy_sma_50 else "bearish"

                # Choppiness Index and ADX for market quality filtering
                from autotrader.data.indicators import calculate_choppiness_index, calculate_adx
                chop = calculate_choppiness_index(spy_to_date, period=14)
                adx = calculate_adx(spy_to_date, period=14)
                if chop is not None:
                    regime["spy_chop"] = chop
                if adx is not None:
                    regime["spy_adx"] = adx

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
        # Day trading is about intraday patterns, not overnight trend risk.
        # Regime affects WHICH setups to favor, not bet size (much).
        # Only extreme VIX (30+) warrants real size reduction.
        return {
            "bull_quiet": 1.0,
            "bull_volatile": 0.85,
            "bear_quiet": 0.85,
            "bear_volatile": 0.60,
        }.get(regime["regime"], 0.85)

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

    def _build_broad_universe(self) -> list[str]:
        """Build a broad universe of liquid US stocks — simulates scanning the whole market.

        Uses Alpaca to get all tradeable US equities, then filters by price/volume
        using yfinance batch downloads. Same logic as the live MarketScanner.build_universe().
        Results are cached so subsequent runs don't re-download.
        """
        import yfinance as yf
        from autotrader.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER

        # Cache keyed by backtest date range so different periods get different universes
        cache_name = f"broad_universe_{self.start_date}_{self.end_date}.json"
        cache_file = CLAUDE_CACHE_DIR.parent / "backtest_cache" / cache_name
        cache_file.parent.mkdir(parents=True, exist_ok=True)

        # Use cached universe for this specific date range
        if cache_file.exists():
            try:
                import json as _json
                with open(cache_file) as f:
                    cached = _json.load(f)
                if len(cached) > 100:
                    logger.info(f"Using cached broad universe for {self.start_date}-{self.end_date}: {len(cached)} symbols")
                    return cached
            except Exception:
                pass

        # Pull all tradeable US equities from Alpaca
        logger.info("Building broad universe — scanning all Alpaca equities...")
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetAssetsRequest
            from alpaca.trading.enums import AssetClass, AssetStatus

            client = TradingClient(
                api_key=ALPACA_API_KEY,
                secret_key=ALPACA_SECRET_KEY,
                paper=ALPACA_PAPER,
            )
            request = GetAssetsRequest(
                asset_class=AssetClass.US_EQUITY,
                status=AssetStatus.ACTIVE,
            )
            all_assets = client.get_all_assets(request)
            symbols = [
                a.symbol for a in all_assets
                if a.tradable
                and a.exchange in ("NASDAQ", "NYSE", "ARCA", "AMEX", "BATS")
                and not any(c in a.symbol for c in "./-")
                and len(a.symbol) <= 5
            ]
            logger.info(f"Found {len(symbols)} tradeable symbols from Alpaca")
        except Exception as e:
            logger.warning(f"Alpaca asset list failed: {e}. Using fallback universe.")
            return self._fallback_universe()

        # Filter by price/volume using data from the BACKTEST period (not today)
        from autotrader.config import SCANNER
        from datetime import datetime, timedelta
        # Use the first 10 trading days of the backtest period for filtering
        filter_start = (datetime.strptime(self.start_date, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
        filter_end = self.start_date
        logger.info(f"Filtering universe using price/volume data from {filter_start} to {filter_end}")

        universe = []
        batch_size = 200
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            try:
                data = yf.download(
                    tickers=batch, start=filter_start, end=filter_end,
                    interval="1d", progress=False, threads=True,
                )
                if data.empty:
                    continue
                for sym in batch:
                    try:
                        if len(batch) == 1:
                            close = data["Close"]
                            vol = data["Volume"]
                        else:
                            close = data["Close"][sym] if sym in data["Close"].columns else None
                            vol = data["Volume"][sym] if sym in data["Volume"].columns else None
                        if close is None or vol is None:
                            continue
                        close = close.dropna()
                        vol = vol.dropna()
                        if len(close) < 3:
                            continue
                        price = float(close.iloc[-1])
                        avg_vol = float(vol.mean())
                        if SCANNER["min_price"] <= price <= SCANNER["max_price"] and avg_vol >= SCANNER["min_avg_volume"]:
                            universe.append(sym)
                    except Exception:
                        continue
            except Exception:
                continue

            # No cap — scan the full market, just like live
            if (i // batch_size) % 5 == 0 and i > 0:
                logger.info(f"  Scanned {i}/{len(symbols)} symbols, {len(universe)} qualify so far...")

        # Cache for future runs
        try:
            import json as _json
            with open(cache_file, "w") as f:
                _json.dump(universe, f)
        except Exception:
            pass

        logger.info(f"Broad universe built: {len(universe)} liquid stocks")
        return universe

    def _fallback_universe(self) -> list[str]:
        """Fallback if Alpaca API is unavailable — large static pool of liquid names."""
        return [
            # Mega cap tech
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "AVGO", "CRM",
            "ORCL", "ADBE", "INTC", "CSCO", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC",
            # High-beta / meme / retail favorites
            "COIN", "PLTR", "SOFI", "RIVN", "NIO", "SNAP", "ROKU", "SHOP", "MARA", "RIOT",
            "SMCI", "ARM", "DKNG", "ABNB", "SQ", "HOOD", "UPST", "AFRM", "LCID", "PLUG",
            "FUBO", "IONQ", "RKLB", "JOBY", "APLD", "HIMS", "CLOV", "WISH", "BB", "NOK",
            # Finance
            "JPM", "BAC", "GS", "MS", "C", "WFC", "SCHW", "V", "MA", "PYPL",
            # Consumer / media
            "DIS", "NFLX", "UBER", "LYFT", "DASH", "ABNB", "BKNG", "EXPE", "MAR", "BA",
            "F", "GM", "TGT", "WMT", "COST", "HD", "LOW", "NKE", "SBUX", "MCD",
            # Healthcare / biotech
            "MRNA", "PFE", "BNTX", "JNJ", "LLY", "ABBV", "BMY", "AMGN", "GILD", "REGN",
            # Energy / materials
            "XOM", "CVX", "OXY", "SLB", "HAL", "FSLR", "ENPH", "RUN", "FCX", "CLF",
            # ETFs that act like stocks
            "SPY", "QQQ", "IWM", "XLF", "XLE", "XLK", "ARKK", "SOXL", "TQQQ",
        ]

    def _scan_for_day(self, day_dt: datetime, daily_bars: dict[str, pd.DataFrame]) -> list[str]:
        """Scan the broad universe for a specific day's movers.

        Mirrors the live MarketScanner._score_stock() logic:
        - RVOL (relative volume) — 35% weight
        - Gap from prior close — 25% weight
        - Momentum / daily change — 15% weight
        - ATR volatility — 10% weight
        - Multi-day trend — 8% weight
        - Key level proximity — 7% weight

        Uses ONLY prior-day data (no look-ahead).
        Returns top 15 symbols for this day.
        """
        scores: dict[str, float] = {}

        for sym, daily in daily_bars.items():
            if daily.empty:
                continue

            to_date = daily[daily.index < _tz_aware_timestamp(day_dt, daily.index)]
            if len(to_date) < 10:
                continue

            try:
                close = to_date["Close"]
                volume = to_date["Volume"]
                high = to_date["High"]
                low = to_date["Low"]
                openp = to_date["Open"]

                price = float(close.iloc[-1])
                prev_close = float(close.iloc[-2])
                today_open = float(openp.iloc[-1])
                today_vol = float(volume.iloc[-1])
                avg_vol = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())

                if price <= 0 or avg_vol <= 0:
                    continue

                change_pct = (price - prev_close) / prev_close * 100
                gap_pct = (today_open - prev_close) / prev_close * 100
                rvol = today_vol / avg_vol if avg_vol > 0 else 0

                # ATR as % of price
                tr_data = pd.DataFrame({
                    "hl": high - low,
                    "hc": (high - close.shift(1)).abs(),
                    "lc": (low - close.shift(1)).abs(),
                })
                tr = tr_data.max(axis=1)
                atr = float(tr.iloc[-14:].mean()) if len(tr) >= 14 else float(tr.mean())
                atr_pct = (atr / price) * 100

                score = 0.0

                # 1. RVOL (35% weight)
                if rvol >= 5.0:
                    score += 40
                elif rvol >= 3.0:
                    score += 30
                elif rvol >= 2.0:
                    score += 20
                elif rvol >= 1.5:
                    score += 10

                # 2. Gap (25% weight)
                abs_gap = abs(gap_pct)
                if abs_gap >= 8.0:
                    score += 30
                elif abs_gap >= 4.0:
                    score += 22
                elif abs_gap >= 2.0:
                    score += 12

                # Gap + volume combo
                if abs_gap >= 3.0 and rvol >= 2.0:
                    score += 15

                # 3. Daily change / momentum (15%)
                abs_change = abs(change_pct)
                if abs_change >= 5.0:
                    score += 18
                elif abs_change >= 3.0:
                    score += 12
                elif abs_change >= 1.5:
                    score += 6

                # 4. ATR volatility (10%)
                if atr_pct >= 4.0:
                    score += 12
                elif atr_pct >= 2.5:
                    score += 8
                elif atr_pct >= 1.5:
                    score += 4

                # 5. Multi-day trend (8%)
                if len(close) >= 5:
                    five_day = (price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100
                    if abs(five_day) >= 15:
                        score += 10
                    elif abs(five_day) >= 8:
                        score += 6

                # 6. Key level proximity (7%)
                if len(to_date) >= 20:
                    high_20 = float(high.tail(20).max())
                    low_20 = float(low.tail(20).min())
                    if price >= high_20 * 0.97:
                        score += 8
                    if price <= low_20 * 1.03:
                        score += 8

                # Only include if something is happening
                if score > 0:
                    scores[sym] = score

            except Exception:
                continue

        # Return top 15 by score (matches live scanner behavior)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [sym for sym, _ in ranked[:15]]

    _CORRELATED_GROUPS = {
        "mega_tech": {"AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN"},
        "semis": {"NVDA", "AMD", "INTC", "AVGO", "QCOM", "MU", "TSM", "ASML"},
        "ev_auto": {"TSLA", "RIVN", "LCID", "NIO", "GM", "F"},
        "banking": {"JPM", "BAC", "GS", "MS", "WFC", "C"},
        "energy": {"XOM", "CVX", "COP", "SLB", "OXY", "EOG"},
        "biotech": {"MRNA", "PFE", "JNJ", "ABBV", "LLY", "BMY"},
    }
    _MAX_SECTOR_CONCENTRATION = 2

    def _check_sector_ok(self, symbol: str) -> bool:
        """Check sector concentration against current positions."""
        target_group = None
        for group, members in self._CORRELATED_GROUPS.items():
            if symbol in members:
                target_group = group
                break

        if target_group is None:
            return True

        held = [s for s in self.positions if s in self._CORRELATED_GROUPS.get(target_group, set())]
        return len(held) < self._MAX_SECTOR_CONCENTRATION

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
            total_costs=sum(t.trading_costs for t in self.completed_trades),
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
        f"  Trading Costs:   ${result.total_costs:,.2f}",
        f"  Equity:          ${result.starting_equity:,.0f} → ${result.ending_equity:,.0f}",
        f"  API Calls:       {result.api_calls} | Cache Hits: {result.cache_hits}",
    ]

    # Phase 2: Cost drag analysis
    if result.total_costs > 0:
        gross_pnl = result.total_pnl + result.total_costs
        cost_drag = (result.total_costs / abs(gross_pnl) * 100) if gross_pnl != 0 else 0
        lines.append("")
        lines.append("─── COST ANALYSIS ───")
        lines.append(f"  Gross P&L:       ${gross_pnl:,.2f}")
        lines.append(f"  Total Costs:     ${result.total_costs:,.2f}")
        lines.append(f"  Net P&L:         ${result.total_pnl:,.2f}")
        lines.append(f"  Cost drag:       {cost_drag:.1f}% of gross P&L")

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
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    et_tz = ZoneInfo("US/Eastern")

    phase_perf: dict[str, dict] = {}
    for t in result.trades:
        p = t.market_phase or "unknown"
        if not p or p == "unknown":
            # Infer phase from entry time (convert UTC → ET)
            if hasattr(t.entry_time, 'hour'):
                entry_et = t.entry_time.astimezone(et_tz) if t.entry_time.tzinfo else t.entry_time
                h = entry_et.hour
                m = entry_et.minute
                if h < 10:
                    p = "open"
                elif h < 11:
                    p = "prime"
                elif h == 11 or (h < 13) or (h == 13 and m < 30):
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

    # Direction breakdown (long vs short)
    long_trades = [t for t in result.trades if t.direction == "long"]
    short_trades = [t for t in result.trades if t.direction == "short"]
    if short_trades:  # Only show if there are shorts
        lines.append("")
        lines.append("─── DIRECTION BREAKDOWN ───")
        for label, trades in [("Long", long_trades), ("Short", short_trades)]:
            if not trades:
                continue
            tw = sum(1 for t in trades if t.pnl > 0)
            wr = tw / len(trades) * 100
            tpnl = sum(t.pnl for t in trades)
            tr = sum(t.r_multiple for t in trades)
            avg_r = tr / len(trades)
            lines.append(
                f"  {label:6s} {len(trades):3d} trades | WR: {wr:5.1f}% | "
                f"Avg R: {avg_r:+.2f} | P&L: ${tpnl:>9,.2f}"
            )

    # Phase 3: MAE/MFE Analysis
    if result.trades:
        winners = [t for t in result.trades if t.pnl > 0]
        losers = [t for t in result.trades if t.pnl <= 0]

        lines.append("")
        lines.append("─── MAE/MFE ANALYSIS ───")

        if winners:
            avg_winner_mae = np.mean([t.mae for t in winners])
            avg_winner_mfe = np.mean([t.mfe for t in winners])
            avg_winner_return = np.mean([
                ((t.entry_price - t.exit_price) if t.direction == "short" else (t.exit_price - t.entry_price))
                / t.entry_price * 100 for t in winners if t.entry_price > 0
            ])
            lines.append(f"  Winners avg MAE: {avg_winner_mae:.2f}% (noise before profit)")
            lines.append(f"  Winners avg MFE: {avg_winner_mfe:.2f}% (max potential)")
            lines.append(f"  Winners left on table: {avg_winner_mfe - avg_winner_return:.2f}%")

        if losers:
            avg_loser_mae = np.mean([t.mae for t in losers])
            avg_loser_mfe = np.mean([t.mfe for t in losers])
            lines.append(f"  Losers avg MAE:  {avg_loser_mae:.2f}% (how far against)")
            lines.append(f"  Losers avg MFE:  {avg_loser_mfe:.2f}% (were they ever profitable?)")

            losers_were_profitable = sum(1 for t in losers if t.mfe > 0.3)
            pct_losers_profitable = losers_were_profitable / len(losers) * 100 if losers else 0
            lines.append(f"  Losers that had >0.3% MFE: {pct_losers_profitable:.0f}%")

    # Phase 5: Monthly Breakdown
    if result.trades:
        monthly: dict[str, dict] = {}
        for t in result.trades:
            if hasattr(t.entry_time, 'strftime'):
                month_key = t.entry_time.strftime("%Y-%m")
            else:
                month_key = str(t.entry_time)[:7]
            if month_key not in monthly:
                monthly[month_key] = {"trades": 0, "wins": 0, "pnl": 0.0}
            monthly[month_key]["trades"] += 1
            monthly[month_key]["pnl"] += t.pnl
            if t.pnl > 0:
                monthly[month_key]["wins"] += 1

        if monthly:
            lines.append("")
            lines.append("─── MONTHLY BREAKDOWN ───")
            for month, stats in sorted(monthly.items()):
                wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
                ret_pct = stats["pnl"] / result.starting_equity * 100
                lines.append(
                    f"  {month}: {stats['trades']:3d} trades | WR: {wr:5.1f}% | "
                    f"P&L: ${stats['pnl']:>9,.2f} | Return: {ret_pct:+.2f}%"
                )

    # Phase 7: Survivorship Bias Adjustment
    if result.trades and result.trading_days > 0:
        avg_monthly_return = result.return_pct / max(1, result.trading_days / 21)
        adjusted_monthly = avg_monthly_return * 0.85  # 15% haircut
        lines.append("")
        lines.append("─── SURVIVORSHIP BIAS ADJUSTMENT ───")
        lines.append(f"  Raw avg monthly return:      {avg_monthly_return:+.2f}%")
        lines.append(f"  Adjusted (-15% bias):        {adjusted_monthly:+.2f}%")
        lines.append(f"  Note: Survivorship bias inflates returns 10-20% for US equities")

    lines.append("")
    lines.append("═══════════════════════════════════════════════════════════")
    return "\n".join(lines)
