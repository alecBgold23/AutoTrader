"""Deterministic signal engine — replaces Claude for reproducible backtesting.

Scores stocks 0-100 using weighted technical factors, then applies a
comprehensive confidence model that incorporates price tier, phase edge,
factor confluence, and stop quality. Only high-conviction, cost-efficient
trades pass the filter.

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
    confidence: float     # 0.0 - 1.0 (comprehensive composite)
    entry_price: float
    stop_loss: float
    take_profit: float
    pattern: str
    reasoning: str
    score: float          # Raw 0-100 technical score
    setup_type: SetupType


class SignalEngine:
    """Deterministic multi-factor scoring engine with comprehensive confidence."""

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
    MIN_SCORE_TO_TRADE = 62  # Technical score gate (first filter)
    MIN_CONFIDENCE = 0.70    # Comprehensive confidence gate (final filter)
    MIN_RVOL = 1.3           # Minimum relative volume
    MIN_RR_RATIO = 2.0       # Minimum reward:risk
    MIN_CONFLUENCE = 3       # Minimum bullish factors (out of 5)

    # Price tier — cheap stocks have terrible cost efficiency
    MIN_PRICE = 30           # Hard floor: skip penny stocks (34.7% WR, $43 avg cost)
    MAX_PRICE = 500          # Skip stocks over $500 (small sample, wide spreads)
    # Note: $30-60 range is penalized via confidence model price_component,
    # not hard-blocked. This lets exceptional setups still pass.

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

        Uses a two-stage filter:
        1. Technical score (0-100) — must pass MIN_SCORE_TO_TRADE
        2. Comprehensive confidence (0-1) — incorporates price tier, phase,
           confluence, and stop quality. Must pass MIN_CONFIDENCE.
        """
        price = price_data["price"]
        if price <= 0:
            return self._hold(symbol, price, 0, "Invalid price")

        # ══════ PRICE TIER GATE ══════
        # Data shows: $5-20 stocks have 34.7% WR and $43 avg cost/trade
        # $50-100 stocks have 57.5% WR and $6 avg cost/trade
        if price < self.MIN_PRICE:
            return self._hold(symbol, price, 0, f"Price ${price:.0f} below ${self.MIN_PRICE} minimum")
        if price > self.MAX_PRICE:
            return self._hold(symbol, price, 0, f"Price ${price:.0f} above ${self.MAX_PRICE} maximum")

        # ══════ PHASE GATE ══════
        # Afternoon: 30% WR across 4 quarters, -$4,140 total. Block it.
        # Lunch: already blocked by the backtest engine, but double-check.
        if phase in ("lunch", "close", "premarket"):
            return self._hold(symbol, price, 0, f"Phase {phase} blocked")
        if phase == "afternoon":
            return self._hold(symbol, price, 0, "Afternoon phase blocked (30% WR across all periods)")

        # ══════ STAGE 1: TECHNICAL SCORE ══════
        trend_score = self._score_trend(indicators, intraday_indicators)
        momentum_score = self._score_momentum(indicators, intraday_indicators)
        volume_score = self._score_volume(price_data, indicators, intraday_indicators)
        pattern_score, detected_setup = self._score_patterns(
            indicators, intraday_indicators, patterns_text, price_data, levels
        )
        location_score = self._score_location(price, indicators, intraday_indicators, levels)

        factor_scores = {
            "trend": trend_score,
            "momentum": momentum_score,
            "volume": volume_score,
            "pattern": pattern_score,
            "location": location_score,
        }

        # Weighted composite
        raw_score = (
            trend_score * self.WEIGHT_TREND / 100 +
            momentum_score * self.WEIGHT_MOMENTUM / 100 +
            volume_score * self.WEIGHT_VOLUME / 100 +
            pattern_score * self.WEIGHT_PATTERN / 100 +
            location_score * self.WEIGHT_LOCATION / 100
        )

        # Phase adjustment (open is noisy but not blocked)
        if phase == "open":
            raw_score *= 0.85

        # Regime adjustment
        if "bear" in regime:
            if detected_setup in (SetupType.MEAN_REVERSION, SetupType.OVERSOLD_BOUNCE):
                raw_score *= 1.05
            else:
                raw_score *= 0.85

        # Volume gate
        rvol = float(indicators.get("relative_volume") or 0)
        if rvol < self.MIN_RVOL:
            return self._hold(symbol, price, raw_score, f"RVOL {rvol:.1f} below minimum {self.MIN_RVOL}")

        # Technical score gate
        if raw_score < self.MIN_SCORE_TO_TRADE:
            return self._hold(symbol, price, raw_score, f"Score {raw_score:.0f} below threshold {self.MIN_SCORE_TO_TRADE}")

        # ══════ CONFLUENCE CHECK ══════
        # Require N+ factors scoring above neutral (>50)
        bullish_factors = sum(1 for s in factor_scores.values() if s > 50)
        if bullish_factors < self.MIN_CONFLUENCE:
            return self._hold(symbol, price, raw_score,
                              f"Only {bullish_factors}/{self.MIN_CONFLUENCE} factors bullish")

        # ══════ ENTRY/STOP/TARGET ══════
        atr = float(indicators.get("atr") or 0)
        if atr <= 0:
            atr = price * 0.02

        entry_price = price
        stop_loss = self._calculate_stop(price, atr, indicators, levels, detected_setup)
        risk = abs(entry_price - stop_loss)

        if risk <= 0 or risk / price > 0.05:
            return self._hold(symbol, price, raw_score, "Risk calculation invalid or >5%")

        take_profit = entry_price + risk * self.TARGET_RR_MULTIPLIER

        rr = (take_profit - entry_price) / risk
        if rr < self.MIN_RR_RATIO:
            return self._hold(symbol, price, raw_score, f"R:R {rr:.1f} below minimum {self.MIN_RR_RATIO}")

        if stop_loss >= entry_price:
            return self._hold(symbol, price, raw_score, "Stop above entry")

        # ══════ STAGE 2: COMPREHENSIVE CONFIDENCE ══════
        confidence = self._compute_confidence(
            raw_score=raw_score,
            factor_scores=factor_scores,
            price=price,
            phase=phase,
            risk_pct=risk / price,
            rvol=rvol,
            detected_setup=detected_setup,
        )

        if confidence < self.MIN_CONFIDENCE:
            return self._hold(symbol, price, raw_score,
                              f"Confidence {confidence:.2f} below {self.MIN_CONFIDENCE}")

        # ══════ BUILD DECISION ══════
        reasoning_parts = []
        if detected_setup != SetupType.NO_SETUP:
            reasoning_parts.append(f"Setup: {detected_setup.value}")
        reasoning_parts.append(f"Score: {raw_score:.0f}/100")
        reasoning_parts.append(f"Conf: {confidence:.2f}")
        reasoning_parts.append(f"Confluence: {bullish_factors}/5")
        reasoning_parts.append(f"T:{trend_score:.0f} M:{momentum_score:.0f} V:{volume_score:.0f} P:{pattern_score:.0f} L:{location_score:.0f}")

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
    # COMPREHENSIVE CONFIDENCE MODEL
    # ═══════════════════════════════════════════

    def _compute_confidence(
        self,
        raw_score: float,
        factor_scores: dict,
        price: float,
        phase: str,
        risk_pct: float,
        rvol: float,
        detected_setup: SetupType,
    ) -> float:
        """Compute comprehensive confidence from ALL system signals.

        Components (weighted to sum to 1.0):
          0.35 — Technical score (already computed)
          0.20 — Confluence quality (how many factors agree, and by how much)
          0.20 — Price tier (cost efficiency — $50-100 sweet spot)
          0.15 — Phase edge (prime phase bonus)
          0.10 — Risk quality (tighter stop = more defined risk = higher confidence)
        """
        # 1. Technical score component (0-1, 0.35 weight)
        tech_component = min(1.0, raw_score / 100)

        # 2. Confluence component (0-1, 0.20 weight)
        # Not just "how many above 50" but "how strongly above 50"
        bullish_strengths = [(s - 50) / 50 for s in factor_scores.values() if s > 50]
        if bullish_strengths:
            # Average excess above neutral, plus bonus for having more factors
            avg_strength = np.mean(bullish_strengths)
            count_bonus = min(1.0, len(bullish_strengths) / 5)  # 5/5 = full bonus
            confluence_component = 0.5 * avg_strength + 0.5 * count_bonus
        else:
            confluence_component = 0.0

        # 3. Price tier component (0-1, 0.20 weight)
        # Backtested: $60-150 = 57.5% WR, $150-300 = decent, $30-60 = 30.6% WR
        if 60 <= price <= 150:
            price_component = 1.0   # Sweet spot
        elif 150 < price <= 300:
            price_component = 0.7   # Still good
        elif 300 < price <= 500:
            price_component = 0.5   # Less liquid
        elif 30 <= price < 60:
            price_component = 0.25  # $30-60 penalized (30.6% WR, -$6,430)
        else:
            price_component = 0.1   # Sub-$30 or edge cases

        # 4. Phase component (0-1, 0.15 weight)
        # Prime: 50-60% WR, Open: mixed, Afternoon: BLOCKED (won't reach here)
        phase_map = {
            "prime": 1.0,       # Best phase
            "power_hour": 0.7,  # Decent
            "open": 0.4,        # Noisy
        }
        phase_component = phase_map.get(phase, 0.3)

        # 5. Risk quality component (0-1, 0.10 weight)
        # Tighter stops (0.5-1.5% risk) = more defined setups = higher confidence
        if 0.005 <= risk_pct <= 0.015:
            risk_component = 1.0   # Tight, well-defined
        elif 0.015 < risk_pct <= 0.025:
            risk_component = 0.7   # Moderate
        elif 0.025 < risk_pct <= 0.04:
            risk_component = 0.4   # Wide
        else:
            risk_component = 0.2   # Too tight or too wide

        # Weighted composite
        confidence = (
            0.35 * tech_component +
            0.20 * confluence_component +
            0.20 * price_component +
            0.15 * phase_component +
            0.10 * risk_component
        )

        return round(confidence, 4)

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
        # Cap extreme volume bonus — RVOL > 5 can mean distribution/panic
        if rvol >= 5.0:
            score += 35
        elif rvol >= 3.0:
            score += 30
        elif rvol >= 2.0:
            score += 25
        elif rvol >= 1.5:
            score += 15
        elif rvol >= 1.2:
            score += 8

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
        # Require price not too extended above OR high (>2% = chasing)
        or_high = intra.get("or_high")
        if intra.get("above_or_high") and or_high and or_high > 0:
            extension_pct = (price_data["price"] - or_high) / or_high * 100
            if extension_pct <= 2.0:
                score += 35
                setup = SetupType.ORB_BREAKOUT
                # Volume confirmation bonus (additive, not a gate)
                vol_acc = intra.get("volume_acceleration")
                if vol_acc is not None and vol_acc >= 1.5:
                    score += 5
            else:
                score += 10  # Extended breakout, lower conviction

        # ── Gap & Go ── BLOCKED
        # Data: -$5,889 across 607 trades, 34.9% WR. Volatile names (GME, GTLB,
        # NVDL) dominate losses. Gap continuation is unreliable intraday.
        # Do NOT assign score or setup for gap-and-go.

        # ── VWAP Reclaim ──
        vwap = ind.get("vwap") or intra.get("vwap_5m")
        if vwap and price_data["price"] > 0:
            vwap_dist = (price_data["price"] - vwap) / price_data["price"] * 100
            if 0 < vwap_dist < 0.5:
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
        if "bull_flag" in patterns_text.lower():
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
        # Data: 29.2% WR, -$834 when used as primary setup. Strong as confirmation.
        ema_bull = intra.get("ema_bullish_5m")
        above_vwap = intra.get("above_vwap_5m")
        if ema_bull and above_vwap and gap_pct > 1.0:
            if setup == SetupType.NO_SETUP:
                pass  # Blocked as standalone — 25% WR, -$997 across 4 periods
            else:
                score += 15  # Strong as confirmation of existing pattern

        return max(0, min(100, score)), setup

    def _score_location(self, price: float, ind: dict, intra: dict, levels: dict) -> float:
        """Score price location relative to key levels 0-100."""
        score = 50

        # VWAP position
        above_vwap = intra.get("above_vwap_5m")
        if above_vwap:
            score += 15
        elif above_vwap is False:
            score -= 10

        # Bollinger Band position
        bb_lower = ind.get("bb_lower")
        bb_upper = ind.get("bb_upper")
        if bb_lower and bb_upper and price:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_pct = (price - bb_lower) / bb_range
                if bb_pct < 0.2:
                    score += 12
                elif bb_pct > 0.8:
                    # Don't penalize breakouts for being near upper BB — that's expected
                    or_high = levels.get("or_high")
                    if not (or_high and price > or_high):
                        score -= 8

        # Support proximity
        support_levels = levels.get("support_levels", [])
        for s in support_levels[:2]:
            dist = (price - s) / price * 100 if price > 0 else 999
            if 0 < dist < 1.5:
                score += 10
                break

        # Resistance proximity
        resistance_levels = levels.get("resistance_levels", [])
        for r in resistance_levels[:2]:
            dist = (r - price) / price * 100 if price > 0 else 999
            if 0 < dist < 0.5:
                score -= 12
                break

        # Opening range position
        or_high = levels.get("or_high")
        or_low = levels.get("or_low")
        if or_high and or_low:
            if price > or_high:
                score += 10
            elif price < or_low:
                score -= 5

        # Prior day close — gap direction
        prior_close = levels.get("prior_day_close")
        if prior_close and price > 0:
            gap = (price - prior_close) / prior_close * 100
            if gap > 2:
                score += 5

        return max(0, min(100, score))

    def _calculate_stop(self, price: float, atr: float, ind: dict, levels: dict, setup: SetupType) -> float:
        """Calculate stop loss using ATR + structural levels."""
        atr_stop = price - (atr * self.STOP_ATR_MULTIPLIER)
        structural_stop = atr_stop

        support_levels = levels.get("support_levels", [])
        for s in support_levels[:3]:
            if s < price and s > price * 0.95:
                candidate = s * (1 - self.STOP_BUFFER_PCT)
                if candidate > atr_stop and (price - candidate) / price > 0.003:
                    structural_stop = candidate
                    break

        if setup == SetupType.ORB_BREAKOUT:
            or_low = levels.get("or_low")
            if or_low and or_low < price:
                structural_stop = or_low * (1 - self.STOP_BUFFER_PCT)

        if setup == SetupType.VWAP_RECLAIM:
            vwap = levels.get("vwap") or ind.get("vwap") or ind.get("vwap_5m")
            if vwap and vwap < price:
                structural_stop = vwap * (1 - self.STOP_BUFFER_PCT)

        stop = max(structural_stop, atr_stop)
        stop = max(stop, price * 0.95)
        stop = min(stop, price * 0.997)

        return stop

    def _hold(self, symbol: str, price: float, score: float, reason: str) -> SignalDecision:
        return SignalDecision(
            action="HOLD", symbol=symbol, confidence=0.0,
            entry_price=price, stop_loss=0.0, take_profit=0.0,
            pattern="", reasoning=reason, score=score,
            setup_type=SetupType.NO_SETUP,
        )
