"""Telegram bot for trade alerts, portfolio monitoring, and manual control."""

import asyncio
import logging
from datetime import datetime, timezone

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from autotrader.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


class TelegramAlerts:
    """Sends trade alerts and receives commands via Telegram."""

    def __init__(self, risk_manager=None, broker=None):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.risk_manager = risk_manager
        self.broker = broker
        self._app: Application | None = None
        self._pending_approvals: dict[str, dict] = {}

        if (not self.bot_token or not self.chat_id
                or self.bot_token.startswith("your_") or self.chat_id.startswith("your_")):
            logger.warning("Telegram not configured — alerts disabled")
            self.enabled = False
        else:
            self.enabled = True
            self.bot = Bot(token=self.bot_token)

    async def start(self):
        """Start the Telegram bot (non-blocking, runs alongside trading loop)."""
        if not self.enabled:
            return

        self._app = (
            Application.builder()
            .token(self.bot_token)
            .build()
        )

        # Register command handlers
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("positions", self._cmd_positions))
        self._app.add_handler(CommandHandler("trades", self._cmd_trades))
        self._app.add_handler(CommandHandler("halt", self._cmd_halt))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))
        self._app.add_handler(CommandHandler("closeall", self._cmd_close_all))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        logger.info("Telegram bot started")
        await self.send_message("AutoTrader bot started. Use /help for commands.")

    async def stop(self):
        """Stop the Telegram bot."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send_message(self, text: str):
        """Send a plain text message."""
        if not self.enabled:
            return
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    async def send_trade_alert(self, trade_info: dict):
        """Send a trade execution alert."""
        side_emoji = "BUY" if trade_info["side"] == "BUY" else "SELL"
        msg = (
            f"*TRADE EXECUTED*\n\n"
            f"{'BUY' if trade_info['side'] == 'BUY' else 'SELL'} "
            f"{trade_info['quantity']} x {trade_info['symbol']}\n"
            f"Price: ${trade_info.get('price', 'market')}\n"
            f"Confidence: {trade_info['confidence']:.0%}\n"
            f"Reasoning: {trade_info['reasoning']}\n"
        )
        if trade_info.get("stop_loss"):
            msg += f"Stop Loss: ${trade_info['stop_loss']:.2f}\n"
        if trade_info.get("take_profit"):
            msg += f"Take Profit: ${trade_info['take_profit']:.2f}\n"

        await self.send_message(msg)

    async def send_trade_proposal(self, proposal_id: str, trade_info: dict) -> bool:
        """Send a trade proposal with APPROVE/REJECT buttons.

        Returns True if successfully sent.
        """
        if not self.enabled:
            return False

        self._pending_approvals[proposal_id] = {
            "trade": trade_info,
            "approved": None,
            "timestamp": datetime.now(timezone.utc),
        }

        keyboard = [
            [
                InlineKeyboardButton("APPROVE", callback_data=f"approve_{proposal_id}"),
                InlineKeyboardButton("REJECT", callback_data=f"reject_{proposal_id}"),
            ]
        ]

        msg = (
            f"*TRADE PROPOSAL*\n\n"
            f"{trade_info['side']} {trade_info['quantity']} x {trade_info['symbol']}\n"
            f"Confidence: {trade_info['confidence']:.0%}\n"
            f"Reasoning: {trade_info['reasoning']}\n\n"
            f"Approve or reject:"
        )

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send trade proposal: {e}")
            return False

    def get_approval_status(self, proposal_id: str) -> bool | None:
        """Check if a proposal has been approved/rejected. Returns None if pending."""
        entry = self._pending_approvals.get(proposal_id)
        if entry:
            return entry["approved"]
        return None

    async def send_daily_summary(self, portfolio: dict, trades_today: list):
        """Send end-of-day summary."""
        positions = portfolio.get("positions", [])
        pos_text = "\n".join(
            f"  {p['symbol']}: {p['qty']} shares (${p['unrealized_pnl']:+,.2f})"
            for p in positions
        ) or "  No positions"

        trades_text = "\n".join(
            f"  {t.side} {t.quantity} {t.symbol} @ ${t.filled_price or 0:.2f}"
            for t in trades_today
        ) or "  No trades today"

        msg = (
            f"*DAILY SUMMARY*\n\n"
            f"Equity: ${portfolio.get('equity', 0):,.2f}\n"
            f"Day P&L: ${portfolio.get('daily_pnl', 0):+,.2f}\n\n"
            f"*Positions:*\n{pos_text}\n\n"
            f"*Trades Today:*\n{trades_text}"
        )
        await self.send_message(msg)

    # ── Command Handlers ────────────────────────────────

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        if not self.broker:
            await update.message.reply_text("Broker not connected")
            return

        account = self.broker.get_account()
        risk_status = self.risk_manager.get_status() if self.risk_manager else {}

        msg = (
            f"*AutoTrader Status*\n\n"
            f"Equity: ${account.get('equity', 0):,.2f}\n"
            f"Cash: ${account.get('cash', 0):,.2f}\n"
            f"Day P&L: ${account.get('daily_pnl', 0):+,.2f}\n"
            f"Mode: {'PAPER' if True else 'LIVE'}\n\n"
            f"*Risk:*\n"
            f"Halted: {'YES' if risk_status.get('halted') else 'No'}\n"
            f"Trades Today: {risk_status.get('todays_trades', 0)}\n"
            f"Consecutive Losses: {risk_status.get('consecutive_losses', 0)}\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions command."""
        if not self.broker:
            await update.message.reply_text("Broker not connected")
            return

        positions = self.broker.get_positions()
        if not positions:
            await update.message.reply_text("No open positions")
            return

        lines = ["*Open Positions*\n"]
        for p in positions:
            lines.append(
                f"{p['symbol']}: {p['qty']} @ ${p['avg_entry_price']:.2f} "
                f"→ ${p['current_price']:.2f} ({p['unrealized_pnl_pct']:+.1f}%)"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /trades command — show recent trades."""
        from autotrader.db.models import get_session, Trade

        session = get_session()
        try:
            trades = (
                session.query(Trade)
                .order_by(Trade.created_at.desc())
                .limit(10)
                .all()
            )
            if not trades:
                await update.message.reply_text("No trades yet")
                return

            lines = ["*Recent Trades*\n"]
            for t in trades:
                lines.append(
                    f"{t.side} {t.quantity} {t.symbol} "
                    f"@ ${t.filled_price or 0:.2f} "
                    f"({t.confidence:.0%} conf) - {t.status}"
                )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        finally:
            session.close()

    async def _cmd_halt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /halt command — stop all trading."""
        if self.risk_manager:
            self.risk_manager.halt("Manual halt via Telegram")
            await update.message.reply_text("TRADING HALTED. Use /resume to restart.")
        else:
            await update.message.reply_text("Risk manager not connected")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command — resume trading."""
        if self.risk_manager:
            self.risk_manager.resume()
            await update.message.reply_text("Trading RESUMED.")
        else:
            await update.message.reply_text("Risk manager not connected")

    async def _cmd_close_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /closeall command — emergency close all positions."""
        if not self.broker:
            await update.message.reply_text("Broker not connected")
            return

        keyboard = [[
            InlineKeyboardButton("YES - CLOSE ALL", callback_data="confirm_closeall"),
            InlineKeyboardButton("Cancel", callback_data="cancel_closeall"),
        ]]
        await update.message.reply_text(
            "Are you sure you want to CLOSE ALL positions and cancel all orders?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        msg = (
            "*AutoTrader Commands*\n\n"
            "/status - Account & risk status\n"
            "/positions - Open positions\n"
            "/trades - Recent trade history\n"
            "/halt - Stop all trading\n"
            "/resume - Resume trading\n"
            "/closeall - Emergency close all\n"
            "/help - This message"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard button presses."""
        query = update.callback_query
        await query.answer()

        data = query.data

        # Trade approval/rejection
        if data.startswith("approve_"):
            proposal_id = data.replace("approve_", "")
            if proposal_id in self._pending_approvals:
                self._pending_approvals[proposal_id]["approved"] = True
                await query.edit_message_text(f"Trade APPROVED")

        elif data.startswith("reject_"):
            proposal_id = data.replace("reject_", "")
            if proposal_id in self._pending_approvals:
                self._pending_approvals[proposal_id]["approved"] = False
                await query.edit_message_text(f"Trade REJECTED")

        elif data == "confirm_closeall":
            if self.broker:
                self.broker.close_all_positions()
                await query.edit_message_text("ALL POSITIONS CLOSED. All orders cancelled.")

        elif data == "cancel_closeall":
            await query.edit_message_text("Close-all cancelled.")
