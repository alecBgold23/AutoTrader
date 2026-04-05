# AutoTrader Deterministic Engine Overhaul

**Purpose:** Replace Claude-as-trader with a deterministic, reproducible signal engine. Add realistic cost modeling, walk-forward validation, MAE/MFE analysis, and parameter optimization. The goal is a system that can be rigorously backtested, validated, and deployed live with confidence.

**Run this prompt in Claude Code:** `Read DETERMINISTIC_ENGINE_PROMPT.md and execute it starting from Phase 1.`

**IMPORTANT:** Execute each phase fully before moving to the next. After each phase, run the backtest and report results before proceeding.

---

## Phase 1: Replace Claude with Deterministic Signal Engine

### Why
Claude API calls introduce non-determinism, look-ahead bias risk, and make walk-forward optimization impossible. Every parameter change requires expensive new API calls. A deterministic engine produces identical results on identical data, enabling proper scientific testing.

### What to Build

Create `autotrader/signals/engine.py` — a pure-Python signal scoring engine that replaces `_ask_claude()` in the backtest.

#### Signal Architecture

The engine scores each stock on a 0-100 scale using weighted factors. A score above the threshold triggers a BUY. The engine also determines entry, stop, and target prices algorithmically.

```python
# autotrader/signals/engine.py

"""Deterministic signal engine — replaces Claude for reproducible backtesting.

Scores stocks 0-100 using weighted technical factors.
Produces identical results on identical data.
No API calls, no randomness, no look-ahead.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum

class SetupType(Enum):
    ORB_BREAKOUT = "ORB Breakout"
    VWAP_RECLAIM = "VWAP Reclaim"
    FIRST_PULLBACK = "First Pullback"
    BULL_FLAG = "Bull Flag"
    MEAN_REVERSION = "Mean Reversion"
    MOMENTUM_CONTINUATION = "Momentum Continuation"
    GAP_AND_GO = "Gap & Go"
    HOD_BREAK = "HOD Break"
    OVERSOLD_BOUNCE = "Oversold Bounce"
    NO_SETUP = "No Setup"

@dataclass
class SignalDecision:
    action: str           # "BUY" or "HOLD"
    symbol: str
    confidence: float     # 0.0 - 1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    pattern: str
    reasoning: str
    score: float          # Raw 0-100 score
    setup_type: SetupType

class SignalEngine:
    """Deterministic multi-factor scoring engine."""

    # ═══════════════════════════════════════════
    # TUNABLE PARAMETERS (for walk-forward optimization)
    # ═══════════════════════════════════════════

    # Factor weights (must sum to 100)
    WEIGHT_TREND = 20        # EMA alignment, price vs SMA
    WEIGHT_MOMENTUM = 20     # RSI, MACD histogram direction
    WEIGHT_VOLUME = 25       # RVOL, volume acceleration
    WEIGHT_PATTERN = 15      # Detected chart/candle patterns
    WEIGHT_LOCATION = 20     # Price relative to VWAP, S/R, key levels

    # Thresholds
    MIN_SCORE_TO_TRADE = 55  # Minimum score (0-100) to generate BUY
    MIN_RVOL = 1.3           # Minimum relative volume
    MIN_RR_RATIO = 2.0       # Minimum reward:risk

    # Stop loss methodology
    STOP_ATR_MULTIPLIER = 1.5  # Stop = entry - (ATR * multiplier)
    STOP_BUFFER_PCT = 0.002    # 0.2% buffer below calculated stop

    # Target methodology
    TARGET_RR_MULTIPLIER = 2.5 # Target = entry + risk * multiplier

    def __init__(self, params: dict | None = None):
        """Initialize with optional parameter overrides for optimization."""
        if params:
            for key, val in params.items():
                if hasattr(self, key):
                    setattr(self, key, val)

    def score(
        self,
        symbol: str,
        price_data: dict,
        indicators: dict,
        intraday_indicators: dict,
        patterns_text: str,
        levels: dict,
        phase: str,
        regime: str,
    ) -> SignalDecision:
        """Score a stock and return a deterministic trading decision.

        This replaces _ask_claude() entirely.
        """
        price = price_data["price"]
        if price <= 0:
            return self._hold(symbol, price, 0, "Invalid price")

        # ── Factor 1: TREND (0-100, weighted) ──
        trend_score = self._score_trend(indicators, intraday_indicators)

        # ── Factor 2: MOMENTUM (0-100, weighted) ──
        momentum_score = self._score_momentum(indicators, intraday_indicators)

        # ── Factor 3: VOLUME (0-100, weighted) ──
        volume_score = self._score_volume(price_data, indicators, intraday_indicators)

        # ── Factor 4: PATTERN (0-100, weighted) ──
        pattern_score, detected_setup = self._score_patterns(
            indicators, intraday_indicators, patterns_text, price_data, levels
        )

        # ── Factor 5: LOCATION (0-100, weighted) ──
        location_score = self._score_location(price, indicators, intraday_indicators, levels)

        # ── Composite Score ──
        raw_score = (
            trend_score * self.WEIGHT_TREND / 100 +
            momentum_score * self.WEIGHT_MOMENTUM / 100 +
            volume_score * self.WEIGHT_VOLUME / 100 +
            pattern_score * self.WEIGHT_PATTERN / 100 +
            location_score * self.WEIGHT_LOCATION / 100
        )

        # ── Phase adjustment ──
        # Reduce score during structurally weak phases
        if phase == "lunch":
            raw_score *= 0.7  # Lunch setups need to be much stronger
        elif phase == "open":
            # Open is high-edge but noisy — only boost momentum setups
            if detected_setup in (SetupType.ORB_BREAKOUT, SetupType.GAP_AND_GO):
                raw_score *= 1.1
            else:
                raw_score *= 0.9

        # ── Regime adjustment ──
        # In bearish regimes, only take momentum AND reversal setups
        if "bear" in regime:
            if detected_setup in (SetupType.MEAN_REVERSION, SetupType.OVERSOLD_BOUNCE):
                raw_score *= 1.05  # Slight boost — reversals work in bear
            else:
                raw_score *= 0.85  # Penalize trend-following in bear

        # ── Volume gate ──
        rvol = float(indicators.get("relative_volume") or 0)
        if rvol < self.MIN_RVOL:
            return self._hold(symbol, price, raw_score, f"RVOL {rvol:.1f} below minimum {self.MIN_RVOL}")

        # ── Score threshold ──
        if raw_score < self.MIN_SCORE_TO_TRADE:
            return self._hold(symbol, price, raw_score, f"Score {raw_score:.0f} below threshold {self.MIN_SCORE_TO_TRADE}")

        # ── Calculate entry, stop, target ──
        atr = float(indicators.get("atr") or 0)
        if atr <= 0:
            atr = price * 0.02  # Fallback: 2% of price

        entry_price = price  # Market order at current price
        stop_loss = self._calculate_stop(price, atr, indicators, levels, detected_setup)
        risk = abs(entry_price - stop_loss)

        if risk <= 0 or risk / price > 0.05:
            return self._hold(symbol, price, raw_score, "Risk calculation invalid or >5%")

        take_profit = entry_price + risk * self.TARGET_RR_MULTIPLIER

        # ── R:R check ──
        rr = (take_profit - entry_price) / risk
        if rr < self.MIN_RR_RATIO:
            return self._hold(symbol, price, raw_score, f"R:R {rr:.1f} below minimum {self.MIN_RR_RATIO}")

        # ── Validate stop makes structural sense ──
        if stop_loss >= entry_price:
            return self._hold(symbol, price, raw_score, "Stop above entry")

        confidence = min(1.0, raw_score / 100)

        reasoning_parts = []
        if detected_setup != SetupType.NO_SETUP:
            reasoning_parts.append(f"Setup: {detected_setup.value}")
        reasoning_parts.append(f"Score: {raw_score:.0f}/100")
        reasoning_parts.append(f"Trend: {trend_score:.0f}")
        reasoning_parts.append(f"Mom: {momentum_score:.0f}")
        reasoning_parts.append(f"Vol: {volume_score:.0f}")
        reasoning_parts.append(f"Loc: {location_score:.0f}")

        return SignalDecision(
            action="BUY",
            symbol=symbol,
            confidence=confidence,
            entry_price=round(entry_price, 2),
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            pattern=detected_setup.value,
            reasoning=" | ".join(reasoning_parts),
            score=raw_score,
            setup_type=detected_setup,
        )

    # ═══════════════════════════════════════════
    # SCORING FUNCTIONS
    # ═══════════════════════════════════════════

    def _score_trend(self, ind: dict, intra: dict) -> float:
        """Score trend alignment 0-100."""
        score = 50  # Neutral baseline

        # Daily trend
        if ind.get("ema_bullish"):
            score += 10  # EMA 9 > EMA 21
        elif ind.get("ema_bullish") is False:
            score -= 10

        if ind.get("above_sma_50"):
            score += 8
        elif ind.get("above_sma_50") is False:
            score -= 8

        if ind.get("above_sma_200"):
            score += 5
        elif ind.get("above_sma_200") is False:
            score -= 5

        if ind.get("golden_cross"):
            score += 7

        # Intraday trend
        if intra.get("ema_bullish_5m"):
            score += 12  # Fast EMA alignment on 5m
        elif intra.get("ema_bullish_5m") is False:
            score -= 12

        # MACD direction
        macd_hist = ind.get("macd_histogram")
        if macd_hist is not None:
            if macd_hist > 0:
                score += 8
            else:
                score -= 8

        return max(0, min(100, score))

    def _score_momentum(self, ind: dict, intra: dict) -> float:
        """Score momentum 0-100."""
        score = 50

        # RSI — sweet spot for longs is 40-60 (room to run, not overbought)
        rsi = ind.get("rsi")
        if rsi is not None:
            if 40 <= rsi <= 60:
                score += 15  # Ideal zone
            elif 30 <= rsi < 40:
                score += 10  # Oversold bounce potential
            elif rsi < 30:
                score += 20  # Deep oversold — strong reversal potential
            elif 60 < rsi <= 70:
                score += 5   # Still has room
            elif rsi > 70:
                score -= 10  # Overbought — risky for new longs

        # Intraday RSI
        rsi_5m = intra.get("rsi_5m")
        if rsi_5m is not None:
            if 35 <= rsi_5m <= 65:
                score += 8
            elif rsi_5m < 30:
                score += 12  # Intraday oversold bounce
            elif rsi_5m > 75:
                score -= 8

        # MACD cross
        if ind.get("macd_bullish_cross"):
            score += 15
        elif ind.get("macd_bearish_cross"):
            score -= 12

        # Stochastic
        stoch_k = ind.get("stoch_k")
        stoch_d = ind.get("stoch_d")
        if stoch_k is not None and stoch_d is not None:
            if stoch_k < 20 and stoch_k > stoch_d:
                score += 10  # Oversold + crossing up
            elif stoch_k > 80 and stoch_k < stoch_d:
                score -= 8   # Overbought + crossing down

        return max(0, min(100, score))

    def _score_volume(self, price_data: dict, ind: dict, intra: dict) -> float:
        """Score volume conviction 0-100."""
        score = 30  # Below-neutral baseline (volume must PROVE itself)

        rvol = float(ind.get("relative_volume") or 0)
        if rvol >= 5.0:
            score += 50  # Extreme event
        elif rvol >= 3.0:
            score += 40  # Major interest
        elif rvol >= 2.0:
            score += 30  # In play
        elif rvol >= 1.5:
            score += 18  # Elevated
        elif rvol >= 1.2:
            score += 8   # Slightly above average

        # Volume acceleration (intraday)
        vol_acc = intra.get("volume_acceleration")
        if vol_acc is not None:
            if vol_acc > 2.0:
                score += 15
            elif vol_acc > 1.5:
                score += 8
            elif vol_acc < 0.7:
                score -= 10  # Volume drying up

        # OBV trend
        obv = ind.get("obv_trend")
        if obv == "rising":
            score += 5
        elif obv == "falling":
            score -= 5

        return max(0, min(100, score))

    def _score_patterns(self, ind: dict, intra: dict, patterns_text: str, price_data: dict, levels: dict):
        """Score pattern quality 0-100 and identify the setup type."""
        score = 30  # Baseline
        setup = SetupType.NO_SETUP

        gap_pct = price_data.get("change_pct", 0)

        # ── ORB Breakout ──
        if intra.get("above_or_high") and intra.get("or_high"):
            score += 35
            setup = SetupType.ORB_BREAKOUT

        # ── Gap & Go ──
        if abs(gap_pct) >= 3.0:
            rvol = float(ind.get("relative_volume") or 0)
            if rvol >= 2.0 and gap_pct > 0:
                score += 30
                if setup == SetupType.NO_SETUP:
                    setup = SetupType.GAP_AND_GO

        # ── VWAP Reclaim ──
        vwap = ind.get("vwap") or intra.get("vwap_5m")
        if vwap and price_data["price"] > 0:
            vwap_dist = (price_data["price"] - vwap) / price_data["price"] * 100
            if 0 < vwap_dist < 0.5:
                # Just reclaimed VWAP from below
                score += 25
                if setup == SetupType.NO_SETUP:
                    setup = SetupType.VWAP_RECLAIM

        # ── Mean Reversion ──
        bb_lower = ind.get("bb_lower")
        rsi = ind.get("rsi")
        if bb_lower and price_data["price"] <= bb_lower and rsi and rsi < 35:
            score += 30
            if setup == SetupType.NO_SETUP:
                setup = SetupType.MEAN_REVERSION

        # ── Oversold Bounce ──
        if rsi and rsi < 30:
            # Check for support level nearby
            support_levels = levels.get("support_levels", [])
            for s in support_levels[:2]:
                if abs(price_data["price"] - s) / price_data["price"] < 0.01:
                    score += 25
                    setup = SetupType.OVERSOLD_BOUNCE
                    break

        # ── HOD Break ──
        today_high = levels.get("today_high")
        if today_high and price_data["price"] > today_high:
            score += 20
            if setup == SetupType.NO_SETUP:
                setup = SetupType.HOD_BREAK

        # ── Pattern text bonuses ──
        if "bull_flag" in patterns_text.lower() or "BULL" in patterns_text:
            score += 15
            if setup == SetupType.NO_SETUP:
                setup = SetupType.BULL_FLAG

        if "morning_star" in patterns_text.lower():
            score += 12
        if "bullish_engulfing" in patterns_text.lower():
            score += 12
        if "hammer" in patterns_text.lower():
            score += 10

        # Penalty for bearish patterns
        if "bearish_engulfing" in patterns_text.lower():
            score -= 15
        if "shooting_star" in patterns_text.lower():
            score -= 12

        # ── Momentum continuation ──
        ema_bull = intra.get("ema_bullish_5m")
        above_vwap = intra.get("above_vwap_5m")
        if ema_bull and above_vwap and gap_pct > 1.0:
            score += 15
            if setup == SetupType.NO_SETUP:
                setup = SetupType.MOMENTUM_CONTINUATION

        return max(0, min(100, score)), setup

    def _score_location(self, price: float, ind: dict, intra: dict, levels: dict) -> float:
        """Score price location relative to key levels 0-100."""
        score = 50

        # VWAP position
        above_vwap = intra.get("above_vwap_5m")
        if above_vwap:
            score += 15  # Institutional buyers in control
        elif above_vwap is False:
            score -= 10

        # Bollinger Band position
        bb_lower = ind.get("bb_lower")
        bb_upper = ind.get("bb_upper")
        bb_middle = ind.get("bb_middle")
        if bb_lower and bb_upper and price:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_pct = (price - bb_lower) / bb_range
                if bb_pct < 0.2:
                    score += 12  # Near lower BB — bounce zone
                elif bb_pct > 0.8:
                    score -= 8   # Near upper BB — risky for new longs

        # Support proximity
        support_levels = levels.get("support_levels", [])
        for s in support_levels[:2]:
            dist = (price - s) / price * 100 if price > 0 else 999
            if 0 < dist < 1.5:
                score += 10  # Near support — good entry zone
                break

        # Resistance proximity (negative — you're buying into a wall)
        resistance_levels = levels.get("resistance_levels", [])
        for r in resistance_levels[:2]:
            dist = (r - price) / price * 100 if price > 0 else 999
            if 0 < dist < 0.5:
                score -= 12  # Too close to resistance
                break

        # Opening range position
        or_high = levels.get("or_high")
        or_low = levels.get("or_low")
        if or_high and or_low:
            if price > or_high:
                score += 10  # Broken out of opening range
            elif price < or_low:
                score -= 5   # Below opening range — weak

        # Prior day close — gap direction
        prior_close = levels.get("prior_day_close")
        if prior_close and price > 0:
            gap = (price - prior_close) / prior_close * 100
            if gap > 2:
                score += 5  # Gapped up — strength

        return max(0, min(100, score))

    def _calculate_stop(self, price: float, atr: float, ind: dict, levels: dict, setup: SetupType) -> float:
        """Calculate stop loss using ATR + structural levels."""
        # Base stop: ATR-based
        atr_stop = price - (atr * self.STOP_ATR_MULTIPLIER)

        # Try to place stop below a structural level
        structural_stop = atr_stop  # Default

        # Check support levels — place stop just below nearest
        support_levels = levels.get("support_levels", [])
        for s in support_levels[:3]:
            if s < price and s > price * 0.95:  # Within 5%
                candidate = s * (1 - self.STOP_BUFFER_PCT)
                # Use structural level if it's tighter than ATR stop but not too tight
                if candidate > atr_stop and (price - candidate) / price > 0.003:
                    structural_stop = candidate
                    break

        # For ORB setups, use the opening range low as stop
        if setup == SetupType.ORB_BREAKOUT:
            or_low = levels.get("or_low")
            if or_low and or_low < price:
                structural_stop = or_low * (1 - self.STOP_BUFFER_PCT)

        # For VWAP reclaim, stop below VWAP
        if setup == SetupType.VWAP_RECLAIM:
            vwap = levels.get("vwap") or ind.get("vwap") or ind.get("vwap_5m")
            if vwap and vwap < price:
                structural_stop = vwap * (1 - self.STOP_BUFFER_PCT)

        # Use the tighter of ATR and structural (but not TOO tight)
        stop = max(structural_stop, atr_stop)

        # Floor: stop can't be more than 5% below entry
        stop = max(stop, price * 0.95)
        # Ceiling: stop must be at least 0.3% below entry
        stop = min(stop, price * 0.997)

        return stop

    def _hold(self, symbol: str, price: float, score: float, reason: str) -> SignalDecision:
        return SignalDecision(
            action="HOLD", symbol=symbol, confidence=0.0,
            entry_price=price, stop_loss=0.0, take_profit=0.0,
            pattern="", reasoning=reason, score=score,
            setup_type=SetupType.NO_SETUP,
        )
```

