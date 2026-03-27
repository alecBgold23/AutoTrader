"""Performance tracking and analytics.

Answers the critical question: does the system have an edge?
Breaks down performance by pattern, time phase, day of week,
and rolling windows to identify what works and what doesn't.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from autotrader.db.models import get_session, Trade, TradingJournal, PortfolioSnapshot

logger = logging.getLogger(__name__)


@dataclass
class PatternStats:
    """Performance stats for a single pattern type."""
    pattern: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_r: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0

    @property
    def avg_r(self) -> float:
        return self.total_r / self.trades if self.trades > 0 else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.trades if self.trades > 0 else 0.0


@dataclass
class PhaseStats:
    """Performance stats for a market phase."""
    phase: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_r: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0

    @property
    def avg_r(self) -> float:
        return self.total_r / self.trades if self.trades > 0 else 0.0


@dataclass
class OverallMetrics:
    """Complete performance summary."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_r: float = 0.0
    avg_r_per_trade: float = 0.0
    expectancy_per_trade: float = 0.0   # Avg $ per trade
    profit_factor: float = 0.0          # Gross wins / gross losses
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_hold_minutes: float = 0.0

    # Breakdowns
    by_pattern: list = field(default_factory=list)
    by_phase: list = field(default_factory=list)
    by_day_of_week: dict = field(default_factory=dict)
    rolling_20: dict = field(default_factory=dict)

    # Recommendations
    patterns_to_avoid: list = field(default_factory=list)
    patterns_validated: list = field(default_factory=list)


def calculate_metrics(days_back: int = 0) -> OverallMetrics:
    """Calculate all performance metrics from the trade database.

    Args:
        days_back: How many days to look back (0 = all time)

    Returns:
        OverallMetrics with full breakdown
    """
    session = get_session()
    try:
        query = session.query(Trade).filter(Trade.side == "BUY", Trade.pnl.isnot(None))
        if days_back > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
            query = query.filter(Trade.created_at >= cutoff)

        trades = query.order_by(Trade.created_at.asc()).all()

        if not trades:
            return OverallMetrics()

        metrics = OverallMetrics()
        metrics.total_trades = len(trades)

        gross_wins = 0.0
        gross_losses = 0.0
        pnls = []
        winners = []
        losers = []
        hold_times = []

        # Pattern and phase accumulators
        pattern_stats: dict[str, PatternStats] = defaultdict(lambda: PatternStats(pattern=""))
        phase_stats: dict[str, PhaseStats] = defaultdict(lambda: PhaseStats(phase=""))
        dow_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "r": 0.0})

        for trade in trades:
            pnl = trade.pnl or 0
            r_mult = trade.r_multiple or 0
            pattern = trade.pattern or "unknown"
            pnls.append(pnl)

            if pnl > 0:
                metrics.wins += 1
                gross_wins += pnl
                winners.append(pnl)
            elif pnl < 0:
                metrics.losses += 1
                gross_losses += abs(pnl)
                losers.append(pnl)

            metrics.total_pnl += pnl
            metrics.total_r += r_mult

            if trade.hold_time_minutes:
                hold_times.append(trade.hold_time_minutes)

            # Pattern breakdown
            ps = pattern_stats[pattern]
            ps.pattern = pattern
            ps.trades += 1
            ps.total_pnl += pnl
            ps.total_r += r_mult
            if pnl > 0:
                ps.wins += 1
            elif pnl < 0:
                ps.losses += 1

            # Phase breakdown (from associated decision)
            phase = _get_trade_phase(session, trade)
            if phase:
                phs = phase_stats[phase]
                phs.phase = phase
                phs.trades += 1
                phs.total_pnl += pnl
                phs.total_r += r_mult
                if pnl > 0:
                    phs.wins += 1
                elif pnl < 0:
                    phs.losses += 1

            # Day of week
            if trade.created_at:
                dow = trade.created_at.strftime("%A")
                dow_stats[dow]["trades"] += 1
                dow_stats[dow]["pnl"] += pnl
                dow_stats[dow]["r"] += r_mult
                if pnl > 0:
                    dow_stats[dow]["wins"] += 1

        # Calculate derived metrics
        metrics.win_rate = (metrics.wins / metrics.total_trades * 100) if metrics.total_trades > 0 else 0
        metrics.avg_r_per_trade = metrics.total_r / metrics.total_trades if metrics.total_trades > 0 else 0
        metrics.expectancy_per_trade = metrics.total_pnl / metrics.total_trades if metrics.total_trades > 0 else 0
        metrics.profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf") if gross_wins > 0 else 0
        metrics.avg_winner = sum(winners) / len(winners) if winners else 0
        metrics.avg_loser = sum(losers) / len(losers) if losers else 0
        metrics.largest_win = max(winners) if winners else 0
        metrics.largest_loss = min(losers) if losers else 0
        metrics.avg_hold_minutes = sum(hold_times) / len(hold_times) if hold_times else 0

        # Sharpe ratio (annualized, using daily returns from journal)
        metrics.sharpe_ratio = _calculate_sharpe(session, days_back)

        # Max drawdown from equity curve
        metrics.max_drawdown_pct = _calculate_max_drawdown(session, days_back)

        # Store breakdowns
        metrics.by_pattern = sorted(pattern_stats.values(), key=lambda x: x.trades, reverse=True)
        metrics.by_phase = sorted(phase_stats.values(), key=lambda x: x.trades, reverse=True)
        metrics.by_day_of_week = dict(dow_stats)

        # Rolling 20-trade window
        if len(pnls) >= 20:
            last_20 = trades[-20:]
            r20_wins = sum(1 for t in last_20 if (t.pnl or 0) > 0)
            r20_pnl = sum(t.pnl or 0 for t in last_20)
            r20_r = sum(t.r_multiple or 0 for t in last_20)
            metrics.rolling_20 = {
                "trades": 20,
                "wins": r20_wins,
                "win_rate": r20_wins / 20 * 100,
                "total_pnl": round(r20_pnl, 2),
                "avg_r": round(r20_r / 20, 2),
                "trend": "improving" if r20_pnl > metrics.expectancy_per_trade * 20 else "degrading",
            }

        # Recommendations: patterns to avoid and validated edges
        for ps in metrics.by_pattern:
            if ps.trades >= 10:  # Need minimum sample
                if ps.win_rate < 35 and ps.avg_r < 0:
                    metrics.patterns_to_avoid.append(ps.pattern)
                elif ps.win_rate > 55 and ps.avg_r > 0:
                    metrics.patterns_validated.append(ps.pattern)

        return metrics

    finally:
        session.close()


