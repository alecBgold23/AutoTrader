"""
COMPREHENSIVE DAY TRADING PATTERNS REFERENCE
=============================================
Extensively researched catalog of every pattern successful day traders use.
Each pattern includes: name, description, type, timeframe, entry trigger,
stop loss, target, confirmation signals, and reliability rating.

This module can be imported by the brain/analyst to identify and act on patterns.
"""

# =============================================================================
# SECTION 1: CANDLESTICK PATTERNS
# =============================================================================

CANDLESTICK_PATTERNS = {

    # -------------------------------------------------------------------------
    # 1.1 SINGLE-CANDLE PATTERNS
    # -------------------------------------------------------------------------

    "doji": {
        "name": "Doji",
        "type": "reversal / indecision",
        "description": (
            "A candle where the open and close are virtually equal, producing "
            "a very small or nonexistent real body with upper and lower shadows. "
            "Signals indecision -- neither buyers nor sellers won the period. "
            "Variants include long-legged doji (long shadows both sides), "
            "dragonfly doji (long lower shadow, no upper -- bullish), and "
            "gravestone doji (long upper shadow, no lower -- bearish)."
        ),
        "timeframe": "5m, 15m preferred. On 1m charts too noisy -- needs extra confirmation.",
        "entry_trigger": (
            "Do NOT trade the doji candle itself. Wait for the NEXT candle to confirm direction. "
            "Bullish doji: enter long when next candle closes above the doji high. "
            "Bearish doji: enter short when next candle closes below the doji low."
        ),
        "stop_loss": (
            "For bullish setup: below the doji low. "
            "For bearish setup: above the doji high."
        ),
        "target": "1:2 risk-reward minimum. Target next support/resistance level.",
        "confirmation": [
            "Location matters most -- doji at support/resistance or key level (VWAP, prior day high/low)",
            "Volume spike on the confirmation candle (2x+ average)",
            "RSI in oversold/overbought territory adds weight",
            "Dragonfly doji at support = strong bullish signal",
            "Gravestone doji at resistance = strong bearish signal",
        ],
        "reliability": "Medium (alone). High when at key levels with volume confirmation.",
        "intraday_notes": (
            "On 1m charts, doji appear constantly and most are meaningless noise. "
            "On 5m and 15m, they become much more significant, especially at VWAP, "
            "prior day high/low, or round numbers. Always require confirmation candle."
        ),
    },

    "hammer": {
        "name": "Hammer / Inverted Hammer",
        "type": "bullish reversal",
        "description": (
            "HAMMER: Small body at the top of the candle range with a long lower shadow "
            "(at least 2x the body length) and little/no upper shadow. Shows sellers "
            "pushed price down during the period but buyers reclaimed it. Found at the "
            "bottom of downtrends. "
            "INVERTED HAMMER: Small body at the bottom with a long upper shadow. "
            "Also bullish reversal at the bottom of a downtrend -- shows buying "
            "interest is emerging."
        ),
        "timeframe": "5m, 15m charts. Usable on 1m for scalping if at key levels.",
        "entry_trigger": (
            "Enter long when the NEXT candle closes above the hammer's high. "
            "Do not enter on the hammer candle itself -- wait for confirmation."
        ),
        "stop_loss": "Below the hammer's low (the tail).",
        "target": (
            "Minimum 1:2 R:R. First target at nearest resistance. "
            "Second target at 2x the hammer's range from entry."
        ),
        "confirmation": [
            "Hammer must appear after a defined downtrend (at least 3-5 red candles prior)",
            "Long lower shadow should be at least 2x the body",
            "Volume on the hammer or confirmation candle should be above average",
            "Location at support, VWAP, or prior day low adds significant weight",
            "Bullish confirmation candle close above hammer high is mandatory",
        ],
        "reliability": "High -- one of the most reliable single-candle reversal signals on 5m+ charts.",
        "intraday_notes": (
            "Hammers at the LOD (low of day) with a volume spike are among the highest "
            "probability long setups in intraday trading. The longer the lower shadow "
            "relative to the body, the stronger the signal."
        ),
    },

    "shooting_star": {
        "name": "Shooting Star / Hanging Man",
        "type": "bearish reversal",
        "description": (
            "SHOOTING STAR: Small body at the bottom of the candle range with a long upper "
            "shadow (at least 2x the body) and little/no lower shadow. The inverse of a "
            "hammer. Shows buyers tried to push price up but sellers overwhelmed them. "
            "Must appear after an uptrend. "
            "HANGING MAN: Same shape as a hammer but appears at the TOP of an uptrend. "
            "The long lower wick shows selling pressure is entering."
        ),
        "timeframe": "5m, 15m. Useful on 1m at HOD (high of day) or key resistance.",
        "entry_trigger": (
            "Enter short when the NEXT candle closes below the shooting star's low. "
            "The confirmation candle is critical -- shooting stars fail often without it."
        ),
        "stop_loss": "Above the shooting star's high (the wick tip).",
        "target": "1:2 R:R minimum. Target nearest support, VWAP, or prior day low.",
        "confirmation": [
            "Must appear after a clear uptrend (3-5 green candles prior minimum)",
            "Upper shadow at least 2x the body length",
            "Volume spike on the shooting star itself = distribution signal",
            "Location at resistance, HOD, prior day high, or round number",
            "Next candle must close bearish below the shooting star low",
        ],
        "reliability": "Medium-High. Very reliable at HOD with climax volume.",
        "intraday_notes": (
            "Shooting stars at the HOD (high of day) especially in the first 30 minutes "
            "after a gap-up are extremely actionable. Look for the upper wick to probe "
            "above a key level and get rejected. Volume on the shooting star candle is "
            "the single best confirmation factor."
        ),
    },

    "marubozu": {
        "name": "Marubozu (Full Body Candle)",
        "type": "continuation / momentum",
        "description": (
            "A candle with a full body and no (or very tiny) wicks. Bullish marubozu "
            "opens at the low and closes at the high -- pure buyer dominance. Bearish "
            "marubozu opens at the high and closes at the low -- pure seller dominance. "
            "Signals strong conviction in the direction."
        ),
        "timeframe": "All intraday timeframes. Strongest on 5m and 15m.",
        "entry_trigger": (
            "Bullish: enter on a pullback to the midpoint of the marubozu or on a break "
            "above the next candle's high. "
            "Bearish: enter short on a pullback to the midpoint or break below next candle low."
        ),
        "stop_loss": "Below the marubozu low (bullish) or above the marubozu high (bearish).",
        "target": "1:2 R:R or the next key level. Marubozu often starts strong moves.",
        "confirmation": [
            "Volume should be significantly above average (2x+)",
            "Often the first candle of a trend change or breakout",
            "No wicks means no indecision -- strong conviction",
        ],
        "reliability": "High for continuation. The bigger the body relative to recent candles, the stronger.",
    },

    "spinning_top": {
        "name": "Spinning Top",
        "type": "indecision / potential reversal",
        "description": (
            "Small real body with long upper and lower shadows of roughly equal length. "
            "Like a doji but with a slightly larger body. Signals hesitation and tug-of-war "
            "between buyers and sellers. Neither side won convincingly."
        ),
        "timeframe": "5m, 15m. Less useful on 1m due to noise.",
        "entry_trigger": (
            "Do not trade the spinning top itself. Wait for next candle direction. "
            "If at resistance and next candle is bearish: short. "
            "If at support and next candle is bullish: long."
        ),
        "stop_loss": "Beyond the spinning top's extreme (high for shorts, low for longs).",
        "target": "Next support/resistance level with minimum 1:2 R:R.",
        "confirmation": [
            "Location at key level is essential",
            "Volume analysis: declining volume into spinning top = exhaustion",
            "Confirmation candle direction is mandatory before entry",
        ],
        "reliability": "Medium. Better as part of a multi-candle pattern than alone.",
    },

    # -------------------------------------------------------------------------
    # 1.2 TWO-CANDLE PATTERNS
    # -------------------------------------------------------------------------

    "bullish_engulfing": {
        "name": "Bullish Engulfing",
        "type": "bullish reversal",
        "description": (
            "A two-candle pattern where a small bearish candle is followed by a larger "
            "bullish candle that completely engulfs (covers) the prior candle's body. "
            "The second candle opens below the prior close and closes above the prior open. "
            "Signals a decisive shift from selling to buying pressure."
        ),
        "timeframe": "5m and 15m are most reliable. Works on 1m at key levels.",
        "entry_trigger": (
            "Enter long at the close of the engulfing candle or on a break above its high "
            "on the next candle. More conservative: wait for the next candle to confirm "
            "by holding above the engulfing candle's midpoint."
        ),
        "stop_loss": "Below the low of the engulfing candle (or the low of the pattern).",
        "target": (
            "Minimum 1:2 R:R. Target the next resistance level, VWAP, or prior day high. "
            "Measured move: the height of the engulfing candle projected upward."
        ),
        "confirmation": [
            "Volume on the engulfing candle should be 2-3x average -- THIS IS CRITICAL",
            "Must appear after a clear downtrend or pullback",
            "The larger the engulfing candle relative to the prior, the stronger",
            "Location at support, VWAP, or prior day low",
            "RSI crossing above 30 from oversold adds weight",
        ],
        "reliability": (
            "HIGH -- one of the most reliable reversal patterns on intraday charts. "
            "Engulfing + volume spike + key level = highest probability setup."
        ),
        "intraday_notes": (
            "Bullish engulfing at VWAP support or at the prior day low with a volume "
            "surge is one of the single best intraday reversal signals. On 5m charts, "
            "accuracy improves 15-20% when combined with volume analysis."
        ),
    },

    "bearish_engulfing": {
        "name": "Bearish Engulfing",
        "type": "bearish reversal",
        "description": (
            "A two-candle pattern where a small bullish candle is followed by a larger "
            "bearish candle that completely engulfs the prior candle's body. "
            "The second candle opens above the prior close and closes below the prior open. "
            "Signals decisive shift from buying to selling."
        ),
        "timeframe": "5m and 15m most reliable.",
        "entry_trigger": (
            "Enter short at the close of the engulfing candle or on a break below its low. "
            "Conservative: wait for next candle to confirm by staying below midpoint."
        ),
        "stop_loss": "Above the high of the engulfing candle.",
        "target": "1:2 R:R minimum. Target next support, VWAP, or prior day low.",
        "confirmation": [
            "Volume spike on engulfing candle (2-3x average)",
            "Must appear after uptrend or bounce",
            "At resistance, HOD, prior day high, or round number",
            "RSI crossing below 70 from overbought",
        ],
        "reliability": "HIGH -- mirror of bullish engulfing. Best at key resistance with volume.",
    },

    "harami": {
        "name": "Harami (Bullish / Bearish)",
        "type": "reversal / indecision",
        "description": (
            "Two-candle pattern where the second candle's body is entirely contained within "
            "the first candle's body (opposite of engulfing). Also called an 'inside bar.' "
            "BULLISH HARAMI: large bearish candle followed by small bullish candle inside it. "
            "BEARISH HARAMI: large bullish candle followed by small bearish candle inside it. "
            "HARAMI CROSS: when the inside bar is a doji -- stronger signal."
        ),
        "timeframe": "5m and 15m. Inside bars on daily chart are the strongest.",
        "entry_trigger": (
            "Bullish harami: enter long when price breaks above the high of the first (mother) candle. "
            "Bearish harami: enter short when price breaks below the low of the mother candle. "
            "The inside bar represents coiled energy -- the breakout direction matters."
        ),
        "stop_loss": (
            "Bullish: below the low of the mother bar. "
            "Bearish: above the high of the mother bar."
        ),
        "target": "1:2 R:R. Height of the mother bar projected from the breakout point.",
        "confirmation": [
            "Volume should contract on the inside bar (coiling) then expand on breakout",
            "Harami cross (doji inside bar) is stronger than regular harami",
            "Location at key level improves accuracy significantly",
            "If inside bar high is broken in day trading, pattern is bullish continuation",
        ],
        "reliability": (
            "Medium alone. Accuracy improves 15-20% with volume analysis. "
            "Harami cross at key levels = high reliability."
        ),
    },

    "tweezer_top_bottom": {
        "name": "Tweezer Top / Tweezer Bottom",
        "type": "reversal",
        "description": (
            "Two consecutive candles with matching highs (tweezer top) or matching lows "
            "(tweezer bottom). Tweezer top: two candles reach the same high -- resistance "
            "confirmed. Tweezer bottom: two candles reach the same low -- support confirmed. "
            "First candle goes with the trend, second candle reverses."
        ),
        "timeframe": "5m, 15m.",
        "entry_trigger": (
            "Tweezer bottom: enter long when next candle breaks above the pattern high. "
            "Tweezer top: enter short when next candle breaks below the pattern low."
        ),
        "stop_loss": "Beyond the matching high/low of the tweezer.",
        "target": "1:2 R:R minimum to next key level.",
        "confirmation": [
            "Matching levels should be precise (within a few cents)",
            "At key support/resistance amplifies reliability",
            "Higher volume on the reversal candle is preferable",
        ],
        "reliability": "Medium-High at key levels. Less reliable in chop.",
    },

    # -------------------------------------------------------------------------
    # 1.3 THREE-CANDLE PATTERNS
    # -------------------------------------------------------------------------

    "morning_star": {
        "name": "Morning Star",
        "type": "bullish reversal",
        "description": (
            "Three-candle pattern: (1) large bearish candle, (2) small-bodied candle "
            "(doji or spinning top) that gaps below the first, (3) large bullish candle "
            "that closes well into the body of the first candle. Signals selling exhaustion "
            "and beginning of buying. The middle candle represents indecision at the bottom."
        ),
        "timeframe": "15m preferred. Works on 5m but gaps are less common intraday.",
        "entry_trigger": (
            "Enter long at the close of the third candle (the bullish candle) "
            "or at the open of the fourth candle for extra confirmation."
        ),
        "stop_loss": "Below the low of the middle candle (the star).",
        "target": (
            "First target: the open of the first bearish candle. "
            "Second target: prior resistance level. Minimum 1:2 R:R."
        ),
        "confirmation": [
            "Third candle should close above the midpoint of the first candle's body",
            "Volume should increase on the third (bullish) candle",
            "Middle candle should have low volume (indecision/exhaustion)",
            "At support level, VWAP, or prior day low = high confidence",
            "If middle candle is a doji (morning doji star) = stronger signal",
        ],
        "reliability": "HIGH -- one of the most trusted three-candle reversal patterns.",
    },

    "evening_star": {
        "name": "Evening Star",
        "type": "bearish reversal",
        "description": (
            "Three-candle pattern: (1) large bullish candle, (2) small-bodied candle "
            "that gaps above the first, (3) large bearish candle that closes well into "
            "the body of the first candle. Mirror of morning star. Signals buying "
            "exhaustion at the top."
        ),
        "timeframe": "15m preferred. Works on 5m.",
        "entry_trigger": (
            "Enter short at the close of the third candle or at the open of the fourth."
        ),
        "stop_loss": "Above the high of the middle candle (the star).",
        "target": "First target: the open of the first bullish candle. Prior support. 1:2 R:R.",
        "confirmation": [
            "Third candle must close below the midpoint of the first candle's body",
            "Volume increase on the third (bearish) candle",
            "At resistance, HOD, or prior day high",
            "Evening doji star (doji middle) = strongest variant",
        ],
        "reliability": "HIGH -- trusted bearish reversal, especially at key resistance levels.",
    },

    "three_white_soldiers": {
        "name": "Three White Soldiers",
        "type": "bullish reversal / continuation",
        "description": (
            "Three consecutive long-bodied bullish candles, each opening within the prior "
            "candle's real body and closing at a new high. Each candle should have small "
            "or no upper shadows (showing sustained buying, not fading into closes). "
            "Appears after a downtrend, signaling strong reversal."
        ),
        "timeframe": "5m and 15m. On 1m, pattern is less meaningful.",
        "entry_trigger": (
            "Enter long at the close of the third candle or slightly above it. "
            "Conservative entry: enter on a pullback to the close of the second candle."
        ),
        "stop_loss": "Below the low of the first candle in the formation.",
        "target": "Measured move: height of the three-candle pattern projected upward. 1:2 R:R.",
        "confirmation": [
            "Each candle should open within the prior candle's body",
            "Each candle should close at or near its high (small upper wicks)",
            "Volume should increase across the three candles (progressive conviction)",
            "Must appear after a downtrend -- not in the middle of a range",
        ],
        "reliability": (
            "HIGH for reversal confirmation. However, by the third candle, "
            "much of the move may have occurred. Best used as confirmation to hold "
            "an existing position rather than initiate a new one at the third candle."
        ),
    },

    "three_black_crows": {
        "name": "Three Black Crows",
        "type": "bearish reversal",
        "description": (
            "Three consecutive long-bodied bearish candles, each opening within the prior "
            "candle's real body and closing at a new low. Mirror of three white soldiers. "
            "Small or no lower shadows. Appears after an uptrend."
        ),
        "timeframe": "5m and 15m.",
        "entry_trigger": (
            "Enter short at the close of the third candle or slightly below. "
            "Conservative: short on a bounce to the close of the second candle."
        ),
        "stop_loss": "Above the high of the first candle in the formation.",
        "target": "Measured move: height of the pattern projected downward.",
        "confirmation": [
            "Each candle opens within the prior body and closes at a new low",
            "Small or no lower shadows on each candle",
            "Increasing volume across the three candles",
            "Must appear after an uptrend",
        ],
        "reliability": "HIGH as bearish reversal confirmation.",
    },

    "three_inside_up_down": {
        "name": "Three Inside Up / Three Inside Down",
        "type": "reversal",
        "description": (
            "THREE INSIDE UP: (1) large bearish candle, (2) small bullish candle inside "
            "the first (bullish harami), (3) bullish candle that closes above the first candle's high. "
            "THREE INSIDE DOWN: inverse -- (1) large bullish, (2) small bearish inside, "
            "(3) bearish candle closing below the first candle's low. "
            "Essentially a harami with confirmation."
        ),
        "timeframe": "5m, 15m.",
        "entry_trigger": (
            "Three inside up: enter long at close of third candle. "
            "Three inside down: enter short at close of third candle."
        ),
        "stop_loss": "Beyond the extreme of the first candle.",
        "target": "1:2 R:R to next key level.",
        "confirmation": [
            "Third candle must close convincingly beyond the first candle",
            "Volume should expand on the third candle",
            "At key support/resistance",
        ],
        "reliability": "Medium-High. The third candle provides the confirmation harami lacks alone.",
    },
}


