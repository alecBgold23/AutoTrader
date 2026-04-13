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
        default=75,
        help="Max analysis cycles per day (default: 75)",
    )
    parser.add_argument("--equity", type=float, default=100_000, help="Starting equity (default: 100000)")
    parser.add_argument("--max-trades", type=int, default=25, help="Max trades per day (default: 25)")
    parser.add_argument(
        "--claude",
        action="store_true",
        help="Use Claude API for decisions instead of deterministic engine (legacy mode)",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="Run walk-forward parameter optimization",
    )

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

    deterministic = not args.claude
    mode_str = "Claude API" if args.claude else "Deterministic Engine"

    print(f"\n{'='*60}")
    print(f"  AutoTrader Intraday Backtest (5-minute bars)")
    print(f"  {args.start} → {args.end}")
    print(f"  Mode: {mode_str}")
    if args.claude:
        print(f"  Model: {args.model}")
    print(f"  Cycles/day: {args.max_cycles_per_day}")
    print(f"  Starting equity: ${args.equity:,.0f}")
    print(f"{'='*60}\n")

    # Walk-forward optimization mode
    if args.optimize:
        from autotrader.backtest.optimizer import WalkForwardOptimizer
        optimizer = WalkForwardOptimizer(
            full_start=args.start,
            full_end=args.end,
            n_windows=4,
        )
        optimizer.run()
        return

    engine = BacktestEngine(
        start=args.start,
        end=args.end,
        starting_equity=args.equity,
        model=args.model,
        max_cycles_per_day=args.max_cycles_per_day,
        max_trades_per_day=args.max_trades,
        deterministic=deterministic,
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
        "mode": mode_str,
        "model": args.model if args.claude else "deterministic",
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
        "total_costs": round(result.total_costs, 2),
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
                "trading_costs": round(t.trading_costs, 2),
                "mae": round(t.mae, 4),
                "mfe": round(t.mfe, 4),
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
