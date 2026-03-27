"""CLI runner for backtests.

Usage:
    python -m autotrader.backtest.runner --start 2025-01-02 --end 2025-03-01
    python -m autotrader.backtest.runner --start 2025-01-02 --end 2025-03-01 --model claude-haiku-4-5-20251001
    python -m autotrader.backtest.runner --start 2025-02-01 --end 2025-02-28 --symbols AAPL,NVDA,TSLA,AMD,META
"""

import argparse
import logging
import sys

from autotrader.backtest.engine import BacktestEngine, format_backtest_result


def main():
    parser = argparse.ArgumentParser(description="AutoTrader Backtester")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--symbols",
        default="AAPL,NVDA,TSLA,AMD,META,MSFT,GOOGL,AMZN,JPM,BA",
        help="Comma-separated symbols (default: top 10 liquid names)",
    )
    parser.add_argument("--model", default="", help="Override Claude model (e.g., claude-haiku-4-5-20251001)")
    parser.add_argument("--equity", type=float, default=100_000, help="Starting equity (default: 100000)")
    parser.add_argument("--max-trades", type=int, default=8, help="Max trades per day (default: 8)")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for lib in ("httpx", "httpcore", "urllib3", "yfinance"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    print(f"\n{'='*50}")
    print(f"  AutoTrader Backtest")
    print(f"  {args.start} → {args.end}")
    print(f"  Symbols: {', '.join(symbols)}")
    print(f"  Model: {args.model or 'default'}")
    print(f"  Starting equity: ${args.equity:,.0f}")
    print(f"{'='*50}\n")

    engine = BacktestEngine(
        start=args.start,
        end=args.end,
        starting_equity=args.equity,
        model=args.model,
        max_trades_per_day=args.max_trades,
    )

    result = engine.run(symbols)

    print("\n" + format_backtest_result(result))

    # Save results
    import json
    from autotrader.config import BASE_DIR
    from datetime import datetime

    results_dir = BASE_DIR / "data" / "backtest_results"
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = results_dir / f"backtest_{timestamp}.json"

    summary = {
        "start_date": result.start_date,
        "end_date": result.end_date,
        "symbols": symbols,
        "model": args.model or "default",
        "trading_days": result.trading_days,
        "total_trades": result.total_trades,
        "wins": result.wins,
        "losses": result.losses,
        "win_rate": result.win_rate,
        "total_pnl": round(result.total_pnl, 2),
        "total_r": round(result.total_r, 2),
        "avg_r": round(result.avg_r, 2),
        "expectancy": round(result.expectancy, 2),
        "profit_factor": round(result.profit_factor, 2),
        "max_drawdown_pct": result.max_drawdown_pct,
        "return_pct": round(result.return_pct, 2),
        "total_slippage": round(result.total_slippage, 2),
        "api_calls": result.api_calls,
        "cache_hits": result.cache_hits,
        "passes_minimum_bar": result.passes_minimum_bar,
        "equity_curve": result.equity_curve,
        "trades": [
            {
                "symbol": t.symbol,
                "pattern": t.pattern,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "quantity": t.quantity,
                "pnl": round(t.pnl, 2),
                "r_multiple": round(t.r_multiple, 2),
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "exit_reason": t.exit_reason,
            }
            for t in result.trades
        ],
    }

    with open(result_file, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nResults saved to: {result_file}")


if __name__ == "__main__":
    main()
