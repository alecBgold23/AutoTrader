"""Candlestick and chart pattern detection — pure Python, no TA-Lib.

Detects the patterns that successful day traders actually trade:
- Candlestick: doji, hammer, shooting star, engulfing, morning/evening star
- Chart: bull/bear flags, breakouts, inside bars
- Intraday: opening range breakout, red-to-green, first pullback, VWAP setups
"""

import logging
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PatternSignal:
    """A detected pattern with trading implications."""
    name: str
    type: str           # "bullish", "bearish", "neutral"
    strength: float     # 0.0 to 1.0
    description: str
    entry_hint: str     # Where to enter
    stop_hint: str      # Where to place stop
    timeframe: str      # Which timeframe detected on


def detect_all_patterns(
    df_daily: pd.DataFrame,
    df_5m: pd.DataFrame | None = None,
    prior_day_high: float | None = None,
    prior_day_low: float | None = None,
    prior_day_close: float | None = None,
    vwap: float | None = None,
) -> list[PatternSignal]:
    """Run all pattern detectors and return found patterns."""
    patterns = []

    # Candlestick patterns on daily chart
    if not df_daily.empty and len(df_daily) >= 5:
        patterns.extend(_detect_candlestick_patterns(df_daily, "daily"))

    # Candlestick patterns on 5-min chart
    if df_5m is not None and not df_5m.empty and len(df_5m) >= 10:
        patterns.extend(_detect_candlestick_patterns(df_5m, "5m"))
        patterns.extend(_detect_intraday_patterns(df_5m, prior_day_high, prior_day_low, prior_day_close, vwap))

    # Chart patterns on daily
    if not df_daily.empty and len(df_daily) >= 20:
        patterns.extend(_detect_chart_patterns(df_daily))

    return patterns


# ═══════════════════════════════════════════════════════════
# CANDLESTICK PATTERNS
# ═══════════════════════════════════════════════════════════

