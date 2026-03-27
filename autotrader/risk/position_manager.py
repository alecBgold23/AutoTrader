"""Active position management — trailing stops, scaling out, time-based exits.

This is what separates amateur from professional day trading:
- Move stops to breakeven after 1R profit
- Scale out (1/3 at 1R, 1/3 at 2R, trail the rest)
- ATR-based trailing stops (not fixed %)
- Close positions approaching EOD
- Cut losers fast, let winners run
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ManagedPosition:
    """Track a position with its management rules."""
    symbol: str
    entry_price: float
    quantity: float
    side: str                    # BUY (long)
    stop_loss: float
    take_profit: float
    entry_time: datetime
    pattern: str = ""

    # Calculated on creation
    risk_per_share: float = 0.0  # |entry - stop|
    r_target_1: float = 0.0     # Entry + 1R
    r_target_2: float = 0.0     # Entry + 2R

    # Position state
    current_stop: float = 0.0
    shares_remaining: float = 0.0
    realized_pnl: float = 0.0
    highest_price: float = 0.0  # For trailing stop
    scale_out_stage: int = 0    # 0=full, 1=sold 1/3, 2=sold 2/3
    broker_stop_order_id: str = ""  # Alpaca stop order ID for broker-side protection

    def __post_init__(self):
        self.risk_per_share = abs(self.entry_price - self.stop_loss)
        if self.side == "BUY":
            self.r_target_1 = self.entry_price + self.risk_per_share
            self.r_target_2 = self.entry_price + (self.risk_per_share * 2)
        self.current_stop = self.stop_loss
        self.shares_remaining = self.quantity
        self.highest_price = self.entry_price


class PositionManager:
    """Manages open positions with professional-grade rules."""

    def __init__(self, atr_trail_multiplier: float = 1.5):
        self.positions: dict[str, ManagedPosition] = {}
        self.atr_trail_multiplier = atr_trail_multiplier

    def add_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit: float,
        pattern: str = "",
    ):
        """Register a new position for active management."""
        pos = ManagedPosition(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            side="BUY",
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=datetime.now(),
            pattern=pattern,
        )
        self.positions[symbol] = pos
        logger.info(
            f"Position tracked: {symbol} | entry=${entry_price:.2f} | "
            f"stop=${stop_loss:.2f} | R=${pos.risk_per_share:.2f} | "
            f"1R=${pos.r_target_1:.2f} | 2R=${pos.r_target_2:.2f}"
        )

    def remove_position(self, symbol: str):
        """Remove a position from tracking."""
        if symbol in self.positions:
            del self.positions[symbol]

    def update(self, symbol: str, current_price: float, current_atr: float | None = None) -> list[dict]:
        """Update a position and return any actions needed.

        Returns list of action dicts:
            {"action": "SELL_PARTIAL", "quantity": N, "reason": "..."}
            {"action": "SELL_ALL", "reason": "..."}
            {"action": "MOVE_STOP", "new_stop": X, "reason": "..."}
        """
        if symbol not in self.positions:
            return []

        pos = self.positions[symbol]
        actions = []

        # Update highest price for trailing stop
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # ── Check: Price hit stop loss ──
        if current_price <= pos.current_stop:
            actions.append({
                "action": "SELL_ALL",
                "reason": f"Stop loss hit (${pos.current_stop:.2f})",
                "quantity": pos.shares_remaining,
            })
            return actions

        # ── Scale Out: Stage 1 — sell 1/3 at 1R ──
        if (pos.scale_out_stage == 0
                and current_price >= pos.r_target_1
                and pos.shares_remaining > 1):
            sell_qty = max(1, int(pos.quantity / 3))
            actual_sell = min(sell_qty, int(pos.shares_remaining) - 1)
            if actual_sell > 0:
                actions.append({
                    "action": "SELL_PARTIAL",
                    "quantity": actual_sell,
                    "reason": f"1R target hit (${pos.r_target_1:.2f}) — taking 1/3 profit",
                })
                pos.shares_remaining -= actual_sell
                pos.scale_out_stage = 1
                pos.realized_pnl += actual_sell * (current_price - pos.entry_price)

                # Move stop to breakeven after 1R
                pos.current_stop = pos.entry_price
                actions.append({
                    "action": "MOVE_STOP",
                    "new_stop": pos.entry_price,
                    "reason": "Moving stop to breakeven after 1R profit",
                })

        # ── Scale Out: Stage 2 — sell another 1/3 at 2R ──
        elif (pos.scale_out_stage == 1
              and current_price >= pos.r_target_2
              and pos.shares_remaining > 1):
            sell_qty = max(1, int(pos.quantity / 3))
            actual_sell = min(sell_qty, int(pos.shares_remaining) - 1)
            if actual_sell > 0:
                actions.append({
                    "action": "SELL_PARTIAL",
                    "quantity": actual_sell,
                    "reason": f"2R target hit (${pos.r_target_2:.2f}) — taking another 1/3",
                })
                pos.shares_remaining -= actual_sell
                pos.scale_out_stage = 2
                pos.realized_pnl += actual_sell * (current_price - pos.entry_price)

        # ── Trailing Stop (ATR-based or fixed) ──
        if pos.scale_out_stage >= 1:  # Only trail after first profit taken
            if current_atr and current_atr > 0:
                # ATR-based trailing stop
                trail_stop = pos.highest_price - (current_atr * self.atr_trail_multiplier)
            else:
                # Fixed trailing (2% from highest)
                trail_stop = pos.highest_price * 0.98

            # Only move stop UP, never down
            if trail_stop > pos.current_stop:
                pos.current_stop = trail_stop
                actions.append({
                    "action": "MOVE_STOP",
                    "new_stop": round(trail_stop, 2),
                    "reason": f"Trailing stop moved to ${trail_stop:.2f} (highest: ${pos.highest_price:.2f})",
                })

        return actions

    def check_time_exit(self, symbol: str, minutes_to_close: int) -> dict | None:
        """Check if a position should be closed due to time.

        Day trading rule: close all positions before EOD.
        """
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]

        # Close if market closing within 15 minutes
        if minutes_to_close <= 15:
            return {
                "action": "SELL_ALL",
                "quantity": pos.shares_remaining,
                "reason": f"EOD close — {minutes_to_close} min to market close",
            }

        # Reduce position if closing within 30 minutes
        if minutes_to_close <= 30 and pos.shares_remaining > 1:
            sell_qty = max(1, int(pos.shares_remaining / 2))
            return {
                "action": "SELL_PARTIAL",
                "quantity": sell_qty,
                "reason": f"Reducing position — {minutes_to_close} min to market close",
            }

        return None

    def get_position_summary(self, symbol: str, current_price: float) -> dict | None:
        """Get a summary of a managed position."""
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        unrealized_pnl = (current_price - pos.entry_price) * pos.shares_remaining
        r_multiple = (current_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

        return {
            "symbol": symbol,
            "entry": pos.entry_price,
            "current_stop": pos.current_stop,
            "shares_remaining": pos.shares_remaining,
            "original_qty": pos.quantity,
            "scale_out_stage": pos.scale_out_stage,
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(pos.realized_pnl, 2),
            "total_pnl": round(unrealized_pnl + pos.realized_pnl, 2),
            "r_multiple": round(r_multiple, 2),
            "highest_price": pos.highest_price,
            "pattern": pos.pattern,
        }

    def get_all_summaries(self, prices: dict[str, float]) -> list[dict]:
        """Get summaries for all managed positions."""
        summaries = []
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, pos.entry_price)
            summary = self.get_position_summary(symbol, price)
            if summary:
                summaries.append(summary)
        return summaries