# =============================================================================
# SECTION 2: CHART PATTERNS
# =============================================================================

CHART_PATTERNS = {

    "bull_flag": {
        "name": "Bull Flag",
        "type": "bullish continuation",
        "description": (
            "A sharp, high-volume move up (the flagpole) followed by a short, "
            "tight consolidation that slopes slightly downward or moves sideways "
            "(the flag). The consolidation has parallel lines forming a small channel. "
            "Price then breaks out upward continuing the original move. "
            "This is THE most popular day trading pattern for momentum traders."
        ),
        "timeframe": "1m and 5m for day trading. Also works on 15m.",
        "entry_trigger": (
            "Enter long when price breaks above the upper trendline of the flag with "
            "increasing volume. The breakout candle should close above the flag. "
            "Aggressive: buy the break of the flag high. "
            "Conservative: wait for a close above and/or a retest of the flag high."
        ),
        "stop_loss": (
            "Below the lower trendline of the flag, or below the most recent swing low "
            "within the flag. Tight stop = below the flag low."
        ),
        "target": (
            "MEASURED MOVE: Measure the height of the flagpole (from the bottom of the "
            "pole to the top). Add that distance to the breakout point. "
            "First target = 50% of the pole height. Second target = 100% of pole height."
        ),
        "confirmation": [
            "Volume must decrease during the flag formation (consolidation)",
            "Volume must SPIKE on the breakout (validates institutional participation)",
            "Flag should retrace only 20-50% of the flagpole (shallow pullback = strong)",
            "Flag duration should be shorter than the pole duration",
            "The tighter and more orderly the flag, the better",
            "Ideally forms near VWAP or above VWAP in an uptrend day",
        ],
        "reliability": (
            "VERY HIGH -- the single most reliable and traded continuation pattern "
            "in intraday trading. Success rate improves dramatically with volume confirmation."
        ),
        "intraday_notes": (
            "Bull flags on the 1m and 5m chart are the bread and butter of momentum day trading. "
            "The best flags form in the first 1-2 hours of the trading day on high relative volume "
            "stocks. Look for flags that hold above VWAP. Multiple flags can form on the same "
            "stock in a single day (flag 1, flag 2, flag 3) -- the first flag is highest probability."
        ),
    },

    "bear_flag": {
        "name": "Bear Flag",
        "type": "bearish continuation",
        "description": (
            "Mirror of bull flag. A sharp move down (flagpole) followed by a slight "
            "upward or sideways consolidation (flag), then a breakdown continuing the "
            "downward move. Parallel consolidation lines slope slightly upward."
        ),
        "timeframe": "1m, 5m, 15m.",
        "entry_trigger": (
            "Enter short when price breaks below the lower trendline of the flag. "
            "Breakout candle must close below the flag."
        ),
        "stop_loss": "Above the upper trendline of the flag or the flag high.",
        "target": "Measured move: flagpole height projected downward from breakout point.",
        "confirmation": [
            "Decreasing volume during flag, increasing on breakdown",
            "Flag retraces only 20-50% of the drop",
            "Below VWAP on the day",
        ],
        "reliability": "HIGH -- same reliability as bull flag but for shorts.",
    },

    "pennant": {
        "name": "Pennant (Bullish / Bearish)",
        "type": "continuation",
        "description": (
            "Similar to a flag but the consolidation forms a small symmetrical triangle "
            "(converging trendlines) instead of a parallel channel. Shows compression "
            "and coiling before a continuation move. Bullish pennant: after upward pole. "
            "Bearish pennant: after downward pole."
        ),
        "timeframe": "1m, 5m, 15m.",
        "entry_trigger": (
            "Enter on the breakout from the triangle in the direction of the prior move. "
            "Must have a candle close beyond the trendline."
        ),
        "stop_loss": "Inside the pennant on the opposite side of the breakout.",
        "target": "Measured move: flagpole height projected from breakout point.",
        "confirmation": [
            "Volume contracts during the pennant (coiling)",
            "Volume expands on breakout",
            "Tighter pennant = more explosive breakout",
            "Should resolve quickly -- if it drags on too long, the pattern weakens",
        ],
        "reliability": "HIGH -- similar to flags. Tighter pennants often produce stronger moves.",
    },

    "ascending_triangle": {
        "name": "Ascending Triangle",
        "type": "bullish continuation / breakout",
        "description": (
            "A flat (horizontal) resistance level on top with rising support (higher lows) "
            "on the bottom. Price compresses into the apex. Buyers are getting more aggressive "
            "(willing to pay higher prices), while sellers defend the same level. "
            "Typically breaks out upward."
        ),
        "timeframe": "5m, 15m for day trading. Also powerful on daily charts.",
        "entry_trigger": (
            "Enter long on a breakout above the flat resistance with a candle close above. "
            "Volume must confirm. Alternative: enter on the rising trendline bounce with "
            "stop below it."
        ),
        "stop_loss": (
            "Below the rising trendline or below the most recent higher low. "
            "For breakout trades: below the resistance level (now support)."
        ),
        "target": (
            "Measured move: the HEIGHT of the triangle (from flat resistance to the "
            "lowest point) projected upward from the breakout level."
        ),
        "confirmation": [
            "Volume should decrease as pattern develops (compression)",
            "Breakout volume must be significantly above average",
            "At least 2 touches on both the flat resistance and the rising support",
            "The more touches, the more significant the breakout",
            "Price in the final third of the triangle before breakout is ideal",
        ],
        "reliability": (
            "HIGH -- ascending triangles break upward ~70% of the time. "
            "One of the most reliable breakout patterns."
        ),
    },

    "descending_triangle": {
        "name": "Descending Triangle",
        "type": "bearish continuation / breakdown",
        "description": (
            "A flat (horizontal) support level on the bottom with declining resistance "
            "(lower highs) on top. Sellers are getting more aggressive, buyers defend "
            "the same support. Typically breaks down."
        ),
        "timeframe": "5m, 15m for day trading.",
        "entry_trigger": (
            "Enter short on a breakdown below the flat support with candle close below. "
            "Volume must confirm."
        ),
        "stop_loss": "Above the declining trendline or the most recent lower high.",
        "target": "Measured move: triangle height projected downward from the breakdown.",
        "confirmation": [
            "Decreasing volume during pattern, spike on breakdown",
            "At least 2 touches on both levels",
            "Lower highs should be progressively lower",
        ],
        "reliability": "HIGH -- descending triangles break down ~70% of the time.",
    },

    "symmetrical_triangle": {
        "name": "Symmetrical Triangle",
        "type": "continuation (direction of prior trend)",
        "description": (
            "Both trendlines converge -- lower highs AND higher lows compress price into "
            "an apex. Represents pure indecision. Can break either way but USUALLY breaks "
            "in the direction of the prior trend."
        ),
        "timeframe": "5m, 15m.",
        "entry_trigger": "Enter on the breakout in either direction with a candle close and volume.",
        "stop_loss": "Inside the triangle on the opposite side of the breakout.",
        "target": "Measured move: widest part of the triangle projected from the breakout.",
        "confirmation": [
            "Volume compression during pattern, expansion on breakout",
            "Breakout in direction of prior trend is higher probability",
            "At least 4 total touches (2 on each trendline)",
        ],
        "reliability": "Medium-High. Less directional bias than ascending/descending triangles.",
    },

    "head_and_shoulders": {
        "name": "Head and Shoulders (and Inverse)",
        "type": "reversal",
        "description": (
            "STANDARD (bearish reversal): Three peaks where the middle peak (head) is "
            "higher than the two outer peaks (shoulders). A neckline connects the lows "
            "between the peaks. When price breaks the neckline, it signals a reversal. "
            "INVERSE (bullish reversal): Three troughs where the middle is deepest. "
            "Neckline connects the highs. Break above neckline = bullish reversal."
        ),
        "timeframe": (
            "15m and above for day trading. Rarely forms cleanly on 1m or 5m intraday. "
            "More common on daily/weekly charts but does appear on 15m-1hr intraday."
        ),
        "entry_trigger": (
            "METHOD 1 (aggressive): Enter on the neckline break -- short when candle "
            "closes below neckline (standard) or long when candle closes above (inverse). "
            "METHOD 2 (conservative): Wait for the neckline break AND a retest of the "
            "neckline from the other side. Retest entry gives better R:R."
        ),
        "stop_loss": (
            "Standard H&S: above the right shoulder. "
            "Inverse H&S: below the right shoulder. "
            "TWO-CANDLE RULE: move stop to two candles back from the entry for better placement."
        ),
        "target": (
            "MEASURED MOVE: Distance from the head to the neckline, projected from the "
            "neckline breakout point. Example: head at $55, neckline at $50, target = $45. "
            "Use multiple targets: T1 = 50% of measured move, T2 = 100%. "
            "Aim for minimum 1:3 risk-reward."
        ),
        "confirmation": [
            "Volume should decline from left shoulder to head to right shoulder",
            "Volume should SPIKE on the neckline break",
            "Right shoulder should have notably less volume than the head",
            "Sloping neckline (down for standard, up for inverse) is still valid",
            "The more symmetrical the shoulders, the more textbook -- but asymmetry is common",
        ],
        "reliability": (
            "HIGH -- one of the most studied and reliable reversal patterns in all of "
            "technical analysis. Bulkowski research: 93% success rate for H&S with "
            "downward breakout."
        ),
    },

    "double_top": {
        "name": "Double Top",
        "type": "bearish reversal",
        "description": (
            "Price reaches a high, pulls back, rallies to approximately the same high, "
            "and fails again. Creates an 'M' shape. The level between the two peaks "
            "(the pullback low) forms the neckline. Signals that buyers cannot push "
            "past resistance and a reversal is likely."
        ),
        "timeframe": "5m, 15m for intraday. Also works on higher timeframes.",
        "entry_trigger": (
            "Enter short when price breaks below the neckline (pullback low between peaks). "
            "Wait for a candle CLOSE below -- not just a wick. "
            "Conservative: wait for a break and retest of the neckline from below."
        ),
        "stop_loss": "Above the highest point of the double top.",
        "target": (
            "MEASURED MOVE: Distance from the peaks to the neckline, projected downward "
            "from the neckline. Example: peaks at $50, neckline at $48, target = $46."
        ),
        "confirmation": [
            "Second peak should have LESS volume than the first (waning buying power)",
            "Neckline break should come with increased volume",
            "Peaks don't need to be exact -- within 1-2% is valid",
            "Bearish divergence on RSI between the two peaks strengthens signal",
            "Beware of fakeout breaks -- wait for candle close below neckline",
        ],
        "reliability": "HIGH -- very reliable reversal pattern, especially with volume divergence.",
    },

    "double_bottom": {
        "name": "Double Bottom",
        "type": "bullish reversal",
        "description": (
            "Price reaches a low, bounces, drops to approximately the same low, and bounces "
            "again. Creates a 'W' shape. The neckline is the high between the two troughs. "
            "Break above neckline = bullish reversal confirmed."
        ),
        "timeframe": "5m, 15m for intraday.",
        "entry_trigger": (
            "Enter long when price breaks above the neckline (the high between the troughs). "
            "Candle close above required. Conservative: wait for break and retest."
        ),
        "stop_loss": "Below the lower of the two troughs.",
        "target": "Measured move: distance from troughs to neckline, projected upward.",
        "confirmation": [
            "Second trough should have less volume than the first (selling exhaustion)",
            "Bullish divergence on RSI between the two troughs",
            "Volume increase on the neckline break",
        ],
        "reliability": "HIGH -- one of the most reliable bullish reversal patterns.",
    },

    "triple_top": {
        "name": "Triple Top",
        "type": "bearish reversal",
        "description": (
            "Three peaks at approximately the same level with pullbacks between them. "
            "Stronger resistance confirmation than double top since price failed three times. "
            "Support level (neckline) connects the two pullback lows between the peaks."
        ),
        "timeframe": "15m and above. Rare on very short timeframes.",
        "entry_trigger": "Enter short when price breaks below the support/neckline. Candle close required.",
        "stop_loss": "Above the high of the third peak.",
        "target": "Measured move: distance from peaks to neckline, projected downward.",
        "confirmation": [
            "Declining volume on each successive peak",
            "Volume spike on breakdown",
            "Three touches at resistance is rare and very significant",
        ],
        "reliability": "VERY HIGH -- three failed attempts is a very strong reversal signal.",
    },

    "triple_bottom": {
        "name": "Triple Bottom",
        "type": "bullish reversal",
        "description": (
            "Three troughs at approximately the same level. Stronger support confirmation "
            "than double bottom. Resistance level connects the two bounce highs."
        ),
        "timeframe": "15m and above.",
        "entry_trigger": "Enter long when price breaks above the resistance/neckline.",
        "stop_loss": "Below the low of the third trough.",
        "target": "Measured move: distance from troughs to neckline, projected upward.",
        "confirmation": [
            "Declining volume on each successive trough (selling exhaustion)",
            "Volume spike on breakout",
        ],
        "reliability": "VERY HIGH.",
    },

    "cup_and_handle": {
        "name": "Cup and Handle",
        "type": "bullish continuation",
        "description": (
            "A rounded U-shaped bottom (the cup) followed by a small downward-sloping "
            "or sideways consolidation (the handle). The cup shows a gradual shift from "
            "selling to buying. The handle is a final shakeout before the breakout. "
            "Considered one of the most dependable bullish continuation patterns."
        ),
        "timeframe": (
            "More common on daily/weekly charts. On intraday, look for it on 15m-1hr. "
            "Intraday cup and handles form over 1-3 hours typically."
        ),
        "entry_trigger": (
            "Enter long when price breaks above the handle resistance (the lip of the cup). "
            "A buy-stop a few ticks above the handle resistance is common. "
            "Must have volume on the breakout."
        ),
        "stop_loss": "Below the handle's low.",
        "target": (
            "MEASURED MOVE: Depth of the cup (from the lip to the bottom) projected "
            "upward from the breakout point. Example: lip at $50, cup bottom at $45, "
            "target = $55."
        ),
        "confirmation": [
            "Volume should decline through the cup formation",
            "Volume should be light during the handle (final shakeout)",
            "Volume must SPIKE on the breakout above the handle",
            "Handle should retrace no more than 50% of the cup",
            "The cup should be U-shaped, not V-shaped (gradual is better)",
            "Handle can be a small flag, channel, or triangle",
        ],
        "reliability": "HIGH -- one of the most reliable bullish patterns, especially with volume.",
    },

    "rising_wedge": {
        "name": "Rising Wedge",
        "type": "bearish reversal (or bearish continuation in downtrend)",
        "description": (
            "Both support and resistance trendlines slope upward but converge, with "
            "resistance rising more slowly than support. Price makes higher highs and "
            "higher lows but the range compresses. Signals that buying momentum is "
            "weakening. Typically breaks DOWN."
        ),
        "timeframe": "5m, 15m for intraday.",
        "entry_trigger": (
            "Enter short on a break below the lower trendline (support). "
            "Wait for a candle close below AND ideally a retest of the broken trendline "
            "from below (now resistance). Retest entry is highest probability."
        ),
        "stop_loss": "Above the last swing high within the wedge.",
        "target": (
            "Measured move: height of the widest part of the wedge projected downward. "
            "Minimum 1:2 R:R."
        ),
        "confirmation": [
            "Volume should decrease as the wedge develops",
            "Breakdown should come with volume expansion",
            "Bearish divergence on RSI adds strong confirmation",
            "At least 2 touches on each trendline",
        ],
        "reliability": "HIGH -- rising wedges resolve to the downside ~65-70% of the time.",
    },

    "falling_wedge": {
        "name": "Falling Wedge",
        "type": "bullish reversal (or bullish continuation in uptrend)",
        "description": (
            "Both trendlines slope downward and converge, with support falling more "
            "slowly than resistance. Price makes lower highs and lower lows but range "
            "compresses. Signals selling momentum is weakening. Breaks UP."
        ),
        "timeframe": "5m, 15m for intraday.",
        "entry_trigger": (
            "Enter long on a break above the upper trendline (resistance). "
            "Wait for candle close above, ideally with a retest of the broken trendline."
        ),
        "stop_loss": "Below the last swing low within the wedge.",
        "target": "Measured move: widest part of wedge projected upward. 1:2 R:R minimum.",
        "confirmation": [
            "Volume contraction during wedge, expansion on breakout",
            "Bullish divergence on RSI",
            "At least 2 touches on each trendline",
        ],
        "reliability": (
            "VERY HIGH -- 74% success rate in bull markets per Bulkowski research. "
            "Falling wedges produce more reliable breakouts than rising wedges."
        ),
    },

    "channel_breakout": {
        "name": "Channel Breakout (Ascending / Descending / Horizontal)",
        "type": "breakout / continuation or reversal",
        "description": (
            "Price trades within parallel trendlines (a channel) and then breaks out. "
            "ASCENDING CHANNEL: both lines slope up. Breakout above = continuation, "
            "breakdown below = reversal. "
            "DESCENDING CHANNEL: both lines slope down. Breakdown = continuation, "
            "breakout above = reversal. "
            "HORIZONTAL CHANNEL (range): flat support and resistance. Break either way."
        ),
        "timeframe": "All intraday timeframes.",
        "entry_trigger": (
            "Enter on a candle close outside the channel with volume. "
            "Best entry: break and retest of the channel boundary."
        ),
        "stop_loss": "Inside the channel, on the opposite side of the breakout.",
        "target": "Measured move: channel width projected from the breakout point.",
        "confirmation": [
            "Volume must expand on the breakout",
            "Candle close outside the channel (not just a wick)",
            "Break and retest is higher probability than immediate chase",
            "The longer the channel existed, the more powerful the breakout",
        ],
        "reliability": "Medium-High. Depends on how well-defined the channel is.",
    },
}


