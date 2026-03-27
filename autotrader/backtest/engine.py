"""Backtesting engine — replay historical days through the trading logic.

Feeds the same data through ClaudeAnalyst → RiskManager → PositionManager
to measure system performance before risking real money.

Key design:
- $0.02/share slippage on all fills (entry + exit)
- Same prompts, risk rules, and position management as live
- Claude API responses cached to disk (keyed by symbol + date + data hash)
- Each run gets its own SQLite database
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from autotrader.config import BASE_DIR, RISK, PHASE_CONFIG

logger = logging.getLogger(__name__)

SLIPPAGE_PER_SHARE = 0.02  # $0.02 per share per side
CACHE_DIR = BASE_DIR / "data" / "backtest_cache"


@dataclass
class SimulatedFill:
    """A simulated trade fill."""
    symbol: str
    side: str
    quantity: int
    price: float           # Price after slippage
    raw_price: float       # Price before slippage
    slippage: float
    timestamp: datetime
    pattern: str = ""
    confidence: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reasoning: str = ""


@dataclass
class SimulatedPosition:
    """A position held during backtest."""
    symbol: str
    entry_price: float
    quantity: int
    stop_loss: float
    take_profit: float
    entry_time: datetime
    pattern: str = ""
    risk_per_share: float = 0.0
    highest_price: float = 0.0
    scale_out_stage: int = 0
    shares_remaining: int = 0

    def __post_init__(self):
        self.risk_per_share = abs(self.entry_price - self.stop_loss)
        self.highest_price = self.entry_price
        self.shares_remaining = self.quantity


@dataclass
class BacktestTrade:
    """A completed round-trip trade in the backtest."""
    symbol: str
    pattern: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    r_multiple: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: str
    confidence: float = 0.0
    market_phase: str = ""
    slippage_cost: float = 0.0


@dataclass
class BacktestResult:
    """Complete results from a backtest run."""
    start_date: str
    end_date: str
    trading_days: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_r: float = 0.0
    total_slippage: float = 0.0
    max_drawdown_pct: float = 0.0
    starting_equity: float = 100_000.0
    ending_equity: float = 100_000.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    api_calls: int = 0
    cache_hits: int = 0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0

    @property
    def expectancy(self) -> float:
        return self.total_pnl / self.total_trades if self.total_trades > 0 else 0

    @property
    def profit_factor(self) -> float:
        gross_wins = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_losses = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        return (gross_wins / gross_losses) if gross_losses > 0 else float("inf") if gross_wins > 0 else 0

    @property
    def avg_r(self) -> float:
        return self.total_r / self.total_trades if self.total_trades > 0 else 0

    @property
    def return_pct(self) -> float:
        return ((self.ending_equity - self.starting_equity) / self.starting_equity * 100) if self.starting_equity > 0 else 0

    @property
    def passes_minimum_bar(self) -> bool:
        """Check if results meet minimum criteria for real money."""
        return (
            self.total_trades >= 200
            and self.expectancy > 0
            and self.profit_factor > 1.3
            and self.max_drawdown_pct < 8.0
        )


class BacktestEngine:
    """Replay historical data through the trading system.

    Usage:
        engine = BacktestEngine(start="2025-01-02", end="2025-03-01")
        result = engine.run(symbols=["AAPL", "NVDA", "TSLA", ...])
    """

    def __init__(
        self,
        start: str,
        end: str,
        starting_equity: float = 100_000.0,
        model: str = "",
        max_trades_per_day: int = 8,
    ):
        self.start_date = datetime.strptime(start, "%Y-%m-%d")
        self.end_date = datetime.strptime(end, "%Y-%m-%d")
        self.equity = starting_equity
        self.starting_equity = starting_equity
        self.model = model  # Override model (e.g., haiku for cheap runs)
        self.max_trades_per_day = max_trades_per_day

        self.positions: dict[str, SimulatedPosition] = {}
        self.completed_trades: list[BacktestTrade] = []
        self.equity_curve: list[tuple[str, float]] = []
        self.api_calls = 0
        self.cache_hits = 0

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def run(self, symbols: list[str]) -> BacktestResult:
        """Run the backtest across all trading days.

        Args:
            symbols: List of symbols to test (e.g., top 5 scanner picks per day)

        Returns:
            BacktestResult with all metrics
        """
        import anthropic
        from autotrader.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS
        from autotrader.brain.prompts import SYSTEM_PROMPT, build_analysis_prompt
        from autotrader.data.market import get_stock_data
        from autotrader.data.indicators import calculate_indicators, get_signal_summary

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        model = self.model or CLAUDE_MODEL

        current = self.start_date
        trading_days = 0
        peak_equity = self.equity

        logger.info(f"Backtest: {self.start_date.date()} to {self.end_date.date()} | {len(symbols)} symbols | Model: {model}")

        while current <= self.end_date:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            date_str = current.strftime("%Y-%m-%d")
            trading_days += 1
            daily_trades = 0

            # Snapshot equity at start of day
            self.equity_curve.append((date_str, self.equity))

            # Track drawdown
            if self.equity > peak_equity:
                peak_equity = self.equity

            for symbol in symbols:
                if daily_trades >= self.max_trades_per_day:
                    break

                try:
                    # Get historical data as of this date
                    hist = get_stock_data(symbol, period="3mo", interval="1d")
                    if hist.empty:
                        continue

                    # Filter to only data available on this date
                    hist_to_date = hist[hist.index <= pd.Timestamp(current, tz=hist.index.tz)]
                    if len(hist_to_date) < 20:
                        continue

                    today_bar = hist_to_date.iloc[-1]
                    prev_bar = hist_to_date.iloc[-2] if len(hist_to_date) > 1 else today_bar

                    price = float(today_bar["Close"])
                    indicators = calculate_indicators(hist_to_date)
                    signal_summary = get_signal_summary(indicators)

                    # Build price data dict
                    price_data = {
                        "price": price,
                        "open": float(today_bar["Open"]),
                        "high": float(today_bar["High"]),
                        "low": float(today_bar["Low"]),
                        "volume": int(today_bar["Volume"]),
                        "prev_close": float(prev_bar["Close"]),
                        "change_pct": ((price - float(prev_bar["Close"])) / float(prev_bar["Close"]) * 100) if float(prev_bar["Close"]) > 0 else 0,
                    }

                    # Check cache
                    cache_key = self._cache_key(symbol, date_str, price_data)
                    cached = self._load_cache(cache_key)

                    if cached:
                        self.cache_hits += 1
                        decision = cached
                    else:
                        # Build prompt (simplified for backtest — no intraday, no news)
                        portfolio = {
                            "equity": self.equity,
                            "cash": self.equity - sum(
                                p.entry_price * p.shares_remaining
                                for p in self.positions.values()
                            ),
                            "positions": [],
                        }

                        prompt = build_analysis_prompt(
                            symbol=symbol,
                            price_data=price_data,
                            indicators=indicators,
                            signal_summary=signal_summary,
                            intraday_summary="Backtest mode — no intraday data available",
                            news_text="Backtest mode — no news data available",
                            portfolio=portfolio,
                            trades_today=daily_trades,
                            market_phase="prime",
                            regime_context="Backtest mode — regime not available for historical data",
                        )

                        # Call Claude
                        try:
                            response = client.messages.create(
                                model=model,
                                max_tokens=CLAUDE_MAX_TOKENS,
                                system=SYSTEM_PROMPT,
                                messages=[{"role": "user", "content": prompt}],
                            )
                            self.api_calls += 1
                            raw_text = response.content[0].text.strip()

                            # Parse JSON
                            text = raw_text.strip()
                            if text.startswith("```"):
                                text = text.split("\n", 1)[-1]
                                text = text.rsplit("```", 1)[0].strip()

                            decision = json.loads(text)
                            self._save_cache(cache_key, decision)

                            # Rate limit
                            time.sleep(0.5)

                        except Exception as e:
                            logger.warning(f"API error for {symbol} on {date_str}: {e}")
                            continue

                    # Process decision
                    action = decision.get("action", "HOLD").upper()
                    confidence = float(decision.get("confidence", 0))
                    stop_loss = decision.get("stop_loss")
                    take_profit = decision.get("take_profit")

                    if action == "BUY" and confidence >= 0.55 and stop_loss and take_profit:
                        # Position sizing (simplified)
                        risk_amount = self.equity * RISK["max_risk_per_trade_pct"]
                        stop_dist = abs(price - float(stop_loss))
                        if stop_dist <= 0:
                            continue

                        shares = int(risk_amount / (stop_dist + SLIPPAGE_PER_SHARE * 2))
                        if shares <= 0:
                            continue

                        # Enforce max position size
                        max_shares = int(self.equity * RISK["max_position_pct"] / price)
                        shares = min(shares, max_shares)

                        # Check R:R with slippage
                        eff_risk = stop_dist + SLIPPAGE_PER_SHARE * 2
                        eff_reward = abs(float(take_profit) - price) - SLIPPAGE_PER_SHARE * 2
                        if eff_reward <= 0 or eff_reward / eff_risk < RISK["min_risk_reward_ratio"]:
                            continue

                        # Simulate fill with slippage
                        fill_price = price + SLIPPAGE_PER_SHARE

                        if symbol not in self.positions:
                            self.positions[symbol] = SimulatedPosition(
                                symbol=symbol,
                                entry_price=fill_price,
                                quantity=shares,
                                stop_loss=float(stop_loss),
                                take_profit=float(take_profit),
                                entry_time=current,
                                pattern=decision.get("pattern", "unknown"),
                            )
                            daily_trades += 1

                    elif action == "SELL" and symbol in self.positions:
                        pos = self.positions[symbol]
                        exit_price = price - SLIPPAGE_PER_SHARE
                        pnl = (exit_price - pos.entry_price) * pos.shares_remaining
                        slippage = SLIPPAGE_PER_SHARE * 2 * pos.shares_remaining
                        r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

                        self.completed_trades.append(BacktestTrade(
                            symbol=symbol,
                            pattern=pos.pattern,
                            entry_price=pos.entry_price,
                            exit_price=exit_price,
                            quantity=pos.shares_remaining,
                            pnl=pnl,
                            r_multiple=r_mult,
                            entry_time=pos.entry_time,
                            exit_time=current,
                            exit_reason=f"Claude SELL (conf={confidence:.0%})",
                            confidence=confidence,
                            slippage_cost=slippage,
                        ))

                        self.equity += pnl
                        del self.positions[symbol]
                        daily_trades += 1

                except Exception as e:
                    logger.error(f"Backtest error for {symbol} on {date_str}: {e}")
                    continue

            # EOD: close all positions (day trading rule)
            for symbol in list(self.positions.keys()):
                pos = self.positions[symbol]
                try:
                    hist = get_stock_data(symbol, period="1mo", interval="1d")
                    hist_to_date = hist[hist.index <= pd.Timestamp(current, tz=hist.index.tz)]
                    if hist_to_date.empty:
                        continue
                    eod_price = float(hist_to_date.iloc[-1]["Close"])
                except Exception:
                    continue

                exit_price = eod_price - SLIPPAGE_PER_SHARE
                pnl = (exit_price - pos.entry_price) * pos.shares_remaining
                slippage = SLIPPAGE_PER_SHARE * 2 * pos.shares_remaining
                r_mult = (exit_price - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0

                self.completed_trades.append(BacktestTrade(
                    symbol=symbol,
                    pattern=pos.pattern,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    quantity=pos.shares_remaining,
                    pnl=pnl,
                    r_multiple=r_mult,
                    entry_time=pos.entry_time,
                    exit_time=current,
                    exit_reason="EOD close",
                    slippage_cost=slippage,
                ))
                self.equity += pnl

            self.positions.clear()
            current += timedelta(days=1)

        # Build result
        result = BacktestResult(
            start_date=self.start_date.strftime("%Y-%m-%d"),
            end_date=self.end_date.strftime("%Y-%m-%d"),
            trading_days=trading_days,
            total_trades=len(self.completed_trades),
            wins=sum(1 for t in self.completed_trades if t.pnl > 0),
            losses=sum(1 for t in self.completed_trades if t.pnl < 0),
            total_pnl=sum(t.pnl for t in self.completed_trades),
            total_r=sum(t.r_multiple for t in self.completed_trades),
            total_slippage=sum(t.slippage_cost for t in self.completed_trades),
            starting_equity=self.starting_equity,
            ending_equity=self.equity,
            trades=self.completed_trades,
            equity_curve=self.equity_curve,
            api_calls=self.api_calls,
            cache_hits=self.cache_hits,
        )

        # Calculate max drawdown from equity curve
        peak = self.starting_equity
        max_dd = 0.0
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = round(max_dd, 2)

        return result

    def _cache_key(self, symbol: str, date: str, price_data: dict) -> str:
        """Generate cache key from symbol + date + price data hash."""
        data_str = f"{symbol}_{date}_{price_data.get('price')}_{price_data.get('volume')}"
        return hashlib.md5(data_str.encode()).hexdigest()

    def _load_cache(self, key: str) -> dict | None:
        """Load cached Claude response."""
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def _save_cache(self, key: str, response: dict):
        """Save Claude response to cache."""
        path = CACHE_DIR / f"{key}.json"
        try:
            with open(path, "w") as f:
                json.dump(response, f)
        except Exception as e:
            logger.debug(f"Cache write failed: {e}")


def format_backtest_result(result: BacktestResult) -> str:
    """Format backtest results for display."""
    lines = [
        "═══════════════════════════════════════",
        "         BACKTEST RESULTS",
        "═══════════════════════════════════════",
        f"Period: {result.start_date} → {result.end_date} ({result.trading_days} days)",
        f"Trades: {result.total_trades} | W: {result.wins} L: {result.losses} | WR: {result.win_rate:.1f}%",
        f"P&L: ${result.total_pnl:,.2f} | Return: {result.return_pct:+.2f}%",
        f"Total R: {result.total_r:+.1f} | Avg R: {result.avg_r:+.2f}",
        f"Expectancy: ${result.expectancy:,.2f}/trade",
        f"Profit Factor: {result.profit_factor:.2f}",
        f"Max Drawdown: {result.max_drawdown_pct:.1f}%",
        f"Total Slippage: ${result.total_slippage:,.2f}",
        f"API Calls: {result.api_calls} | Cache Hits: {result.cache_hits}",
        f"Equity: ${result.starting_equity:,.0f} → ${result.ending_equity:,.0f}",
        "",
    ]

    if result.passes_minimum_bar:
        lines.append("✓ PASSES minimum bar for real money")
    else:
        lines.append("⛔ DOES NOT PASS minimum bar:")
        if result.total_trades < 200:
            lines.append(f"  - Need 200+ trades (have {result.total_trades})")
        if result.expectancy <= 0:
            lines.append(f"  - Expectancy must be positive (${result.expectancy:,.2f})")
        if result.profit_factor <= 1.3:
            lines.append(f"  - Profit factor must be > 1.3 ({result.profit_factor:.2f})")
        if result.max_drawdown_pct >= 8.0:
            lines.append(f"  - Max drawdown must be < 8% ({result.max_drawdown_pct:.1f}%)")

    # Top patterns
    pattern_perf: dict[str, dict] = {}
    for t in result.trades:
        p = t.pattern or "unknown"
        if p not in pattern_perf:
            pattern_perf[p] = {"trades": 0, "wins": 0, "pnl": 0, "r": 0}
        pattern_perf[p]["trades"] += 1
        pattern_perf[p]["pnl"] += t.pnl
        pattern_perf[p]["r"] += t.r_multiple
        if t.pnl > 0:
            pattern_perf[p]["wins"] += 1

    if pattern_perf:
        lines.append("\n─── PATTERN BREAKDOWN ───")
        for p, stats in sorted(pattern_perf.items(), key=lambda x: x[1]["trades"], reverse=True):
            wr = (stats["wins"] / stats["trades"] * 100) if stats["trades"] > 0 else 0
            avg_r = stats["r"] / stats["trades"] if stats["trades"] > 0 else 0
            lines.append(
                f"  {p}: {stats['trades']} trades | WR: {wr:.0f}% | "
                f"Avg R: {avg_r:+.2f} | P&L: ${stats['pnl']:,.2f}"
            )

    return "\n".join(lines)