### Integration into BacktestEngine

Modify `autotrader/backtest/engine.py`:

1. Import `SignalEngine` and `SignalDecision`
2. Initialize `self.signal_engine = SignalEngine()` in `__init__`
3. Replace the `_ask_claude()` call block (lines ~740-754) with:

```python
decision = self.signal_engine.score(
    symbol=sym,
    price_data=price_data,
    indicators=indicators,
    intraday_indicators=intraday_indicators,
    patterns_text=patterns_text,
    levels=key_levels,
    phase=phase,
    regime=regime.get("label", "unknown"),
)

if decision.action != "BUY":
    continue
```

4. Remove all Claude cache logic, API call tracking, and the `BACKTEST_SYSTEM_PROMPT`
5. Remove `self.api_calls` and `self.cache_hits` tracking
6. Add `--deterministic` flag to runner.py (default True, `--claude` flag for legacy)

### Test After Phase 1

Run: `python -m autotrader.backtest.runner --start 2024-12-02 --end 2025-03-28`

**Expected:** Results will differ from Claude-based runs. That's fine. We need a reproducible baseline before optimizing. Report:
- Total trades, win rate, profit factor, Sharpe, max drawdown
- Pattern breakdown (which SetupTypes are profitable)
- Phase breakdown

---

## Phase 2: Realistic Cost Modeling

