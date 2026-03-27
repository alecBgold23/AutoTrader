"""Market data fetching — Alpaca for real-time, yfinance for bulk historical.

Data source strategy:
- Real-time prices (get_current_price): Alpaca snapshots — no delay, broker-native
- Intraday bars (get_intraday_data): Alpaca bars — consistent, no rate limits
- Historical daily (get_stock_data): yfinance — free, good for 3mo+ lookbacks
- Batch historical (get_batch_prices): yfinance — efficient for 800+ stock scans
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockSnapshotRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.common.exceptions import APIError

from autotrader.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

logger = logging.getLogger(__name__)

# Alpaca data client — initialized once, reused for all real-time calls
_alpaca_data: StockHistoricalDataClient | None = None


def _get_alpaca_data_client() -> StockHistoricalDataClient:
    """Lazy-init Alpaca data client."""
    global _alpaca_data
    if _alpaca_data is None:
        _alpaca_data = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
    return _alpaca_data


# ── Real-Time (Alpaca) ────────────────────────────────────


def get_current_price(symbol: str) -> dict | None:
    """Get current price via Alpaca snapshot (real-time, not delayed).

    Falls back to yfinance if Alpaca fails (e.g., for non-Alpaca symbols like ^VIX).
    """
    try:
        client = _get_alpaca_data_client()
        request = StockSnapshotRequest(symbol_or_symbols=symbol)
        snapshot = client.get_stock_snapshot(request)
        snap = snapshot[symbol]

        latest_price = float(snap.latest_trade.price)
        daily = snap.daily_bar
        prev = snap.previous_daily_bar

        change = latest_price - float(prev.close) if prev else 0
        change_pct = (change / float(prev.close) * 100) if prev and float(prev.close) > 0 else 0

        return {
            "symbol": symbol,
            "price": round(latest_price, 2),
            "open": round(float(daily.open), 2),
            "high": round(float(daily.high), 2),
            "low": round(float(daily.low), 2),
            "volume": int(daily.volume),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "prev_close": round(float(prev.close), 2) if prev else 0,
        }
    except (APIError, KeyError) as e:
        logger.debug(f"Alpaca snapshot failed for {symbol}: {e} — trying yfinance")
        return _get_current_price_yfinance(symbol)
    except Exception as e:
        logger.debug(f"Alpaca snapshot error for {symbol}: {e} — trying yfinance")
        return _get_current_price_yfinance(symbol)


def _get_current_price_yfinance(symbol: str) -> dict | None:
    """Fallback: get price via yfinance (for ^VIX and other non-Alpaca symbols)."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d", interval="1d")

        if hist.empty:
            return None

        current = hist.iloc[-1]
        prev_close = hist.iloc[-2]["Close"] if len(hist) > 1 else current["Open"]
        price = current["Close"]
        change = price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0

        return {
            "symbol": symbol,
            "price": round(price, 2),
            "open": round(current["Open"], 2),
            "high": round(current["High"], 2),
            "low": round(current["Low"], 2),
            "volume": int(current["Volume"]),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev_close, 2),
        }
    except Exception as e:
        logger.error(f"yfinance price fetch also failed for {symbol}: {e}")
        return None


