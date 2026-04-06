"""Fetch and cache 5-minute historical bars from Alpaca for backtesting.

Caches locally as CSV files in data/backtest_cache/ so repeated runs
don't re-download. Alpaca allows up to 30 days per request per symbol.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from autotrader.config import BASE_DIR, ALPACA_API_KEY, ALPACA_SECRET_KEY

logger = logging.getLogger(__name__)

CACHE_DIR = BASE_DIR / "data" / "backtest_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _read_cached_csv(path: Path) -> pd.DataFrame:
    """Read a cached CSV with proper datetime index handling."""
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def _find_cached_file(symbol: str, start_date: str, end_date: str, interval: str = "1d") -> Path | None:
    """Find any cached file for this symbol that covers the requested date range.

    Looks for exact match first, then searches for files whose cached range
    contains the requested range. Returns the best match (smallest covering range).
    """
    # Exact match first
    exact = CACHE_DIR / f"{symbol}_{start_date.replace('-', '')}_{end_date.replace('-', '')}_{interval}.csv"
    if exact.exists():
        return exact

    # Search for any file that covers the requested range
    import re
    pattern = re.compile(rf"^{re.escape(symbol)}_(\d{{8}})_(\d{{8}})_{re.escape(interval)}\.csv$")
    req_start = int(start_date.replace("-", ""))
    req_end = int(end_date.replace("-", ""))

    best = None
    best_span = float("inf")
    for f in CACHE_DIR.iterdir():
        m = pattern.match(f.name)
        if not m:
            continue
        cached_start = int(m.group(1))
        cached_end = int(m.group(2))
        # Cached range must contain the requested range (with some tolerance for daily data)
        if cached_start <= req_start and cached_end >= req_end:
            span = cached_end - cached_start
            if span < best_span:
                best = f
                best_span = span
    return best


def _get_alpaca_data_client():
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(
        api_key=ALPACA_API_KEY,
        secret_key=ALPACA_SECRET_KEY,
    )


def _cache_path(symbol: str, start: str, end: str) -> Path:
    """Path for a cached CSV file: data/backtest_cache/AAPL_20250102_20250201_5m.csv"""
    return CACHE_DIR / f"{symbol}_{start.replace('-', '')}_{end.replace('-', '')}_5m.csv"


def fetch_5m_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch 5-minute bars for a symbol over a date range.

    Checks cache first. If not cached, downloads from Alpaca in 30-day chunks.

    Returns DataFrame with columns: Open, High, Low, Close, Volume
    Index is timezone-aware datetime.
    """
    cache_file = _cache_path(symbol, start_date, end_date)

    if not force_refresh:
        # Try exact match first, then fuzzy search
        cached = _find_cached_file(symbol, start_date, end_date, "5m") if not cache_file.exists() else cache_file
        if cached and cached.exists():
            try:
                df = _read_cached_csv(cached)
                if not df.empty:
                    logger.debug(f"Cache hit: {symbol} {start_date} to {end_date} ({len(df)} bars)")
                    return df
            except Exception:
                pass  # Re-download if cache is corrupt

    # Download from Alpaca in 30-day chunks
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    client = _get_alpaca_data_client()
    tf = TimeFrame(5, TimeFrameUnit.Minute)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=timezone.utc)

    all_frames = []
    chunk_start = start_dt
    chunk_size = timedelta(days=28)  # Stay under 30-day limit

    while chunk_start < end_dt:
        chunk_end = min(chunk_start + chunk_size, end_dt)
        try:
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=chunk_start,
                end=chunk_end,
            )
            bars = client.get_stock_bars(request)
            df = bars.df

            if not df.empty:
                # Handle multi-index (symbol, timestamp)
                if isinstance(df.index, pd.MultiIndex):
                    if symbol in df.index.get_level_values(0):
                        df = df.loc[symbol]
                    else:
                        chunk_start = chunk_end
                        continue

                # Normalize column names
                col_map = {}
                for col in df.columns:
                    cl = col.lower() if isinstance(col, str) else str(col).lower()
                    if "open" in cl:
                        col_map[col] = "Open"
                    elif "high" in cl:
                        col_map[col] = "High"
                    elif "low" in cl:
                        col_map[col] = "Low"
                    elif "close" in cl:
                        col_map[col] = "Close"
                    elif "volume" in cl:
                        col_map[col] = "Volume"
                df = df.rename(columns=col_map)
                keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
                df = df[keep]
                all_frames.append(df)

        except Exception as e:
            logger.warning(f"Alpaca 5m fetch failed for {symbol} {chunk_start.date()}-{chunk_end.date()}: {e}")

        chunk_start = chunk_end

    if not all_frames:
        logger.warning(f"No 5m data for {symbol} from {start_date} to {end_date}")
        return pd.DataFrame()

    result = pd.concat(all_frames)
    result = result[~result.index.duplicated(keep="first")]
    result = result.sort_index()

    # Save to cache
    try:
        result.to_csv(cache_file)
        logger.info(f"Cached {symbol}: {len(result)} bars ({start_date} to {end_date})")
    except Exception as e:
        logger.warning(f"Failed to cache {symbol}: {e}")

    return result