### Add to `autotrader/backtest/engine.py`

```python
# Realistic cost model for US equities via Alpaca
class CostModel:
    """Models all real trading costs."""

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
        return price + cls.entry_cost(price, shares) / shares

    @classmethod
    def effective_exit(cls, price: float, shares: int) -> float:
        """Price you effectively receive (lower than market)."""
        return price - cls.exit_cost(price, shares) / shares
```

### Integration

Replace all instances of `+ SLIPPAGE_PER_SHARE` and `- SLIPPAGE_PER_SHARE` with the CostModel methods. Update P&L calculations to subtract round-trip costs.

In `_close_position()`:
```python
costs = CostModel.round_trip_cost(pos.entry_price, exit_price, qty)
pnl = (exit_price - pos.entry_price) * qty - costs
```

Track total costs in BacktestResult:
```python
total_costs: float = 0.0  # New field
```

### Test After Phase 2

Re-run the same backtest. Compare P&L before and after costs. Report the total cost drag as a percentage of gross P&L.

---

## Phase 3: MAE/MFE Analysis

### Why
Maximum Adverse Excursion (MAE) = how far a trade goes against you before the outcome.
Maximum Favorable Excursion (MFE) = how far a trade goes in your favor before exit.

This data tells you:
- Are your stops too tight? (MAE clusters near stop → getting stopped out on noise)
- Are your stops too wide? (MAE spread out → taking unnecessary risk)
- Are you leaving money on the table? (MFE >> exit price → cutting winners)
- Are your scale-out levels optimal? (Is 1R the right first target?)

