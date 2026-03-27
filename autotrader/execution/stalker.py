"""Entry Stalker — patient limit-order entries instead of chasing at market.

A real day trader doesn't chase. They:
1. Identify the setup (oversold bounce, pullback, VWAP test)
2. Identify WHERE they want to get in (support level, VWAP, pullback zone)
3. Place a limit order at that level and WAIT
4. If it fills — great, they got a better entry and better R:R
5. If it doesn't fill in 10 min — setup is dead, cancel and move on

This class manages that entire flow for the automated system.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# How long to wait for a limit order to fill before cancelling
DEFAULT_TIMEOUT_MINUTES = 10

# If price drops this far below the stop loss, the setup is blown — cancel
INVALIDATION_BUFFER_PCT = 0.005  # 0.5% below stop = invalidated


@dataclass
class StalkedEntry:
    """A pending limit order being monitored."""
    symbol: str
    order_id: str
    limit_price: float
    quantity: int
    stop_loss: float
    take_profit: float
    pattern: str
    confidence: float
    reasoning: str
    created_at: datetime = field(default_factory=datetime.now)
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES
    status: str = "pending"  # pending, filled, cancelled, expired, invalidated

    @property
    def is_expired(self) -> bool:
        return datetime.now() > self.created_at + timedelta(minutes=self.timeout_minutes)

    @property
    def age_seconds(self) -> int:
        return int((datetime.now() - self.created_at).total_seconds())


class EntryStalker:
    """Manages pending limit orders — the patient entry system.

    Flow:
    1. Brain says "BUY FUBO, entry $9.10, stop $9.00, target $10.50"
    2. Current price is $9.35 — limit order placed at $9.10
    3. Every 30s, check: filled? expired? invalidated?
    4. If filled → hand off to PositionManager for stop/target tracking
    5. If expired (10 min) → cancel, move on
    6. If invalidated (price breaks below stop) → cancel
    """

    def __init__(self):
        self.pending: dict[str, StalkedEntry] = {}  # symbol → entry

    def add_entry(
        self,
        symbol: str,
        order_id: str,
        limit_price: float,
        quantity: int,
        stop_loss: float,
        take_profit: float,
        pattern: str = "",
        confidence: float = 0.0,
        reasoning: str = "",
        timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES,
    ):
        """Register a new limit order for monitoring."""
        # Cancel any existing stalked entry for this symbol
        if symbol in self.pending:
            logger.info(f"Replacing existing stalked entry for {symbol}")

        entry = StalkedEntry(
            symbol=symbol,
            order_id=order_id,
            limit_price=limit_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            pattern=pattern,
            confidence=confidence,
            reasoning=reasoning,
            timeout_minutes=timeout_minutes,
        )
        self.pending[symbol] = entry

        logger.info(
            f"STALKING {symbol}: limit ${limit_price:.2f} | "
            f"stop ${stop_loss:.2f} | target ${take_profit:.2f} | "
            f"timeout {timeout_minutes}min | {pattern}"
        )

    def check_entries(self, broker, current_prices: dict[str, float]) -> list[dict]:
        """Check all pending entries. Returns list of actions for filled/cancelled orders.

        Args:
            broker: AlpacaBroker instance for order status checks
            current_prices: {symbol: current_price} for invalidation checks

        Returns:
            List of action dicts:
                {"action": "filled", "entry": StalkedEntry, "fill_price": float}
                {"action": "cancelled", "entry": StalkedEntry, "reason": str}
        """
        actions = []
        to_remove = []

        for symbol, entry in self.pending.items():
            try:
                # 1. Check if order filled
                order_status = broker.get_order_status(entry.order_id)

                if order_status and order_status.get("status") == "filled":
                    fill_price = order_status.get("filled_avg_price", entry.limit_price)
                    entry.status = "filled"
                    actions.append({
                        "action": "filled",
                        "entry": entry,
                        "fill_price": fill_price,
                    })
                    to_remove.append(symbol)
                    logger.info(
                        f"STALKED ENTRY FILLED: {symbol} @ ${fill_price:.2f} "
                        f"(wanted ${entry.limit_price:.2f}, waited {entry.age_seconds}s)"
                    )
                    continue

                if order_status and order_status.get("status") in ("cancelled", "rejected", "expired"):
                    entry.status = "cancelled"
                    to_remove.append(symbol)
                    logger.info(f"Stalked entry {symbol}: order {order_status.get('status')}")
                    continue

                # 2. Check timeout
                if entry.is_expired:
                    entry.status = "expired"
                    broker.cancel_order(entry.order_id)
                    actions.append({
                        "action": "cancelled",
                        "entry": entry,
                        "reason": f"Timeout ({entry.timeout_minutes}min) — setup expired",
                    })
                    to_remove.append(symbol)
                    logger.info(
                        f"STALKED ENTRY EXPIRED: {symbol} "
                        f"(limit ${entry.limit_price:.2f}, waited {entry.age_seconds}s)"
                    )
                    continue

                # 3. Check invalidation — price broke below stop loss
                current_price = current_prices.get(symbol)
                if current_price and current_price < entry.stop_loss * (1 - INVALIDATION_BUFFER_PCT):
                    entry.status = "invalidated"
                    broker.cancel_order(entry.order_id)
                    actions.append({
                        "action": "cancelled",
                        "entry": entry,
                        "reason": f"Invalidated — price ${current_price:.2f} broke below stop ${entry.stop_loss:.2f}",
                    })
                    to_remove.append(symbol)
                    logger.info(
                        f"STALKED ENTRY INVALIDATED: {symbol} "
                        f"(price ${current_price:.2f} < stop ${entry.stop_loss:.2f})"
                    )
                    continue

            except Exception as e:
                logger.error(f"Error checking stalked entry {symbol}: {e}")

        # Clean up completed entries
        for symbol in to_remove:
            del self.pending[symbol]

        return actions

    def cancel_all(self, broker):
        """Cancel all pending stalked entries (e.g., EOD cleanup)."""
        for symbol, entry in list(self.pending.items()):
            try:
                broker.cancel_order(entry.order_id)
                logger.info(f"Cancelled stalked entry: {symbol}")
            except Exception as e:
                logger.error(f"Error cancelling stalked entry {symbol}: {e}")
        self.pending.clear()

    def has_pending(self, symbol: str) -> bool:
        return symbol in self.pending

    @property
    def count(self) -> int:
        return len(self.pending)