def _detect_candlestick_patterns(df: pd.DataFrame, timeframe: str) -> list[PatternSignal]:
    """Detect candlestick patterns on any timeframe."""
    patterns = []
    if len(df) < 3:
        return patterns

    o = df["Open"].values
    h = df["High"].values
    l = df["Low"].values
    c = df["Close"].values

    # Use last few candles
    for i in range(-1, max(-4, -len(df)), -1):
        idx = len(df) + i
        if idx < 2:
            break

        body = abs(c[idx] - o[idx])
        candle_range = h[idx] - l[idx]
        if candle_range == 0:
            continue

        upper_shadow = h[idx] - max(o[idx], c[idx])
        lower_shadow = min(o[idx], c[idx]) - l[idx]
        is_green = c[idx] > o[idx]
        body_pct = body / candle_range

        # Only check the most recent candle for most patterns
        if i != -1:
            continue

        # ── Doji ──
        if body_pct < 0.1 and candle_range > 0:
            doji_type = "neutral"
            desc = "Doji — indecision"
            if lower_shadow > upper_shadow * 2:
                doji_type = "bullish"
                desc = "Dragonfly Doji — bullish reversal signal"
            elif upper_shadow > lower_shadow * 2:
                doji_type = "bearish"
                desc = "Gravestone Doji — bearish reversal signal"

            patterns.append(PatternSignal(
                name="doji", type=doji_type, strength=0.5,
                description=desc,
                entry_hint="Wait for next candle confirmation",
                stop_hint="Below/above doji range",
                timeframe=timeframe,
            ))

        # ── Hammer (bullish) ──
        if (lower_shadow >= body * 2
                and upper_shadow < body * 0.5
                and body_pct > 0.1
                and body_pct < 0.4):
            # Check if preceded by downtrend (3+ declining candles)
            if idx >= 3 and c[idx-1] < c[idx-2] < c[idx-3]:
                patterns.append(PatternSignal(
                    name="hammer", type="bullish", strength=0.7,
                    description="Hammer at bottom of downtrend — strong bullish reversal",
                    entry_hint="Buy on next candle close above hammer high",
                    stop_hint=f"Below hammer low ({l[idx]:.2f})",
                    timeframe=timeframe,
                ))

        # ── Shooting Star (bearish) ──
        if (upper_shadow >= body * 2
                and lower_shadow < body * 0.5
                and body_pct > 0.1
                and body_pct < 0.4):
            if idx >= 3 and c[idx-1] > c[idx-2] > c[idx-3]:
                patterns.append(PatternSignal(
                    name="shooting_star", type="bearish", strength=0.7,
                    description="Shooting Star at top of uptrend — bearish reversal",
                    entry_hint="Sell on next candle close below shooting star low",
                    stop_hint=f"Above shooting star high ({h[idx]:.2f})",
                    timeframe=timeframe,
                ))

        # ── Bullish Engulfing ──
        if idx >= 1:
            prev_body = abs(c[idx-1] - o[idx-1])
            if (c[idx-1] < o[idx-1]           # Previous was red
                    and c[idx] > o[idx]         # Current is green
                    and c[idx] > o[idx-1]       # Current close > prev open
                    and o[idx] < c[idx-1]       # Current open < prev close
                    and body > prev_body * 1.0): # Current body bigger
                patterns.append(PatternSignal(
                    name="bullish_engulfing", type="bullish", strength=0.75,
                    description="Bullish Engulfing — buyers overwhelmed sellers",
                    entry_hint="Buy at current price or on pullback to engulfing candle midpoint",
                    stop_hint=f"Below engulfing low ({min(l[idx], l[idx-1]):.2f})",
                    timeframe=timeframe,
                ))

        # ── Bearish Engulfing ──
        if idx >= 1:
            prev_body = abs(c[idx-1] - o[idx-1])
            if (c[idx-1] > o[idx-1]           # Previous was green
                    and c[idx] < o[idx]         # Current is red
                    and c[idx] < o[idx-1]       # Current close < prev open
                    and o[idx] > c[idx-1]       # Current open > prev close
                    and body > prev_body * 1.0):
                patterns.append(PatternSignal(
                    name="bearish_engulfing", type="bearish", strength=0.75,
                    description="Bearish Engulfing — sellers overwhelmed buyers",
                    entry_hint="Sell/avoid at current price",
                    stop_hint=f"Above engulfing high ({max(h[idx], h[idx-1]):.2f})",
                    timeframe=timeframe,
                ))

        # ── Inside Bar / Harami ──
        if idx >= 1:
            if (h[idx] < h[idx-1] and l[idx] > l[idx-1]):
                ib_type = "neutral"
                if c[idx-1] < o[idx-1]:  # Previous red → potential bullish
                    ib_type = "bullish"
                elif c[idx-1] > o[idx-1]:  # Previous green → potential bearish
                    ib_type = "bearish"
                patterns.append(PatternSignal(
                    name="inside_bar", type=ib_type, strength=0.5,
                    description="Inside Bar — consolidation before breakout",
                    entry_hint=f"Buy break above {h[idx]:.2f} or sell break below {l[idx]:.2f}",
                    stop_hint="Opposite side of inside bar range",
                    timeframe=timeframe,
                ))

    # ── Morning Star (3-candle bullish reversal) ──
    if len(df) >= 3:
        o3, h3, l3, c3 = o[-3], h[-3], l[-3], c[-3]
        o2, h2, l2, c2 = o[-2], h[-2], l[-2], c[-2]
        o1, h1, l1, c1 = o[-1], h[-1], l[-1], c[-1]

        body3 = abs(c3 - o3)
        body2 = abs(c2 - o2)
        body1 = abs(c1 - o1)

        if (c3 < o3                           # 1st candle red
                and body2 < body3 * 0.3       # 2nd candle small body
                and c1 > o1                    # 3rd candle green
                and c1 > (o3 + c3) / 2        # 3rd closes above 1st midpoint
                and body1 > body2):            # 3rd body bigger than 2nd
            patterns.append(PatternSignal(
                name="morning_star", type="bullish", strength=0.8,
                description="Morning Star — strong 3-candle bullish reversal",
                entry_hint="Buy at current price",
                stop_hint=f"Below the star candle low ({l2:.2f})",
                timeframe=timeframe,
            ))

    # ── Evening Star (3-candle bearish reversal) ──
    if len(df) >= 3:
        if (c3 > o3                           # 1st candle green
                and body2 < body3 * 0.3       # 2nd candle small body
                and c1 < o1                    # 3rd candle red
                and c1 < (o3 + c3) / 2        # 3rd closes below 1st midpoint
                and body1 > body2):
            patterns.append(PatternSignal(
                name="evening_star", type="bearish", strength=0.8,
                description="Evening Star — strong 3-candle bearish reversal",
                entry_hint="Sell at current price",
                stop_hint=f"Above the star candle high ({h2:.2f})",
                timeframe=timeframe,
            ))

    # ── Three White Soldiers ──
    if len(df) >= 3:
        if (c[-3] > o[-3] and c[-2] > o[-2] and c[-1] > o[-1]  # All green
                and c[-1] > c[-2] > c[-3]                        # Ascending closes
                and o[-2] > o[-3] and o[-1] > o[-2]              # Ascending opens
                and abs(c[-3]-o[-3]) > (h[-3]-l[-3])*0.5         # Decent bodies
                and abs(c[-2]-o[-2]) > (h[-2]-l[-2])*0.5
                and abs(c[-1]-o[-1]) > (h[-1]-l[-1])*0.5):
            patterns.append(PatternSignal(
                name="three_white_soldiers", type="bullish", strength=0.75,
                description="Three White Soldiers — strong bullish momentum",
                entry_hint="Buy on pullback to last soldier's midpoint",
                stop_hint=f"Below first soldier low ({l[-3]:.2f})",
                timeframe=timeframe,
            ))

    # ── Three Black Crows ──
    if len(df) >= 3:
        if (c[-3] < o[-3] and c[-2] < o[-2] and c[-1] < o[-1]
                and c[-1] < c[-2] < c[-3]
                and o[-2] < o[-3] and o[-1] < o[-2]
                and abs(c[-3]-o[-3]) > (h[-3]-l[-3])*0.5
                and abs(c[-2]-o[-2]) > (h[-2]-l[-2])*0.5
                and abs(c[-1]-o[-1]) > (h[-1]-l[-1])*0.5):
            patterns.append(PatternSignal(
                name="three_black_crows", type="bearish", strength=0.75,
                description="Three Black Crows — strong bearish momentum",
                entry_hint="Sell or avoid longs",
                stop_hint=f"Above first crow high ({h[-3]:.2f})",
                timeframe=timeframe,
            ))

    return patterns