### What to Build

Add MAE/MFE tracking to `SimulatedPosition`:

```python
@dataclass
class SimulatedPosition:
    # ... existing fields ...
    mae: float = 0.0           # Maximum adverse excursion (worst drawdown from entry)
    mfe: float = 0.0           # Maximum favorable excursion (best unrealized gain from entry)
    mae_time: datetime = None  # When MAE occurred
    mfe_time: datetime = None  # When MFE occurred
```

In `_manage_positions_at_bar()`, after updating `bar_high` and `bar_low`:

```python
# Track MAE/MFE
adverse = (pos.entry_price - bar_low) / pos.entry_price * 100  # % drawdown
favorable = (bar_high - pos.entry_price) / pos.entry_price * 100  # % gain

if adverse > pos.mae:
    pos.mae = adverse
    pos.mae_time = current_time

if favorable > pos.mfe:
    pos.mfe = favorable
    pos.mfe_time = current_time
```

Add MAE/MFE to `BacktestTrade`:
```python
mae: float = 0.0
mfe: float = 0.0
```

### MAE/MFE Report

Add to `format_backtest_result()`:

```python
# MAE/MFE Analysis
if result.trades:
    winners = [t for t in result.trades if t.pnl > 0]
    losers = [t for t in result.trades if t.pnl <= 0]

    lines.append("")
    lines.append("─── MAE/MFE ANALYSIS ───")

    if winners:
        avg_winner_mae = np.mean([t.mae for t in winners])
        avg_winner_mfe = np.mean([t.mfe for t in winners])
        lines.append(f"  Winners avg MAE: {avg_winner_mae:.2f}% (noise before profit)")
        lines.append(f"  Winners avg MFE: {avg_winner_mfe:.2f}% (max potential)")
        lines.append(f"  Winners avg exit vs MFE: {avg_winner_mfe - np.mean([(t.exit_price - t.entry_price)/t.entry_price*100 for t in winners]):.2f}% left on table")

    if losers:
        avg_loser_mae = np.mean([t.mae for t in losers])
        avg_loser_mfe = np.mean([t.mfe for t in losers])
        lines.append(f"  Losers avg MAE:  {avg_loser_mae:.2f}% (how far against before stopped)")
        lines.append(f"  Losers avg MFE:  {avg_loser_mfe:.2f}% (were they ever profitable?)")

        # Key insight: if losers' avg MFE > 1R, your stops may be too tight
        # If losers' avg MFE < 0.3R, the setup was never working
        losers_with_mfe = [t for t in losers if t.mfe > 0 and t.entry_price > 0]
        if losers_with_mfe:
            pct_losers_were_profitable = len([t for t in losers_with_mfe
                if t.mfe > abs(t.entry_price - (t.entry_price - t.mae/100*t.entry_price)) / t.entry_price * 100
            ]) / len(losers) * 100
            lines.append(f"  Losers that were profitable at some point: {pct_losers_were_profitable:.0f}%")
```

