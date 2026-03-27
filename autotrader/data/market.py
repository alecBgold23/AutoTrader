"""Market data fetching via yfinance and Alpaca."""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def get_stock_data(symbol: str, period: str = "1mo", interval: str = "1d") -> pd.DataFrame:
    """Fetch historical OHLCV data from Yahoo Finance.

    Args:
        symbol: Ticker symbol (e.g. "AAPL")
        period: Data period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max)
        interval: Bar interval (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo)

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
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


def get_current_price(symbol: str) -> dict | None:
    """Get the latest price and basic info for a symbol."""
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
        logger.error(f"Failed to get current price for {symbol}: {e}")
        return None


def get_batch_prices(symbols: list[str]) -> dict[str, dict]:
    """Get current prices for multiple symbols efficiently."""
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


def get_intraday_data(symbol: str, interval: str = "5m") -> pd.DataFrame:
    """Fetch intraday data (last 5 days max for intervals < 1d)."""
    return get_stock_data(symbol, period="5d", interval=interval)


def get_multi_timeframe_data(symbol: str) -> dict[str, pd.DataFrame]:
    """Get data at multiple timeframes for deeper analysis.

    Returns dict with keys: '5m', '15m', '1h', '1d'
    """
    timeframes = {}

    # Daily (3 months for longer-term context)
    daily = get_stock_data(symbol, period="3mo", interval="1d")
    if not daily.empty:
        timeframes["1d"] = daily

    # Hourly (1 month)
    hourly = get_stock_data(symbol, period="1mo", interval="1h")
    if not hourly.empty:
        timeframes["1h"] = hourly

    # 15-minute (5 days)
    m15 = get_stock_data(symbol, period="5d", interval="15m")
    if not m15.empty:
        timeframes["15m"] = m15

    # 5-minute (5 days)
    m5 = get_stock_data(symbol, period="5d", interval="5m")
    if not m5.empty:
        timeframes["5m"] = m5

    return timeframes