def format_metrics_for_log(metrics: OverallMetrics) -> str:
    """Format metrics as a readable log string."""
    if metrics.total_trades == 0:
        return "No trades to analyze yet."

    lines = [
        "═══ PERFORMANCE REPORT ═══",
        f"Total trades: {metrics.total_trades} | W: {metrics.wins} L: {metrics.losses} | Win rate: {metrics.win_rate:.1f}%",
        f"Total P&L: ${metrics.total_pnl:,.2f} | Expectancy: ${metrics.expectancy_per_trade:,.2f}/trade",
        f"Total R: {metrics.total_r:+.1f} | Avg R: {metrics.avg_r_per_trade:+.2f}/trade",
        f"Profit factor: {metrics.profit_factor:.2f} | Sharpe: {metrics.sharpe_ratio:.2f}",
        f"Max drawdown: {metrics.max_drawdown_pct:.1f}%",
        f"Avg winner: ${metrics.avg_winner:,.2f} | Avg loser: ${metrics.avg_loser:,.2f}",
        f"Largest win: ${metrics.largest_win:,.2f} | Largest loss: ${metrics.largest_loss:,.2f}",
    ]

    if metrics.avg_hold_minutes > 0:
        lines.append(f"Avg hold time: {metrics.avg_hold_minutes:.0f} min")

    if metrics.by_pattern:
        lines.append("\n─── BY PATTERN ───")
        for ps in metrics.by_pattern[:10]:
            tag = ""
            if ps.pattern in metrics.patterns_to_avoid:
                tag = " ⛔ AVOID"
            elif ps.pattern in metrics.patterns_validated:
                tag = " ✓ EDGE"
            lines.append(
                f"  {ps.pattern}: {ps.trades} trades | "
                f"WR: {ps.win_rate:.0f}% | Avg R: {ps.avg_r:+.2f} | "
                f"P&L: ${ps.total_pnl:,.2f}{tag}"
            )

    if metrics.by_phase:
        lines.append("\n─── BY PHASE ───")
        for phs in metrics.by_phase:
            lines.append(
                f"  {phs.phase}: {phs.trades} trades | "
                f"WR: {phs.win_rate:.0f}% | Avg R: {phs.avg_r:+.2f} | "
                f"P&L: ${phs.total_pnl:,.2f}"
            )

    if metrics.by_day_of_week:
        lines.append("\n─── BY DAY ───")
        for dow in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            if dow in metrics.by_day_of_week:
                d = metrics.by_day_of_week[dow]
                wr = (d["wins"] / d["trades"] * 100) if d["trades"] > 0 else 0
                lines.append(
                    f"  {dow}: {d['trades']} trades | WR: {wr:.0f}% | "
                    f"P&L: ${d['pnl']:,.2f}"
                )

    if metrics.rolling_20:
        r = metrics.rolling_20
        lines.append(
            f"\n─── LAST 20 TRADES ({r['trend'].upper()}) ───\n"
            f"  WR: {r['win_rate']:.0f}% | Avg R: {r['avg_r']:+.2f} | P&L: ${r['total_pnl']:,.2f}"
        )

    if metrics.patterns_to_avoid:
        lines.append(f"\n⛔ PATTERNS TO REMOVE: {', '.join(metrics.patterns_to_avoid)}")
    if metrics.patterns_validated:
        lines.append(f"✓ VALIDATED EDGES: {', '.join(metrics.patterns_validated)}")

    return "\n".join(lines)