---

## Phase 4: Walk-Forward Optimization

### What to Build

Create `autotrader/backtest/optimizer.py`:

```python
"""Walk-forward parameter optimization.

Splits data into rolling windows:
  [===== IN-SAMPLE =====][== OUT-OF-SAMPLE ==]
                    [===== IN-SAMPLE =====][== OUT-OF-SAMPLE ==]
                                      [===== IN-SAMPLE =====][== OUT-OF-SAMPLE ==]

Optimizes parameters on in-sample, tests on out-of-sample.
A robust strategy performs consistently across ALL windows.
"""

import itertools
from datetime import datetime, timedelta

class WalkForwardOptimizer:
    """Walk-forward parameter optimization for the SignalEngine."""

    PARAM_GRID = {
        "MIN_SCORE_TO_TRADE": [45, 50, 55, 60, 65],
        "MIN_RVOL": [1.0, 1.3, 1.5, 2.0],
        "STOP_ATR_MULTIPLIER": [1.0, 1.5, 2.0, 2.5],
        "TARGET_RR_MULTIPLIER": [2.0, 2.5, 3.0],
        "WEIGHT_TREND": [15, 20, 25],
        "WEIGHT_VOLUME": [20, 25, 30],
    }

    def __init__(self, full_start: str, full_end: str,
                 n_windows: int = 4, in_sample_pct: float = 0.70):
        self.full_start = full_start
        self.full_end = full_end
        self.n_windows = n_windows
        self.in_sample_pct = in_sample_pct

    def run(self):
        """Run walk-forward optimization."""
        from autotrader.backtest.engine import BacktestEngine
        from autotrader.backtest.data_fetcher import get_trading_days

        all_days = get_trading_days(self.full_start, self.full_end)
        window_size = len(all_days) // self.n_windows
        split = int(window_size * self.in_sample_pct)

        # Generate parameter combinations (limit to avoid explosion)
        # Use 2-parameter sweeps, not full grid
        param_combos = self._generate_smart_combos()

        results_by_window = []

        for w in range(self.n_windows):
            start_idx = w * window_size
            end_idx = start_idx + window_size
            if end_idx > len(all_days):
                break

            is_start = all_days[start_idx].strftime("%Y-%m-%d")
            is_end = all_days[start_idx + split - 1].strftime("%Y-%m-%d")
            oos_start = all_days[start_idx + split].strftime("%Y-%m-%d")
            oos_end = all_days[end_idx - 1].strftime("%Y-%m-%d")

            print(f"\n{'='*60}")
            print(f"Window {w+1}/{self.n_windows}")
            print(f"  In-sample:     {is_start} → {is_end}")
            print(f"  Out-of-sample: {oos_start} → {oos_end}")

            # Optimize on in-sample
            best_params = None
            best_pf = 0

            for params in param_combos:
                engine = BacktestEngine(
                    start=is_start, end=is_end,
                    signal_params=params,
                )
                result = engine.run()
                if result.total_trades >= 30 and result.profit_factor > best_pf:
                    best_pf = result.profit_factor
                    best_params = params

            if best_params is None:
                print(f"  No valid parameters found for window {w+1}")
                continue

            print(f"  Best in-sample params: {best_params}")
            print(f"  Best in-sample PF: {best_pf:.2f}")

            # Test on out-of-sample with best params
            oos_engine = BacktestEngine(
                start=oos_start, end=oos_end,
                signal_params=best_params,
            )
            oos_result = oos_engine.run()

            print(f"  OOS trades: {oos_result.total_trades}")
            print(f"  OOS PF: {oos_result.profit_factor:.2f}")
            print(f"  OOS Return: {oos_result.return_pct:+.2f}%")
            print(f"  OOS Sharpe: {oos_result.sharpe_ratio:.2f}")

            results_by_window.append({
                "window": w + 1,
                "params": best_params,
                "is_pf": best_pf,
                "oos_pf": oos_result.profit_factor,
                "oos_return": oos_result.return_pct,
                "oos_sharpe": oos_result.sharpe_ratio,
                "oos_trades": oos_result.total_trades,
            })

        # Summary
        print(f"\n{'='*60}")
        print("WALK-FORWARD SUMMARY")
        print(f"{'='*60}")

        if results_by_window:
            oos_returns = [r["oos_return"] for r in results_by_window]
            oos_pfs = [r["oos_pf"] for r in results_by_window]
            profitable_windows = sum(1 for r in oos_returns if r > 0)

            print(f"Windows profitable: {profitable_windows}/{len(results_by_window)}")
            print(f"Avg OOS return: {np.mean(oos_returns):+.2f}%")
            print(f"Avg OOS PF: {np.mean(oos_pfs):.2f}")
            print(f"Worst window: {min(oos_returns):+.2f}%")
            print(f"Best window: {max(oos_returns):+.2f}%")

            if profitable_windows >= len(results_by_window) * 0.75:
                print("\n>>> WALK-FORWARD: PASS <<<")
            else:
                print("\n>>> WALK-FORWARD: FAIL <<<")

    def _generate_smart_combos(self) -> list[dict]:
        """Generate parameter combinations without full grid explosion.

        Strategy: fix most params at default, sweep 2 at a time.
        """
        defaults = {
            "MIN_SCORE_TO_TRADE": 55,
            "MIN_RVOL": 1.3,
            "STOP_ATR_MULTIPLIER": 1.5,
            "TARGET_RR_MULTIPLIER": 2.5,
            "WEIGHT_TREND": 20,
            "WEIGHT_VOLUME": 25,
        }

        combos = [defaults.copy()]  # Always include default

        # Sweep pairs
        pairs = [
            ("MIN_SCORE_TO_TRADE", "MIN_RVOL"),
            ("STOP_ATR_MULTIPLIER", "TARGET_RR_MULTIPLIER"),
            ("WEIGHT_TREND", "WEIGHT_VOLUME"),
        ]

        for key1, key2 in pairs:
            for v1 in self.PARAM_GRID[key1]:
                for v2 in self.PARAM_GRID[key2]:
                    combo = defaults.copy()
                    combo[key1] = v1
                    combo[key2] = v2
                    combos.append(combo)

        # Deduplicate
        unique = []
        seen = set()
        for c in combos:
            key = tuple(sorted(c.items()))
            if key not in seen:
                seen.add(key)
                unique.append(c)

        return unique
```