# =============================================================================
# SECTION 3: INTRADAY-SPECIFIC PATTERNS / STRATEGIES
# =============================================================================

INTRADAY_PATTERNS = {

    "opening_range_breakout": {
        "name": "Opening Range Breakout (ORB)",
        "type": "breakout",
        "description": (
            "Uses the price range (high and low) established during the first X minutes "
            "of the market open as a framework. When price breaks above or below this "
            "range, it triggers a directional trade. The opening range is when the most "
            "information is being priced in -- overnight news, pre-market activity, etc."
        ),
        "timeframes": {
            "5_min_orb": (
                "First 5 minutes (9:30-9:35). Smallest range, tightest stops, "
                "most false breakouts. Best for aggressive scalpers."
            ),
            "15_min_orb": (
                "First 15 minutes (9:30-9:45). Most popular timeframe. "
                "Balances between avoiding noise and capturing early moves. "
                "Good starting point for most traders."
            ),
            "30_min_orb": (
                "First 30 minutes (9:30-10:00). Wider range, fewer false breakouts, "
                "but also captures more of the initial move (less profit potential)."
            ),
            "60_min_orb": (
                "First 60 minutes (9:30-10:30). Widest range, fewest false breakouts. "
                "Highest win rate (89.4% in backtests) but widest stops."
            ),
        },
        "setup_rules": (
            "1. At 9:30 AM EST market open, start your timer. "
            "2. Mark the HIGH and LOW of the chosen time period. "
            "3. Draw horizontal lines at these levels. "
            "4. The range should be at least 0.2% of the stock price to be meaningful. "
            "5. Avoid very wide ranges (> 2% of price) as stops will be too large."
        ),
        "entry_trigger": (
            "LONG: A full candle closes ABOVE the opening range high. "
            "SHORT: A full candle closes BELOW the opening range low. "
            "Do NOT enter on a wick above/below -- must be a CLOSE. "
            "Volume on the breakout candle should be higher than the average "
            "of the candles during the range formation."
        ),
        "stop_loss": {
            "conservative": "Opposite end of the opening range (1:1 R:R framework).",
            "moderate": "50% of the range (midpoint) -- gives 1:1.5+ R:R.",
            "aggressive": "Just below/above the breakout candle -- tight but higher fail rate.",
        },
        "target": {
            "target_1": "The range height (measured move) projected from the breakout point.",
            "target_2": "Next key level (prior day high/low, premarket levels, VWAP).",
            "target_3": "2x the range height for runners.",
            "time_based": "Many traders exit within 30-90 minutes if momentum fades.",
        },
        "confirmation": [
            "Volume spike on the breakout candle is ESSENTIAL",
            "Pre-market catalyst (news, earnings, sector momentum) = higher success",
            "Avoid ORB on low-volume, no-catalyst stocks -- too many false breakouts",
            "If the range is too narrow, the stock may not have enough momentum",
            "If the range is too wide, the risk (stop distance) may be too large",
            "Best on stocks with Average True Range (ATR) that exceeds the opening range",
        ],
        "reliability": (
            "HIGH -- one of the most backtested and validated day trading strategies. "
            "60-min ORB: 89.4% win rate in backtests. 15-min ORB: 60-70% win rate. "
            "5-min ORB: 55-65% win rate. Success increases dramatically with volume filters."
        ),
        "risk_management": "Limit risk to 1-2% of total trading capital per ORB trade.",
    },

    "gap_and_go": {
        "name": "Gap and Go",
        "type": "momentum continuation",
        "description": (
            "A stock gaps up (opens significantly higher than prior close) on a catalyst "
            "and continues to move higher after the open. The gap signals overnight demand "
            "that is continuing into the regular session. Trades in the direction of the gap."
        ),
        "setup_criteria": [
            "Gap up of at least 4-5% from prior close (significant move)",
            "Clear catalyst: earnings beat, FDA approval, contract win, upgrade, etc.",
            "Pre-market volume at least 2-3x average daily volume",
            "Price above VWAP in pre-market or reclaims VWAP quickly after open",
            "Clean chart: not running into major overhead resistance",
        ],
        "entry_trigger": (
            "METHOD 1 (First candle break): Buy when the first 1-min or 5-min candle "
            "breaks above the pre-market high. "
            "METHOD 2 (Pullback entry): Wait for a pullback to the gap level or VWAP "
            "and enter on the bounce. Better R:R but may miss the move. "
            "METHOD 3 (Flag breakout): Wait for the first bull flag to form after the gap "
            "and enter on the flag breakout."
        ),
        "stop_loss": (
            "Below the low of the first candle, below VWAP, or below the gap level "
            "depending on entry method. If the stock fills the gap, the thesis is broken."
        ),
        "target": (
            "Intraday targets based on ATR, prior resistance levels, or round numbers. "
            "Let winners run with a trailing stop. Take partial profits at 1:2 R:R."
        ),
        "confirmation": [
            "Sustained volume throughout the session (not just at open)",
            "Price holds above VWAP after initial move",
            "Level 2 shows strong buying pressure (large bids, orders hitting the ask)",
            "Sector momentum in the same direction helps",
            "Time-based: if no follow-through within 30 minutes, consider exiting",
        ],
        "reliability": (
            "HIGH on stocks with genuine catalysts and volume. "
            "LOW on low-float, no-catalyst gap-ups (pump and dump risk)."
        ),
    },

    "gap_and_fade": {
        "name": "Gap and Fade (Gap Fill)",
        "type": "mean reversion / reversal",
        "description": (
            "A stock gaps up or down but fails to follow through. Instead, price reverses "
            "to fill the gap (return to prior day's close). Works on gaps that lack "
            "fundamental justification, exhaustion gaps, or overextended moves."
        ),
        "setup_criteria": [
            "Gap lacks a strong fundamental catalyst (sympathy play, low quality news)",
            "Gap into major resistance (prior high, round number) and gets rejected",
            "Pre-market volume fading as open approaches",
            "Extended gap (>10%) on a stock with history of gap fills",
            "Gap into an overbought RSI reading",
        ],
        "entry_trigger": (
            "WAIT for reversal confirmation -- do NOT short a gap-up at the open blindly. "
            "Enter when: (1) first 5-min candle closes red, (2) price breaks below VWAP, "
            "(3) shooting star or bearish engulfing at HOD, (4) clear lower high forms "
            "after the open. "
            "For gap-down fades: enter long when first candle closes green or price "
            "reclaims VWAP."
        ),
        "stop_loss": (
            "Above the HOD (for shorting gap-up fades). "
            "Below the LOD (for buying gap-down fades). "
            "Wider stops needed -- gap fills can be volatile."
        ),
        "target": (
            "TARGET 1: Prior day close (the full gap fill). "
            "TARGET 2: VWAP if trading against the gap. "
            "Many gaps fill 50-80% rather than completely."
        ),
        "confirmation": [
            "Volume declining after the open (momentum fading)",
            "Price rejection at key levels (failed breakout)",
            "Level 2 showing large offers / resistance at the top",
            "Statistical tendency: ~70% of gaps eventually fill (varies by type)",
        ],
        "reliability": (
            "Medium-High for gaps without catalysts. "
            "LOW for gaps with strong fundamental catalysts (those tend to Go, not Fade)."
        ),
    },

    "vwap_reclaim": {
        "name": "VWAP Reclaim",
        "type": "bullish reversal / momentum shift",
        "description": (
            "A stock opens below VWAP (or trades below it for 1-2 hours) and then "
            "reclaims VWAP with conviction and volume. This signals that the dynamic "
            "of the day has changed -- buyers have taken over from sellers. "
            "VWAP (Volume Weighted Average Price) represents the average price "
            "weighted by volume -- institutional benchmark."
        ),
        "setup_criteria": [
            "Stock must have a catalyst or significant daily chart setup",
            "Must be trading below VWAP for a meaningful period (not just 5 minutes)",
            "Morning selloff should be on declining/low volume (not conviction selling)",
            "Wait at least 15-30 minutes for VWAP to develop meaningful data",
        ],
        "entry_trigger": (
            "Enter long when price closes back above VWAP with a strong candle "
            "and volume surge. The candle should close convincingly above VWAP, "
            "not just touch it. Ideal: break of market structure (higher high) "
            "coincides with VWAP reclaim."
        ),
        "stop_loss": "Just below VWAP. If price loses VWAP again, the thesis is invalid.",
        "target": (
            "TARGET 1: Pre-market high or HOD. "
            "TARGET 2: Prior day high. "
            "TARGET 3: Let it run with a trailing stop if momentum is strong."
        ),
        "confirmation": [
            "Volume surge on the reclaim candle is critical",
            "The FIRST VWAP test after a strong move is the highest probability",
            "VWAP should be sloping or flat -- not declining sharply",
            "Midday news/catalyst causing the reclaim adds conviction",
            "If VWAP is completely flat and price is chopping across it = NO TRADE",
        ],
        "reliability": (
            "HIGH when on a stock with a catalyst and clear volume expansion. "
            "VWAP reclaims are one of the most watched institutional-level signals."
        ),
    },

    "vwap_rejection": {
        "name": "VWAP Rejection",
        "type": "bearish continuation / short setup",
        "description": (
            "A stock is trending down, attempts to bounce back up to VWAP, but gets "
            "rejected. VWAP acts as dynamic resistance. When price fails to reclaim VWAP "
            "and starts rolling over, that is a high-probability short entry."
        ),
        "entry_trigger": (
            "Enter short when price tests VWAP from below, fails to close above it, "
            "and the next candle starts moving lower. The failed VWAP test is the signal."
        ),
        "stop_loss": "Just above VWAP. Tight stop since the level is clearly defined.",
        "target": "LOD retest, prior day low, or next support level below.",
        "confirmation": [
            "Price approaches VWAP on declining volume (weak bounce)",
            "Rejection candle (shooting star, bearish engulfing) at VWAP",
            "Sellers visible on Level 2 at VWAP",
            "Overall market weak / sector weak adds confirmation",
        ],
        "reliability": "HIGH for intraday shorts on weak/downtrending stocks.",
    },

    "hod_lod_break_and_retest": {
        "name": "HOD/LOD Break and Retest",
        "type": "breakout continuation",
        "description": (
            "HOD = High of Day. LOD = Low of Day. When a stock breaks to a new HOD or LOD, "
            "it often pulls back to retest the broken level before continuing. The retest "
            "confirms the breakout is legitimate and gives a better entry than chasing."
        ),
        "entry_trigger": (
            "HOD BREAK: Stock makes new high of day, pulls back to the prior HOD level "
            "(now support), and bounces. Enter long on the bounce with confirmation. "
            "LOD BREAK: Stock makes new low of day, bounces to the prior LOD level "
            "(now resistance), and rejects. Enter short on the rejection."
        ),
        "stop_loss": (
            "HOD play: below the retest low (if price falls back below prior HOD, thesis fails). "
            "LOD play: above the retest high."
        ),
        "target": "Next key level, measured move, or trailing stop.",
        "confirmation": [
            "Volume should expand on the initial break",
            "Pullback/retest should be on decreasing volume (healthy retest)",
            "Bounce/rejection off the retest level should have volume increase",
            "The break should be decisive, not a marginal new high/low",
        ],
        "reliability": "HIGH -- one of the most reliable intraday setups. Break and retest is a staple.",
    },

    "red_to_green": {
        "name": "Red to Green Move",
        "type": "bullish reversal / momentum",
        "description": (
            "A stock that opened in the red (below prior day close) reverses during the "
            "session and trades above the prior day close (turns green). This signals "
            "that selling pressure has been fully absorbed and buyers are now in control. "
            "Very powerful on high-volume, catalyst stocks."
        ),
        "entry_trigger": (
            "Enter long when price crosses above the prior day's close (the red-to-green "
            "level). Best to wait for a 1-min or 5-min candle to CLOSE above this level. "
            "Even better: wait for a break and retest of the prior close."
        ),
        "stop_loss": "Below the session low or below VWAP (whichever is closer to entry).",
        "target": (
            "Pre-market high, prior day high, or new HOD. "
            "Red to green moves often produce large intraday moves because shorts "
            "who shorted the gap-down are now trapped and must cover."
        ),
        "confirmation": [
            "High relative volume (the stock must be 'in play')",
            "Strong catalyst supporting the reversal",
            "Volume increasing as price approaches and crosses the prior close",
            "Short squeeze dynamics: high short interest amplifies the move",
        ],
        "reliability": (
            "HIGH on stocks with catalysts and high volume. The red-to-green level "
            "is one of the most watched intraday levels by active traders."
        ),
    },

    "green_to_red": {
        "name": "Green to Red Move",
        "type": "bearish reversal / short signal",
        "description": (
            "A stock that opened in the green (above prior close) reverses and trades "
            "below the prior close (turns red). Signals that the gap-up or early strength "
            "has been completely reversed and sellers are now dominant."
        ),
        "entry_trigger": (
            "Enter short when price crosses below the prior day close. Wait for a candle "
            "close below. Best to wait for a retest of the prior close from below (now resistance)."
        ),
        "stop_loss": "Above the session high or above VWAP.",
        "target": "Prior day low, LOD, or next support level.",
        "confirmation": [
            "Increasing volume on the breakdown",
            "Failed gap-up (catalyst was weak or stock was already extended)",
            "VWAP lost and acting as resistance",
        ],
        "reliability": "HIGH -- opposite of red-to-green with same dynamics.",
    },

    "first_pullback": {
        "name": "First Pullback After Strong Move",
        "type": "continuation",
        "description": (
            "After a stock makes a strong initial move (e.g., gap-up and run, ORB breakout), "
            "the FIRST pullback is the highest probability continuation entry. The first "
            "pullback is where institutional traders who missed the initial move step in. "
            "Subsequent pullbacks (2nd, 3rd) have decreasing probability."
        ),
        "entry_trigger": (
            "After the initial move, wait for the first consolidation or pullback. "
            "Enter when: (1) a bull flag forms and breaks out, (2) price bounces off VWAP, "
            "(3) price holds at a key support level and resumes the trend. "
            "The pullback should retrace 20-50% of the initial move (shallow = strong)."
        ),
        "stop_loss": "Below the pullback low. If the pullback erases more than 50-62% of the move, the thesis weakens.",
        "target": (
            "New HOD, measured move from the pullback, or prior resistance level. "
            "The first pullback often produces a move equal to or greater than the initial move."
        ),
        "confirmation": [
            "Volume should decrease during the pullback (healthy profit-taking, not panic)",
            "Volume should increase when price resumes the trend direction",
            "The pullback should be orderly (flag/pennant), not a violent reversal",
            "VWAP should hold as support during the pullback",
            "The deeper the pullback, the less reliable the continuation",
        ],
        "reliability": (
            "VERY HIGH -- the first pullback after a strong move is consistently one of "
            "the highest win rate setups in day trading. The second pullback is decent. "
            "Third pullback and beyond: significantly lower probability."
        ),
    },

    "abcd_pattern": {
        "name": "ABCD Pattern",
        "type": "harmonic / reversal",
        "description": (
            "A four-point pattern with two legs (AB and CD) and one retracement (BC). "
            "AB is the initial move, BC is the retracement, and CD is the continuation "
            "that mirrors AB. Point D is where the pattern completes and a reversal is "
            "expected. AB and CD should be roughly equal in length and time."
        ),
        "fibonacci_rules": {
            "bc_retracement": "BC should retrace 38.2% to 78.6% of AB (ideally 61.8% to 78.6%).",
            "cd_extension": "CD should be 127.2% to 161.8% extension of BC.",
            "ab_cd_equality": "AB and CD should be approximately equal in price AND time.",
        },
        "entry_trigger": (
            "BULLISH ABCD: Enter LONG at point D after confirming reversal. "
            "Look for a hammer, bullish engulfing, or RSI crossing above 30 at D. "
            "BEARISH ABCD: Enter SHORT at point D after confirming reversal. "
            "Look for shooting star, bearish engulfing, or RSI crossing below 70 at D. "
            "DO NOT enter blindly at D -- wait for a confirmation candle."
        ),
        "stop_loss": "Slightly beyond point D (below D for bullish, above D for bearish).",
        "target": {
            "target_1": "38.2% retracement of CD.",
            "target_2": "61.8% retracement of CD.",
            "target_3": "Point A (the 100% retracement -- full reversal).",
        },
        "confirmation": [
            "Fibonacci levels should align (BC at 61.8% of AB, CD at 127.2% of BC)",
            "AB and CD approximately equal in distance and duration",
            "Reversal candlestick at point D",
            "Volume increase at point D reversal",
            "RSI divergence at point D",
        ],
        "reliability": (
            "Medium-High. Very reliable when Fibonacci levels align precisely. "
            "Less reliable when proportions are significantly off."
        ),
    },

    "parabolic_reversal": {
        "name": "Parabolic Move + Reversal",
        "type": "reversal / short selling",
        "description": (
            "A stock enters a parabolic (near-vertical) move up, accelerating on "
            "each candle with widening range. This is the final phase of an exhaustion "
            "move. When it reverses, the drop is violent because there are no more "
            "buyers left. Parabolic shorts are among the most profitable (and risky) "
            "day trades."
        ),
        "characteristics": [
            "Each successive candle has a wider range than the last (acceleration)",
            "Price moves almost vertically -- 45 degrees, then 60, then near 90",
            "Volume spikes to extreme levels (climax volume)",
            "RSI goes above 80-90 (extreme overbought)",
            "Price is far extended from VWAP and all moving averages",
            "Retail FOMO buying is at maximum (social media buzz, chat room hype)",
        ],
        "entry_trigger": (
            "DO NOT short into a parabolic move -- wait for the top to form. "
            "SHORT ENTRY SIGNALS: "
            "(1) Climax candle: huge volume candle that closes near its low (distribution). "
            "(2) First red candle after a series of green candles at the top. "
            "(3) Break below VWAP on the way down. "
            "(4) Shooting star or bearish engulfing at the peak. "
            "(5) Failed new high attempt (double top at the peak). "
            "SAFEST: Wait for the stock to break below intraday VWAP before shorting."
        ),
        "stop_loss": (
            "Above the HOD (the parabolic peak). "
            "WARNING: stops must be WIDE because parabolic stocks are extremely volatile. "
            "Position size must be SMALL to compensate for the wide stop."
        ),
        "target": (
            "TARGET 1: VWAP. "
            "TARGET 2: Opening price / gap fill. "
            "TARGET 3: Prior day close. "
            "Parabolic reversals routinely give back 50-100% of the parabolic move. "
            "Large caps: 5-15% drops. Mid/small caps: 20-30% drops from peak."
        ),
        "confirmation": [
            "CLIMAX VOLUME: the highest volume candle at the top is distribution (smart money selling to retail)",
            "Climax candle closes at or near its low despite the high volume",
            "RSI divergence: price makes new high but RSI makes lower high",
            "Time of day: many parabolic tops happen in the first 30-60 minutes",
            "The more vertical the move, the more violent the reversal",
        ],
        "reliability": (
            "HIGH for identifying the reversal AFTER it starts. "
            "LOW for calling the exact top. Never try to pick the top of a parabolic -- "
            "wait for confirmation. The first red candle after the climax is the signal."
        ),
        "risk_warning": (
            "EXTREMELY DANGEROUS if entered too early. Parabolic moves can continue "
            "far longer than expected. Only trade with strict risk management and "
            "small position size. Never short a parabolic move that has not shown "
            "a confirmed reversal signal."
        ),
    },

    "orb_failure": {
        "name": "Opening Range Breakout Failure (ORB Failure / False Breakout)",
        "type": "reversal / mean reversion",
        "description": (
            "Price breaks above/below the opening range but quickly reverses back inside. "
            "The false breakout traps traders and the reversal is often sharp as stops "
            "get hit. Trading the failure of an ORB can be more profitable than the ORB itself."
        ),
        "entry_trigger": (
            "Price breaks above OR high, holds for 1-3 candles, then reverses back inside range. "
            "Enter SHORT when price closes back below the OR high (bull trap). "
            "Enter LONG when price closes back above the OR low (bear trap). "
            "The reversal back inside the range is the trigger."
        ),
        "stop_loss": "Above the failed breakout high (for shorts) or below the failed breakout low (for longs).",
        "target": "Opposite end of the opening range, then beyond.",
        "confirmation": [
            "Breakout had LOW volume (no conviction behind it = fake)",
            "Quick reversal back inside (within 2-5 candles)",
            "Increasing volume on the reversal",
        ],
        "reliability": "Medium-High. False breakouts are very tradeable once recognized.",
    },
}