# ═══════════════════════════════════════════════════════════
# INTRADAY-SPECIFIC PATTERNS
# ═══════════════════════════════════════════════════════════

def _detect_intraday_patterns(
    df: pd.DataFrame,
    prior_day_high: float | None,
    prior_day_low: float | None,
    prior_day_close: float | None,
    vwap: float | None,
) -> list[PatternSignal]:
    """Detect intraday-specific patterns on 5-min data."""
    patterns = []
    if len(df) < 10:
        return patterns

    c = df["Close"].values
    h = df["High"].values
    l = df["Low"].values
    o = df["Open"].values
    v = df["Volume"].values
    price = float(c[-1])

    # Get today's data only
    if hasattr(df.index, 'date'):
        today = df.index.date[-1]
        today_mask = df.index.date == today
        today_df = df[today_mask]
    else:
        today_df = df.iloc[-78:]  # ~6.5 hours of 5-min bars

    if len(today_df) < 2:
        return patterns

    today_c = today_df["Close"].values
    today_h = today_df["High"].values
    today_l = today_df["Low"].values
    today_o = today_df["Open"].values
    today_v = today_df["Volume"].values

    # ── Opening Range Breakout (ORB) ──
    # First 6 bars = 30 minutes on 5-min chart
    if len(today_df) >= 7:
        or_bars = 6  # 30-minute ORB
        or_high = float(today_h[:or_bars].max())
        or_low = float(today_l[:or_bars].min())
        or_range = or_high - or_low

        if price > or_high and or_range > 0:
            patterns.append(PatternSignal(
                name="orb_breakout_long", type="bullish", strength=0.7,
                description=f"Opening Range Breakout UP — price above 30-min high ({or_high:.2f})",
                entry_hint=f"Buy at {price:.2f}, ORB high was {or_high:.2f}",
                stop_hint=f"Below ORB midpoint ({(or_high+or_low)/2:.2f}) or ORB low ({or_low:.2f})",
                timeframe="5m",
            ))
        elif price < or_low and or_range > 0:
            patterns.append(PatternSignal(
                name="orb_breakout_short", type="bearish", strength=0.7,
                description=f"Opening Range Breakdown — price below 30-min low ({or_low:.2f})",
                entry_hint=f"Sell at {price:.2f}, ORB low was {or_low:.2f}",
                stop_hint=f"Above ORB midpoint ({(or_high+or_low)/2:.2f})",
                timeframe="5m",
            ))

    # ── Red to Green Move ──
    if prior_day_close and len(today_df) >= 3:
        opened_below = float(today_o[0]) < prior_day_close
        now_above = price > prior_day_close
        if opened_below and now_above:
            patterns.append(PatternSignal(
                name="red_to_green", type="bullish", strength=0.65,
                description=f"Red to Green — opened below prior close ({prior_day_close:.2f}), now above",
                entry_hint="Buy the reclaim with volume confirmation",
                stop_hint=f"Below prior close ({prior_day_close:.2f})",
                timeframe="5m",
            ))

    # ── Green to Red Move ──
    if prior_day_close and len(today_df) >= 3:
        opened_above = float(today_o[0]) > prior_day_close
        now_below = price < prior_day_close
        if opened_above and now_below:
            patterns.append(PatternSignal(
                name="green_to_red", type="bearish", strength=0.65,
                description=f"Green to Red — opened above prior close ({prior_day_close:.2f}), now below",
                entry_hint="Sell/avoid — momentum shifted bearish",
                stop_hint=f"Above prior close ({prior_day_close:.2f})",
                timeframe="5m",
            ))

    # ── VWAP Reclaim ──
    if vwap and len(today_df) >= 4:
        was_below = any(float(x) < vwap for x in today_c[-6:-1])
        now_above = price > vwap
        if was_below and now_above:
            patterns.append(PatternSignal(
                name="vwap_reclaim", type="bullish", strength=0.65,
                description=f"VWAP Reclaim — price crossed back above VWAP ({vwap:.2f})",
                entry_hint=f"Buy at {price:.2f}, VWAP at {vwap:.2f}",
                stop_hint=f"Below VWAP ({vwap:.2f})",
                timeframe="5m",
            ))

    # ── VWAP Rejection ──
    if vwap and len(today_df) >= 4:
        was_above = any(float(x) > vwap for x in today_c[-6:-1])
        now_below = price < vwap
        if was_above and now_below:
            patterns.append(PatternSignal(
                name="vwap_rejection", type="bearish", strength=0.65,
                description=f"VWAP Rejection — price failed to hold above VWAP ({vwap:.2f})",
                entry_hint="Sell or avoid longs",
                stop_hint=f"Above VWAP ({vwap:.2f})",
                timeframe="5m",
            ))

    # ── First Pullback (after strong opening move) ──
    if len(today_df) >= 10:
        # Check if first 30 min had strong move (>1.5%)
        first_30_change = ((float(today_c[5]) - float(today_o[0])) / float(today_o[0])) * 100
        if abs(first_30_change) > 1.5:
            # Check if we're in a pullback (last 3-5 bars retracing)
            recent = today_c[-5:]
            if first_30_change > 0:  # Bullish opening, look for pullback
                if float(recent[-1]) < float(recent.max()) and float(recent[-1]) > float(today_o[0]):
                    patterns.append(PatternSignal(
                        name="first_pullback_long", type="bullish", strength=0.7,
                        description=f"First Pullback — strong open (+{first_30_change:.1f}%), now pulling back for entry",
                        entry_hint="Buy on bounce from pullback low",
                        stop_hint="Below the pullback low or opening range low",
                        timeframe="5m",
                    ))
            else:  # Bearish opening
                if float(recent[-1]) > float(recent.min()) and float(recent[-1]) < float(today_o[0]):
                    patterns.append(PatternSignal(
                        name="first_pullback_short", type="bearish", strength=0.7,
                        description=f"First Pullback — weak open ({first_30_change:.1f}%), bouncing for short entry",
                        entry_hint="Sell on rejection at pullback high",
                        stop_hint="Above the pullback high or opening range high",
                        timeframe="5m",
                    ))

    # ── HOD/LOD Break ──
    if len(today_df) >= 10:
        today_hod = float(today_h.max())
        today_lod = float(today_l.min())
        # Check if price just made new HOD in last 2 bars
        prior_hod = float(today_h[:-2].max()) if len(today_h) > 2 else today_hod
        if price >= today_hod and today_hod > prior_hod:
            patterns.append(PatternSignal(
                name="hod_breakout", type="bullish", strength=0.6,
                description=f"New High of Day breakout ({today_hod:.2f})",
                entry_hint="Buy the breakout with volume",
                stop_hint=f"Below prior HOD ({prior_hod:.2f})",
                timeframe="5m",
            ))

    # ── Prior Day Level Break ──
    if prior_day_high and price > prior_day_high:
        patterns.append(PatternSignal(
            name="prior_day_high_break", type="bullish", strength=0.6,
            description=f"Breaking above prior day high ({prior_day_high:.2f})",
            entry_hint="Buy with volume confirmation",
            stop_hint=f"Below prior day high ({prior_day_high:.2f})",
            timeframe="5m",
        ))
    if prior_day_low and price < prior_day_low:
        patterns.append(PatternSignal(
            name="prior_day_low_break", type="bearish", strength=0.6,
            description=f"Breaking below prior day low ({prior_day_low:.2f})",
            entry_hint="Sell or avoid longs",
            stop_hint=f"Above prior day low ({prior_day_low:.2f})",
            timeframe="5m",
        ))

    return patterns


