"""Risk management engine — the safety net for autonomous trading."""

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from sqlalchemy import func

from autotrader.config import RISK, PHASE_CONFIG
from autotrader.db.models import get_session, Trade, RiskEvent, PortfolioSnapshot

logger = logging.getLogger(__name__)


@dataclass
class TradeProposal:
    """A proposed trade from the Claude brain."""
    symbol: str
    side: str        # BUY or SELL
    confidence: float
    reasoning: str
    stop_loss: float | None = None
    take_profit: float | None = None
    entry_price: float | None = None  # Claude's ideal entry (for limit orders)
    current_price: float = 0.0
    pattern: str = ""
    quantity_hint: int = 0    # Claude's suggested quantity


@dataclass
class RiskVerdict:
    """Result of a risk check."""
    approved: bool
    reason: str = ""
    adjusted_quantity: float = 0.0


class RiskManager:
    """Validates trades against risk rules before execution."""

    def __init__(self):
        self._halted = False
        self._halt_reason = ""
        self._consecutive_losses = 0
        self._cooldown_until: datetime | None = None
        self._peak_equity: float = 0.0

    @property
    def is_halted(self) -> bool:
        """Check if trading is halted."""
        if self._cooldown_until and datetime.now(timezone.utc) < self._cooldown_until:
            return True
        return self._halted

    def halt(self, reason: str = "Manual halt"):
        """Halt all trading."""
        self._halted = True
        self._halt_reason = reason
        self._log_risk_event("halt", reason, "All trading halted")
        logger.warning(f"TRADING HALTED: {reason}")

    def resume(self):
        """Resume trading."""
        self._halted = False
        self._halt_reason = ""
        self._cooldown_until = None
        logger.info("Trading resumed")

    def check_trade(self, proposal: TradeProposal, portfolio: dict, market_phase: str = "") -> RiskVerdict:
        """Run all risk checks on a proposed trade."""
        # ── Check: Is trading halted? ──────────────────
        if self.is_halted:
            return RiskVerdict(False, f"Trading halted: {self._halt_reason}")

        # ── Check: Phase-specific confidence threshold ─
        phase_conf = PHASE_CONFIG.get(market_phase, {})
        min_confidence = phase_conf.get("min_confidence", RISK["min_confidence_to_trade"])
        if proposal.confidence < min_confidence:
            phase_label = f" ({market_phase})" if market_phase else ""
            return RiskVerdict(
                False,
                f"Confidence {proposal.confidence:.0%} below minimum {min_confidence:.0%}{phase_label}"
            )

        equity = portfolio.get("equity", 0)
        cash = portfolio.get("cash", 0)
        buying_power = portfolio.get("buying_power", 0)

        if equity <= 0:
            return RiskVerdict(False, "No equity in account")

        # Update peak equity for drawdown tracking
        if equity > self._peak_equity:
            self._peak_equity = equity

        # ── Check: Maximum drawdown ────────────────────
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - equity) / self._peak_equity
            if drawdown >= RISK["max_drawdown_pct"]:
                self.halt(f"Max drawdown reached: {drawdown:.1%}")
                return RiskVerdict(False, f"Max drawdown {drawdown:.1%} exceeded limit")

        # ── Check: Daily loss limit ────────────────────
        daily_pnl = self._get_daily_pnl()
        if daily_pnl is not None and equity > 0:
            daily_loss_pct = abs(daily_pnl) / equity if daily_pnl < 0 else 0
            if daily_loss_pct >= RISK["max_daily_loss_pct"]:
                self.halt(f"Daily loss limit: {daily_loss_pct:.1%}")
                return RiskVerdict(False, f"Daily loss {daily_loss_pct:.1%} exceeded limit")

        # ── Check: Max trades per day ──────────────────
        todays_trades = self._count_todays_trades()
        if todays_trades >= RISK["max_trades_per_day"]:
            return RiskVerdict(False, f"Max daily trades reached ({todays_trades})")

        # ── Check: Consecutive losses ──────────────────
        if self._consecutive_losses >= RISK["max_consecutive_losses"]:
            cooldown_min = RISK["cooldown_after_losses_minutes"]
            self._cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_min)
            self._log_risk_event(
                "cooldown",
                f"{self._consecutive_losses} consecutive losses",
                f"Cooling down for {cooldown_min} minutes"
            )
            return RiskVerdict(False, f"Cooldown: {self._consecutive_losses} consecutive losses")

        # ── Check: Total exposure ──────────────────────
        total_position_value = sum(
            float(p.get("market_value", 0)) for p in portfolio.get("positions", [])
        )
        if equity > 0 and total_position_value / equity >= RISK["max_total_exposure_pct"]:
            if proposal.side == "BUY":
                return RiskVerdict(
                    False,
                    f"Total exposure {total_position_value/equity:.0%} at limit "
                    f"({RISK['max_total_exposure_pct']:.0%})"
                )

        # ── Check: Risk/Reward ratio ───────────────────
        if proposal.side == "BUY" and proposal.stop_loss and proposal.take_profit:
            risk = abs(proposal.current_price - proposal.stop_loss)
            reward = abs(proposal.take_profit - proposal.current_price)
            if risk > 0:
                rr_ratio = reward / risk
                if rr_ratio < RISK["min_risk_reward_ratio"]:
                    return RiskVerdict(
                        False,
                        f"R/R ratio {rr_ratio:.1f}:1 below minimum {RISK['min_risk_reward_ratio']}:1"
                    )

        # ── Calculate position size ────────────────────
        if proposal.side == "BUY":
            quantity = self._calculate_position_size(proposal, equity, buying_power, market_phase)

            # Use Claude's hint if it's more conservative
            if proposal.quantity_hint > 0:
                quantity = min(quantity, proposal.quantity_hint)

            if quantity <= 0:
                return RiskVerdict(False, "Position size too small or insufficient buying power")

            # Check max position concentration
            current_position_value = self._get_position_value(
                proposal.symbol, portfolio.get("positions", [])
            )
            new_total = current_position_value + (quantity * proposal.current_price)
            if equity > 0 and new_total / equity > RISK["max_position_pct"]:
                max_additional = (RISK["max_position_pct"] * equity) - current_position_value
                quantity = max(0, int(max_additional / proposal.current_price))
                if quantity <= 0:
                    return RiskVerdict(
                        False,
                        f"Position in {proposal.symbol} would exceed {RISK['max_position_pct']:.0%} limit"
                    )
        else:
            quantity = self._get_position_qty(proposal.symbol, portfolio.get("positions", []))
            if quantity <= 0:
                return RiskVerdict(False, f"No position in {proposal.symbol} to sell")

        return RiskVerdict(
            approved=True,
            reason="All risk checks passed",
            adjusted_quantity=quantity,
        )

    def record_trade_result(self, won: bool):
        """Track consecutive wins/losses for circuit breaker."""
        if won:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1

    def _calculate_position_size(self, proposal: TradeProposal, equity: float, buying_power: float, market_phase: str = "") -> int:
        """Calculate number of shares using fixed-fractional position sizing.

        Adjusts size by market phase — smaller during open/lunch, full during prime/power_hour.
        """
        risk_amount = equity * RISK["max_risk_per_trade_pct"]

        # Phase-based size adjustment
        phase_conf = PHASE_CONFIG.get(market_phase, {})
        size_multiplier = phase_conf.get("size_multiplier", 1.0)
        risk_amount *= size_multiplier
        stop_loss_pct = RISK["default_stop_loss_pct"]

        if proposal.stop_loss and proposal.current_price > 0:
            stop_loss_pct = abs(proposal.current_price - proposal.stop_loss) / proposal.current_price

        if stop_loss_pct <= 0:
            stop_loss_pct = RISK["default_stop_loss_pct"]

        price = proposal.current_price
        if price <= 0:
            return 0

        risk_based_shares = risk_amount / (price * stop_loss_pct)
        max_by_buying_power = buying_power / price
        max_by_position = (equity * RISK["max_position_pct"]) / price

        shares = min(risk_based_shares, max_by_buying_power, max_by_position)

        return max(0, int(shares))

    def _get_daily_pnl(self) -> float | None:
        """Get today's P&L from the portfolio snapshots."""
        session = get_session()
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
            snap = (
                session.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.created_at >= today_start)
                .order_by(PortfolioSnapshot.created_at.desc())
                .first()
            )
            return snap.daily_pnl if snap else None
        finally:
            session.close()

    def _count_todays_trades(self) -> int:
        """Count trades executed today."""
        session = get_session()
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
            count = (
                session.query(func.count(Trade.id))
                .filter(Trade.created_at >= today_start)
                .scalar()
            )
            return count or 0
        finally:
            session.close()

    def _get_position_value(self, symbol: str, positions: list) -> float:
        """Get current market value of a position."""
        for pos in positions:
            if pos.get("symbol") == symbol:
                return float(pos.get("market_value", 0))
        return 0.0

    def _get_position_qty(self, symbol: str, positions: list) -> float:
        """Get current quantity of a position."""
        for pos in positions:
            if pos.get("symbol") == symbol:
                return float(pos.get("qty", 0))
        return 0.0

    def _log_risk_event(self, event_type: str, details: str, action: str):
        """Log a risk event to the database."""
        session = get_session()
        try:
            event = RiskEvent(
                event_type=event_type,
                details=details,
                action_taken=action,
            )
            session.add(event)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to log risk event: {e}")
            session.rollback()
        finally:
            session.close()

    def get_status(self) -> dict:
        """Get current risk manager status."""
        return {
            "halted": self.is_halted,
            "halt_reason": self._halt_reason,
            "consecutive_losses": self._consecutive_losses,
            "peak_equity": self._peak_equity,
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "todays_trades": self._count_todays_trades(),
        }