# =============================================================================
# SECTION 4: VOLUME PATTERNS
# =============================================================================

VOLUME_PATTERNS = {

    "volume_climax": {
        "name": "Volume Climax (Buying/Selling Climax)",
        "type": "reversal signal",
        "description": (
            "An extreme volume spike (3-5x+ average volume) that often marks the END "
            "of a trend. SELLING CLIMAX: extreme volume at the bottom of a downtrend -- "
            "signals panic selling is complete and smart money is accumulating. "
            "BUYING CLIMAX: extreme volume at the top of an uptrend -- signals "
            "euphoric buying is complete and smart money is distributing."
        ),
        "how_to_identify": [
            "Volume bar is 3-5x+ the average volume for that timeframe",
            "SELLING CLIMAX: long lower shadow candle (hammer-like) on massive volume at a low",
            "BUYING CLIMAX: long upper shadow candle (shooting star-like) on massive volume at a high",
            "The climax candle often closes near its midpoint or against the trend",
            "Buying climax: closes near lows despite massive volume (distribution)",
            "Selling climax: closes near highs despite massive volume (accumulation)",
        ],
        "entry_trigger": (
            "Do NOT enter on the climax candle itself. Wait for follow-through. "
            "AFTER SELLING CLIMAX: enter long on the next bullish candle or when "
            "price breaks above the climax candle's high. "
            "AFTER BUYING CLIMAX: enter short on the next bearish candle or when "
            "price breaks below the climax candle's low."
        ),
        "stop_loss": "Beyond the extreme of the climax candle.",
        "target": "Significant because climax events often start new trends. Trail the stop.",
        "reliability": (
            "HIGH for identifying the end of a trend. Volume climax events are among "
            "the most reliable reversal signals in all of trading, especially when "
            "combined with key support/resistance levels."
        ),
    },

    "volume_dryup_before_breakout": {
        "name": "Volume Dry-Up Before Breakout",
        "type": "breakout precursor",
        "description": (
            "Volume progressively decreases as price consolidates near a key level "
            "(support, resistance, triangle apex, flag). The declining volume signals "
            "that sellers (or buyers) are exhausted and the stock is 'coiling' for a "
            "breakout. When volume then SPIKES, the breakout is valid."
        ),
        "how_to_identify": [
            "3-5+ candles of progressively decreasing volume",
            "Price is in a tight range (consolidation, flag, triangle)",
            "Near a key level (resistance for longs, support for shorts)",
            "Volume is at its LOWEST just before the breakout candle",
        ],
        "entry_trigger": (
            "Enter when volume SPIKES on a breakout candle after the dry-up period. "
            "The contrast between the low-volume consolidation and the high-volume "
            "breakout is the signal. Volume on breakout should be 2-3x the dry-up candles."
        ),
        "stop_loss": "Inside the consolidation pattern, below the breakout level.",
        "target": "Measured move from the pattern, or next key level.",
        "reliability": (
            "VERY HIGH -- volume dry-up followed by volume expansion is one of the "
            "most reliable precursors to a genuine breakout. If a breakout happens "
            "WITHOUT a volume spike, it is much more likely to be a false breakout."
        ),
    },

    "accumulation": {
        "name": "Accumulation (Wyckoff)",
        "type": "bullish / bottoming",
        "description": (
            "Smart money (institutions) quietly buying over time, typically during a "
            "base or after a selloff. Price stays in a range but volume patterns reveal "
            "heavier volume on up-moves and lighter volume on down-moves. "
            "Wyckoff accumulation phases: Selling Climax -> Automatic Rally -> "
            "Secondary Test -> Spring (shakeout) -> Sign of Strength -> Markup begins."
        ),
        "how_to_identify": [
            "Tight price action on low overall volume",
            "Higher volume on up days vs. down days within the range",
            "A 'spring' or false breakdown below support on low volume (shakeout)",
            "Price holds above the spring low and reverses sharply up on volume",
        ],
        "entry_trigger": (
            "Enter long on the 'spring' (false breakdown below the range on low volume "
            "that immediately reverses). Or enter on the 'sign of strength' breakout "
            "above the range high on expanding volume."
        ),
        "stop_loss": "Below the spring low or below the accumulation range low.",
        "target": "The accumulation range width projected upward. Markup phase can be significant.",
        "reliability": "HIGH when correctly identified. Wyckoff accumulation is the foundation of smart money analysis.",
    },

    "distribution": {
        "name": "Distribution (Wyckoff)",
        "type": "bearish / topping",
        "description": (
            "Smart money selling into strength, typically during a topping range. "
            "Price stays in a range but volume patterns reveal heavier volume on "
            "down-moves and lighter volume on up-moves. "
            "Wyckoff distribution: Buying Climax -> Automatic Reaction -> "
            "Secondary Test -> Upthrust (false breakout) -> Sign of Weakness -> Markdown."
        ),
        "how_to_identify": [
            "High volume with little price progress (churning at the top)",
            "Higher volume on down days vs. up days within the range",
            "An 'upthrust' or false breakout above resistance on low volume that reverses",
            "Progressive lower highs within the range (weakening rallies)",
        ],
        "entry_trigger": (
            "Enter short on the 'upthrust' (false breakout above range on low volume "
            "that reverses). Or enter short on the 'sign of weakness' breakdown below "
            "range support on expanding volume."
        ),
        "stop_loss": "Above the upthrust high or the distribution range high.",
        "target": "Distribution range width projected downward.",
        "reliability": "HIGH when correctly identified. Mirror of accumulation.",
    },

    "time_of_day_volume": {
        "name": "Time-of-Day Volume Patterns (U-Shaped Curve)",
        "type": "contextual / timing",
        "description": (
            "Intraday volume consistently follows a U-shaped pattern across all "
            "market days. Understanding this pattern is critical for timing entries "
            "and managing expectations."
        ),
        "periods": {
            "open_rush": {
                "time": "9:30 - 10:00 AM ET (first 30 minutes)",
                "volume": "HIGHEST volume of the day. 30-50% of daily volume in first hour.",
                "characteristics": (
                    "Largest price moves happen here. HOD occurs in first 30 min ~24% of time. "
                    "LOD occurs in first 30 min ~27% of time. Most volatile and highest "
                    "opportunity but also highest risk of whipsaws."
                ),
                "trading_notes": (
                    "Best time for: Gap and Go, ORB, momentum plays. "
                    "Warning: first 5 minutes are extremely noisy -- many pros wait until 9:35-9:45."
                ),
            },
            "morning_session": {
                "time": "10:00 - 11:30 AM ET",
                "volume": "Moderate, declining from open. Still good for trading.",
                "characteristics": (
                    "Trends established in the first 30 min often continue here. "
                    "First pullbacks after strong opens happen in this window. "
                    "Still enough volume for clean setups."
                ),
                "trading_notes": "Good time for: first pullback, VWAP reclaim/rejection, flag breakouts.",
            },
            "lunch_lull": {
                "time": "11:30 AM - 1:30 PM ET",
                "volume": "LOWEST volume of the day. Liquidity drops significantly.",
                "characteristics": (
                    "Narrower ranges, more chop, more false breakouts. Institutional traders "
                    "step away. Price can reverse morning trends temporarily (lunch reversal). "
                    "Lower volume means wider spreads on less liquid stocks."
                ),
                "trading_notes": (
                    "MOST EXPERIENCED TRADERS REDUCE ACTIVITY OR STOP TRADING DURING LUNCH. "
                    "If you must trade: reduce position size, widen stops, lower expectations. "
                    "This is where many day traders give back morning profits."
                ),
            },
            "afternoon_session": {
                "time": "1:30 - 3:00 PM ET",
                "volume": "Moderate, gradually increasing toward close.",
                "characteristics": (
                    "Volume picks up as European markets close and US afternoon session begins. "
                    "New trends can start here. Lunchtime reversals may reverse again."
                ),
                "trading_notes": "Good time to reassess: is the morning trend resuming or reversing?",
            },
            "power_hour": {
                "time": "3:00 - 4:00 PM ET (last hour)",
                "volume": "SECOND HIGHEST volume of the day. Surges near 3:50-4:00 PM.",
                "characteristics": (
                    "Institutional rebalancing, mutual fund/ETF activity, closing auction "
                    "positioning, options hedging. HOD occurs in closing 15 minutes >20% of time. "
                    "Strong trends often accelerate. Weak trends reverse."
                ),
                "trading_notes": (
                    "Good time for: trend continuation trades, power hour breakouts, "
                    "end-of-day momentum. Watch for acceleration of the day's dominant trend. "
                    "Many day traders close all positions by 3:50 PM."
                ),
            },
        },
        "reliability": "VERY HIGH -- the U-shaped volume pattern is one of the most consistent market phenomena.",
    },

    "volume_divergence": {
        "name": "Volume-Price Divergence",
        "type": "warning / reversal precursor",
        "description": (
            "When price makes a new high but volume is LOWER than the prior high, "
            "it signals weakening conviction. Similarly, price making a new low on "
            "lower volume signals selling exhaustion. Volume should confirm the trend -- "
            "when it diverges, a reversal is approaching."
        ),
        "how_to_identify": [
            "New HOD on declining volume vs. the prior HOD push",
            "New LOD on declining volume vs. the prior LOD push",
            "Series of higher price peaks with progressively lower volume peaks",
        ],
        "entry_trigger": (
            "Do not trade the divergence alone -- use it as a WARNING. "
            "Combine with a reversal candle pattern or level break for entry."
        ),
        "reliability": "HIGH as a leading indicator. One of the earliest warning signs of trend exhaustion.",
    },
}