# ═══════════════════════════════════════════════════════════
# CHART PATTERNS (daily timeframe)
# ═══════════════════════════════════════════════════════════

def _detect_chart_patterns(df: pd.DataFrame) -> list[PatternSignal]:
    """Detect chart patterns on daily data."""
    patterns = []
    if len(df) < 20:
        return patterns

    c = df["Close"].values
    h = df["High"].values
    l = df["Low"].values
    v = df["Volume"].values
    price = float(c[-1])

    # ── Bull Flag ──
    # Strong move up (5+ days, >5%), then tight consolidation (5-10 days, <3%)
    if len(df) >= 15:
        # Check for pole (strong move in last 15 bars)
        for pole_len in range(5, 10):
            start_idx = -(pole_len + 8)
            end_idx = -8
            if abs(start_idx) > len(df):
                continue
            pole_change = (c[end_idx] - c[start_idx]) / c[start_idx] * 100
            if pole_change > 5:  # Strong bullish pole
                # Check for flag (consolidation in last 5-8 bars)
                flag_data = c[-8:]
                flag_range = (flag_data.max() - flag_data.min()) / flag_data.mean() * 100
                if flag_range < 4:  # Tight consolidation
                    # Volume should decrease during flag
                    pole_vol = float(v[start_idx:end_idx].mean())
                    flag_vol = float(v[-8:].mean())
                    if flag_vol < pole_vol * 0.8:
                        patterns.append(PatternSignal(
                            name="bull_flag", type="bullish", strength=0.75,
                            description=f"Bull Flag — {pole_change:.1f}% pole + tight consolidation ({flag_range:.1f}% range)",
                            entry_hint=f"Buy on breakout above flag high ({float(h[-8:].max()):.2f})",
                            stop_hint=f"Below flag low ({float(l[-8:].min()):.2f})",
                            timeframe="daily",
                        ))
                        break

    # ── Bear Flag ──
    if len(df) >= 15:
        for pole_len in range(5, 10):
            start_idx = -(pole_len + 8)
            end_idx = -8
            if abs(start_idx) > len(df):
                continue
            pole_change = (c[end_idx] - c[start_idx]) / c[start_idx] * 100
            if pole_change < -5:  # Strong bearish pole
                flag_data = c[-8:]
                flag_range = (flag_data.max() - flag_data.min()) / flag_data.mean() * 100
                if flag_range < 4:
                    pole_vol = float(v[start_idx:end_idx].mean())
                    flag_vol = float(v[-8:].mean())
                    if flag_vol < pole_vol * 0.8:
                        patterns.append(PatternSignal(
                            name="bear_flag", type="bearish", strength=0.75,
                            description=f"Bear Flag — {pole_change:.1f}% pole + tight consolidation",
                            entry_hint=f"Sell on breakdown below flag low ({float(l[-8:].min()):.2f})",
                            stop_hint=f"Above flag high ({float(h[-8:].max()):.2f})",
                            timeframe="daily",
                        ))
                        break

    # ── Bollinger Band Squeeze ──
    # BB width contracts to recent low → expect big move
    if len(df) >= 20:
        sma20 = pd.Series(c).rolling(20).mean()
        std20 = pd.Series(c).rolling(20).std()
        bb_width = (std20 * 4) / sma20 * 100  # Total BB width as % of price
        if len(bb_width.dropna()) >= 5:
            current_width = float(bb_width.iloc[-1])
            avg_width = float(bb_width.iloc[-20:].mean())
            if current_width < avg_width * 0.6:
                patterns.append(PatternSignal(
                    name="bb_squeeze", type="neutral", strength=0.6,
                    description=f"Bollinger Band Squeeze — volatility compressed, expecting breakout",
                    entry_hint="Wait for direction (buy break above upper BB, sell break below lower)",
                    stop_hint="Opposite Bollinger Band",
                    timeframe="daily",
                ))

    # ── 20-Day Breakout ──
    if len(df) >= 20:
        twenty_high = float(h[-21:-1].max())
        twenty_low = float(l[-21:-1].min())
        if price > twenty_high:
            patterns.append(PatternSignal(
                name="twenty_day_breakout", type="bullish", strength=0.65,
                description=f"20-Day High Breakout — price above {twenty_high:.2f}",
                entry_hint="Buy the breakout",
                stop_hint=f"Below breakout level ({twenty_high:.2f})",
                timeframe="daily",
            ))
        elif price < twenty_low:
            patterns.append(PatternSignal(
                name="twenty_day_breakdown", type="bearish", strength=0.65,
                description=f"20-Day Low Breakdown — price below {twenty_low:.2f}",
                entry_hint="Sell or avoid longs",
                stop_hint=f"Above breakdown level ({twenty_low:.2f})",
                timeframe="daily",
            ))

    return patterns