### Integration

Add `signal_params` parameter to `BacktestEngine.__init__()`:
```python
def __init__(self, ..., signal_params: dict | None = None):
    ...
    self.signal_engine = SignalEngine(params=signal_params)
```

Add CLI entry point:
```python
# In runner.py, add --optimize flag
if args.optimize:
    optimizer = WalkForwardOptimizer(
        full_start=args.start,
        full_end=args.end,
        n_windows=4,
    )
    optimizer.run()
```

### Test After Phase 4

Run: `python -m autotrader.backtest.runner --start 2024-09-01 --end 2025-03-28 --optimize`

This needs at least 6 months of data. Report which parameter combinations are stable across windows.

---

## Phase 5: Extended Backtest Period

### Why
20 days is noise. You need to test across:
- A rally (late 2024 post-election)
- A selloff (if any)
- A choppy range
- A VIX spike
- Different rate/macro environments

### What to Do

1. Run the deterministic engine on **6+ months**: `--start 2024-09-01 --end 2025-03-28`
2. Break results by month to see consistency
3. If any month has a drawdown > 8%, investigate why

### Monthly Breakdown Report

Add to the engine:

```python
# Group trades by month
monthly = {}
for t in result.trades:
    month_key = t.entry_time.strftime("%Y-%m")
    if month_key not in monthly:
        monthly[month_key] = {"trades": 0, "wins": 0, "pnl": 0.0}
    monthly[month_key]["trades"] += 1
    monthly[month_key]["pnl"] += t.pnl
    if t.pnl > 0:
        monthly[month_key]["wins"] += 1

lines.append("")
lines.append("─── MONTHLY BREAKDOWN ───")
for month, stats in sorted(monthly.items()):
    wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
    ret_pct = stats["pnl"] / starting_equity * 100
    lines.append(
        f"  {month}: {stats['trades']:3d} trades | WR: {wr:5.1f}% | "
        f"P&L: ${stats['pnl']:>9,.2f} | Return: {ret_pct:+.2f}%"
    )
```