# =============================================================================
# SECTION 5: SUPPORT / RESISTANCE & KEY LEVELS
# =============================================================================

SUPPORT_RESISTANCE = {

    "key_levels_hierarchy": {
        "name": "Day Trading Key Levels (Priority Order)",
        "description": (
            "Successful day traders mark these levels BEFORE the market opens every day. "
            "Levels are listed in approximate order of importance for intraday trading."
        ),
        "levels": {
            "1_prior_day_high_low_close": {
                "name": "Prior Day High, Low, and Close",
                "importance": "CRITICAL",
                "description": (
                    "Yesterday's high, low, and closing price are the most-watched "
                    "levels by all participants. Institutions use these as reference points. "
                    "Prior day close = green/red threshold. Prior day high = overhead resistance. "
                    "Prior day low = support target."
                ),
                "how_price_reacts": (
                    "Price often stalls, reverses, or accelerates at these levels. "
                    "A break above prior day high is very bullish (no overhead resistance from yesterday). "
                    "A break below prior day low is very bearish."
                ),
            },
            "2_premarket_high_low": {
                "name": "Pre-Market High and Low",
                "importance": "HIGH",
                "description": (
                    "The high and low of the pre-market session (4:00 AM - 9:30 AM ET). "
                    "Especially important on stocks with catalysts/news. Pre-market high "
                    "is often the first target for gap-and-go trades."
                ),
                "how_price_reacts": (
                    "Break above premarket high = new buying territory, often triggers acceleration. "
                    "Rejection at premarket high = potential short. "
                    "Premarket low = support, loss of this level = bearish."
                ),
            },
            "3_vwap": {
                "name": "VWAP (Volume Weighted Average Price)",
                "importance": "CRITICAL",
                "description": (
                    "The average price weighted by volume for the day. Resets daily. "
                    "THE institutional reference price. If above VWAP = bullish bias. "
                    "If below VWAP = bearish bias. Simplest and most effective intraday "
                    "indicator for direction."
                ),
                "how_price_reacts": (
                    "Acts as magnet (price returns to it), support (above), or resistance (below). "
                    "Institutions buy at/below VWAP and sell at/above VWAP. "
                    "VWAP becomes more powerful when it aligns with other levels (confluence)."
                ),
            },
            "4_whole_numbers_psychological": {
                "name": "Whole Numbers / Psychological Levels",
                "importance": "MODERATE-HIGH",
                "description": (
                    "Round numbers ($10, $20, $50, $100, $150, $200, etc.) and half-dollars "
                    "($10.50, $25.50, etc.) act as psychological support/resistance. Traders "
                    "place orders at round numbers, algorithms target them, options strikes "
                    "are at round numbers."
                ),
                "how_price_reacts": (
                    "Price often hesitates, consolidates, or reverses at round numbers. "
                    "$100, $200, $500, $1000 are major levels. "
                    "Breakout above a round number that holds = strong continuation signal."
                ),
            },
            "5_moving_averages": {
                "name": "Moving Averages (9 EMA, 20 EMA, 50 SMA, 200 SMA)",
                "importance": "MODERATE-HIGH",
                "description": (
                    "Dynamic support/resistance levels. For intraday: "
                    "9 EMA = immediate trend. 20 EMA = short-term trend. "
                    "50 SMA on 5m chart = intraday trend. "
                    "200 SMA on daily chart = major trend direction."
                ),
                "how_price_reacts": (
                    "Trending stocks ride the 9 EMA (strongest) or 20 EMA on pullbacks. "
                    "A break of the 9 EMA to the 20 EMA is a normal pullback. "
                    "A break below the 20 EMA signals potential trend change."
                ),
            },
            "6_opening_range_levels": {
                "name": "Opening Range High/Low",
                "importance": "HIGH (first hour)",
                "description": "The high and low of the first 5/15/30/60 minutes. Framework for ORB trades.",
            },
            "7_hod_lod": {
                "name": "High of Day / Low of Day",
                "importance": "HIGH",
                "description": (
                    "Dynamic levels that change throughout the day. A break to new HOD "
                    "after consolidation is bullish. A break to new LOD is bearish. "
                    "These are the most obvious levels everyone is watching."
                ),
            },
            "8_prior_pivot_points": {
                "name": "Pivot Points (and S1/S2/R1/R2)",
                "importance": "MODERATE",
                "description": (
                    "Calculated from prior day's high, low, and close. "
                    "Pivot = (H + L + C) / 3. R1 = 2*Pivot - Low. S1 = 2*Pivot - High. "
                    "Widely used by floor traders and algorithms."
                ),
            },
        },
    },

    "confluence_zones": {
        "name": "Confluence Zones",
        "importance": "HIGHEST PROBABILITY",
        "description": (
            "When MULTIPLE levels align at the same price, the level becomes extremely "
            "significant. Example: prior day high + VWAP + round number at the same price. "
            "Confluence zones are where the highest probability trades happen."
        ),
        "examples": [
            "VWAP + prior day high at $50.00 (round number) = triple confluence",
            "9 EMA + 20 EMA + premarket low = triple confluence",
            "Prior day close + opening range low + $100 round number",
        ],
        "trading_rule": (
            "The more levels that converge at a price, the stronger the expected "
            "reaction. 2 levels = worth noting. 3+ levels = high conviction trade."
        ),
    },

    "level_2_order_flow": {
        "name": "Level 2 / Order Flow Reading",
        "description": (
            "Level 2 data shows the order book: all bid (buy) and ask (sell) orders "
            "at various prices. Time & Sales (the tape) shows actual executed transactions. "
            "Together they reveal the real-time battle between buyers and sellers."
        ),
        "key_signals": {
            "buying_pressure": (
                "Aggressive buying: orders hitting the ASK repeatedly (willing to pay market price). "
                "Large bids stacking up at support levels. Ask being eaten quickly."
            ),
            "selling_pressure": (
                "Aggressive selling: orders hitting the BID repeatedly. "
                "Large offers stacking at resistance. Bids getting pulled (sellers overwhelming)."
            ),
            "spoofing_warning": (
                "Large orders that appear and disappear may be spoofing (fake orders to "
                "manipulate perception). Not all visible liquidity is real. Institutions use "
                "iceberg orders (only show a fraction of true size)."
            ),
            "absorption": (
                "When a large bid absorbs heavy selling without price dropping, it signals "
                "institutional support. When a large offer absorbs buying without price rising, "
                "it signals institutional distribution."
            ),
        },
        "how_to_use": (
            "Level 2 is used for TIMING, not direction. Use your chart patterns and "
            "levels for direction. Use Level 2 to fine-tune your entry -- enter when "
            "you see order flow confirming your directional thesis."
        ),
        "reliability": (
            "Medium -- Level 2 reading is an art, not a science. Helpful for confirmation "
            "but should never be the sole reason for a trade. Modern markets are heavily "
            "algo-driven and much of the order book is artificial."
        ),
    },
}


