"""Market regime detection — SPY trend + VIX level.

Tells the system what KIND of market we're in so Claude can adapt:
- Bull + Quiet: Full size, favor momentum/breakouts
- Bull + Volatile: Reduce size, tighter stops, favor pullbacks
- Bear + Quiet: Reduce size, favor mean reversion
- Bear + Volatile: Minimal size, defensive only
"""

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class RegimeState:
    """Current market regime snapshot."""
    spy_trend: str           # "bullish" or "bearish" (above/below 50-day SMA)
    vix_level: str           # "quiet", "elevated", "volatile", "extreme"
    regime: str              # "bull_quiet", "bull_volatile", "bear_quiet", "bear_volatile"
    spy_price: float = 0.0
    spy_sma_50: float = 0.0
    spy_change_pct: float = 0.0
    vix_price: float = 0.0
    updated_at: datetime | None = None


class MarketRegime:
    """Detect market regime using SPY trend and VIX level.

    Updated once per trading loop cycle. Results are cached and
    passed to Claude's prompt and the risk manager's position sizer.
    """

    # VIX thresholds
    VIX_QUIET = 16.0
    VIX_ELEVATED = 22.0
    VIX_VOLATILE = 30.0
    # Above 30 = extreme

    # Size multipliers by regime
    REGIME_SIZE_MULTIPLIERS = {
        "bull_quiet": 1.0,       # Full size — best conditions
        "bull_volatile": 0.70,   # Reduce size — choppy but trending up
        "bear_quiet": 0.50,      # Half size — counter-trend environment
        "bear_volatile": 0.25,   # Minimal — survival mode
    }

    def __init__(self):
        self.state: RegimeState | None = None

    def update(self) -> RegimeState:
        """Fetch SPY + VIX data and determine current regime."""
        try:
            from autotrader.data.market import get_stock_data, get_current_price

            # ── SPY trend ──
            spy_price_data = get_current_price("SPY")
            spy_hist = get_stock_data("SPY", period="3mo", interval="1d")

            spy_price = spy_price_data["price"] if spy_price_data else 0.0
            spy_change = spy_price_data.get("change_pct", 0.0) if spy_price_data else 0.0

            # Calculate 50-day SMA
            spy_sma_50 = 0.0
            if spy_hist is not None and not spy_hist.empty and len(spy_hist) >= 50:
                spy_sma_50 = float(spy_hist["Close"].tail(50).mean())

            spy_trend = "bullish" if spy_price >= spy_sma_50 else "bearish"

            # ── VIX level ──
            vix_price_data = get_current_price("^VIX")
            vix_price = vix_price_data["price"] if vix_price_data else 18.0  # default moderate

            if vix_price < self.VIX_QUIET:
                vix_level = "quiet"
            elif vix_price < self.VIX_ELEVATED:
                vix_level = "elevated"
            elif vix_price < self.VIX_VOLATILE:
                vix_level = "volatile"
            else:
                vix_level = "extreme"

            # ── Combine into regime ──
            if spy_trend == "bullish" and vix_level in ("quiet", "elevated"):
                regime = "bull_quiet"
            elif spy_trend == "bullish" and vix_level in ("volatile", "extreme"):
                regime = "bull_volatile"
            elif spy_trend == "bearish" and vix_level in ("quiet", "elevated"):
                regime = "bear_quiet"
            else:
                regime = "bear_volatile"

            self.state = RegimeState(
                spy_trend=spy_trend,
                vix_level=vix_level,
                regime=regime,
                spy_price=spy_price,
                spy_sma_50=spy_sma_50,
                spy_change_pct=spy_change,
                vix_price=vix_price,
                updated_at=datetime.now(),
            )

            logger.info(
                f"Market regime: {regime} | SPY=${spy_price:.2f} "
                f"({'above' if spy_trend == 'bullish' else 'below'} 50SMA=${spy_sma_50:.2f}) | "
                f"VIX={vix_price:.1f} ({vix_level})"
            )
            return self.state

        except Exception as e:
            logger.error(f"Failed to update market regime: {e}")
            # Return a conservative default
            self.state = RegimeState(
                spy_trend="unknown",
                vix_level="elevated",
                regime="bear_quiet",
                updated_at=datetime.now(),
            )
            return self.state

    def get_size_multiplier(self) -> float:
        """Get position size multiplier for current regime.

        Applied on top of the phase-based multiplier in RiskManager.
        """
        if not self.state:
            return 0.5  # Conservative default if not yet updated
        return self.REGIME_SIZE_MULTIPLIERS.get(self.state.regime, 0.5)

    def should_trade(self) -> bool:
        """Check if regime allows new trades.

        Returns False only in extreme conditions (VIX > 30 + bearish SPY).
        Even bear_volatile allows trades — just at minimal size.
        """
        if not self.state:
            return True  # Don't block if we can't check
        # Only block in extreme VIX + bearish
        if self.state.vix_level == "extreme" and self.state.spy_trend == "bearish":
            logger.warning("Regime BLOCK: Extreme VIX + bearish SPY — no new trades")
            return False
        return True

    def get_regime_context_for_prompt(self) -> str:
        """Format regime data for Claude's analysis prompt."""
        if not self.state:
            return "Market regime: Not yet determined"

        s = self.state
        trend_emoji = "UP" if s.spy_trend == "bullish" else "DOWN"
        regime_desc = {
            "bull_quiet": "BULLISH + LOW VOL — Best conditions. Full aggression on clean setups.",
            "bull_volatile": "BULLISH + HIGH VOL — Trend is up but choppy. Tighter stops, favor pullbacks over breakouts.",
            "bear_quiet": "BEARISH + LOW VOL — Counter-trend. Favor mean reversion, reduce size.",
            "bear_volatile": "BEARISH + HIGH VOL — Danger zone. Minimal size, defensive only, A+ setups only.",
        }.get(s.regime, "Unknown regime")

        lines = [
            f"SPY: ${s.spy_price:.2f} ({s.spy_change_pct:+.2f}%) | Trend: {trend_emoji} (50SMA=${s.spy_sma_50:.2f})",
            f"VIX: {s.vix_price:.1f} ({s.vix_level})",
            f"REGIME: {s.regime.upper().replace('_', ' ')} — {regime_desc}",
        ]
        return "\n".join(lines)