def format_metrics_for_telegram(metrics: OverallMetrics) -> str:
    """Format metrics for Telegram weekly report."""
    if metrics.total_trades == 0:
        return "*Weekly Report*\nNo trades this period."

    lines = [
        "*Weekly Performance Report*",
        f"Trades: {metrics.total_trades} | WR: {metrics.win_rate:.0f}%",
        f"P&L: ${metrics.total_pnl:,.2f} | R: {metrics.total_r:+.1f}",
        f"Expectancy: ${metrics.expectancy_per_trade:,.2f}/trade",
        f"Profit Factor: {metrics.profit_factor:.2f}",
        f"Max DD: {metrics.max_drawdown_pct:.1f}%",
    ]

    if metrics.rolling_20:
        r = metrics.rolling_20
        lines.append(f"\nLast 20: WR {r['win_rate']:.0f}% | R {r['avg_r']:+.2f} ({r['trend']})")

    if metrics.patterns_validated:
        lines.append(f"\nBest: {', '.join(metrics.patterns_validated)}")
    if metrics.patterns_to_avoid:
        lines.append(f"Worst: {', '.join(metrics.patterns_to_avoid)}")

    return "\n".join(lines)


# ── Private helpers ───────────────────────────────────────


def _get_trade_phase(session, trade: Trade) -> str | None:
    """Look up the market phase for a trade from its associated decision."""
    from autotrader.db.models import Decision
    decision = (
        session.query(Decision)
        .filter(
            Decision.symbol == trade.symbol,
            Decision.action == "BUY",
            Decision.created_at <= trade.created_at,
        )
        .order_by(Decision.created_at.desc())
        .first()
    )
    return decision.market_phase if decision else None


def _calculate_sharpe(session, days_back: int = 0) -> float:
    """Calculate annualized Sharpe ratio from daily P&L in journal."""
    query = session.query(TradingJournal.total_pnl)
    if days_back > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        query = query.filter(TradingJournal.created_at >= cutoff)

    daily_pnls = [row[0] for row in query.all() if row[0] is not None]

    if len(daily_pnls) < 5:
        return 0.0

    mean_pnl = sum(daily_pnls) / len(daily_pnls)
    variance = sum((p - mean_pnl) ** 2 for p in daily_pnls) / len(daily_pnls)
    std_pnl = math.sqrt(variance) if variance > 0 else 0

    if std_pnl == 0:
        return 0.0

    # Annualize: ~252 trading days
    return (mean_pnl / std_pnl) * math.sqrt(252)


def _calculate_max_drawdown(session, days_back: int = 0) -> float:
    """Calculate max drawdown % from portfolio snapshots."""
    query = session.query(PortfolioSnapshot.total_equity).order_by(PortfolioSnapshot.created_at.asc())
    if days_back > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        query = query.filter(PortfolioSnapshot.created_at >= cutoff)

    equities = [row[0] for row in query.all() if row[0] and row[0] > 0]

    if len(equities) < 2:
        return 0.0

    peak = equities[0]
    max_dd = 0.0

    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return round(max_dd, 2)
