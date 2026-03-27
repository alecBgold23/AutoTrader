"""Main trading loop — the orchestrator.

Day Trading Workflow (modeled after professional prop traders):
1. 9:00 AM:  Build universe (scan entire market)
2. 9:15 AM:  Pre-market scan for gappers and volume
3. 9:30 AM:  Market opens — trade ORBs, Gap & Go on opening drive
4. 10:00 AM: Prime trading — first pullbacks, flag breakouts, VWAP tests
5. 11:00 AM: Lunch — reduce activity, only A+ setups
6. 1:30 PM:  Afternoon — volume returns, continuations
7. 3:00 PM:  Power hour — institutional flow, late breakouts
8. 3:50 PM:  Close all positions — day trade rule
9. 4:30 PM:  Daily summary + journal entry
"""

import asyncio
import logging
import signal
import sys
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from autotrader.config import (
    SCAN_INTERVAL_OPEN, SCAN_INTERVAL_NORMAL, SCAN_INTERVAL_POWER_HOUR,
    AUTONOMY_MODE, APPROVAL_TIMEOUT_SECONDS, RISK, LOG_DIR, LOG_LEVEL,
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    DAY_TRADE_MODE, CLOSE_ALL_EOD, EOD_CLOSE_MINUTE,
    SCANNER, WATCHLIST_FALLBACK, PHASE_CONFIG,
)
from autotrader.brain.analyst import ClaudeAnalyst
from autotrader.execution.broker import AlpacaBroker
from autotrader.risk.manager import RiskManager
from autotrader.risk.position_manager import PositionManager
from autotrader.alerts.telegram import TelegramAlerts
from autotrader.data.scanner import MarketScanner
from autotrader.data.market import get_current_price
from autotrader.execution.stalker import EntryStalker
from autotrader.db.models import init_db, get_session, Trade, Decision, TradingJournal

logger = logging.getLogger("autotrader")