---

## Phase 6: Position Management Optimization

### Use MAE/MFE Data to Tune

After running the full backtest with MAE/MFE tracking:

1. **Scale-out levels:** If MFE data shows winners consistently reach 1.5R before pulling back, your 1R first scale-out is leaving money on the table. If winners rarely reach 2R, your second target is too ambitious.

2. **Time stop:** Your 30-minute hard close is aggressive. If MAE/MFE shows many winners take 45-90 minutes to fully play out, extend it:
   - Losers at 30 min with MFE < 0.3R → close immediately (thesis dead)
   - Winners at 30 min with current R > 0.5 → keep with trailing stop
   - Flat trades at 30 min → close (opportunity cost)
   - **Extend the hard time stop to 60 minutes** and let the trailing stop logic handle exits

3. **Trailing stop after scale-out:** Currently trails at 1R from high. Use ATR instead — it adapts to the stock's actual volatility.

### Parameter Search for Position Management

```python
PM_GRID = {
    "first_scale_r": [0.75, 1.0, 1.25, 1.5],       # First scale-out level
    "first_scale_pct": [0.33, 0.50],                  # Portion to sell at first target
    "time_stop_minutes": [30, 45, 60, 90],             # Hard time stop
    "trail_atr_mult": [0.75, 1.0, 1.5, 2.0],          # Trailing stop ATR multiplier
}
```

