"""CLI runner for intraday backtests.

Usage:
    python -m autotrader.backtest.runner --start 2025-01-02 --end 2025-03-28 --model claude-haiku-4-5-20251001 --max-cycles-per-day 6
    python -m autotrader.backtest.runner --start 2025-02-01 --end 2025-02-28 --model claude-sonnet-4-20250514
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from autotrader.backtest.engine import BacktestEngine, format_backtest_result
from autotrader.config import BASE_DIR


def main():
    parser = argparse.ArgumentParser(description="AutoTrader Intraday Backtester (5-min bars)")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model to use (default: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--max-cycles-per-day",
        type=int,
        default=14,
        help="Max analysis cycles per day (default: 14)",
    )
    parser.add_argument("--equity", type=float, default=100_000, help="Starting equity (default: 100000)")
    parser.add_argument("--max-trades", type=int, default=25, help="Max trades per day (default: 25)")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for lib in ("httpx", "httpcore", "urllib3", "yfinance", "alpaca"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    print(f"\n{'='*60}")
    print(f"  AutoTrader Intraday Backtest (5-minute bars)")
    print(f"  {args.start} → {args.end}")
    print(f"  Model: {args.model}")
    print(f"  Cycles/day: {args.max_cycles_per_day}")
    print(f"  Starting equity: ${args.equity:,.0f}")
    print(f"{'='*60}\n")

    engine = BacktestEngine(
        start=args.start,
        end=args.end,
        starting_equity=args.equity,
        model=args.model,
        max_cycles_per_day=args.max_cycles_per_day,
        max_trades_per_day=args.max_trades,
    )

    result = engine.run()

    # Print results
    print(format_backtest_result(result))

    # Save results
    results_dir = BASE_DIR / "data" / "backtest_results"
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save equity curve as CSV
    equity_file = results_dir / f"equity_{timestamp}.csv"
    with open(equity_file, "w") as f:
        f.write("date,equity\n")
        for date, eq in result.equity_curve:
            f.write(f"{date},{eq:.2f}\n")

    # Save full results as JSON
    result_file = results_dir / f"backtest_{timestamp}.json"
    summary = {
        "start_date": result.start_date,
        "end_date": result.end_date,
        "model": args.model,
        "max_cycles_per_day": args.max_cycles_per_day,
        "trading_days": result.trading_days,
        "total_trades": result.total_trades,
        "wins": result.wins,
        "losses": result.losses,
        "win_rate": round(result.win_rate, 2),
        "total_pnl": round(result.total_pnl, 2),
        "total_r": round(result.total_r, 2),
        "avg_r": round(result.avg_r, 2),
        "expectancy": round(result.expectancy, 2),
        "profit_factor": round(result.profit_factor, 2),
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "return_pct": round(result.return_pct, 2),
        "avg_win": round(result.avg_win, 2),
        "avg_loss": round(result.avg_loss, 2),
        "total_slippage": round(result.total_slippage, 2),
        "api_calls": result.api_calls,
        "cache_hits": result.cache_hits,
        "passes_minimum_bar": result.passes_minimum_bar,
        "starting_equity": result.starting_equity,
        "ending_equity": round(result.ending_equity, 2),
        "equity_curve": result.equity_curve,
        "trades": [
            {
                "symbol": t.symbol,
                "pattern": t.pattern,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "quantity": t.quantity,
                "pnl": round(t.pnl, 2),
                "r_multiple": round(t.r_multiple, 2),
                "entry_time": t.entry_time.isoformat() if hasattr(t.entry_time, 'isoformat') else str(t.entry_time),
                "exit_time": t.exit_time.isoformat() if hasattr(t.exit_time, 'isoformat') else str(t.exit_time),
                "exit_reason": t.exit_reason,
                "confidence": round(t.confidence, 2),
                "market_phase": t.market_phase,
            }
            for t in result.trades
        ],
    }

    with open(result_file, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nEquity curve: {equity_file}")
    print(f"Full results: {result_file}")


if __name__ == "__main__":
    main()
