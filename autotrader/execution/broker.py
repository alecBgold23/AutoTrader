"""Alpaca broker integration for order execution."""

import logging
from datetime import datetime, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
    TrailingStopOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, OrderStatus
from alpaca.common.exceptions import APIError

from autotrader.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER
from autotrader.risk.manager import TradeProposal, RiskVerdict
from autotrader.db.models import get_session, Trade, PortfolioSnapshot

logger = logging.getLogger(__name__)


class AlpacaBroker:
    """Manages all interactions with the Alpaca brokerage API."""

    def __init__(self):
        self.client = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=ALPACA_PAPER,
        )
        mode = "PAPER" if ALPACA_PAPER else "LIVE"
        logger.info(f"Alpaca broker initialized in {mode} mode")

    def get_account(self) -> dict:
        """Get account summary."""
        try:
            account = self.client.get_account()
            return {
                "equity": float(account.equity),
                "cash": float(account.cash),
                "buying_power": float(account.buying_power),
                "portfolio_value": float(account.portfolio_value),
                "daily_pnl": float(account.equity) - float(account.last_equity),
                "status": account.status.value if hasattr(account.status, 'value') else str(account.status),
                "pattern_day_trader": account.pattern_day_trader,
                "day_trade_count": account.daytrade_count,
            }
        except APIError as e:
            logger.error(f"Failed to get account: {e}")
            return {}

    def get_positions(self) -> list[dict]:
        """Get all open positions."""
        try:
            positions = self.client.get_all_positions()
            return [
                {
                    "symbol": pos.symbol,
                    "qty": float(pos.qty),
                    "side": pos.side.value if hasattr(pos.side, 'value') else str(pos.side),
                    "market_value": float(pos.market_value),
                    "avg_entry_price": float(pos.avg_entry_price),
                    "current_price": float(pos.current_price),
                    "unrealized_pnl": float(pos.unrealized_pl),
                    "unrealized_pnl_pct": float(pos.unrealized_plpc) * 100,
                    "change_today": float(pos.change_today) * 100,
                }
                for pos in positions
            ]
        except APIError as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def get_portfolio(self) -> dict:
        """Get complete portfolio state (account + positions)."""
        account = self.get_account()
        positions = self.get_positions()
        account["positions"] = positions
        return account

    def execute_trade(self, proposal: TradeProposal, verdict: RiskVerdict) -> Trade | None:
        """Execute a trade that has been approved by risk management.

        Args:
            proposal: The approved trade proposal
            verdict: Risk verdict with adjusted quantity

        Returns:
            Trade record or None on failure
        """
        if not verdict.approved:
            logger.warning(f"Attempted to execute unapproved trade: {verdict.reason}")
            return None

        quantity = verdict.adjusted_quantity
        if quantity <= 0:
            logger.warning(f"Cannot execute trade with quantity {quantity}")
            return None

        side = OrderSide.BUY if proposal.side == "BUY" else OrderSide.SELL

        try:
            if proposal.side == "SELL":
                # For sells, close the position (handles bracket order children)
                self.cancel_orders_for_symbol(proposal.symbol)
                order = self._place_market_order(proposal.symbol, quantity, side)
            else:
                # For buys, use simple market order — PositionManager handles stops/targets
                # (Bracket orders lock shares and prevent future sells)
                order = self._place_market_order(proposal.symbol, quantity, side)

            if not order:
                return None

            # Log to database
            trade = self._log_trade(proposal, verdict, order)
            logger.info(
                f"TRADE EXECUTED: {proposal.side} {quantity} {proposal.symbol} "
                f"(order_id={order.id}, confidence={proposal.confidence:.0%})"
            )
            return trade

        except APIError as e:
            logger.error(f"Alpaca API error executing trade: {e}")
            return None
        except Exception as e:
            logger.error(f"Error executing trade: {e}")
            return None

    def sell_shares(self, symbol: str, quantity: int) -> bool:
        """Sell a specific number of shares (for partial sells / scale-outs).

        Cancels any pending orders for the symbol first to free held shares.
        """
        try:
            # Cancel pending orders that may be holding shares (bracket children, stops, etc.)
            self.cancel_orders_for_symbol(symbol)

            request = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = self.client.submit_order(request)
            logger.info(f"PARTIAL SELL: {quantity} shares of {symbol} (order_id={order.id})")
            return True
        except APIError as e:
            logger.error(f"Failed to sell {quantity} shares of {symbol}: {e}")
            return False

    def close_position(self, symbol: str) -> bool:
        """Close an entire position. Cancels pending orders first to free held shares."""
        try:
            # Cancel any pending orders (bracket children, stops, etc.) that hold shares
            self.cancel_orders_for_symbol(symbol)

            self.client.close_position(symbol)
            logger.info(f"Position closed: {symbol}")
            return True
        except APIError as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            return False

    def cancel_orders_for_symbol(self, symbol: str):
        """Cancel all open orders for a specific symbol."""
        try:
            orders = self.client.get_orders()
            for order in orders:
                if order.symbol == symbol and order.status in (
                    OrderStatus.NEW, OrderStatus.ACCEPTED,
                    OrderStatus.PENDING_NEW, OrderStatus.PARTIALLY_FILLED,
                ):
                    self.client.cancel_order_by_id(order.id)
                    logger.info(f"Cancelled order {order.id} for {symbol} ({order.side} {order.qty})")
        except Exception as e:
            logger.error(f"Error cancelling orders for {symbol}: {e}")

    def close_all_positions(self) -> bool:
        """Emergency: close all positions."""
        try:
            self.client.close_all_positions(cancel_orders=True)
            logger.warning("ALL POSITIONS CLOSED")
            return True
        except APIError as e:
            logger.error(f"Failed to close all positions: {e}")
            return False

    def get_open_orders(self) -> list[dict]:
        """Get all open/pending orders."""
        try:
            orders = self.client.get_orders()
            return [
                {
                    "id": str(order.id),
                    "symbol": order.symbol,
                    "side": order.side.value if hasattr(order.side, 'value') else str(order.side),
                    "qty": float(order.qty) if order.qty else 0,
                    "type": order.type.value if hasattr(order.type, 'value') else str(order.type),
                    "status": order.status.value if hasattr(order.status, 'value') else str(order.status),
                    "created_at": str(order.created_at),
                }
                for order in orders
            ]
        except APIError as e:
            logger.error(f"Failed to get orders: {e}")
            return []

    def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        try:
            self.client.cancel_orders()
            logger.info("All open orders cancelled")
            return True
        except APIError as e:
            logger.error(f"Failed to cancel orders: {e}")
            return False

    def snapshot_portfolio(self):
        """Save a portfolio snapshot to the database."""
        portfolio = self.get_portfolio()
        if not portfolio:
            return

        session = get_session()
        try:
            snap = PortfolioSnapshot(
                total_equity=portfolio.get("equity", 0),
                cash=portfolio.get("cash", 0),
                buying_power=portfolio.get("buying_power", 0),
                positions=[
                    {"symbol": p["symbol"], "qty": p["qty"],
                     "market_value": p["market_value"], "pnl": p["unrealized_pnl"]}
                    for p in portfolio.get("positions", [])
                ],
                daily_pnl=portfolio.get("daily_pnl", 0),
                total_pnl=sum(
                    p.get("unrealized_pnl", 0) for p in portfolio.get("positions", [])
                ),
            )
            session.add(snap)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to snapshot portfolio: {e}")
            session.rollback()
        finally:
            session.close()

    def place_limit_buy(self, symbol: str, quantity: int, limit_price: float) -> str | None:
        """Place a limit buy order. Returns order_id or None."""
        try:
            request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.BUY,
                limit_price=round(limit_price, 2),
                time_in_force=TimeInForce.DAY,
            )
            order = self.client.submit_order(request)
            logger.info(
                f"LIMIT BUY placed: {quantity} {symbol} @ ${limit_price:.2f} "
                f"(order_id={order.id})"
            )
            return str(order.id)
        except APIError as e:
            logger.error(f"Failed to place limit buy for {symbol}: {e}")
            return None

    def get_order_status(self, order_id: str) -> dict | None:
        """Get the status of a specific order."""
        try:
            order = self.client.get_order_by_id(order_id)
            return {
                "id": str(order.id),
                "symbol": order.symbol,
                "status": order.status.value if hasattr(order.status, 'value') else str(order.status),
                "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
                "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
                "qty": float(order.qty) if order.qty else 0,
            }
        except APIError as e:
            logger.error(f"Failed to get order status {order_id}: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order by ID."""
        try:
            self.client.cancel_order_by_id(order_id)
            return True
        except APIError as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    # ── Private methods ────────────────────────────────

    def _place_market_order(self, symbol: str, qty: int, side: OrderSide):
        """Place a simple market order."""
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        return self.client.submit_order(request)

    def _place_bracket_order(self, symbol: str, qty: int, side: OrderSide,
                              stop_loss: float, take_profit: float):
        """Place a bracket order (entry + stop loss + take profit)."""
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
        )
        return self.client.submit_order(request)

    def _place_stop_loss(self, symbol: str, qty: int, stop_price: float):
        """Place a standalone stop loss order."""
        try:
            request = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=round(stop_price, 2),
            )
            return self.client.submit_order(request)
        except Exception as e:
            logger.error(f"Failed to place stop loss for {symbol}: {e}")
            return None

    def _log_trade(self, proposal: TradeProposal, verdict: RiskVerdict, order) -> Trade:
        """Log a trade to the database."""
        session = get_session()
        try:
            trade = Trade(
                symbol=proposal.symbol,
                side=proposal.side,
                quantity=verdict.adjusted_quantity,
                order_type="market",
                filled_price=float(order.filled_avg_price) if order.filled_avg_price else None,
                status=order.status.value if hasattr(order.status, 'value') else str(order.status),
                alpaca_order_id=str(order.id),
                confidence=proposal.confidence,
                reasoning=proposal.reasoning,
                stop_loss=proposal.stop_loss,
                take_profit=proposal.take_profit,
            )
            session.add(trade)
            session.commit()
            session.refresh(trade)
            return trade
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")
            session.rollback()
            return None
        finally:
            session.close()