---

## Phase 7: Survivorship Bias Mitigation

### What to Do

You can't fully fix this without a survivorship-free dataset (Norgate, Sharadar, CRSP — all paid). But you can mitigate it:

1. **Pad expected returns down 15%** — academic research shows survivorship bias inflates returns by 10-20% for US equities.

2. **Add a "delisting return" penalty:** For each trade, apply a small random probability (0.5%) that the stock gaps down 50% overnight (simulating a sudden delisting or halt). This is rough but directionally correct.

3. **Track which stocks in your backtest universe NO LONGER EXIST** by cross-referencing against current Alpaca tradeable symbols. If you find any, note them.

```python
# In the results summary
lines.append(f"  Survivorship bias adjustment: -15% applied to expected live returns")
lines.append(f"  Adjusted expected monthly return: {adjusted_monthly:+.2f}%")
```

---

## PASS/FAIL Criteria (Updated)

After all phases, the system must meet ALL of these on 6+ months of data:

| Metric | Threshold | Why |
|--------|-----------|-----|
| Total trades | ≥ 500 | Statistical significance |
| Win rate | 48-65% | Realistic for day trading |
| Profit factor | > 1.3 | Covers costs with margin |
| Sharpe ratio | 0.75 - 3.0 | Below 0.75 = weak, above 3.0 = overfit |
| Max drawdown | < 10% | Sustainable risk |
| Monthly return (avg) | > 1.5% | Realistic aspirational target |
| Walk-forward pass rate | ≥ 75% of windows profitable | Robustness |
| Parameter stability | Edge survives ±20% param change | Not curve-fit |

### On Your 5% Monthly Goal

If the system consistently delivers 2-3% monthly across 6+ months with walk-forward validation, that's an excellent result — better than 99% of retail traders. Push for 5% only after proving 2-3% is stable and repeatable. Compounding 2.5% monthly = ~34% annually, which would put you in elite territory.

---

## Execution Order

1. **Phase 1:** Build signal engine, integrate, get baseline results
2. **Phase 2:** Add cost modeling, measure drag
3. **Phase 3:** Add MAE/MFE tracking, analyze stop/target quality
4. **Phase 4:** Walk-forward optimization (needs Phase 1 done first)
5. **Phase 5:** Extended backtest (6+ months)
6. **Phase 6:** Tune position management using MAE/MFE insights
7. **Phase 7:** Survivorship adjustment

After all phases: compare deterministic engine vs old Claude-based results honestly. The deterministic engine should be WORSE on the specific backtest period (because Claude was subtly overfitting to patterns in its training data), but MORE ROBUST across time periods.

---

## Important Note

This prompt builds a **replacement** for the Claude decision engine in backtesting. The live system can still use Claude for analysis IF the deterministic engine validates that the underlying signals have edge. But the backtest must be deterministic to be trustworthy.

The 30-minute time stop adjustment in Phase 6 is critical — your current system cuts winners too early. The MAE/MFE data will tell you exactly how much money you're leaving on the table.

Run Phase 1 first. Report results. Then proceed.