# ═══════════════════════════════════════════════════════════
# SUPPORT / RESISTANCE DETECTION
# ═══════════════════════════════════════════════════════════

def find_support_resistance(df: pd.DataFrame, num_levels: int = 5) -> dict:
    """Find key support and resistance levels using fractal highs/lows and price clustering.

    Returns dict with 'support' and 'resistance' lists of price levels.
    """
    if len(df) < 20:
        return {"support": [], "resistance": []}

    h = df["High"].values
    l = df["Low"].values
    c = df["Close"].values
    price = float(c[-1])

    # Find fractal highs (local maxima) and lows (local minima)
    # A fractal high: bar's high is highest in ±2 bars
    fractal_highs = []
    fractal_lows = []
    window = 3

    for i in range(window, len(h) - window):
        if h[i] == max(h[i-window:i+window+1]):
            fractal_highs.append(float(h[i]))
        if l[i] == min(l[i-window:i+window+1]):
            fractal_lows.append(float(l[i]))

    # Cluster nearby levels (within 0.5% of each other)
    all_levels = fractal_highs + fractal_lows
    if not all_levels:
        return {"support": [], "resistance": []}

    clusters = _cluster_levels(all_levels, threshold_pct=0.5)

    # Separate into support (below price) and resistance (above price)
    support = sorted([lvl for lvl in clusters if lvl < price], reverse=True)[:num_levels]
    resistance = sorted([lvl for lvl in clusters if lvl > price])[:num_levels]

    return {
        "support": [round(s, 2) for s in support],
        "resistance": [round(r, 2) for r in resistance],
    }