def get_intraday_data(symbol: str, interval: str = "5m") -> pd.DataFrame:
    """Get intraday bars via Alpaca (real-time, consistent).

    Falls back to yfinance if Alpaca fails.
    """
    try:
        client = _get_alpaca_data_client()

        if "1m" in interval:
            tf = TimeFrame(1, TimeFrameUnit.Minute)
        elif "15m" in interval:
            tf = TimeFrame(15, TimeFrameUnit.Minute)
        else:
            tf = TimeFrame(5, TimeFrameUnit.Minute)

        now = datetime.now(timezone.utc)
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=now - timedelta(days=2),
            end=now,
        )
        bars = client.get_stock_bars(request)
        df = bars.df

        if df.empty:
            logger.debug(f"No Alpaca intraday data for {symbol} — trying yfinance")
            return _get_intraday_yfinance(symbol, interval)

        # Alpaca returns multi-index (symbol, timestamp) — select our symbol
        if isinstance(df.index, pd.MultiIndex):
            if symbol in df.index.get_level_values(0):
                df = df.loc[symbol]
            else:
                return _get_intraday_yfinance(symbol, interval)

        # Rename to match yfinance convention used by indicators.py
        col_map = {}
        for col in df.columns:
            col_lower = col.lower() if isinstance(col, str) else str(col).lower()
            if "open" in col_lower:
                col_map[col] = "Open"
            elif "high" in col_lower:
                col_map[col] = "High"
            elif "low" in col_lower:
                col_map[col] = "Low"
            elif "close" in col_lower:
                col_map[col] = "Close"
            elif "volume" in col_lower:
                col_map[col] = "Volume"

        df = df.rename(columns=col_map)

        # Keep only OHLCV columns
        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        return df[keep]

    except Exception as e:
        logger.debug(f"Alpaca intraday failed for {symbol}: {e} — trying yfinance")
        return _get_intraday_yfinance(symbol, interval)


def _get_intraday_yfinance(symbol: str, interval: str = "5m") -> pd.DataFrame:
    """Fallback: get intraday data via yfinance."""
    return get_stock_data(symbol, period="5d", interval=interval)


# ── Historical (yfinance — bulk/cheap) ────────────────────


def get_stock_data(symbol: str, period: str = "1mo", interval: str = "1d") -> pd.DataFrame:
    """Fetch historical OHLCV data from Yahoo Finance.

    Used for:
    - Daily indicators (period="3mo", interval="1d")
    - Scanner universe building (bulk download)
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            logger.warning(f"No data returned for {symbol}")
            return pd.DataFrame()
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logger.error(f"Failed to fetch data for {symbol}: {e}")
        return pd.DataFrame()


def get_batch_prices(symbols: list[str]) -> dict[str, dict]:
    """Get current prices for multiple symbols efficiently (yfinance batch).

    Used by scanner for universe scoring — Alpaca snapshots would be
    too slow for 800+ symbols.
    """
    results = {}
    try:
        data = yf.download(
            tickers=symbols,
            period="2d",
            interval="1d",
            progress=False,
            threads=True,
        )
        if data.empty:
            return results

        for sym in symbols:
            try:
                if len(symbols) == 1:
                    close = data["Close"]
                    vol = data["Volume"]
                    openp = data["Open"]
                    high = data["High"]
                    low = data["Low"]
                else:
                    close = data["Close"][sym]
                    vol = data["Volume"][sym]
                    openp = data["Open"][sym]
                    high = data["High"][sym]
                    low = data["Low"][sym]

                close = close.dropna()
                if len(close) < 1:
                    continue

                price = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) > 1 else float(openp.dropna().iloc[-1])
                change = price - prev
                change_pct = (change / prev) * 100 if prev else 0

                results[sym] = {
                    "symbol": sym,
                    "price": round(price, 2),
                    "open": round(float(openp.dropna().iloc[-1]), 2),
                    "high": round(float(high.dropna().iloc[-1]), 2),
                    "low": round(float(low.dropna().iloc[-1]), 2),
                    "volume": int(vol.dropna().iloc[-1]),
                    "change": round(change, 2),
                    "change_pct": round(change_pct, 2),
                    "prev_close": round(prev, 2),
                }
            except Exception:
                continue

    except Exception as e:
        logger.error(f"Batch price download failed: {e}")

    return results


def get_multi_timeframe_data(symbol: str) -> dict[str, pd.DataFrame]:
    """Get data at multiple timeframes for deeper analysis."""
    timeframes = {}

    daily = get_stock_data(symbol, period="3mo", interval="1d")
    if not daily.empty:
        timeframes["1d"] = daily

    hourly = get_stock_data(symbol, period="1mo", interval="1h")
    if not hourly.empty:
        timeframes["1h"] = hourly

    m15 = get_intraday_data(symbol, interval="15m")
    if not m15.empty:
        timeframes["15m"] = m15

    m5 = get_intraday_data(symbol, interval="5m")
    if not m5.empty:
        timeframes["5m"] = m5

    return timeframes