def fetch_daily_bars(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch daily bars for indicator calculation (needs 3mo lookback before start).

    Uses yfinance for simplicity (free, reliable for daily data).
    Searches cache fuzzy — any cached file covering the requested range works.
    """
    # Fuzzy cache lookup — finds any file covering the requested range
    cached = _find_cached_file(symbol, start_date, end_date, "1d")
    if cached:
        try:
            df = _read_cached_csv(cached)
            if not df.empty:
                return df
        except Exception:
            pass

    import yfinance as yf

    # Need ~200 days lookback for SMA(200)
    start_dt = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=250)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=5)

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start_dt.strftime("%Y-%m-%d"), end=end_dt.strftime("%Y-%m-%d"), interval="1d")
        if df.empty:
            return pd.DataFrame()
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        try:
            df.to_csv(cache_file)
        except Exception:
            pass
        return df
    except Exception as e:
        logger.warning(f"Failed to fetch daily data for {symbol}: {e}")
        return pd.DataFrame()


def fetch_daily_bars_batch(symbols: list[str], start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
    """Batch-fetch daily bars for many symbols using yf.download().

    Much more rate-limit friendly than individual Ticker.history() calls.
    Checks cache first, only downloads uncached symbols.
    """
    import yfinance as yf

    result = {}
    uncached = []

    # Check cache first (fuzzy — any file covering the range works)
    for sym in symbols:
        cached = _find_cached_file(sym, start_date, end_date, "1d")
        if cached:
            try:
                df = _read_cached_csv(cached)
                if not df.empty:
                    result[sym] = df
                    continue
            except Exception:
                pass
        uncached.append(sym)

    if not uncached:
        return result

    logger.info(f"Batch downloading daily bars for {len(uncached)} uncached symbols...")

    start_dt = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=250)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=5)

    # Download in chunks of 100 to avoid timeouts
    chunk_size = 100
    for i in range(0, len(uncached), chunk_size):
        chunk = uncached[i:i + chunk_size]
        try:
            data = yf.download(
                chunk,
                start=start_dt.strftime("%Y-%m-%d"),
                end=end_dt.strftime("%Y-%m-%d"),
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
            if data.empty:
                continue

            for sym in chunk:
                try:
                    if len(chunk) == 1:
                        df = data[["Open", "High", "Low", "Close", "Volume"]].copy()
                    else:
                        df = data[sym][["Open", "High", "Low", "Close", "Volume"]].copy()
                    df = df.dropna(how="all")
                    if df.empty:
                        continue
                    # Cache it
                    cache_file = CACHE_DIR / f"{sym}_{start_date.replace('-', '')}_{end_date.replace('-', '')}_1d.csv"
                    try:
                        df.to_csv(cache_file)
                    except Exception:
                        pass
                    result[sym] = df
                except Exception:
                    pass

            logger.info(f"  Batch {i // chunk_size + 1}: downloaded {len(chunk)} symbols")
        except Exception as e:
            logger.warning(f"Batch download failed for chunk {i}: {e}")

    return result


def get_trading_days(start_date: str, end_date: str) -> list[datetime]:
    """Get list of trading days in range using SPY data as reference."""
    # Try cached SPY daily first to avoid yfinance rate limits
    spy_df = fetch_spy_daily(start_date, end_date)
    if not spy_df.empty:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        days = []
        for d in spy_df.index:
            dt = d.to_pydatetime() if hasattr(d, 'to_pydatetime') else d
            # Strip timezone for comparison
            dt_naive = dt.replace(tzinfo=None) if hasattr(dt, 'replace') and dt.tzinfo else dt
            if start_dt <= dt_naive <= end_dt:
                days.append(dt)
        if days:
            return days

    import yfinance as yf
    spy = yf.Ticker("SPY")
    hist = spy.history(start=start_date, end=end_date, interval="1d")
    if hist.empty:
        return []
    return [d.to_pydatetime() for d in hist.index]


def fetch_spy_daily(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch SPY daily data for regime detection."""
    return fetch_daily_bars("SPY", start_date, end_date)


def fetch_vix_daily(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch VIX daily data for regime detection."""
    import yfinance as yf
    # Fuzzy cache lookup
    cached = _find_cached_file("VIX", start_date, end_date, "1d")
    if cached:
        try:
            return _read_cached_csv(cached)
        except Exception:
            pass

    start_dt = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=100)
    try:
        ticker = yf.Ticker("^VIX")
        df = ticker.history(start=start_dt.strftime("%Y-%m-%d"), end=end_date, interval="1d")
        if df.empty:
            return pd.DataFrame()
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        try:
            df.to_csv(cache_file)
        except Exception:
            pass
        return df
    except Exception:
        return pd.DataFrame()