class AutoTrader:
    """The main autonomous day trading system."""

    def __init__(self):
        self.analyst = ClaudeAnalyst()
        self.broker = AlpacaBroker()
        self.risk = RiskManager()
        self.positions = PositionManager(atr_trail_multiplier=1.5)
        self.telegram = TelegramAlerts(risk_manager=self.risk, broker=self.broker)
        self.scanner = MarketScanner()
        self.stalker = EntryStalker()
        self.scheduler = AsyncIOScheduler()
        self._running = False
        self._trades_today = 0
        self._wins_today = 0
        self._losses_today = 0
        self._daily_r = 0.0  # Total R earned today

    async def start(self):
        """Start the trading system."""
        logger.info("=" * 60)
        logger.info("  AutoTrader v3.0 — AI Day Trading Platform")
        logger.info("  Pattern + Location + Volume = Edge")
        logger.info("=" * 60)

        init_db()
        logger.info("Database initialized")

        await self.telegram.start()
        self.broker.snapshot_portfolio()

        account = self.broker.get_account()
        logger.info(f"Account equity: ${account.get('equity', 0):,.2f}")
        logger.info(f"Day trade mode: {DAY_TRADE_MODE}")
        logger.info(f"Autonomy mode: {AUTONOMY_MODE}")

        # Build trading universe
        logger.info("Building trading universe (scanning entire market)...")
        self.scanner.build_universe()
        logger.info(f"Universe: {len(self.scanner.universe)} liquid stocks")

        # Initial scan
        self.scanner.scan_for_movers()
        logger.info(f"Hot list: {len(self.scanner.hot_list)} candidates")

        # ── Schedule jobs ──────────────────────────────

        # Main trading loop — interval adjusts dynamically per phase
        self._current_scan_interval = SCAN_INTERVAL_NORMAL
        self.scheduler.add_job(
            self._trading_loop,
            IntervalTrigger(minutes=SCAN_INTERVAL_NORMAL),
            id="trading_loop",
            name="Main Trading Loop",
            max_instances=1,
        )

        # Position management (check trailing stops, scale-outs)
        self.scheduler.add_job(
            self._manage_positions,
            IntervalTrigger(minutes=2),
            id="position_mgmt",
            name="Position Management",
        )

        # Entry stalker — monitor pending limit orders every 30 seconds
        self.scheduler.add_job(
            self._check_stalked_entries,
            IntervalTrigger(seconds=30),
            id="entry_stalker",
            name="Entry Stalker",
        )

        # Refresh hot list
        self.scheduler.add_job(
            self._refresh_hot_list,
            IntervalTrigger(minutes=SCANNER["hot_list_refresh_minutes"]),
            id="hot_list_refresh",
            name="Hot List Refresh",
        )

        # Portfolio snapshots every 30 min
        self.scheduler.add_job(
            self._snapshot_portfolio,
            IntervalTrigger(minutes=30),
            id="portfolio_snapshot",
            name="Portfolio Snapshot",
        )

        # Daily universe rebuild at 9:00 AM ET
        self.scheduler.add_job(
            self._rebuild_universe,
            CronTrigger(hour=9, minute=0, timezone="US/Eastern"),
            id="universe_rebuild",
            name="Daily Universe Rebuild",
        )

        # EOD close all positions
        if CLOSE_ALL_EOD:
            self.scheduler.add_job(
                self._eod_close_all,
                CronTrigger(hour=15, minute=EOD_CLOSE_MINUTE, timezone="US/Eastern"),
                id="eod_close",
                name="EOD Close All",
            )

        # Daily summary + journal at 4:30 PM ET
        self.scheduler.add_job(
            self._daily_summary,
            CronTrigger(hour=16, minute=30, timezone="US/Eastern"),
            id="daily_summary",
            name="Daily Summary",
        )

        self.scheduler.start()
        self._running = True

        logger.info("AutoTrader is LIVE.")
        await self.telegram.send_message(
            f"*AutoTrader v3.0 LIVE*\n"
            f"Equity: ${account.get('equity', 0):,.2f}\n"
            f"Universe: {len(self.scanner.universe)} stocks\n"
            f"Hot list: {len(self.scanner.hot_list)} movers\n"
            f"Mode: {AUTONOMY_MODE} | EOD close: {CLOSE_ALL_EOD}"
        )

        # Run initial cycle
        await self._trading_loop()

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Gracefully shut down."""
        logger.info("Shutting down AutoTrader...")
        self._running = False
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        await self.telegram.send_message("AutoTrader shutting down.")
        await self.telegram.stop()
        self.broker.snapshot_portfolio()
        logger.info("AutoTrader stopped.")

    # ══════════════════════════════════════════════════════
    # CORE TRADING LOOP
    # ══════════════════════════════════════════════════════

    async def _trading_loop(self):
        """Scan → detect patterns → analyze with Claude → risk check → execute."""
        if self.risk.is_halted:
            logger.info("Trading halted — skipping cycle")
            return

        if not self._is_market_hours():
            logger.debug("Outside market hours")
            return

        market_phase = self._get_market_phase()

        # Dynamically adjust scan interval based on phase
        phase_conf = PHASE_CONFIG.get(market_phase, {})
        new_interval = phase_conf.get("scan_interval", SCAN_INTERVAL_NORMAL)
        if new_interval != self._current_scan_interval:
            self._current_scan_interval = new_interval
            self.scheduler.reschedule_job(
                "trading_loop",
                trigger=IntervalTrigger(minutes=new_interval),
            )
            logger.info(f"Scan interval adjusted to {new_interval} min for {market_phase}")

        # During lunch, scan fewer candidates (but don't skip entirely — let Claude decide)
        if market_phase == "lunch":
            logger.info("Lunch period — scanning with reduced candidates")

        logger.info(
            f"--- Trading cycle | Phase: {market_phase} | "
            f"Hot list: {len(self.scanner.hot_list)} | "
            f"Trades: {self._trades_today} | R: {self._daily_r:+.1f} ---"
        )

        portfolio = self.broker.get_portfolio()
        if not portfolio.get("equity"):
            logger.error("Could not get portfolio — skipping")
            return

        # Check daily R limit (-3R = stop trading)
        if self._daily_r <= -3.0:
            logger.warning(f"Daily R limit hit ({self._daily_r:.1f}R) — stopping for the day")
            self.risk.halt("Daily R limit (-3R)")
            return

        # Refresh hot list if stale
        if self.scanner.needs_hot_list_refresh():
            self.scanner.scan_for_movers()

        # Get candidates
        candidates = self.scanner.get_top_candidates()
        if not candidates:
            from autotrader.data.scanner import ScanCandidate
            candidates = [ScanCandidate(symbol=s, score=1.0, flags=["FALLBACK"]) for s in WATCHLIST_FALLBACK]

        symbols_to_analyze = [c.symbol for c in candidates]

        # Always monitor existing positions
        for sym in list(self.positions.positions.keys()):
            if sym not in symbols_to_analyze:
                symbols_to_analyze.append(sym)

        # During lunch, only analyze existing positions
        if market_phase == "lunch":
            symbols_to_analyze = [s for s in symbols_to_analyze if s in self.positions.positions]
            if not symbols_to_analyze:
                return

        logger.info(f"Analyzing: {', '.join(symbols_to_analyze[:10])}{'...' if len(symbols_to_analyze) > 10 else ''}")

        for symbol in symbols_to_analyze:
            if self.risk.is_halted:
                break

            scanner_flags = ""
            for c in candidates:
                if c.symbol == symbol:
                    scanner_flags = (
                        f"Score: {c.score} | Chg: {c.change_pct:+.1f}% | "
                        f"RVOL: {c.relative_volume:.1f}x | Gap: {c.gap_pct:+.1f}% | "
                        f"ATR: {c.atr_pct:.1f}% | Float: {c.float_category} | "
                        f"5D: {c.five_day_change:+.1f}% | Flags: {', '.join(c.flags)}"
                    )
                    break

            try:
                await self._analyze_and_trade(symbol, portfolio, scanner_flags, market_phase)
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")

            await asyncio.sleep(1)

        logger.info(f"--- Cycle complete | W:{self._wins_today} L:{self._losses_today} R:{self._daily_r:+.1f} ---")

    async def _analyze_and_trade(self, symbol: str, portfolio: dict, scanner_flags: str, market_phase: str):
        """Analyze one symbol with full pattern detection and potentially trade."""

        result = self.analyst.analyze(
            symbol=symbol,
            portfolio=portfolio,
            scanner_flags=scanner_flags,
            trades_today=self._trades_today,
            market_phase=market_phase,
        )
        if not result:
            return

        self._log_decision(result, market_phase)

        if result.action == "HOLD":
            logger.debug(
                f"{symbol}: HOLD | conf={result.confidence:.0%} | "
                f"pattern={result.pattern} | "
                f"patterns_found={len(result.detected_patterns)}"
            )
            return

        # ── SELL: Check if we actually own this stock ──
        if result.action == "SELL":
            owned_positions = {p["symbol"]: p for p in portfolio.get("positions", [])}
            if symbol not in owned_positions:
                logger.debug(f"{symbol}: SELL signal but no position — skipping (can't short)")
                return

            owned = owned_positions[symbol]
            pnl = owned.get("unrealized_pnl", 0)

            logger.info(
                f"{symbol}: SELL | conf={result.confidence:.0%} | "
                f"pattern={result.pattern} | P&L=${pnl:+,.2f} | {result.reasoning[:120]}"
            )

            # Close the entire position
            if result.confidence >= RISK["min_confidence_to_trade"]:
                success = self.broker.close_position(symbol)
                if success:
                    self._trades_today += 1
                    self.positions.remove_position(symbol)

                    # Track win/loss
                    if pnl > 0:
                        self._wins_today += 1
                        self.risk.record_trade_result(won=True)
                    elif pnl < 0:
                        self._losses_today += 1
                        self.risk.record_trade_result(won=False)

                    logger.info(f"CLOSED {symbol}: P&L ${pnl:+,.2f}")

                    await self.telegram.send_trade_alert({
                        "symbol": symbol,
                        "side": "SELL",
                        "quantity": int(owned["qty"]),
                        "price": owned["current_price"],
                        "confidence": result.confidence,
                        "pattern": result.pattern,
                        "reasoning": result.reasoning,
                        "stop_loss": None,
                        "take_profit": None,
                    })
            return

        # ── BUY: Normal flow with risk check ──
        price_data = get_current_price(symbol)
        if not price_data:
            return
        current_price = price_data["price"]

        # Skip if we already have a stalked entry waiting for this symbol
        if self.stalker.has_pending(symbol):
            logger.debug(f"{symbol}: Already stalking entry — skipping")
            return

        proposal = self.analyst.to_trade_proposal(result, current_price)
        verdict = self.risk.check_trade(proposal, portfolio, market_phase)

        if not verdict.approved:
            logger.info(f"{symbol}: BLOCKED — {verdict.reason}")
            self._update_decision_blocked(result, verdict.reason)
            return

        # ── Entry Decision: Market order vs Limit order (stalk the entry) ──
        entry_price = result.entry_price
        use_limit = False

        if entry_price and entry_price > 0 and result.stop_loss:
            # How far below current price is the ideal entry?
            entry_gap_pct = (current_price - entry_price) / current_price

            # Use limit order if:
            # 1. Entry is meaningfully below current price (>0.3%)
            # 2. Confidence is not extreme (< 85% — extreme confidence = don't miss it)
            # 3. Entry is above the stop loss (makes sense)
            if (entry_gap_pct > 0.003
                    and result.confidence < 0.85
                    and entry_price > result.stop_loss):
                use_limit = True

        if use_limit and (AUTONOMY_MODE == "full_auto" or result.confidence >= RISK["min_confidence_full_auto"]):
            # STALK THE ENTRY — place limit order and wait
            logger.info(
                f"{symbol}: BUY LIMIT | conf={result.confidence:.0%} | "
                f"entry=${entry_price:.2f} (current ${current_price:.2f}, "
                f"{(current_price - entry_price) / current_price:.1%} better) | "
                f"pattern={result.pattern} | SL=${result.stop_loss} | "
                f"TP=${result.take_profit} | {result.reasoning[:100]}"
            )

            order_id = self.broker.place_limit_buy(
                symbol=symbol,
                quantity=int(verdict.adjusted_quantity),
                limit_price=entry_price,
            )
            if order_id:
                self.stalker.add_entry(
                    symbol=symbol,
                    order_id=order_id,
                    limit_price=entry_price,
                    quantity=int(verdict.adjusted_quantity),
                    stop_loss=result.stop_loss,
                    take_profit=result.take_profit,
                    pattern=result.pattern,
                    confidence=result.confidence,
                    reasoning=result.reasoning,
                )
                await self.telegram.send_message(
                    f"*STALKING ENTRY*: {symbol}\n"
                    f"Limit: ${entry_price:.2f} (current ${current_price:.2f})\n"
                    f"Stop: ${result.stop_loss:.2f} | Target: ${result.take_profit:.2f}\n"
                    f"Pattern: {result.pattern}\n"
                    f"Waiting up to 10 min for fill..."
                )

        elif AUTONOMY_MODE == "full_auto" or result.confidence >= RISK["min_confidence_full_auto"]:
            # MARKET ORDER — price is at level or high confidence, go now
            logger.info(
                f"{symbol}: BUY MARKET | conf={result.confidence:.0%} | "
                f"pattern={result.pattern} | SL=${result.stop_loss} | "
                f"TP=${result.take_profit} | {result.reasoning[:120]}"
            )

            trade = self.broker.execute_trade(proposal, verdict)
            if trade:
                self._trades_today += 1

                if result.stop_loss and result.take_profit:
                    self.positions.add_position(
                        symbol=symbol,
                        entry_price=current_price,
                        quantity=verdict.adjusted_quantity,
                        stop_loss=result.stop_loss,
                        take_profit=result.take_profit,
                        pattern=result.pattern,
                    )

                await self.telegram.send_trade_alert({
                    "symbol": symbol,
                    "side": result.action,
                    "quantity": verdict.adjusted_quantity,
                    "price": current_price,
                    "confidence": result.confidence,
                    "pattern": result.pattern,
                    "reasoning": result.reasoning,
                    "stop_loss": result.stop_loss,
                    "take_profit": result.take_profit,
                })

        elif AUTONOMY_MODE in ("notify_first", "require_approval"):
            proposal_id = str(uuid.uuid4())[:8]
            await self.telegram.send_trade_proposal(proposal_id, {
                "symbol": symbol,
                "side": result.action,
                "quantity": verdict.adjusted_quantity,
                "confidence": result.confidence,
                "pattern": result.pattern,
                "reasoning": result.reasoning,
            })

            approved = await self._wait_for_approval(proposal_id)
            should_execute = (
                (AUTONOMY_MODE == "notify_first" and approved is not False) or
                (AUTONOMY_MODE == "require_approval" and approved is True)
            )

            if should_execute:
                trade = self.broker.execute_trade(proposal, verdict)
                if trade:
                    self._trades_today += 1
                    if result.stop_loss and result.take_profit:
                        self.positions.add_position(
                            symbol=symbol,
                            entry_price=current_price,
                            quantity=verdict.adjusted_quantity,
                            stop_loss=result.stop_loss,
                            take_profit=result.take_profit,
                            pattern=result.pattern,
                        )

    # ══════════════════════════════════════════════════════
    # POSITION MANAGEMENT (runs every 2 min)
    # ══════════════════════════════════════════════════════

    async def _manage_positions(self):
        """Active position management — trailing stops, scale-outs, time exits."""
        if not self.positions.positions:
            return

        if not self._is_market_hours():
            return

        minutes_to_close = self._minutes_to_close()

        for symbol in list(self.positions.positions.keys()):
            try:
                price_data = get_current_price(symbol)
                if not price_data:
                    continue

                current_price = price_data["price"]

                # Get ATR for trailing stop calculation
                from autotrader.data.indicators import calculate_indicators
                from autotrader.data.market import get_stock_data
                hist = get_stock_data(symbol, period="1mo", interval="1d")
                indicators = calculate_indicators(hist)
                atr = indicators.get("atr")

                # Check for scale-outs, trailing stop moves
                actions = self.positions.update(symbol, current_price, atr)

                # Check time-based exits
                time_action = self.positions.check_time_exit(symbol, minutes_to_close)
                if time_action:
                    actions.append(time_action)

                for action in actions:
                    if action["action"] == "SELL_ALL":
                        logger.info(f"POSITION MGMT: {symbol} — {action['reason']}")
                        pnl = self._get_position_pnl(symbol, current_price)
                        success = self.broker.close_position(symbol)
                        if success:
                            self.positions.remove_position(symbol)
                            self._trades_today += 1
                            if pnl > 0:
                                self._wins_today += 1
                                self.risk.record_trade_result(won=True)
                            elif pnl < 0:
                                self._losses_today += 1
                                self.risk.record_trade_result(won=False)
                            logger.info(f"CLOSED {symbol}: P&L ${pnl:+,.2f} | {action['reason']}")
                        await self.telegram.send_message(
                            f"*Position Closed*: {symbol} (P&L: ${pnl:+,.2f})\n{action['reason']}"
                        )

                    elif action["action"] == "SELL_PARTIAL":
                        qty = action["quantity"]
                        logger.info(f"POSITION MGMT: {symbol} — Selling {qty} shares: {action['reason']}")
                        success = self.broker.sell_shares(symbol, qty)
                        if success:
                            logger.info(f"SCALE OUT {symbol}: sold {qty} shares")
                        await self.telegram.send_message(
                            f"*Scale Out*: {symbol} — {qty} shares\n{action['reason']}"
                        )

                    elif action["action"] == "MOVE_STOP":
                        logger.info(f"POSITION MGMT: {symbol} — Stop → ${action['new_stop']:.2f}: {action['reason']}")

            except Exception as e:
                logger.error(f"Position management error for {symbol}: {e}")

    # ══════════════════════════════════════════════════════
    # ENTRY STALKER (runs every 30 seconds)
    # ══════════════════════════════════════════════════════

    async def _check_stalked_entries(self):
        """Monitor pending limit orders — fill, expire, or invalidate."""
        if not self.stalker.pending:
            return

        if not self._is_market_hours():
            return

        # Get current prices for all stalked symbols
        current_prices = {}
        for symbol in self.stalker.pending:
            price_data = get_current_price(symbol)
            if price_data:
                current_prices[symbol] = price_data["price"]

        # Check all entries
        actions = self.stalker.check_entries(self.broker, current_prices)

        for action in actions:
            entry = action["entry"]

            if action["action"] == "filled":
                # Limit order filled — register position for management
                fill_price = action["fill_price"]
                self._trades_today += 1

                self.positions.add_position(
                    symbol=entry.symbol,
                    entry_price=fill_price,
                    quantity=entry.quantity,
                    stop_loss=entry.stop_loss,
                    take_profit=entry.take_profit,
                    pattern=entry.pattern,
                )

                # Calculate how much better the entry was
                # (compared to if we had bought at market when signal fired)
                savings = ""
                if fill_price < entry.limit_price * 1.01:  # filled at or near limit
                    savings = f" (stalked {entry.age_seconds}s for better fill)"

                await self.telegram.send_trade_alert({
                    "symbol": entry.symbol,
                    "side": "BUY",
                    "quantity": entry.quantity,
                    "price": fill_price,
                    "confidence": entry.confidence,
                    "pattern": entry.pattern,
                    "reasoning": entry.reasoning + savings,
                    "stop_loss": entry.stop_loss,
                    "take_profit": entry.take_profit,
                })

            elif action["action"] == "cancelled":
                logger.info(
                    f"Stalked entry cancelled: {entry.symbol} — {action['reason']}"
                )
                await self.telegram.send_message(
                    f"*Entry Cancelled*: {entry.symbol}\n{action['reason']}"
                )

    # ══════════════════════════════════════════════════════
    # SCHEDULED JOBS
    # ══════════════════════════════════════════════════════

    async def _refresh_hot_list(self):
        if not self._is_market_hours():
            return
        logger.info("Refreshing hot list...")
        self.scanner.scan_for_movers()

    async def _rebuild_universe(self):
        logger.info("Daily universe rebuild...")
        self.scanner.build_universe()
        self.scanner.scan_for_movers()

        # Reset daily counters
        self._trades_today = 0
        self._wins_today = 0
        self._losses_today = 0
        self._daily_r = 0.0

        await self.telegram.send_message(
            f"*Pre-Market Scan*\n"
            f"Universe: {len(self.scanner.universe)} stocks\n"
            f"Hot list: {len(self.scanner.hot_list)} movers\n"
            f"{self.scanner.get_scan_summary()}"
        )

    async def _eod_close_all(self):
        # Cancel any pending stalked entries first
        if self.stalker.pending:
            logger.info(f"EOD: Cancelling {self.stalker.count} stalked entries")
            self.stalker.cancel_all(self.broker)

        positions = self.broker.get_positions()
        if not positions:
            logger.info("EOD: No positions to close")
            return

        logger.warning(f"EOD: Closing {len(positions)} positions")
        await self.telegram.send_message(f"*EOD CLOSE*: Closing {len(positions)} positions")

        for pos in positions:
            symbol = pos.get("symbol")
            pnl = pos.get("unrealized_pnl", 0)
            self.broker.close_position(symbol)
            self.positions.remove_position(symbol)

            if pnl > 0:
                self._wins_today += 1
            elif pnl < 0:
                self._losses_today += 1

            logger.info(f"EOD closed: {symbol} (P&L: ${pnl:,.2f})")

        self.broker.cancel_all_orders()

    async def _snapshot_portfolio(self):
        self.broker.snapshot_portfolio()

    async def _daily_summary(self):
        """End-of-day summary, journal entry, then auto-shutdown."""
        portfolio = self.broker.get_portfolio()

        session = get_session()
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
            trades = session.query(Trade).filter(Trade.created_at >= today_start).all()

            # Write trading journal entry
            today_str = datetime.now().strftime("%Y-%m-%d")
            total = self._wins_today + self._losses_today
            win_rate = (self._wins_today / total * 100) if total > 0 else 0

            journal = TradingJournal(
                date=today_str,
                total_trades=self._trades_today,
                wins=self._wins_today,
                losses=self._losses_today,
                win_rate=win_rate,
                total_pnl=portfolio.get("daily_pnl", 0),
                total_r=self._daily_r,
                universe_size=len(self.scanner.universe),
                hot_list_size=len(self.scanner.hot_list),
                ending_equity=portfolio.get("equity", 0),
            )
            session.merge(journal)
            session.commit()

            await self.telegram.send_daily_summary(portfolio, trades)
        except Exception as e:
            logger.error(f"Daily summary error: {e}")
            session.rollback()
        finally:
            session.close()

        # Reset counters
        self._trades_today = 0
        self._wins_today = 0
        self._losses_today = 0
        self._daily_r = 0.0

        # Auto-shutdown after daily summary — launchd will restart tomorrow
        logger.info("Daily summary complete. Shutting down until tomorrow.")
        await self.stop()

    # ══════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════

    def _get_position_pnl(self, symbol: str, current_price: float) -> float:
        """Get unrealized P&L for a position."""
        if symbol in self.positions.positions:
            pos = self.positions.positions[symbol]
            return (current_price - pos.entry_price) * pos.shares_remaining
        # Fallback: check broker
        for p in self.broker.get_positions():
            if p["symbol"] == symbol:
                return p.get("unrealized_pnl", 0)
        return 0.0

    async def _wait_for_approval(self, proposal_id: str) -> bool | None:
        elapsed = 0
        while elapsed < APPROVAL_TIMEOUT_SECONDS:
            status = self.telegram.get_approval_status(proposal_id)
            if status is not None:
                return status
            await asyncio.sleep(5)
            elapsed += 5
        return None

    def _is_market_hours(self) -> bool:
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        now_et = datetime.now(ZoneInfo("US/Eastern"))
        if now_et.weekday() >= 5:
            return False

        market_open = now_et.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
        market_close = now_et.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
        return market_open <= now_et <= market_close

    def _get_market_phase(self) -> str:
        """Determine current market phase — drives strategy selection."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        now_et = datetime.now(ZoneInfo("US/Eastern"))
        h, m = now_et.hour, now_et.minute
        minutes = h * 60 + m

        if minutes < 570:  # Before 9:30
            return "premarket"
        elif minutes < 600:  # 9:30-10:00
            return "open"
        elif minutes < 660:  # 10:00-11:00
            return "prime"
        elif minutes < 810:  # 11:00-1:30
            return "lunch"
        elif minutes < 900:  # 1:30-3:00
            return "afternoon"
        elif minutes < 950:  # 3:00-3:50
            return "power_hour"
        else:  # 3:50+
            return "close"

    def _minutes_to_close(self) -> int:
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        now_et = datetime.now(ZoneInfo("US/Eastern"))
        close_time = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        diff = (close_time - now_et).total_seconds() / 60
        return max(0, int(diff))

    def _log_decision(self, result, market_phase: str = ""):
        session = get_session()
        try:
            decision = Decision(
                symbol=result.symbol,
                action=result.action,
                confidence=result.confidence,
                reasoning=result.reasoning,
                pattern=result.pattern,
                indicators=result.indicators,
                news_summary=result.raw_response,
                market_phase=market_phase,
            )
            session.add(decision)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to log decision: {e}")
            session.rollback()
        finally:
            session.close()

    def _update_decision_blocked(self, result, reason: str):
        session = get_session()
        try:
            decision = (
                session.query(Decision)
                .filter(Decision.symbol == result.symbol)
                .order_by(Decision.created_at.desc())
                .first()
            )
            if decision:
                decision.blocked_reason = reason
                session.commit()
        except Exception as e:
            logger.error(f"Failed to update decision: {e}")
            session.rollback()
        finally:
            session.close()


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(
        LOG_DIR / f"autotrader_{datetime.now().strftime('%Y%m%d')}.log"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    for lib in ("httpx", "httpcore", "urllib3", "yfinance", "apscheduler", "peewee"):
        logging.getLogger(lib).setLevel(logging.WARNING)


async def run():
    setup_logging()
    trader = AutoTrader()

    loop = asyncio.get_event_loop()
    def shutdown_handler():
        logger.info("Received shutdown signal")
        asyncio.ensure_future(trader.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    try:
        await trader.start()
    except KeyboardInterrupt:
        await trader.stop()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        await trader.stop()
        sys.exit(1)
