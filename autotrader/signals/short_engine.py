"""Deterministic SHORT signal engine — scores bearish setups for intraday shorting.

Completely independent from the long engine. Designed to be tested in isolation
first, then merged into the main system only if consistently profitable.

Key short patterns:
- Gap & Fade: Gap-up stocks that fail to hold → sell the trap
- ORB Breakdown: Price breaks below opening range low
- VWAP Rejection: Failed reclaim of VWAP from below
- Bear Flag: Consolidation after a drop, then continuation
- Failed Breakout: Breaks above resistance then reverses below it
- LOD Break: Breaking below low of day with volume
- Exhaustion Short: Parabolic move up on declining volume + reversal candle
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum


class ShortSetupType(Enum):
    GAP_AND_FADE = "Gap & Fade"
    ORB_BREAKDOWN = "ORB Breakdown"
    VWAP_REJECTION = "VWAP Rejection"
    BEAR_FLAG = "Bear Flag"
    FAILED_BREAKOUT = "Failed Breakout"
    LOD_BREAK = "LOD Break"
    EXHAUSTION_SHORT = "Exhaustion Short"
    MOMENTUM_BREAKDOWN = "Momentum Breakdown"
    NO_SETUP = "No Setup"


@dataclass
class ShortSignalDecision:
    action: str           # "SHORT" or "HOLD"
    symbol: str
    confidence: float     # 0.0 - 1.0
    entry_price: float
    stop_loss: float      # ABOVE entry for shorts
    take_profit: float    # BELOW entry for shorts
    pattern: str
    reasoning: str
    score: float
    setup_type: ShortSetupType


class ShortSignalEngine:
    """Deterministic multi-factor scoring engine for SHORT setups."""

    # Factor weights (must sum to 100)
    WEIGHT_TREND = 20        # Bearish trend alignment
    WEIGHT_MOMENTUM = 20     # Overbought conditions, bearish divergence
    WEIGHT_VOLUME = 25       # Selling pressure, distribution
    WEIGHT_PATTERN = 15      # Bearish patterns
    WEIGHT_LOCATION = 20     # Extended from key levels, resistance rejection

    # Thresholds
    MIN_SCORE_TO_TRADE = 62
    MIN_CONFIDENCE = 0.70
    MIN_RVOL = 1.3
    MIN_RR_RATIO = 2.0
    MIN_CONFLUENCE = 3       # Minimum bearish factors (out of 5)

    # Price tier — same as long side, cheap stocks are untradeable
    MIN_PRICE = 30
    MAX_PRICE = 500

    # Stop/target
    STOP_ATR_MULTIPLIER = 1.5
    STOP_BUFFER_PCT = 0.002
    TARGET_RR_MULTIPLIER = 2.5

    def __init__(self, params: dict | None = None):
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
    ) -> ShortSignalDecision:
        """Score a stock for SHORT potential."""
        price = price_data["price"]
        if price <= 0:
            return self._hold(symbol, price, 0, "Invalid price")

        # Price gate
        if price < self.MIN_PRICE or price > self.MAX_PRICE:
            return self._hold(symbol, price, 0, f"Price ${price:.0f} outside range")

        # Phase gate — same blocked phases as longs
        if phase in ("lunch", "close", "premarket"):
            return self._hold(symbol, price, 0, f"Phase {phase} blocked")
        if phase == "afternoon":
            return self._hold(symbol, price, 0, "Afternoon blocked")

        # ══════ STAGE 1: TECHNICAL SCORE ══════
        trend_score = self._score_trend_bearish(indicators, intraday_indicators)
        momentum_score = self._score_momentum_bearish(indicators, intraday_indicators)
        volume_score = self._score_volume_selling(price_data, indicators, intraday_indicators)
        pattern_score, detected_setup = self._score_patterns_bearish(
            indicators, intraday_indicators, patterns_text, price_data, levels
        )
        location_score = self._score_location_bearish(price, indicators, intraday_indicators, levels)

        factor_scores = {
            "trend": trend_score,
            "momentum": momentum_score,
            "volume": volume_score,
            "pattern": pattern_score,
            "location": location_score,
        }

        raw_score = (
            trend_score * self.WEIGHT_TREND / 100 +
            momentum_score * self.WEIGHT_MOMENTUM / 100 +
            volume_score * self.WEIGHT_VOLUME / 100 +
            pattern_score * self.WEIGHT_PATTERN / 100 +
            location_score * self.WEIGHT_LOCATION / 100
        )

        # Phase adjustment
        if phase == "open":
            raw_score *= 0.85  # Open is noisy for both directions

        # Regime boost — shorts SHINE in bear markets
        if "bear" in regime:
            raw_score *= 1.10
        elif "bull_quiet" in regime:
            raw_score *= 0.80  # Harder to short in calm bull markets

        # Volume gate
        rvol = float(indicators.get("relative_volume") or 0)
        if rvol < self.MIN_RVOL:
            return self._hold(symbol, price, raw_score, f"RVOL {rvol:.1f} below {self.MIN_RVOL}")

        # Score gate
        if raw_score < self.MIN_SCORE_TO_TRADE:
            return self._hold(symbol, price, raw_score, f"Score {raw_score:.0f} below {self.MIN_SCORE_TO_TRADE}")

        # Confluence check
        bearish_factors = sum(1 for s in factor_scores.values() if s > 50)
        if bearish_factors < self.MIN_CONFLUENCE:
            return self._hold(symbol, price, raw_score,
                              f"Only {bearish_factors}/{self.MIN_CONFLUENCE} factors bearish")

        # ══════ ENTRY/STOP/TARGET (reversed for shorts) ══════
        atr = float(indicators.get("atr") or 0)
        if atr <= 0:
            atr = price * 0.02

        entry_price = price
        stop_loss = self._calculate_stop_short(price, atr, indicators, levels, detected_setup)
        risk = abs(stop_loss - entry_price)  # Stop is ABOVE entry for shorts

        if risk <= 0 or risk / price > 0.05:
            return self._hold(symbol, price, raw_score, "Risk calculation invalid or >5%")

        take_profit = entry_price - risk * self.TARGET_RR_MULTIPLIER  # Target is BELOW entry

        rr = (entry_price - take_profit) / risk
        if rr < self.MIN_RR_RATIO:
            return self._hold(symbol, price, raw_score, f"R:R {rr:.1f} below {self.MIN_RR_RATIO}")

        if stop_loss <= entry_price:
            return self._hold(symbol, price, raw_score, "Stop below entry (invalid for short)")

        # ══════ STAGE 2: CONFIDENCE ══════
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
        if detected_setup != ShortSetupType.NO_SETUP:
            reasoning_parts.append(f"Setup: {detected_setup.value}")
        reasoning_parts.append(f"Score: {raw_score:.0f}/100")
        reasoning_parts.append(f"Conf: {confidence:.2f}")
        reasoning_parts.append(f"Confluence: {bearish_factors}/5")
        reasoning_parts.append(f"T:{trend_score:.0f} M:{momentum_score:.0f} V:{volume_score:.0f} P:{pattern_score:.0f} L:{location_score:.0f}")

        return ShortSignalDecision(
            action="SHORT",
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
    # CONFIDENCE MODEL (tuned for shorts)
    # ═══════════════════════════════════════════

    def _compute_confidence(self, raw_score, factor_scores, price, phase,
                            risk_pct, rvol, detected_setup) -> float:
        # 1. Technical score (0.35)
        tech_component = min(1.0, raw_score / 100)

        # 2. Confluence (0.20)
        bearish_strengths = [(s - 50) / 50 for s in factor_scores.values() if s > 50]
        if bearish_strengths:
            avg_strength = np.mean(bearish_strengths)
            count_bonus = min(1.0, len(bearish_strengths) / 5)
            confluence_component = 0.5 * avg_strength + 0.5 * count_bonus
        else:
            confluence_component = 0.0

        # 3. Price tier (0.20) — same sweet spots apply to shorts
        if 60 <= price <= 150:
            price_component = 1.0
        elif 150 < price <= 300:
            price_component = 0.7
        elif 300 < price <= 500:
            price_component = 0.5
        elif 30 <= price < 60:
            price_component = 0.25
        else:
            price_component = 0.1

        # 4. Phase (0.15) — prime is best for shorts too
        phase_map = {
            "prime": 1.0,
            "power_hour": 0.7,
            "open": 0.4,
        }
        phase_component = phase_map.get(phase, 0.3)

        # 5. Risk quality (0.10)
        if 0.005 <= risk_pct <= 0.015:
            risk_component = 1.0
        elif 0.015 < risk_pct <= 0.025:
            risk_component = 0.7
        elif 0.025 < risk_pct <= 0.04:
            risk_component = 0.4
        else:
            risk_component = 0.2

        confidence = (
            0.35 * tech_component +
            0.20 * confluence_component +
            0.20 * price_component +
            0.15 * phase_component +
            0.10 * risk_component
        )
        return round(confidence, 4)

    # ═══════════════════════════════════════════
    # BEARISH SCORING FUNCTIONS
    # ═══════════════════════════════════════════

    def _score_trend_bearish(self, ind: dict, intra: dict) -> float:
        """Score bearish trend alignment 0-100. Higher = more bearish."""
        score = 50

        # Daily trend — INVERTED from long engine
        if ind.get("ema_bullish") is False:
            score += 10  # EMA 9 < EMA 21 = bearish
        elif ind.get("ema_bullish"):
            score -= 10

        if ind.get("above_sma_50") is False:
            score += 8   # Below SMA50 = bearish
        elif ind.get("above_sma_50"):
            score -= 8

        if ind.get("above_sma_200") is False:
            score += 5
        elif ind.get("above_sma_200"):
            score -= 5

        # Intraday trend
        if intra.get("ema_bullish_5m") is False:
            score += 12  # 5m EMA bearish
        elif intra.get("ema_bullish_5m"):
            score -= 12

        # MACD direction
        macd_hist = ind.get("macd_histogram")
        if macd_hist is not None:
            if macd_hist < 0:
                score += 8   # Bearish
            else:
                score -= 8

        return max(0, min(100, score))

    def _score_momentum_bearish(self, ind: dict, intra: dict) -> float:
        """Score bearish momentum 0-100. Higher = more overbought/ready to drop."""
        score = 50

        # RSI — overbought is good for shorts
        rsi = ind.get("rsi")
        if rsi is not None:
            if rsi > 70:
                score += 20  # Deeply overbought — great for shorts
            elif 60 < rsi <= 70:
                score += 15  # Overbought zone
            elif 50 < rsi <= 60:
                score += 5   # Slightly elevated
            elif 40 <= rsi <= 50:
                score -= 5   # Neutral
            elif rsi < 30:
                score -= 15  # Already oversold — DON'T short here

        # Intraday RSI
        rsi_5m = intra.get("rsi_5m")
        if rsi_5m is not None:
            if rsi_5m > 70:
                score += 12
            elif rsi_5m > 60:
                score += 5
            elif rsi_5m < 30:
                score -= 10  # Already crashed intraday

        # MACD bearish cross
        if ind.get("macd_bearish_cross"):
            score += 15
        elif ind.get("macd_bullish_cross"):
            score -= 12

        # Stochastic — overbought turning down
        stoch_k = ind.get("stoch_k")
        stoch_d = ind.get("stoch_d")
        if stoch_k is not None and stoch_d is not None:
            if stoch_k > 80 and stoch_k < stoch_d:
                score += 10  # Overbought + crossing down
            elif stoch_k < 20 and stoch_k > stoch_d:
                score -= 8   # Oversold + crossing up (bad for shorts)

        return max(0, min(100, score))

    def _score_volume_selling(self, price_data: dict, ind: dict, intra: dict) -> float:
        """Score selling volume conviction 0-100."""
        score = 30  # Below neutral baseline

        rvol = float(ind.get("relative_volume") or 0)
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

        # Volume acceleration
        vol_acc = intra.get("volume_acceleration")
        if vol_acc is not None:
            if vol_acc > 2.0:
                score += 15
            elif vol_acc > 1.5:
                score += 8
            elif vol_acc < 0.7:
                score -= 10

        # OBV — falling OBV = distribution (good for shorts)
        obv = ind.get("obv_trend")
        if obv == "falling":
            score += 8
        elif obv == "rising":
            score -= 5

        return max(0, min(100, score))

    def _score_patterns_bearish(self, ind, intra, patterns_text, price_data, levels):
        """Score bearish pattern quality 0-100."""
        score = 30
        setup = ShortSetupType.NO_SETUP
        price = price_data["price"]
        gap_pct = price_data.get("change_pct", 0)

        # ── Gap & Fade ── BLOCKED
        # Data: 23.1% WR, -$1,583 in 2024. Same as Gap & Go on long side —
        # gap patterns are unreliable intraday regardless of direction.
        # Do NOT assign score or setup.
        vwap = ind.get("vwap") or intra.get("vwap_5m")

        # ── ORB Breakdown ──
        or_low = intra.get("or_low")
        if or_low and price < or_low and or_low > 0:
            extension_pct = (or_low - price) / or_low * 100
            if extension_pct <= 2.0:  # Not too extended below OR low
                score += 35
                if setup == ShortSetupType.NO_SETUP:
                    setup = ShortSetupType.ORB_BREAKDOWN
                # Volume confirmation bonus (additive, not a gate)
                vol_acc = intra.get("volume_acceleration")
                if vol_acc is not None and vol_acc >= 1.5:
                    score += 5
            else:
                score += 10

        # ── VWAP Rejection ──
        # Price is below VWAP and tried to get above but failed
        if vwap and price > 0:
            vwap_dist = (price - vwap) / price * 100
            if -0.5 < vwap_dist < 0:
                # Just below VWAP — rejection
                score += 25
                if setup == ShortSetupType.NO_SETUP:
                    setup = ShortSetupType.VWAP_REJECTION

        # ── Failed Breakout ──
        today_high = levels.get("today_high")
        if today_high and price < today_high and today_high > 0:
            # Was at HOD but reversed — failed breakout
            if intra.get("was_at_hod"):
                score += 25
                if setup == ShortSetupType.NO_SETUP:
                    setup = ShortSetupType.FAILED_BREAKOUT

        # ── LOD Break ──
        today_low = levels.get("today_low")
        if today_low and price < today_low:
            score += 20
            if setup == ShortSetupType.NO_SETUP:
                setup = ShortSetupType.LOD_BREAK

        # ── Pattern text — bearish patterns are GOOD for shorts ──
        if "bear_flag" in patterns_text.lower():
            score += 15
            if setup == ShortSetupType.NO_SETUP:
                setup = ShortSetupType.BEAR_FLAG

        if "bearish_engulfing" in patterns_text.lower():
            score += 12
        if "shooting_star" in patterns_text.lower():
            score += 12
        if "evening_star" in patterns_text.lower():
            score += 12

        # Penalty for bullish patterns (bad for shorts)
        if "bullish_engulfing" in patterns_text.lower():
            score -= 15
        if "hammer" in patterns_text.lower():
            score -= 12
        if "morning_star" in patterns_text.lower():
            score -= 12

        # ── Momentum breakdown ──
        ema_bearish = intra.get("ema_bullish_5m") is False
        below_vwap = intra.get("above_vwap_5m") is False
        if ema_bearish and below_vwap and gap_pct < -1.0:
            if setup == ShortSetupType.NO_SETUP:
                pass  # Blocked as standalone — weak signal
            else:
                score += 15  # Strong as confirmation

        return max(0, min(100, score)), setup

    def _score_location_bearish(self, price, ind, intra, levels) -> float:
        """Score price location for shorts 0-100. Higher = better short location."""
        score = 50

        # Below VWAP is good for shorts
        above_vwap = intra.get("above_vwap_5m")
        if above_vwap is False:
            score += 15
        elif above_vwap:
            score -= 10  # Above VWAP = strong, bad for shorts

        # Upper Bollinger = extended, good for shorts
        bb_lower = ind.get("bb_lower")
        bb_upper = ind.get("bb_upper")
        if bb_lower and bb_upper and price:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_pct = (price - bb_lower) / bb_range
                if bb_pct > 0.8:
                    score += 12  # Near upper BB = extended
                elif bb_pct < 0.2:
                    # Don't penalize ORB breakdowns for being near lower BB
                    or_low = levels.get("or_low")
                    if not (or_low and price < or_low):
                        score -= 8   # Near lower BB = already oversold

        # Resistance proximity — being rejected from resistance is good for shorts
        resistance_levels = levels.get("resistance_levels", [])
        for r in resistance_levels[:2]:
            dist = (r - price) / price * 100 if price > 0 else 999
            if 0 < dist < 1.5:
                score += 10  # Near resistance — rejection play
                break

        # Support proximity — approaching support is BAD for shorts (bounce risk)
        support_levels = levels.get("support_levels", [])
        for s in support_levels[:2]:
            dist = (price - s) / price * 100 if price > 0 else 999
            if 0 < dist < 1.5:
                score -= 12  # Too close to support
                break

        # Below opening range = bearish
        or_high = levels.get("or_high")
        or_low = levels.get("or_low")
        if or_high and or_low:
            if price < or_low:
                score += 10
            elif price > or_high:
                score -= 5  # Above OR = strength, bad for shorts

        # Gap direction — gap down is continuation (good for shorts)
        prior_close = levels.get("prior_day_close")
        if prior_close and price > 0:
            gap = (price - prior_close) / prior_close * 100
            if gap < -2:
                score += 5  # Gap down continuation

        return max(0, min(100, score))

    def _calculate_stop_short(self, price, atr, ind, levels, setup) -> float:
        """Calculate stop loss for SHORT — stop is ABOVE entry."""
        atr_stop = price + (atr * self.STOP_ATR_MULTIPLIER)
        structural_stop = atr_stop

        # Use resistance levels as structural stops for shorts
        resistance_levels = levels.get("resistance_levels", [])
        for r in resistance_levels[:3]:
            if r > price and r < price * 1.05:
                candidate = r * (1 + self.STOP_BUFFER_PCT)
                if candidate < atr_stop and (candidate - price) / price > 0.003:
                    structural_stop = candidate
                    break

        # ORB Breakdown: stop at OR high
        if setup == ShortSetupType.ORB_BREAKDOWN:
            or_high = levels.get("or_high")
            if or_high and or_high > price:
                structural_stop = or_high * (1 + self.STOP_BUFFER_PCT)

        # VWAP Rejection: stop just above VWAP
        if setup == ShortSetupType.VWAP_REJECTION:
            vwap = levels.get("vwap") or ind.get("vwap") or ind.get("vwap_5m")
            if vwap and vwap > price:
                structural_stop = vwap * (1 + self.STOP_BUFFER_PCT)

        # Use the tighter stop (lower value, since stop is above entry)
        stop = min(structural_stop, atr_stop)
        # Floor: at least 0.3% above entry
        stop = max(stop, price * 1.003)
        # Ceiling: no more than 5% above entry
        stop = min(stop, price * 1.05)

        return stop

    def _hold(self, symbol, price, score, reason) -> ShortSignalDecision:
        return ShortSignalDecision(
            action="HOLD", symbol=symbol, confidence=0.0,
            entry_price=price, stop_loss=0.0, take_profit=0.0,
            pattern="", reasoning=reason, score=score,
            setup_type=ShortSetupType.NO_SETUP,
        )
