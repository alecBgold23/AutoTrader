"""Batch data pre-fetcher for comprehensive backtesting.

Downloads and caches all market data needed to run backtests across
any date range without hitting API rate limits during the actual backtest.

Usage:
    python -m autotrader.backtest.prefetch --start 2018-01-01 --end 2025-03-28

This fetches:
1. Daily bars for the FULL Alpaca universe (no 800-stock cap)
2. Builds broad universe for each year
3. Runs the scanner for each trading day to identify hot-list symbols
4. Fetches 5m bars (Alpaca) only for scanner-selected symbols

Once cached, backtests run in seconds with zero API calls.
"""

import argparse
import json
import logging
import sys
import time as time_module
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from autotrader.config import BASE_DIR, SCANNER

logger = logging.getLogger(__name__)
CACHE_DIR = BASE_DIR / "data" / "backtest_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_all_tradeable_symbols() -> list[str]:
    """Get all tradeable US equity symbols from Alpaca."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus
    from autotrader.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER

    client = TradingClient(
        api_key=ALPACA_API_KEY,
        secret_key=ALPACA_SECRET_KEY,
        paper=ALPACA_PAPER,
    )
    request = GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY,
        status=AssetStatus.ACTIVE,
    )
    all_assets = client.get_all_assets(request)
    symbols = [
        a.symbol for a in all_assets
        if a.tradable
        and a.exchange in ("NASDAQ", "NYSE", "ARCA", "AMEX", "BATS")
        and not any(c in a.symbol for c in "./-")
        and len(a.symbol) <= 5
    ]
    return sorted(symbols)


def fetch_daily_batch(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Batch-download daily bars using yf.download(). Much faster than individual calls.

    Checks cache first, only downloads uncached symbols.
    """
    result = {}
    uncached = []

    start_key = start.replace("-", "")
    end_key = end.replace("-", "")

    for sym in symbols:
        cache_file = CACHE_DIR / f"{sym}_{start_key}_{end_key}_1d.csv"
        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if not df.empty:
                    result[sym] = df
                    continue
            except Exception:
                pass
        uncached.append(sym)

    if not uncached:
        return result

    print(f"  Downloading daily bars for {len(uncached)} uncached symbols...")

    # Need lookback for SMA(200)
    start_dt = datetime.strptime(start, "%Y-%m-%d") - timedelta(days=250)
    end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=5)

    batch_size = 100  # yfinance handles ~100 at a time well
    for i in range(0, len(uncached), batch_size):
        batch = uncached[i:i + batch_size]
        try:
            data = yf.download(
                tickers=batch,
                start=start_dt.strftime("%Y-%m-%d"),
                end=end_dt.strftime("%Y-%m-%d"),
                interval="1d",
                progress=False,
                threads=True,
            )
            if data.empty:
                continue

            for sym in batch:
                try:
                    if len(batch) == 1:
                        close = data["Close"]
                        df = data[["Open", "High", "Low", "Close", "Volume"]]
                    else:
                        if sym not in data["Close"].columns:
                            continue
                        df = pd.DataFrame({
                            "Open": data["Open"][sym],
                            "High": data["High"][sym],
                            "Low": data["Low"][sym],
                            "Close": data["Close"][sym],
                            "Volume": data["Volume"][sym],
                        })
                    df = df.dropna()
                    if df.empty or len(df) < 5:
                        continue

                    # Cache it
                    cache_file = CACHE_DIR / f"{sym}_{start_key}_{end_key}_1d.csv"
                    df.to_csv(cache_file)
                    result[sym] = df
                except Exception:
                    continue
        except Exception as e:
            print(f"    Batch {i//batch_size + 1} failed: {e}")
            time_module.sleep(2)
            continue

        downloaded = len(result) - (len(symbols) - len(uncached))
        if (i // batch_size) % 5 == 0 and i > 0:
            print(f"    {i}/{len(uncached)} downloaded, {downloaded} successful...")

        # Small delay between batches to avoid rate limiting
        time_module.sleep(0.5)

    return result


def filter_universe(daily_bars: dict[str, pd.DataFrame], start: str, end: str) -> list[str]:
    """Filter to liquid stocks — NO CAP on universe size."""
    universe = []
    for sym, df in daily_bars.items():
        if df.empty or len(df) < 20:
            continue
        try:
            price = float(df["Close"].iloc[-1])
            avg_vol = float(df["Volume"].iloc[-20:].mean())
            if SCANNER["min_price"] <= price <= SCANNER["max_price"] and avg_vol >= SCANNER["min_avg_volume"]:
                universe.append(sym)
        except Exception:
            continue
    return sorted(universe)


def fetch_5m_bars_alpaca(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch 5-minute bars from Alpaca. Cached."""
    from autotrader.backtest.data_fetcher import fetch_5m_bars
    return fetch_5m_bars(symbol, start, end)


def prefetch_range(start: str, end: str):
    """Pre-fetch all data for a date range."""
    from autotrader.backtest.data_fetcher import (
        fetch_spy_daily, fetch_vix_daily, get_trading_days,
    )

    print(f"\n{'='*60}")
    print(f"PRE-FETCHING: {start} → {end}")
    print(f"{'='*60}")

    # 1. Get all symbols
    all_symbols = get_all_tradeable_symbols()
    print(f"Total tradeable symbols: {len(all_symbols)}")

    # 2. Download daily bars (batch — fast)
    daily_bars = fetch_daily_batch(all_symbols, start, end)
    print(f"Daily data loaded: {len(daily_bars)} symbols")

    # 3. Filter to liquid universe (NO CAP)
    universe = filter_universe(daily_bars, start, end)
    print(f"Liquid universe (no cap): {len(universe)} symbols")

    # Save universe cache
    universe_file = CACHE_DIR / f"broad_universe_{start}_{end}.json"
    with open(universe_file, "w") as f:
        json.dump(universe, f)

    # 4. SPY/VIX
    spy_daily = fetch_spy_daily(start, end)
    vix_daily = fetch_vix_daily(start, end)
    print(f"SPY: {len(spy_daily)} bars, VIX: {len(vix_daily)} bars")

    # 5. Get trading days
    trading_days = get_trading_days(start, end)
    print(f"Trading days: {len(trading_days)}")

    # 6. Run scanner for each day → build hot lists → fetch 5m for hot symbols
    from autotrader.backtest.engine import BacktestEngine, _tz_aware_timestamp

    # We need scanner logic. Create a temporary engine just for scanning.
    engine = BacktestEngine(start=start, end=end, deterministic=True)

    # Override the universe with our full one
    all_hot_symbols = set()
    scan_count = 0

    print(f"Scanning each trading day for movers...")
    for day_dt in trading_days:
        hot_list = engine._scan_for_day(day_dt, daily_bars)
        all_hot_symbols.update(hot_list)
        scan_count += 1
        if scan_count % 50 == 0:
            print(f"  Day {scan_count}/{len(trading_days)}: {len(all_hot_symbols)} unique symbols selected so far")

    print(f"Scanner selected {len(all_hot_symbols)} unique symbols across {len(trading_days)} days")

    # 7. Fetch 5m bars for all hot symbols
    print(f"Fetching 5m bars for {len(all_hot_symbols)} symbols...")
    fetched_5m = 0
    for i, sym in enumerate(sorted(all_hot_symbols)):
        cache_file = CACHE_DIR / f"{sym}_{start.replace('-', '')}_{end.replace('-', '')}_5m.csv"
        if cache_file.exists():
            fetched_5m += 1
            continue
        try:
            df = fetch_5m_bars_alpaca(sym, start, end)
            if not df.empty:
                fetched_5m += 1
        except Exception as e:
            pass

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(all_hot_symbols)} processed, {fetched_5m} with data")
        time_module.sleep(0.2)  # Gentle rate limiting

    print(f"5m data: {fetched_5m}/{len(all_hot_symbols)} symbols cached")
    print(f"{'='*60}")
    print(f"DONE: {start} → {end}")
    print(f"  Universe: {len(universe)} liquid stocks")
    print(f"  Hot symbols: {len(all_hot_symbols)}")
    print(f"  5m data: {fetched_5m} symbols")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Pre-fetch market data for backtesting")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--chunk-years", type=int, default=1,
        help="Split into N-year chunks (default: 1)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for lib in ("httpx", "httpcore", "urllib3", "yfinance", "alpaca"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    # Split into yearly chunks
    current = start_dt
    while current < end_dt:
        chunk_end = min(current + timedelta(days=365), end_dt)
        prefetch_range(
            current.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d"),
        )
        current = chunk_end + timedelta(days=1)


if __name__ == "__main__":
    main()
