"""Technical indicator calculation using the 'ta' library (pure Python, no numba)."""

import logging

import numpy as np
import pandas as pd
from ta.trend import SMAIndicator, EMAIndicator, MACD, IchimokuIndicator, ADXIndicator
from ta.momentum import RSIIndicator, StochRSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice

logger = logging.getLogger(__name__)


def calculate_indicators(df: pd.DataFrame) -> dict:
    """Calculate all technical indicators from OHLCV data.

    Args:
        df: DataFrame with Open, High, Low, Close, Volume columns

    Returns:
        Dict of indicator name -> current value
    """
    if df.empty or len(df) < 20:
        logger.warning("Insufficient data for indicator calculation")
        return {}

    try:
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # ── Trend Indicators ────────────────────────
        sma_20 = SMAIndicator(close, window=20).sma_indicator()
        sma_50 = SMAIndicator(close, window=50).sma_indicator() if len(df) >= 50 else None
        sma_200 = SMAIndicator(close, window=200).sma_indicator() if len(df) >= 200 else None
        ema_9 = EMAIndicator(close, window=9).ema_indicator()
        ema_21 = EMAIndicator(close, window=21).ema_indicator()

        # ── Momentum Indicators ─────────────────────
        rsi_indicator = RSIIndicator(close, window=14)
        rsi = rsi_indicator.rsi()

        macd_indicator = MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_line = macd_indicator.macd()
        macd_signal = macd_indicator.macd_signal()
        macd_hist = macd_indicator.macd_diff()

        stoch = None
        stoch_k = None
        stoch_d = None
        if len(df) >= 14:
            stoch = StochasticOscillator(high, low, close, window=14, smooth_window=3)
            stoch_k = stoch.stoch()
            stoch_d = stoch.stoch_signal()

        # ── Volatility Indicators ───────────────────
        bb = BollingerBands(close, window=20, window_dev=2)
        bb_upper = bb.bollinger_hband()
        bb_middle = bb.bollinger_mavg()
        bb_lower = bb.bollinger_lband()

        atr = AverageTrueRange(high, low, close, window=14).average_true_range() if len(df) >= 14 else None

        # ── Volume Indicators ───────────────────────
        obv = OnBalanceVolumeIndicator(close, volume).on_balance_volume()

        # ── VWAP (intraday) ─────────────────────────
        vwap_val = None
        try:
            vwap = VolumeWeightedAveragePrice(high, low, close, volume)
            vwap_series = vwap.volume_weighted_average_price()
            vwap_val = _safe_round(vwap_series)
        except Exception:
            pass

        # ── Build result dict (latest values) ───────
        result = {
            "price": round(float(close.iloc[-1]), 2),

            # Moving averages
            "sma_20": _safe_round(sma_20),
            "sma_50": _safe_round(sma_50),
            "sma_200": _safe_round(sma_200),
            "ema_9": _safe_round(ema_9),
            "ema_21": _safe_round(ema_21),

            # Trend signals
            "above_sma_20": _safe_compare(close, sma_20),
            "above_sma_50": _safe_compare(close, sma_50) if sma_50 is not None else None,
            "above_sma_200": _safe_compare(close, sma_200) if sma_200 is not None else None,
            "golden_cross": _safe_compare(sma_50, sma_200) if sma_200 is not None and sma_50 is not None else None,
            "ema_bullish": _safe_compare(ema_9, ema_21),

            # RSI
            "rsi": _safe_round(rsi),
            "rsi_overbought": _safe_val(rsi) is not None and _safe_val(rsi) > 70,
            "rsi_oversold": _safe_val(rsi) is not None and _safe_val(rsi) < 30,

            # MACD
            "macd": _safe_round(macd_line),
            "macd_signal": _safe_round(macd_signal),
            "macd_histogram": _safe_round(macd_hist),
            "macd_bullish_cross": _macd_cross(macd_line, macd_signal, bullish=True),
            "macd_bearish_cross": _macd_cross(macd_line, macd_signal, bullish=False),

            # Stochastic
            "stoch_k": _safe_round(stoch_k),
            "stoch_d": _safe_round(stoch_d),

            # Bollinger Bands
            "bb_upper": _safe_round(bb_upper),
            "bb_middle": _safe_round(bb_middle),
            "bb_lower": _safe_round(bb_lower),
            "bb_width_pct": _bb_width(bb_upper, bb_lower, close),

            # ATR (volatility)
            "atr": _safe_round(atr),
            "atr_pct": _atr_pct(atr, close),

            # VWAP
            "vwap": vwap_val,
            "above_vwap": _safe_val(close) > vwap_val if vwap_val and _safe_val(close) else None,

            # Volume
            "volume": int(volume.iloc[-1]) if not volume.empty else 0,
            "volume_sma_20": int(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else 0,
            "relative_volume": round(
                float(volume.iloc[-1]) / float(volume.rolling(20).mean().iloc[-1]), 2
            ) if len(volume) >= 20 and float(volume.rolling(20).mean().iloc[-1]) > 0 else None,
            "obv_trend": _obv_trend(obv),

            # Support / Resistance (simple)
            "day_high": round(float(high.iloc[-1]), 2),
            "day_low": round(float(low.iloc[-1]), 2),
            "prev_close": round(float(close.iloc[-2]), 2) if len(close) > 1 else None,
        }

        # Multi-day runner detection — needed for First Red Day short pattern.
        # Counts consecutive green days (close > open) going backward from the
        # most recent completed day (iloc[-2], since iloc[-1] is today/partial).
        # Also computes total % run over those green days.
        consecutive_green = 0
        for j in range(len(df) - 2, -1, -1):
            if float(close.iloc[j]) > float(df["Open"].iloc[j]):
                consecutive_green += 1
            else:
                break
        result["consecutive_green_days"] = consecutive_green
        if consecutive_green >= 2:
            run_start_idx = max(0, len(df) - 2 - consecutive_green)
            run_start_price = float(close.iloc[run_start_idx])
            if run_start_price > 0:
                result["multi_day_run_pct"] = round(
                    (float(close.iloc[-2]) - run_start_price) / run_start_price * 100, 2
                )

        return result

    except Exception as e:
        logger.error(f"Error calculating indicators: {e}")
        return {}


def calculate_intraday_indicators(df_5m: pd.DataFrame) -> dict:
    """Calculate indicators specifically for intraday (5-min) data.

    Focuses on patterns useful for day trading.
    """
    if df_5m.empty or len(df_5m) < 20:
        return {}

    try:
        close = df_5m["Close"]
        high = df_5m["High"]
        low = df_5m["Low"]
        volume = df_5m["Volume"]

        result = {}

        # VWAP
        try:
            vwap = VolumeWeightedAveragePrice(high, low, close, volume)
            vwap_series = vwap.volume_weighted_average_price()
            result["vwap_5m"] = _safe_round(vwap_series)
            result["above_vwap_5m"] = (
                _safe_val(close) > _safe_val(vwap_series)
                if _safe_val(close) and _safe_val(vwap_series) else None
            )
        except Exception:
            pass

        # Fast EMA for intraday
        ema_5 = EMAIndicator(close, window=5).ema_indicator()
        ema_13 = EMAIndicator(close, window=13).ema_indicator()
        result["ema_5_5m"] = _safe_round(ema_5)
        result["ema_13_5m"] = _safe_round(ema_13)
        result["ema_bullish_5m"] = _safe_compare(ema_5, ema_13)

        # RSI on 5-min
        rsi = RSIIndicator(close, window=14).rsi()
        result["rsi_5m"] = _safe_round(rsi)

        # Opening range (first 6 bars = first 30 min for 5-min chart)
        # We take today's data only
        today_mask = df_5m.index.date == df_5m.index.date[-1] if hasattr(df_5m.index, 'date') else None
        if today_mask is not None:
            today_data = df_5m[today_mask]
            if len(today_data) >= 6:
                opening_range_high = float(today_data["High"].iloc[:6].max())
                opening_range_low = float(today_data["Low"].iloc[:6].min())
                current = float(close.iloc[-1])
                result["or_high"] = round(opening_range_high, 2)
                result["or_low"] = round(opening_range_low, 2)
                result["above_or_high"] = current > opening_range_high
                result["below_or_low"] = current < opening_range_low
                result["or_breakout"] = current > opening_range_high or current < opening_range_low

        # Session high/low proximity — needed for Failed Breakout detection
        if today_mask is not None and len(today_data) >= 3:
            session_high = float(today_data["High"].max())
            session_low = float(today_data["Low"].min())
            current = float(close.iloc[-1])
            # "Was at HOD/LOD" = price within 0.5% of session extreme
            result["was_at_hod"] = current >= session_high * 0.995
            result["was_at_lod"] = current <= session_low * 1.005

        # Volume trend (last 12 bars = 1 hour)
        if len(volume) >= 12:
            recent_vol = float(volume.iloc[-12:].mean())
            earlier_vol = float(volume.iloc[-24:-12].mean()) if len(volume) >= 24 else recent_vol
            if earlier_vol > 0:
                result["volume_acceleration"] = round(recent_vol / earlier_vol, 2)

        return result

    except Exception as e:
        logger.error(f"Error calculating intraday indicators: {e}")
        return {}


def calculate_dual_thrust_range(daily_df: pd.DataFrame, today_open: float,
                                lookback: int = 5, k1: float = 0.5, k2: float = 0.5) -> dict:
    """Dual Thrust dynamic range — adaptive ORB replacement.

    Uses multi-day price action to set breakout thresholds that adapt to
    recent volatility. On quiet days, range is smaller → tighter triggers.
    On volatile days, range is wider → fewer false breakouts.

    Formula:
        range = max(HH(N) - LC(N), HC(N) - LL(N))
        upper = today_open + k1 * range
        lower = today_open - k2 * range

    Where HH=highest high, LC=lowest close, HC=highest close, LL=lowest low
    over the lookback period.

    Returns dict with dt_upper, dt_lower, dt_range, or empty dict if insufficient data.
    """
    if daily_df is None or len(daily_df) < lookback:
        return {}

    recent = daily_df.iloc[-lookback:]
    hh = float(recent["High"].max())
    ll = float(recent["Low"].min())
    hc = float(recent["Close"].max())
    lc = float(recent["Close"].min())

    # Dual Thrust range: max of (HH-LC, HC-LL)
    dt_range = max(hh - lc, hc - ll)
    if dt_range <= 0:
        return {}

    return {
        "dt_upper": round(today_open + k1 * dt_range, 2),
        "dt_lower": round(today_open - k2 * dt_range, 2),
        "dt_range": round(dt_range, 2),
    }


def get_signal_summary(indicators: dict) -> str:
    """Generate a human-readable signal summary from indicators."""
    if not indicators:
        return "No indicators available"

    signals = []

    # Trend
    if indicators.get("above_sma_200"):
        signals.append("BULLISH: Price above 200-day SMA (long-term uptrend)")
    elif indicators.get("above_sma_200") is False:
        signals.append("BEARISH: Price below 200-day SMA (long-term downtrend)")

    if indicators.get("golden_cross"):
        signals.append("BULLISH: Golden cross (50 SMA > 200 SMA)")

    if indicators.get("ema_bullish"):
        signals.append("BULLISH: EMA(9) above EMA(21) — short-term uptrend")
    elif indicators.get("ema_bullish") is False:
        signals.append("BEARISH: EMA(9) below EMA(21) — short-term downtrend")

    # MACD crosses
    if indicators.get("macd_bullish_cross"):
        signals.append("BULLISH: MACD just crossed above signal line")
    elif indicators.get("macd_bearish_cross"):
        signals.append("BEARISH: MACD just crossed below signal line")

    # RSI
    rsi = indicators.get("rsi")
    if rsi is not None:
        if rsi > 70:
            signals.append(f"OVERBOUGHT: RSI at {rsi}")
        elif rsi < 30:
            signals.append(f"OVERSOLD: RSI at {rsi}")
        else:
            signals.append(f"NEUTRAL: RSI at {rsi}")

    # MACD
    macd_hist = indicators.get("macd_histogram")
    if macd_hist is not None:
        if macd_hist > 0:
            signals.append("BULLISH: MACD histogram positive")
        else:
            signals.append("BEARISH: MACD histogram negative")

    # Bollinger Bands
    price = indicators.get("price", 0)
    bb_lower = indicators.get("bb_lower")
    bb_upper = indicators.get("bb_upper")
    if bb_lower and bb_upper and price:
        if price <= bb_lower:
            signals.append("OVERSOLD: Price at lower Bollinger Band")
        elif price >= bb_upper:
            signals.append("OVERBOUGHT: Price at upper Bollinger Band")

    # VWAP
    if indicators.get("above_vwap"):
        signals.append("BULLISH: Price above VWAP")
    elif indicators.get("above_vwap") is False:
        signals.append("BEARISH: Price below VWAP")

    # Volume
    rvol = indicators.get("relative_volume")
    if rvol and rvol >= 2.0:
        signals.append(f"HIGH VOLUME: {rvol:.1f}x average — strong conviction")
    elif rvol and rvol >= 1.5:
        signals.append(f"ABOVE AVG VOLUME: {rvol:.1f}x average")

    return "\n".join(signals)


def get_intraday_signal_summary(intraday: dict) -> str:
    """Signal summary specifically for intraday data."""
    if not intraday:
        return "No intraday data"

    signals = []

    if intraday.get("or_breakout"):
        if intraday.get("above_or_high"):
            signals.append(f"BREAKOUT: Price above opening range high ({intraday['or_high']})")
        elif intraday.get("below_or_low"):
            signals.append(f"BREAKDOWN: Price below opening range low ({intraday['or_low']})")

    if intraday.get("above_vwap_5m"):
        signals.append("BULLISH: Price above intraday VWAP")
    elif intraday.get("above_vwap_5m") is False:
        signals.append("BEARISH: Price below intraday VWAP")

    if intraday.get("ema_bullish_5m"):
        signals.append("BULLISH: Intraday EMA(5) above EMA(13)")
    elif intraday.get("ema_bullish_5m") is False:
        signals.append("BEARISH: Intraday EMA(5) below EMA(13)")

    rsi_5m = intraday.get("rsi_5m")
    if rsi_5m is not None:
        if rsi_5m > 70:
            signals.append(f"INTRADAY OVERBOUGHT: 5m RSI at {rsi_5m}")
        elif rsi_5m < 30:
            signals.append(f"INTRADAY OVERSOLD: 5m RSI at {rsi_5m}")

    vol_acc = intraday.get("volume_acceleration")
    if vol_acc and vol_acc > 1.5:
        signals.append(f"VOLUME ACCELERATING: {vol_acc:.1f}x vs prior hour")

    return "\n".join(signals) if signals else "No strong intraday signals"


# ── Helpers ────────────────────────────────────────────

def _safe_val(series):
    """Get the last value of a series safely."""
    if series is None:
        return None
    try:
        val = series.iloc[-1]
        if pd.isna(val):
            return None
        return float(val)
    except (IndexError, TypeError):
        return None


def _safe_round(series, decimals=2):
    """Get rounded last value of a series."""
    val = _safe_val(series)
    return round(val, decimals) if val is not None else None


def _safe_compare(series_a, series_b):
    """Compare last values of two series."""
    a = _safe_val(series_a)
    b = _safe_val(series_b)
    if a is None or b is None:
        return None
    return a > b


def _obv_trend(obv_series) -> str:
    """Determine OBV trend over last 5 periods."""
    if obv_series is None or len(obv_series) < 5:
        return "unknown"
    recent = obv_series.iloc[-5:]
    if recent.iloc[-1] > recent.iloc[0]:
        return "rising"
    elif recent.iloc[-1] < recent.iloc[0]:
        return "falling"
    return "flat"


def _macd_cross(macd_line, macd_signal, bullish=True):
    """Detect if MACD just crossed the signal line."""
    if macd_line is None or macd_signal is None:
        return False
    try:
        curr_macd = float(macd_line.iloc[-1])
        prev_macd = float(macd_line.iloc[-2])
        curr_sig = float(macd_signal.iloc[-1])
        prev_sig = float(macd_signal.iloc[-2])
        if bullish:
            return prev_macd <= prev_sig and curr_macd > curr_sig
        else:
            return prev_macd >= prev_sig and curr_macd < curr_sig
    except (IndexError, TypeError):
        return False


def _bb_width(bb_upper, bb_lower, close):
    """Bollinger Band width as % of price."""
    upper = _safe_val(bb_upper)
    lower = _safe_val(bb_lower)
    price = _safe_val(close)
    if upper and lower and price and price > 0:
        return round(((upper - lower) / price) * 100, 2)
    return None


def _atr_pct(atr_series, close):
    """ATR as percentage of current price."""
    atr_val = _safe_val(atr_series)
    price = _safe_val(close)
    if atr_val and price and price > 0:
        return round((atr_val / price) * 100, 2)
    return None


def calculate_choppiness_index(df: pd.DataFrame, period: int = 14) -> float | None:
    """Calculate Choppiness Index (CHOP) from OHLCV data.

    CHOP > 61.8 = choppy/range-bound market
    CHOP < 38.2 = strongly trending market
    """
    if df is None or df.empty or len(df) < period + 1:
        return None
    try:
        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        atr_sum = tr.iloc[-period:].sum()
        hh = float(high.iloc[-period:].max())
        ll = float(low.iloc[-period:].min())

        if hh <= ll or atr_sum <= 0:
            return None

        chop = 100.0 * np.log10(atr_sum / (hh - ll)) / np.log10(period)
        return round(float(chop), 2)
    except Exception:
        return None


def calculate_adx(df: pd.DataFrame, period: int = 14) -> float | None:
    """Calculate ADX (Average Directional Index) from OHLCV data.

    ADX < 20 = weak/no trend
    ADX > 25 = strong trend
    """
    if df is None or df.empty or len(df) < period + 10:
        return None
    try:
        adx = ADXIndicator(df["High"], df["Low"], df["Close"], window=period)
        val = adx.adx().iloc[-1]
        if pd.isna(val):
            return None
        return round(float(val), 2)
    except Exception:
        return None