# =============================================================================
# SECTION 6: PATTERN RELIABILITY RANKINGS (INTRADAY)
# =============================================================================

RELIABILITY_RANKINGS = {
    "description": (
        "Reliability rankings for all patterns when used on intraday charts (1m, 5m, 15m) "
        "with proper confirmation. Rankings assume pattern + volume + location at key level."
    ),
    "tier_1_highest_probability": [
        "Bull Flag / Bear Flag (with volume confirmation)",
        "Opening Range Breakout (60-min ORB, with volume)",
        "First Pullback After Strong Move",
        "Bullish/Bearish Engulfing (at key level with volume spike)",
        "Volume Dry-Up Before Breakout (then volume spike)",
        "HOD/LOD Break and Retest",
        "VWAP Reclaim (with catalyst)",
        "Falling Wedge Breakout",
    ],
    "tier_2_high_probability": [
        "Morning Star / Evening Star",
        "Head and Shoulders (15m+)",
        "Double Top / Double Bottom",
        "Ascending / Descending Triangle Breakout",
        "Red to Green / Green to Red Move",
        "Gap and Go (with catalyst)",
        "Hammer / Shooting Star (at key levels)",
        "Cup and Handle Breakout",
        "Three White Soldiers / Three Black Crows",
        "Volume Climax Reversal",
    ],
    "tier_3_moderate_probability": [
        "ABCD Pattern",
        "Pennant Breakout",
        "Symmetrical Triangle",
        "Channel Breakout",
        "Parabolic Reversal (high reward but risky)",
        "Gap and Fade",
        "Rising Wedge Breakdown",
        "ORB Failure / False Breakout",
    ],
    "tier_4_lower_probability_on_intraday": [
        "Doji (alone, without confirmation)",
        "Spinning Top (alone)",
        "Harami (alone, without third candle confirmation)",
        "Tweezer Top/Bottom",
        "Triple Top/Bottom (rare intraday)",
    ],
    "critical_note": (
        "ANY pattern's reliability increases dramatically when combined with: "
        "(1) Volume confirmation, (2) Key support/resistance level, (3) Time of day "
        "(first hour or power hour, NOT lunch). The trifecta of PATTERN + LOCATION + "
        "VOLUME is the foundation of all successful pattern-based day trading."
    ),
}