def _cluster_levels(levels: list[float], threshold_pct: float = 0.5) -> list[float]:
    """Cluster nearby price levels and return the average of each cluster."""
    if not levels:
        return []

    sorted_levels = sorted(levels)
    clusters = []
    current_cluster = [sorted_levels[0]]

    for level in sorted_levels[1:]:
        if abs(level - current_cluster[-1]) / current_cluster[-1] * 100 < threshold_pct:
            current_cluster.append(level)
        else:
            clusters.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [level]

    clusters.append(sum(current_cluster) / len(current_cluster))

    # Weight by how many times a level was touched (more touches = stronger)
    # Return all unique clusters
    return clusters


def get_key_levels(
    df_daily: pd.DataFrame,
    df_5m: pd.DataFrame | None = None,
    vwap: float | None = None,
) -> dict:
    """Get all key levels a day trader would watch.

    Returns dict with named price levels.
    """
    levels = {}

    if not df_daily.empty and len(df_daily) >= 2:
        # Prior day levels
        levels["prior_day_high"] = round(float(df_daily["High"].iloc[-2]), 2)
        levels["prior_day_low"] = round(float(df_daily["Low"].iloc[-2]), 2)
        levels["prior_day_close"] = round(float(df_daily["Close"].iloc[-2]), 2)

        # Current day
        levels["today_open"] = round(float(df_daily["Open"].iloc[-1]), 2)

    # VWAP
    if vwap:
        levels["vwap"] = round(vwap, 2)

    # Support / Resistance from daily
    if not df_daily.empty:
        sr = find_support_resistance(df_daily)
        levels["support_levels"] = sr["support"][:3]
        levels["resistance_levels"] = sr["resistance"][:3]

    # Intraday levels from 5m data
    if df_5m is not None and not df_5m.empty:
        if hasattr(df_5m.index, 'date'):
            today = df_5m.index.date[-1]
            today_data = df_5m[df_5m.index.date == today]
            if len(today_data) >= 6:
                levels["or_high"] = round(float(today_data["High"].iloc[:6].max()), 2)
                levels["or_low"] = round(float(today_data["Low"].iloc[:6].min()), 2)
                levels["today_high"] = round(float(today_data["High"].max()), 2)
                levels["today_low"] = round(float(today_data["Low"].min()), 2)

    # Round numbers (psychological levels)
    if "prior_day_close" in levels:
        price = levels["prior_day_close"]
        # Find nearest round numbers
        magnitude = 10 ** max(0, len(str(int(price))) - 2)
        round_below = int(price / magnitude) * magnitude
        round_above = round_below + magnitude
        levels["round_number_below"] = round_below
        levels["round_number_above"] = round_above

    return levels


def format_patterns_for_prompt(patterns: list[PatternSignal]) -> str:
    """Format detected patterns into text for Claude's prompt."""
    if not patterns:
        return "No significant patterns detected"

    lines = []
    # Sort by strength (strongest first)
    for p in sorted(patterns, key=lambda x: x.strength, reverse=True):
        emoji = {"bullish": "BULL", "bearish": "BEAR", "neutral": "NEUT"}[p.type]
        lines.append(
            f"[{emoji}] {p.name.upper()} ({p.timeframe}) — strength {p.strength:.0%}\n"
            f"  {p.description}\n"
            f"  Entry: {p.entry_hint}\n"
            f"  Stop: {p.stop_hint}"
        )

    return "\n".join(lines)


def format_levels_for_prompt(levels: dict) -> str:
    """Format key levels into text for Claude's prompt."""
    if not levels:
        return "No key levels available"

    lines = []
    for key, val in levels.items():
        if isinstance(val, list):
            if val:
                lines.append(f"  {key}: {', '.join(f'${v:.2f}' for v in val)}")
        elif isinstance(val, (int, float)):
            lines.append(f"  {key}: ${val:.2f}")

    return "\n".join(lines) if lines else "No key levels available"