# =============================================================================
# SECTION 7: UNIVERSAL RULES FOR ALL PATTERNS
# =============================================================================

UNIVERSAL_RULES = {
    "confirmation_trifecta": {
        "rule": "Pattern + Location + Volume = High Probability Trade",
        "details": (
            "A pattern alone is NOT enough. It must occur at a key level (VWAP, prior day "
            "high/low, round number, moving average) AND be confirmed by volume. This "
            "trifecta is what separates profitable traders from unprofitable ones."
        ),
    },
    "volume_rules": {
        "breakout_volume": "Breakout candles should have 2-3x average volume. Without volume, the breakout is suspect.",
        "consolidation_volume": "Volume should DECREASE during consolidation patterns (flags, triangles, etc.).",
        "reversal_volume": "Reversal candles at key levels should have above-average volume.",
        "climax_volume": "3-5x+ average volume marks exhaustion and potential trend change.",
    },
    "stop_loss_rules": {
        "always_use_stops": "EVERY trade needs a pre-defined stop loss before entry. No exceptions.",
        "logical_placement": "Place stops at levels where your thesis is invalidated, not arbitrary amounts.",
        "dont_widen_stops": "Never move a stop loss further away to 'give it room.' If your level is hit, you were wrong.",
    },
    "risk_reward": {
        "minimum_rr": "Minimum 1:2 risk-to-reward for most setups. Ideally 1:3+.",
        "position_sizing": "Risk 1-2% of account per trade maximum. Calculate position size from stop distance.",
    },
    "time_of_day": {
        "best_times": "First hour (9:30-10:30 AM) and last hour (3:00-4:00 PM) = highest probability.",
        "worst_time": "Lunch (11:30 AM - 1:30 PM) = lowest probability. Reduce size or stop trading.",
    },
    "higher_timeframe_alignment": {
        "rule": (
            "Always check the higher timeframe trend before entering. A bull flag on the 5m "
            "chart in a 15m downtrend is lower probability. Align your intraday trades with "
            "the higher timeframe direction."
        ),
    },
}
