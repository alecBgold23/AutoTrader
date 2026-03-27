# Algorithmic Day Trading Research for Python Trading Bots

Comprehensive research compiled March 2026. Covers programmatic pattern detection,
scanning algorithms, technical indicators, entry/exit algorithms, market microstructure,
data sources, and backtesting.

---

## 1. PROGRAMMATIC PATTERN DETECTION

### 1.1 Candlestick Pattern Detection

**Libraries:**

| Library | Patterns | Notes |
|---------|----------|-------|
| **TA-Lib** (python wrapper) | 61 candlestick patterns | Industry standard, C-based, fastest. Requires system-level install. |
| **pandas-ta** | 62 candlestick patterns, 212+ total indicators | Pure Python, pip-installable, DataFrame extension. |
| **ta** (by bukosabino) | ~40 indicators, limited candlestick | Lightweight, good for basics. |

**TA-Lib Candlestick Detection - Code Pattern:**
```python
import talib
import numpy as np

# All pattern functions return: -100 (bearish), 0 (no pattern), +100 (bullish)
# Some return -200/+200 for "strong" signals

# Single-candle patterns
hammer = talib.CDLHAMMER(open, high, low, close)
doji = talib.CDLDOJI(open, high, low, close)
shooting_star = talib.CDLSHOOTINGSTAR(open, high, low, close)

# Multi-candle patterns
engulfing = talib.CDLENGULFING(open, high, low, close)       # 2-candle
morning_star = talib.CDLMORNINGSTAR(open, high, low, close)   # 3-candle
three_white_soldiers = talib.CDL3WHITESOLDIERS(open, high, low, close)

# Scan ALL 61 patterns at once
candle_names = talib.get_function_groups()['Pattern Recognition']
for pattern in candle_names:
    func = getattr(talib, pattern)
    result = func(open, high, low, close)
    signals = result[result != 0]
    if len(signals) > 0:
        print(f"{pattern}: {len(signals)} signals")
```

**Key Patterns for Day Trading (ranked by reliability):**
1. **Engulfing** (bullish/bearish) - Strongest reversal signal
2. **Hammer / Inverted Hammer** - Bottom reversal
3. **Morning Star / Evening Star** - 3-candle reversal (most reliable)
4. **Doji** - Indecision, combine with context
5. **Three White Soldiers / Three Black Crows** - Strong continuation

### 1.2 Support/Resistance Detection Algorithms

**Method 1: Fractal Highs/Lows (Swing Points)**
```python
from scipy.signal import argrelextrema
import numpy as np

# Find local maxima (resistance) and minima (support)
# order=5 means: point must be highest/lowest within 5 bars on each side
window = 5
local_max_idx = argrelextrema(close_prices, np.greater, order=window)[0]
local_min_idx = argrelextrema(close_prices, np.less, order=window)[0]

resistance_levels = close_prices[local_max_idx]
support_levels = close_prices[local_min_idx]
```

**Method 2: K-Means Clustering of Price Levels**
```python
from sklearn.cluster import KMeans

# Combine all highs and lows into one array
price_points = np.concatenate([highs, lows]).reshape(-1, 1)

# Cluster into N groups - N is a hyperparameter (try 4-8)
kmeans = KMeans(n_clusters=6, random_state=42)
kmeans.fit(price_points)

sr_levels = sorted(kmeans.cluster_centers_.flatten())
```

**Method 3: Agglomerative Clustering (better for variable-count levels)**
```python
from sklearn.cluster import AgglomerativeClustering

# distance_threshold controls how close prices must be to form one level
clustering = AgglomerativeClustering(
    distance_threshold=price_range * 0.02,  # 2% of price range
    n_clusters=None
)
labels = clustering.fit_predict(price_points.reshape(-1, 1))

# Average each cluster to get S/R level
for label in set(labels):
    cluster_prices = price_points[labels == label]
    level = np.mean(cluster_prices)
    touch_count = len(cluster_prices)  # More touches = stronger level
```

**Method 4: Pivot Points (Classic, Fibonacci, Camarilla)**
```python
# Classic Pivot Points (reset daily for intraday)
pivot = (prev_high + prev_low + prev_close) / 3
r1 = 2 * pivot - prev_low
s1 = 2 * pivot - prev_high
r2 = pivot + (prev_high - prev_low)
s2 = pivot - (prev_high - prev_low)
r3 = prev_high + 2 * (pivot - prev_low)
s3 = prev_low - 2 * (prev_high - pivot)

# Fibonacci Pivot Points
r1_fib = pivot + 0.382 * (prev_high - prev_low)
r2_fib = pivot + 0.618 * (prev_high - prev_low)
r3_fib = pivot + 1.000 * (prev_high - prev_low)
s1_fib = pivot - 0.382 * (prev_high - prev_low)
s2_fib = pivot - 0.618 * (prev_high - prev_low)
s3_fib = pivot - 1.000 * (prev_high - prev_low)

# Camarilla Pivot Points (tighter levels, good for intraday)
r1_cam = prev_close + (prev_high - prev_low) * 1.1 / 12
r2_cam = prev_close + (prev_high - prev_low) * 1.1 / 6
r3_cam = prev_close + (prev_high - prev_low) * 1.1 / 4
r4_cam = prev_close + (prev_high - prev_low) * 1.1 / 2
```

### 1.3 VWAP Calculation

```python
# VWAP = cumulative(typical_price * volume) / cumulative(volume)
# MUST reset daily for intraday trading

typical_price = (df['high'] + df['low'] + df['close']) / 3
df['vwap'] = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()

# VWAP with standard deviation bands (like Bollinger on VWAP)
df['vwap_cum_vol'] = df['volume'].cumsum()
df['vwap_cum_tp_vol'] = (typical_price * df['volume']).cumsum()
df['vwap'] = df['vwap_cum_tp_vol'] / df['vwap_cum_vol']

# Upper/Lower bands at 1 and 2 standard deviations
squared_diff = (typical_price - df['vwap']) ** 2
df['vwap_variance'] = (squared_diff * df['volume']).cumsum() / df['vwap_cum_vol']
df['vwap_std'] = np.sqrt(df['vwap_variance'])
df['vwap_upper1'] = df['vwap'] + df['vwap_std']
df['vwap_lower1'] = df['vwap'] - df['vwap_std']
df['vwap_upper2'] = df['vwap'] + 2 * df['vwap_std']
df['vwap_lower2'] = df['vwap'] - 2 * df['vwap_std']
```

**VWAP Trading Rules:**
- Price above VWAP = bullish bias (institutional buying)
- Price below VWAP = bearish bias (institutional selling)
- VWAP acts as dynamic intraday support/resistance
- First touch of VWAP after a gap often provides a high-probability trade

### 1.4 Opening Range Breakout (ORB) Detection

```python
# Define the opening range (first 15 or 30 minutes)
ORB_MINUTES = 15  # or 30
market_open = pd.Timestamp('09:30', tz='US/Eastern')
orb_end = market_open + pd.Timedelta(minutes=ORB_MINUTES)

# Get bars within the opening range
orb_bars = df[(df.index >= market_open) & (df.index < orb_end)]
orb_high = orb_bars['high'].max()
orb_low = orb_bars['low'].min()
orb_mid = (orb_high + orb_low) / 2

# Detect breakout after ORB period
post_orb = df[df.index >= orb_end]
for idx, bar in post_orb.iterrows():
    if bar['close'] > orb_high:  # Bullish breakout
        # CONFIRM with: RVOL > 2.0, price > VWAP, spread < 0.1%
        entry = bar['close']
        stop = orb_low  # or orb_mid for tighter stop
        target = entry + (entry - stop) * 2  # 2:1 R/R
        break
    elif bar['close'] < orb_low:  # Bearish breakdown
        entry = bar['close']
        stop = orb_high
        target = entry - (stop - entry) * 2
        break
```

**ORB Best Practices:**
- 15-minute ORB works best for volatile large-caps
- 30-minute ORB works best for mid-caps and calmer markets
- Require close ABOVE/BELOW range (not just wick)
- Combine with RVOL > 1.5 and VWAP alignment
- One trade per side per day maximum
- Lock out 5 minutes before/after major news (FOMC, CPI, etc.)

### 1.5 Relative Volume (RVOL) Calculation

```python
# Simple RVOL
lookback_days = 20  # or 50
avg_volume = df['volume'].rolling(window=lookback_days).mean()
df['rvol'] = df['volume'] / avg_volume

# Time-of-day adjusted RVOL (more accurate for intraday)
# Compare current volume to average volume AT THE SAME TIME on prior days
def calculate_rvol_at_time(current_vol, historical_data, current_time, lookback=20):
    """Compare volume now to volume at same time on previous days."""
    same_time_vols = []
    for day in historical_data.index.normalize().unique()[-lookback:]:
        day_data = historical_data[historical_data.index.normalize() == day]
        matching = day_data[day_data.index.time == current_time.time()]
        if not matching.empty:
            same_time_vols.append(matching['volume'].iloc[0])
    if same_time_vols:
        return current_vol / np.mean(same_time_vols)
    return 1.0
```

**RVOL Thresholds:**
- RVOL < 0.5: Dead stock, avoid trading
- RVOL 0.5-1.0: Below average, cautious
- RVOL 1.0-1.5: Normal activity
- RVOL 1.5-2.0: Elevated interest, worth watching
- **RVOL 2.0-3.0: Stock is "in play" - prime for day trading**
- RVOL > 3.0: Major catalyst, high conviction setups
- RVOL > 5.0: Extreme event (earnings, FDA, M&A)

### 1.6 Volume Spike / Climax Detection

```python
# Method 1: Standard deviation based (like UnusualVolumeDetector)
lookback = 100  # bars
vol_mean = df['volume'].rolling(lookback).mean()
vol_std = df['volume'].rolling(lookback).std()
df['vol_z_score'] = (df['volume'] - vol_mean) / vol_std

# Thresholds
df['volume_spike'] = df['vol_z_score'] > 3.0      # Notable spike
df['volume_extreme'] = df['vol_z_score'] > 5.0     # Major event
df['volume_climax'] = df['vol_z_score'] > 10.0     # Blow-off / capitulation

# Method 2: Ratio-based
df['vol_ratio'] = df['volume'] / vol_mean
# vol_ratio > 3x = spike, > 5x = extreme, > 10x = climax

# Climax detection: high volume + reversal candle
df['sell_climax'] = (
    (df['vol_z_score'] > 3) &
    (df['close'] < df['open']) &           # Red candle
    (df['close'] > df['low'] + 0.6 * (df['high'] - df['low']))  # Long lower wick
)
df['buy_climax'] = (
    (df['vol_z_score'] > 3) &
    (df['close'] > df['open']) &           # Green candle
    (df['close'] < df['high'] - 0.6 * (df['high'] - df['low']))  # Long upper wick
)
```

---

## 2. SCANNING ALGORITHMS

### 2.1 How Professional Scanners Work (Trade Ideas Model)

**Architecture:**
- Continuously monitors 8,000+ US equities in real-time
- Uses streaming market data (trades, quotes, bars) via direct exchange feeds
- 500+ configurable filters combined with AND logic
- AI component ("Holly") runs Monte Carlo simulations overnight on millions of scenarios

**Core Scanning Loop:**
```python
# Conceptual architecture of a real-time scanner
class MarketScanner:
    def __init__(self):
        self.symbols = {}          # symbol -> metadata
        self.prev_close = {}       # symbol -> previous close
        self.alerts_fired = {}     # symbol -> set of alert types
        self.filters = []          # list of filter functions

    async def on_bar(self, symbol, bar):
        """Called every time a new bar arrives for any symbol."""
        # Run all filters
        passes_all = all(f(symbol, bar) for f in self.filters)
        if passes_all and symbol not in self.alerts_fired:
            self.alerts_fired[symbol] = True
            await self.emit_alert(symbol, bar)
```

**Key Filters Used by Professional Scanners:**

| Category | Filter | Typical Values |
|----------|--------|----------------|
| Price | Min/Max Price | $1-$500 (avoid < $1 pennies) |
| Price | % Change from Close | > 3% for gaps |
| Volume | Min Daily Volume | > 100,000 shares |
| Volume | RVOL | > 2.0 |
| Volume | Current Volume | > 50,000 in first hour |
| Float | Float Size | < 50M for momentum plays |
| Float | Short Interest % | > 15% for squeeze candidates |
| Technical | Above/Below VWAP | Direction filter |
| Technical | New 5-min High/Low | Breakout detection |
| Technical | RSI Range | 30-70 (or extremes) |
| Spread | Max Spread % | < 0.5% of price |
| Market Cap | Min Market Cap | > $100M (avoid micro-caps) |

### 2.2 Gap Scanner

```python
def scan_gaps(symbols_data, min_gap_pct=3.0, min_volume=100000, min_rvol=1.5):
    """
    Scan for stocks gapping up or down from previous close.
    Run pre-market (4:00 AM - 9:30 AM ET) and at open.
    """
    results = []
    for symbol, data in symbols_data.items():
        prev_close = data['prev_close']
        current_price = data['current_price']  # Pre-market or open price
        volume = data['premarket_volume']

        gap_pct = ((current_price - prev_close) / prev_close) * 100

        if abs(gap_pct) >= min_gap_pct and volume >= min_volume:
            rvol = data['current_volume'] / data['avg_volume']
            if rvol >= min_rvol:
                results.append({
                    'symbol': symbol,
                    'gap_pct': gap_pct,
                    'direction': 'UP' if gap_pct > 0 else 'DOWN',
                    'volume': volume,
                    'rvol': rvol,
                    'float': data.get('float_shares'),
                    'catalyst': data.get('news_catalyst', 'Unknown')
                })

    # Sort by absolute gap percentage
    return sorted(results, key=lambda x: abs(x['gap_pct']), reverse=True)
```

**Gap Scanner Filters:**
- Gap > 3% (small), > 5% (medium), > 10% (large)
- Pre-market volume > 100K shares
- RVOL > 1.5
- Price > $2.00 (avoid penny stocks)
- Float < 50M shares (for momentum gap plays)
- Average daily volume > 200K (ensures liquidity)

### 2.3 Momentum Scanner

```python
def scan_momentum(symbols_data, timeframe_minutes=5):
    """
    Scan for stocks with strong intraday momentum.
    """
    results = []
    for symbol, data in symbols_data.items():
        bars = data['bars']  # Recent 5-min bars
        if len(bars) < 3:
            continue

        # Calculate momentum metrics
        pct_change_5m = (bars[-1]['close'] - bars[-2]['close']) / bars[-2]['close'] * 100
        pct_change_15m = (bars[-1]['close'] - bars[-4]['close']) / bars[-4]['close'] * 100

        # Check for consecutive green bars
        green_bars = sum(1 for b in bars[-5:] if b['close'] > b['open'])

        # Volume acceleration
        recent_vol = sum(b['volume'] for b in bars[-3:])
        prior_vol = sum(b['volume'] for b in bars[-6:-3])
        vol_accel = recent_vol / max(prior_vol, 1)

        # Higher highs and higher lows check
        hh = bars[-1]['high'] > bars[-2]['high'] > bars[-3]['high']
        hl = bars[-1]['low'] > bars[-2]['low'] > bars[-3]['low']

        if (abs(pct_change_5m) > 1.0 and
            green_bars >= 3 and
            vol_accel > 1.5 and
            data['rvol'] > 2.0):

            results.append({
                'symbol': symbol,
                'momentum_5m': pct_change_5m,
                'momentum_15m': pct_change_15m,
                'vol_acceleration': vol_accel,
                'consecutive_green': green_bars,
                'higher_highs': hh and hl,
                'rvol': data['rvol']
            })

    return sorted(results, key=lambda x: x['momentum_5m'], reverse=True)
```

### 2.4 Volume Spike Scanner

```python
def scan_volume_spikes(symbols_data, z_score_threshold=3.0, min_price=2.0):
    """
    Detect unusual volume activity across the market.
    Based on the UnusualVolumeDetector approach.
    """
    results = []
    for symbol, data in symbols_data.items():
        if data['price'] < min_price:
            continue

        # Calculate z-score of current volume vs historical
        hist_volumes = data['historical_volumes']  # Last 100 days
        mean_vol = np.mean(hist_volumes)
        std_vol = np.std(hist_volumes)

        if std_vol == 0:
            continue

        z_score = (data['current_volume'] - mean_vol) / std_vol

        if z_score >= z_score_threshold:
            results.append({
                'symbol': symbol,
                'z_score': z_score,
                'current_vol': data['current_volume'],
                'avg_vol': mean_vol,
                'vol_ratio': data['current_volume'] / mean_vol,
                'price_change_pct': data['price_change_pct'],
                'price': data['price']
            })

    return sorted(results, key=lambda x: x['z_score'], reverse=True)
```

### 2.5 Float Rotation / Short Interest Scanning

**Float Rotation:** When a stock's daily volume exceeds its float (shares available for public trading), the float has "rotated." This indicates extreme speculative interest.

```python
def scan_float_rotation(symbols_data, min_rotation=0.5):
    """
    Find stocks where volume is approaching or exceeding the float.
    """
    results = []
    for symbol, data in symbols_data.items():
        if not data.get('float_shares'):
            continue

        rotation = data['current_volume'] / data['float_shares']

        if rotation >= min_rotation:
            results.append({
                'symbol': symbol,
                'float_rotation': rotation,
                'float_shares': data['float_shares'],
                'volume': data['current_volume'],
                'short_interest_pct': data.get('short_interest_pct', 0),
                'days_to_cover': data.get('days_to_cover', 0)
            })

    return sorted(results, key=lambda x: x['float_rotation'], reverse=True)
```

**Data Sources for Float / Short Interest:**
- **FINRA API:** Free, publishes short interest twice monthly (mid-month and end-of-month)
  - Endpoint: `https://api.finra.org/data/group/otcMarket/name/EquityShortInterest`
  - Returns: date, shorted quantity, reported facility
- **FinViz:** `finvizfinance` Python library (unofficial, use responsibly)
- **Yahoo Finance:** `yfinance` provides `info['sharesShort']`, `info['floatShares']`
- **Alpaca snapshots:** Volume data, combine with float from another source
- **SEC EDGAR:** Institutional holdings for calculating float

### 2.6 Real-Time vs Delayed Data

| Aspect | Real-Time | Delayed (15-min) |
|--------|-----------|-------------------|
| Gap scanning | Pre-market essential | Useless for pre-market |
| ORB strategy | Mandatory | Cannot execute |
| Momentum scanning | Critical for entries | Entries always late |
| EOD analysis | Not needed | Sufficient |
| Cost | $0-200/month | Usually free |
| Latency | < 100ms (WebSocket) | 15 minutes |

**For day trading, real-time data is non-negotiable.** Delayed data is only useful for backtesting and end-of-day analysis.

---

## 3. OPTIMAL TECHNICAL INDICATORS FOR ALGO TRADING

### 3.1 Research-Backed Indicator Performance

From a 2026 study analyzing ~100 years of DJIA data (in-sample: 1928-1995, out-of-sample: 1996-2024):

| Indicator | Win Rate | Return Rate | Best For |
|-----------|----------|-------------|----------|
| **RSI(14)** | **79.4%** | 1.23 | Overbought/oversold reversals |
| **Bollinger Bands** | **77.8%** | 1.31 | Volatility/mean reversion |
| Donchian Channels | 74.1% | 1.38 | Breakout detection |
| Williams %R | 71.7% | 1.15 | Momentum extremes |
| **Ichimoku** | 62.3% | **1.77** | Trend + support/resistance |
| **EMA(50)** | 58.1% | **1.60** | Trend direction |
| SMA(50) | 56.7% | 1.48 | Trend direction |
| CCI(20) | 55.2% | 1.47 | Momentum |
| **MACD** | 54.8% | 1.35 | Trend + momentum |

**Key Takeaway:** RSI and Bollinger Bands have the highest win rates. Ichimoku and EMA(50) have the highest return rates. MACD is middling but useful as a confirmation tool.

### 3.2 Signal Combination Strategy (Multi-Indicator Confluence)

**The "Triple Confirmation" Approach:**
Use 2-3 indicators from different categories to reduce false signals:
1. **Trend indicator:** EMA crossover or VWAP direction
2. **Momentum indicator:** RSI or MACD
3. **Volatility indicator:** Bollinger Bands or ATR

```python
def generate_signal(df):
    """
    Multi-indicator confluence signal generator.
    Signal = 1 (buy), -1 (sell), 0 (no signal)
    """
    signals = pd.DataFrame(index=df.index)

    # 1. TREND: 9 EMA vs 21 EMA
    ema9 = df['close'].ewm(span=9).mean()
    ema21 = df['close'].ewm(span=21).mean()
    signals['trend'] = np.where(ema9 > ema21, 1, -1)

    # 2. MOMENTUM: RSI(14)
    rsi = talib.RSI(df['close'], timeperiod=14)
    signals['momentum'] = np.where(rsi < 30, 1, np.where(rsi > 70, -1, 0))

    # 3. VOLATILITY: Bollinger Band position
    upper, middle, lower = talib.BBANDS(df['close'], timeperiod=20, nbdevup=2, nbdevdn=2)
    signals['volatility'] = np.where(df['close'] < lower, 1, np.where(df['close'] > upper, -1, 0))

    # 4. VOLUME: RVOL confirmation
    avg_vol = df['volume'].rolling(20).mean()
    signals['volume'] = np.where(df['volume'] > 1.5 * avg_vol, 1, 0)

    # CONFLUENCE: Require at least 3 of 4 signals aligned
    signal_sum = signals['trend'] + signals['momentum'] + signals['volatility']
    volume_confirmed = signals['volume'] == 1

    df['buy_signal'] = (signal_sum >= 2) & volume_confirmed
    df['sell_signal'] = (signal_sum <= -2) & volume_confirmed

    return df
```

### 3.3 Avoiding Over-Fitting Indicators

**Rules of Thumb:**
1. **Limit free parameters to 3-5 total** across your strategy
2. **Require parameter stability:** Edge must survive +/-10% perturbation of each parameter
3. **Use standard parameters first:** RSI(14), MACD(12,26,9), BB(20,2) exist for a reason
4. **Out-of-sample testing:** Train on 70% of data, validate on 30%
5. **Walk-forward analysis:** Re-optimize on rolling windows, test on next window
6. **If Sharpe > 3.0, it is almost certainly overfit**
7. **Fewer indicators is better:** A strategy with 2 indicators beating one with 5 is more robust

**Warning Signs of Overfitting:**
- Strategy only works on one specific ticker
- Tiny parameter changes destroy performance
- Unrealistic win rates (> 85% is suspicious for intraday)
- Backtested returns diverge massively from paper trading
- Strategy requires very specific market conditions

### 3.4 Best Moving Average Combinations for Intraday

| Combination | Timeframe | Use Case |
|-------------|-----------|----------|
| **9 EMA / 21 EMA** | 1-5 min | Fast scalping signals |
| **8 EMA / 21 EMA / 55 EMA** | 5-15 min | Trend confirmation with filter |
| **VWAP + 9 EMA** | 1-5 min | Institutional bias + fast signal |
| **20 SMA + Bollinger Bands** | 5-15 min | Mean reversion |
| **50 EMA** | 15 min-1hr | Major intraday trend direction |

**EMA + VWAP Intraday Strategy (specific parameters):**
- Fast EMA: 8 or 9 period
- Slow EMA: 21 period
- Trend filter: 55 EMA
- Direction filter: VWAP
- Chart: 5-minute bars

**Entry Rules:**
- Long: 9 EMA crosses above 21 EMA AND price above VWAP AND price above 55 EMA
- Short: 9 EMA crosses below 21 EMA AND price below VWAP AND price below 55 EMA

### 3.5 RSI Divergence Detection

```python
from scipy.signal import argrelextrema

def detect_rsi_divergence(df, rsi_period=14, lookback=5):
    """
    Detect bullish and bearish RSI divergences.

    Bullish divergence: price makes lower low, RSI makes higher low
    Bearish divergence: price makes higher high, RSI makes lower high
    """
    rsi = talib.RSI(df['close'].values, timeperiod=rsi_period)

    # Find swing lows in price and RSI
    price_lows = argrelextrema(df['close'].values, np.less, order=lookback)[0]
    rsi_lows = argrelextrema(rsi, np.less, order=lookback)[0]

    # Find swing highs
    price_highs = argrelextrema(df['close'].values, np.greater, order=lookback)[0]
    rsi_highs = argrelextrema(rsi, np.greater, order=lookback)[0]

    divergences = []

    # Bullish divergence: price lower low + RSI higher low
    for i in range(1, len(price_lows)):
        curr_idx = price_lows[i]
        prev_idx = price_lows[i-1]

        if (df['close'].values[curr_idx] < df['close'].values[prev_idx] and
            rsi[curr_idx] > rsi[prev_idx]):
            divergences.append({
                'type': 'bullish',
                'index': curr_idx,
                'price': df['close'].values[curr_idx],
                'rsi': rsi[curr_idx]
            })

    # Bearish divergence: price higher high + RSI lower high
    for i in range(1, len(price_highs)):
        curr_idx = price_highs[i]
        prev_idx = price_highs[i-1]

        if (df['close'].values[curr_idx] > df['close'].values[prev_idx] and
            rsi[curr_idx] < rsi[prev_idx]):
            divergences.append({
                'type': 'bearish',
                'index': curr_idx,
                'price': df['close'].values[curr_idx],
                'rsi': rsi[curr_idx]
            })

    return divergences
```

### 3.6 MACD Histogram Analysis

```python
def analyze_macd_histogram(df, fast=12, slow=26, signal=9):
    """
    MACD histogram reveals momentum acceleration/deceleration.
    """
    macd, signal_line, histogram = talib.MACD(
        df['close'].values, fastperiod=fast, slowperiod=slow, signalperiod=signal
    )

    df['macd'] = macd
    df['macd_signal'] = signal_line
    df['macd_hist'] = histogram

    # Key signals:

    # 1. Zero-line crossover (trend change)
    df['macd_bullish_cross'] = (macd > signal_line) & (np.roll(macd, 1) <= np.roll(signal_line, 1))
    df['macd_bearish_cross'] = (macd < signal_line) & (np.roll(macd, 1) >= np.roll(signal_line, 1))

    # 2. Histogram acceleration (momentum building)
    df['hist_accelerating'] = (
        (histogram > np.roll(histogram, 1)) &
        (np.roll(histogram, 1) > np.roll(histogram, 2))
    )

    # 3. Histogram deceleration (momentum fading - exit warning)
    df['hist_decelerating'] = (
        (abs(histogram) < abs(np.roll(histogram, 1))) &
        (abs(np.roll(histogram, 1)) < abs(np.roll(histogram, 2)))
    )

    # 4. Histogram divergence from price (similar to RSI divergence)
    # Price new high + histogram lower high = bearish divergence

    return df
```

**MACD Trading Rules:**
- Histogram crossing from negative to positive = momentum shift bullish
- Histogram expanding = trend strengthening (hold position)
- Histogram shrinking = trend weakening (tighten stops)
- MACD line above zero + histogram positive = strong uptrend
- **Best signal:** MACD cross ABOVE zero line (not below) for longs

### 3.7 Bollinger Band Squeeze Detection

```python
def detect_bb_squeeze(df, bb_period=20, bb_std=2, kc_period=20, kc_mult=1.5):
    """
    Bollinger Band Squeeze: BB inside Keltner Channels.
    Indicates low volatility about to expand -> big move coming.

    Squeeze fires when BB width < KC width.
    Direction determined by momentum (MACD histogram or linear regression slope).
    """
    # Bollinger Bands
    upper_bb, middle_bb, lower_bb = talib.BBANDS(
        df['close'].values, timeperiod=bb_period, nbdevup=bb_std, nbdevdn=bb_std
    )
    bb_width = upper_bb - lower_bb

    # Keltner Channels
    atr = talib.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=kc_period)
    ema = talib.EMA(df['close'].values, timeperiod=kc_period)
    upper_kc = ema + kc_mult * atr
    lower_kc = ema - kc_mult * atr
    kc_width = upper_kc - lower_kc

    # Squeeze detection
    df['squeeze_on'] = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    df['squeeze_off'] = ~df['squeeze_on']

    # Squeeze just fired (transition from on to off)
    df['squeeze_fire'] = df['squeeze_off'] & df['squeeze_on'].shift(1)

    # Direction: use momentum oscillator
    # Linear regression slope of close over last 20 bars
    from scipy.stats import linregress
    momentum = []
    for i in range(len(df)):
        if i < bb_period:
            momentum.append(0)
        else:
            window = df['close'].values[i-bb_period:i]
            slope, _, _, _, _ = linregress(range(len(window)), window)
            momentum.append(slope)
    df['squeeze_momentum'] = momentum

    # Signal: squeeze fires + positive momentum = long
    # Signal: squeeze fires + negative momentum = short
    df['squeeze_long'] = df['squeeze_fire'] & (df['squeeze_momentum'] > 0)
    df['squeeze_short'] = df['squeeze_fire'] & (df['squeeze_momentum'] < 0)

    return df
```

**Squeeze Statistics:**
- MACD divergences near Bollinger Band extremes: ~65% success rate, 1.8:1 reward-to-risk
- Squeeze setups work best on 5-min and 15-min charts for intraday
- Require volume confirmation (RVOL > 1.5) to filter false squeezes

---

## 4. ENTRY/EXIT ALGORITHMS

### 4.1 Programmatic Entry Point Determination

```python
class EntryEngine:
    """
    Multi-factor entry scoring system.
    Each factor adds to a score; trade when score exceeds threshold.
    """
    def calculate_entry_score(self, symbol_data):
        score = 0
        max_score = 10

        # Factor 1: Trend alignment (0-2 points)
        if symbol_data['ema9'] > symbol_data['ema21'] > symbol_data['ema55']:
            score += 2  # Strong uptrend
        elif symbol_data['ema9'] > symbol_data['ema21']:
            score += 1  # Moderate uptrend

        # Factor 2: VWAP position (0-2 points)
        if symbol_data['close'] > symbol_data['vwap']:
            score += 1
        if symbol_data['close'] > symbol_data['vwap'] and symbol_data['vwap_rising']:
            score += 1  # Price above rising VWAP

        # Factor 3: Volume confirmation (0-2 points)
        if symbol_data['rvol'] > 2.0:
            score += 2
        elif symbol_data['rvol'] > 1.5:
            score += 1

        # Factor 4: RSI in favorable zone (0-2 points)
        rsi = symbol_data['rsi']
        if 40 <= rsi <= 60:
            score += 2  # Not overbought, room to run
        elif 30 <= rsi <= 70:
            score += 1

        # Factor 5: Price action (0-2 points)
        if symbol_data['breaking_resistance']:
            score += 2
        elif symbol_data['bouncing_support']:
            score += 1

        return score, max_score

    def should_enter(self, symbol_data, min_score=7):
        score, max_score = self.calculate_entry_score(symbol_data)
        return score >= min_score
```

### 4.2 Trailing Stop Algorithms

**Method 1: Fixed Percentage Trailing Stop**
```python
class FixedPercentTrailingStop:
    def __init__(self, trail_pct=0.02):  # 2% trail
        self.trail_pct = trail_pct
        self.highest_price = 0
        self.stop_price = 0

    def update(self, current_price):
        if current_price > self.highest_price:
            self.highest_price = current_price
            self.stop_price = self.highest_price * (1 - self.trail_pct)
        return self.stop_price

    def is_triggered(self, current_price):
        return current_price <= self.stop_price
```

**Method 2: ATR-Based Trailing Stop (Chandelier Exit)**
```python
class ATRTrailingStop:
    """
    Chandelier Exit: trails from highest high by N * ATR.
    Adapts to volatility - wider stops in volatile markets, tighter in calm.
    """
    def __init__(self, atr_multiplier=3.0, atr_period=14):
        self.multiplier = atr_multiplier
        self.period = atr_period
        self.highest_high = 0

    def calculate(self, df):
        atr = talib.ATR(df['high'].values, df['low'].values, df['close'].values,
                        timeperiod=self.period)

        # Chandelier Exit Long
        highest_high = df['high'].rolling(self.period).max()
        df['chandelier_exit_long'] = highest_high - self.multiplier * atr

        # Chandelier Exit Short
        lowest_low = df['low'].rolling(self.period).min()
        df['chandelier_exit_short'] = lowest_low + self.multiplier * atr

        return df
```

**ATR Multiplier Guidelines:**
- 1.5x ATR: Tight stop, frequent stops, best for scalping
- 2.0x ATR: Standard, good balance of protection and room
- 3.0x ATR: Wide stop, fewer false triggers, best for trend-following
- 4.0x ATR: Very wide, only for high-conviction swing trades

**Method 3: Moving Average Trailing Stop**
```python
# Trail using the 9 EMA as dynamic stop
# Exit when price closes below 9 EMA (for longs)
def ma_trailing_stop(df, period=9):
    df['trailing_stop'] = df['close'].ewm(span=period).mean()
    df['exit_signal'] = df['close'] < df['trailing_stop']
    return df
```

### 4.3 Partial Profit Taking (Scaling Out)

```python
class ScaleOutManager:
    """
    Scale out of positions at predetermined levels.
    Common approach: 1/3 at 1R, 1/3 at 2R, trail remaining 1/3.
    """
    def __init__(self, entry_price, stop_price, total_shares):
        self.entry = entry_price
        self.stop = stop_price
        self.risk = abs(entry_price - stop_price)  # 1R
        self.total_shares = total_shares
        self.remaining_shares = total_shares
        self.scale_levels = [
            {'r_multiple': 1.0, 'pct_to_sell': 0.33, 'filled': False},
            {'r_multiple': 2.0, 'pct_to_sell': 0.33, 'filled': False},
            # Remaining 34% trails with ATR stop
        ]

    def check_scale_out(self, current_price):
        orders = []
        profit = current_price - self.entry  # Assumes long

        for level in self.scale_levels:
            target = self.entry + level['r_multiple'] * self.risk
            if current_price >= target and not level['filled']:
                shares_to_sell = int(self.total_shares * level['pct_to_sell'])
                orders.append({
                    'action': 'sell',
                    'shares': shares_to_sell,
                    'reason': f"Scale out at {level['r_multiple']}R"
                })
                level['filled'] = True
                self.remaining_shares -= shares_to_sell

        return orders

    def move_stop_to_breakeven(self):
        """After first scale-out, move stop to entry (breakeven)."""
        self.stop = self.entry
```

### 4.4 Time-Based Exits

```python
import pytz

def check_time_exits(position, current_time):
    """
    Time-based exit rules for day trading.
    """
    et = current_time.astimezone(pytz.timezone('US/Eastern'))

    # Rule 1: No new entries after 3:30 PM ET
    if et.hour == 15 and et.minute >= 30:
        return {'action': 'no_new_entries', 'reason': 'Too close to market close'}

    # Rule 2: Close all positions by 3:50 PM ET (10 min before close)
    if et.hour == 15 and et.minute >= 50:
        return {'action': 'close_all', 'reason': 'End of day liquidation'}

    # Rule 3: Avoid first 5 minutes (9:30-9:35) - too chaotic
    if et.hour == 9 and et.minute < 35:
        return {'action': 'wait', 'reason': 'Opening volatility period'}

    # Rule 4: Lunch hour caution (11:30 AM - 1:00 PM) - low volume
    if (et.hour == 11 and et.minute >= 30) or et.hour == 12:
        return {'action': 'reduce_size', 'reason': 'Lunch hour low volume'}

    # Rule 5: Max hold time for scalps
    if position.get('strategy') == 'scalp':
        hold_minutes = (current_time - position['entry_time']).seconds / 60
        if hold_minutes > 30:
            return {'action': 'close', 'reason': 'Max scalp hold time exceeded'}

    return {'action': 'hold'}
```

### 4.5 Handling Partial Fills

```python
class OrderManager:
    def __init__(self, broker_api):
        self.api = broker_api

    def handle_partial_fill(self, order_id, intended_qty, filled_qty):
        """
        Handle when only part of an order fills.
        """
        remaining = intended_qty - filled_qty
        fill_pct = filled_qty / intended_qty

        if fill_pct == 0:
            # No fill - cancel and re-evaluate
            self.api.cancel_order(order_id)
            return 'cancelled'

        elif fill_pct < 0.5:
            # Less than half filled
            # Option A: Cancel remainder and adjust position size
            self.api.cancel_order(order_id)
            # Adjust stop and target proportionally
            return 'partial_adjusted'

        elif fill_pct >= 0.5:
            # More than half filled - let it ride
            # Keep the remaining order active for 30 more seconds
            # Then cancel if not filled
            return 'waiting'

    def submit_bracket_order(self, symbol, qty, side, entry_price, stop_price, target_price):
        """
        Submit a bracket order: entry + stop loss + take profit.
        Uses OCO (One-Cancels-Other) for exit orders.
        """
        # Parent order (entry)
        entry_order = self.api.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type='limit',
            time_in_force='day',
            limit_price=entry_price,
            order_class='bracket',
            stop_loss={'stop_price': stop_price},
            take_profit={'limit_price': target_price}
        )
        return entry_order
```

### 4.6 Bracket Order Strategy

```python
def create_bracket_from_setup(symbol, setup):
    """
    Create a full bracket order from a trade setup.

    setup = {
        'entry': 150.25,
        'stop': 149.00,
        'direction': 'long',
        'risk_dollars': 100,  # Max risk per trade
    }
    """
    risk_per_share = abs(setup['entry'] - setup['stop'])
    position_size = int(setup['risk_dollars'] / risk_per_share)

    # Risk/reward targets
    r1_target = setup['entry'] + 1.0 * risk_per_share  # 1:1
    r2_target = setup['entry'] + 2.0 * risk_per_share  # 2:1
    r3_target = setup['entry'] + 3.0 * risk_per_share  # 3:1

    return {
        'symbol': symbol,
        'side': 'buy' if setup['direction'] == 'long' else 'sell',
        'qty': position_size,
        'entry_price': setup['entry'],
        'stop_loss': setup['stop'],
        'targets': [
            {'price': r1_target, 'qty_pct': 0.33},
            {'price': r2_target, 'qty_pct': 0.33},
            {'price': r3_target, 'qty_pct': 0.34}
        ]
    }
```

---

## 5. MARKET MICROSTRUCTURE

### 5.1 Time-of-Day Effects

| Time (ET) | Characteristics | Trading Implications |
|-----------|----------------|---------------------|
| **4:00-9:30 AM** | Pre-market: Low liquidity, wide spreads | Scan only, do not trade. Identify gappers. |
| **9:30-9:35 AM** | Opening chaos: extreme volatility | Avoid trading. Let ORB form. |
| **9:35-10:30 AM** | **Best trading window**: High volume, tight spreads, clear direction | Prime execution window. ORB breakouts. |
| **10:30-11:30 AM** | First fade: Overnight moves reverse, volume drops | Take profits from AM trades. Be cautious. |
| **11:30 AM-1:00 PM** | **Lunch hour**: Lowest volume, widest spreads, choppy | Avoid new trades. Algorithms dominate. |
| **1:00-2:00 PM** | Moderate pickup | Resume scanning with caution. |
| **2:00-3:00 PM** | Institutional positioning begins | Good trend trades, follow the flow. |
| **3:00-3:50 PM** | **Power hour**: Volume surges, strong moves | Second-best window. Trend continuation. |
| **3:50-4:00 PM** | Close all day trades | Liquidate all day positions. |

### 5.2 Spread Impact

```python
def calculate_spread_cost(bid, ask, shares):
    """
    The spread is a hidden cost on every trade.
    You pay the ask when buying and receive the bid when selling.
    """
    spread = ask - bid
    spread_pct = spread / ((bid + ask) / 2) * 100
    cost_per_share = spread / 2  # Half-spread per side
    total_cost = cost_per_share * shares * 2  # Round trip

    return {
        'spread': spread,
        'spread_pct': spread_pct,
        'cost_per_share': cost_per_share,
        'round_trip_cost': total_cost
    }

# Example: Stock at $50.00 x $50.05
# Spread = $0.05 (0.1%)
# 1000 shares round trip cost = $50
# That's $50 you need to overcome just to break even
```

**Spread Guidelines for Day Trading:**
- < 0.05% spread: Excellent (large-cap, high-volume)
- 0.05-0.10%: Good for most strategies
- 0.10-0.25%: Acceptable for momentum plays with large moves
- 0.25-0.50%: Marginal, only trade with very strong setups
- > 0.50%: Avoid. The spread will eat your profits.

### 5.3 Slippage Considerations

```python
def estimate_slippage(order_size, avg_volume, spread, order_type='market'):
    """
    Estimate slippage for a market order.
    Rule of thumb: the larger your order relative to volume, the more slippage.
    """
    # Participation rate: what % of volume is your order
    participation_rate = order_size / avg_volume

    if order_type == 'limit':
        return 0  # No slippage on limit orders (but risk of non-fill)

    # Empirical slippage model (approximate)
    if participation_rate < 0.001:  # < 0.1% of volume
        slippage = spread * 0.25   # ~25% of spread
    elif participation_rate < 0.01:  # < 1% of volume
        slippage = spread * 0.50   # ~50% of spread
    elif participation_rate < 0.05:  # < 5% of volume
        slippage = spread * 1.0    # Full spread
    else:
        slippage = spread * 2.0    # 2x spread or more - avoid

    return slippage
```

**Slippage Mitigation:**
- Use limit orders when possible (sacrifice speed for price)
- Keep order size < 1% of average daily volume
- Avoid market orders in first/last 5 minutes
- Use TWAP/VWAP execution for large orders (split over time)
- Trade liquid stocks (ADV > 500K shares)

### 5.4 Liquidity Requirements

**Minimum Liquidity Filters for Day Trading:**
- Average Daily Volume (ADV): > 500,000 shares (conservative), > 200,000 (aggressive)
- Average Daily Dollar Volume: > $5 million
- Bid-ask spread: < 0.10% of price
- Price: > $5.00 (many brokers restrict short selling below $5)
- Market cap: > $300 million

### 5.5 Avoiding Penny Stocks and Illiquid Names

```python
def liquidity_filter(symbol_data):
    """
    Filter out stocks that are too risky to day trade.
    """
    FILTERS = {
        'min_price': 5.00,           # No penny stocks
        'max_price': 500.00,         # Avoid insanely expensive (low share count)
        'min_adv': 200000,           # Minimum average daily volume
        'min_dollar_volume': 5e6,    # $5M minimum daily dollar volume
        'max_spread_pct': 0.25,      # Quarter percent max spread
        'min_market_cap': 300e6,     # $300M minimum market cap
    }

    price = symbol_data['price']
    adv = symbol_data['avg_daily_volume']
    dollar_vol = price * adv
    spread_pct = symbol_data['spread'] / price * 100
    mkt_cap = symbol_data.get('market_cap', float('inf'))

    passes = (
        price >= FILTERS['min_price'] and
        price <= FILTERS['max_price'] and
        adv >= FILTERS['min_adv'] and
        dollar_vol >= FILTERS['min_dollar_volume'] and
        spread_pct <= FILTERS['max_spread_pct'] and
        mkt_cap >= FILTERS['min_market_cap']
    )

    return passes
```

---

## 6. DATA SOURCES FOR REAL-TIME SCANNING

### 6.1 API Comparison

| Provider | Real-Time | WebSocket | Free Tier | Cost (Paid) | Best For |
|----------|-----------|-----------|-----------|-------------|----------|
| **Alpaca** | Yes (SIP + IEX) | Yes | IEX free, SIP with account | $0-99/mo | Trading + data combo |
| **Polygon.io** | Yes (SIP) | Yes | Delayed free | $29-199/mo | Institutional-grade depth |
| **Finnhub** | Yes | Yes | 30 calls/sec free | $49-499/mo | Fundamentals + real-time |
| **Alpha Vantage** | 15-min delay | No | 25 calls/day | $49/mo | Learning, backtesting |
| **Yahoo Finance** | 15-min delay | No | Unlimited (unofficial) | Free | Backtesting only |
| **iTick** | Yes | Yes | Basic quotes free | Varies | Global markets |
| **Databento** | Yes (SIP) | Yes | Pay-per-use | ~$0.01/symbol/day | Low-latency, all equities |
| **Twelve Data** | Yes | Yes | 800 calls/day free | $29-299/mo | Multi-asset |

### 6.2 Alpaca Data API - Detailed Capabilities

**REST Endpoints:**
```python
from alpaca.data import StockHistoricalDataClient, StockLatestDataClient
from alpaca.data.requests import (
    StockBarsRequest, StockSnapshotRequest, StockLatestBarRequest
)
from alpaca.data.timeframe import TimeFrame

client = StockHistoricalDataClient(api_key, secret_key)

# Historical bars
bars = client.get_stock_bars(StockBarsRequest(
    symbol_or_symbols=["AAPL", "TSLA"],
    timeframe=TimeFrame.Minute,
    start="2026-03-25",
    end="2026-03-26"
))

# Latest snapshot (current price, latest trade, latest quote, minute bar, daily bar)
snapshot = client.get_stock_snapshot(StockSnapshotRequest(
    symbol_or_symbols="AAPL"
))
# snapshot contains: latest_trade, latest_quote, minute_bar, daily_bar, prev_daily_bar
```

**WebSocket Streaming:**
```python
from alpaca.data.live import StockDataStream

stream = StockDataStream(api_key, secret_key, feed='sip')  # or 'iex' for free

async def on_bar(bar):
    """Called every minute with OHLCV data."""
    print(f"{bar.symbol}: O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}")

async def on_trade(trade):
    """Called on every individual trade execution."""
    print(f"{trade.symbol}: {trade.price} x {trade.size}")

async def on_quote(quote):
    """Called on every bid/ask update."""
    spread = quote.ask_price - quote.bid_price
    print(f"{quote.symbol}: {quote.bid_price} x {quote.ask_price} (spread: {spread})")

# Subscribe
stream.subscribe_bars(on_bar, "AAPL", "TSLA", "NVDA")
stream.subscribe_trades(on_trade, "AAPL")
stream.subscribe_quotes(on_quote, "AAPL")

stream.run()
```

**Feed Tiers:**
- **IEX feed (free):** Data from IEX exchange only (~3-5% of volume). Good for learning.
- **SIP feed (paid):** Consolidated data from ALL exchanges. Required for serious trading.
- **Delayed SIP:** 15-minute delay. Free. Only for backtesting/analysis.

**Snapshot for Scanning (most efficient):**
```python
# Get snapshots for multiple symbols at once - ideal for scanners
from alpaca.data.requests import StockSnapshotRequest

# Can request up to 100+ symbols per call
snapshots = client.get_stock_snapshot(StockSnapshotRequest(
    symbol_or_symbols=["AAPL", "TSLA", "NVDA", "AMD", "META"]
))

for symbol, snap in snapshots.items():
    print(f"{symbol}:")
    print(f"  Price: {snap.latest_trade.price}")
    print(f"  Bid: {snap.latest_quote.bid_price}")
    print(f"  Ask: {snap.latest_quote.ask_price}")
    print(f"  Today Volume: {snap.daily_bar.volume}")
    print(f"  Prev Close: {snap.previous_daily_bar.close}")
```

### 6.3 Yahoo Finance Limitations

```python
import yfinance as yf

# Yahoo Finance is useful for:
# - Historical data (free, unlimited)
# - Fundamentals (market cap, float, short interest)
# - NOT suitable for real-time trading

# Key limitations:
# 1. Data is delayed 15+ minutes
# 2. Rate limits are aggressive and undocumented
# 3. API can change without notice (unofficial)
# 4. No WebSocket streaming
# 5. Intraday data limited to last 60 days at 1-min resolution

# Good for: building watchlists, backtesting, getting fundamentals
ticker = yf.Ticker("AAPL")
info = ticker.info
float_shares = info.get('floatShares')
short_interest = info.get('sharesShort')
short_pct = info.get('shortPercentOfFloat')
avg_volume = info.get('averageVolume')
```

### 6.4 Free vs Paid Data Summary

**Free Options (good for learning/paper trading):**
- Alpaca IEX feed (real-time but limited exchange coverage)
- Finnhub free tier (30 req/sec, WebSocket available)
- Alpha Vantage (25 calls/day, very limited)
- Yahoo Finance (delayed, good for fundamentals)

**Paid Options (required for live trading):**
- Alpaca SIP ($0 with funded account for some plans)
- Polygon.io Starter ($29/mo) - excellent for scanning
- Databento (~$0.01/symbol/day) - pay only for what you use
- Finnhub Premium ($49/mo) - good balance

**Recommended Stack for a Day Trading Bot:**
1. **Alpaca** for trading execution + real-time streaming (SIP feed)
2. **Yahoo Finance** for fundamentals (float, short interest, market cap)
3. **Polygon.io** or **Finnhub** as backup/additional data

---

## 7. BACKTESTING CONSIDERATIONS

### 7.1 Why Backtesting Matters

- A strategy showing 20% annual returns in backtest might deliver only 8% live after accounting for 0.5% slippage per trade and 0.1% commissions
- Backtesting reveals edge decay, max drawdown, and win rate before risking real capital
- **Never trade a strategy that has not been backtested on at least 2 years of data**

### 7.2 Python Backtesting Frameworks

| Framework | Complexity | Speed | Best For |
|-----------|-----------|-------|----------|
| **backtesting.py** | Low | Fast | Quick prototyping, simple strategies |
| **Backtrader** | Medium | Medium | Full-featured, good documentation |
| **Zipline** | High | Fast | Institutional-grade, Quantopian heritage |
| **VectorBT** | Medium | Very Fast | Vectorized operations, optimization |
| **QuantConnect (Lean)** | High | Fast | Cloud backtesting, multi-asset |

### 7.3 Common Pitfalls

**1. Lookahead Bias**
- Using future data to make past decisions
- Example: Using today's close to decide today's entry
- Fix: Ensure all indicators use only data available BEFORE the signal bar
```python
# WRONG: Using current bar's close to generate signal for current bar
signal = df['close'] > df['sma20']

# RIGHT: Using previous bar's data to generate signal for current bar
signal = df['close'].shift(1) > df['sma20'].shift(1)
```

**2. Survivorship Bias**
- Testing only on currently-listed stocks (ignoring delisted/bankrupt companies)
- Inflates returns because you only see the "survivors"
- Fix: Use datasets that include delisted securities (Norgate Data, Sharadar, CRSP)

**3. Overfitting / Curve Fitting**
- Optimizing parameters until they perfectly fit historical data
- Red flags: Sharpe > 3.0, win rate > 85%, only works on specific dates
- Fix: Split data 70/30 train/test, walk-forward analysis

**4. Ignoring Transaction Costs**
```python
# Always include realistic costs in backtest
COMMISSION_PER_SHARE = 0.005  # $0.005/share (or $0 for many brokers)
SLIPPAGE_PER_SHARE = 0.02     # $0.02 estimated slippage
SEC_FEE_RATE = 0.0000278      # SEC fee on sells ($27.80 per million)

def apply_costs(trade_price, shares, side):
    commission = COMMISSION_PER_SHARE * shares
    slippage = SLIPPAGE_PER_SHARE * shares
    sec_fee = trade_price * shares * SEC_FEE_RATE if side == 'sell' else 0

    if side == 'buy':
        effective_price = trade_price + SLIPPAGE_PER_SHARE
    else:
        effective_price = trade_price - SLIPPAGE_PER_SHARE

    total_cost = commission + slippage + sec_fee
    return effective_price, total_cost
```

**5. Not Accounting for Liquidity**
- Backtesting assumes infinite liquidity (you can always buy/sell at the tested price)
- Reality: Large orders move the market
- Fix: Cap position size at 1% of ADV, add realistic slippage

### 7.4 Walk-Forward Analysis (Gold Standard)

```python
def walk_forward_analysis(df, strategy_func, optimize_func,
                          in_sample_pct=0.70, n_windows=5):
    """
    Walk-forward analysis: optimize on in-sample, test on out-of-sample,
    repeat across rolling windows.

    A robust strategy performs consistently across ALL windows.
    """
    total_bars = len(df)
    window_size = total_bars // n_windows
    results = []

    for i in range(n_windows):
        start = i * window_size
        end = start + window_size
        window_data = df.iloc[start:end]

        # Split into in-sample (optimize) and out-of-sample (validate)
        split = int(len(window_data) * in_sample_pct)
        in_sample = window_data.iloc[:split]
        out_of_sample = window_data.iloc[split:]

        # Optimize parameters on in-sample data
        best_params = optimize_func(in_sample)

        # Test with those parameters on out-of-sample data
        oos_result = strategy_func(out_of_sample, best_params)
        results.append(oos_result)

    # Evaluate consistency
    returns = [r['total_return'] for r in results]
    sharpes = [r['sharpe_ratio'] for r in results]

    print(f"Walk-Forward Results ({n_windows} windows):")
    print(f"  Avg Return: {np.mean(returns):.2%}")
    print(f"  Std Return: {np.std(returns):.2%}")
    print(f"  Min Return: {np.min(returns):.2%}")
    print(f"  Avg Sharpe: {np.mean(sharpes):.2f}")
    print(f"  Win Rate across windows: {sum(1 for r in returns if r > 0)}/{n_windows}")

    return results
```

### 7.5 Monte Carlo Validation

```python
def monte_carlo_validation(trades, n_simulations=1000):
    """
    Shuffle trade order to see if results are robust or luck-dependent.
    If 95% of shuffled equity curves are profitable, the strategy is robust.
    """
    original_returns = [t['return_pct'] for t in trades]
    final_equities = []

    for _ in range(n_simulations):
        shuffled = np.random.permutation(original_returns)
        equity = [10000]  # Starting capital
        for r in shuffled:
            equity.append(equity[-1] * (1 + r))
        final_equities.append(equity[-1])

    final_equities = np.array(final_equities)

    print(f"Monte Carlo ({n_simulations} simulations):")
    print(f"  Median final equity: ${np.median(final_equities):,.0f}")
    print(f"  5th percentile: ${np.percentile(final_equities, 5):,.0f}")
    print(f"  95th percentile: ${np.percentile(final_equities, 95):,.0f}")
    print(f"  % profitable: {(final_equities > 10000).mean() * 100:.1f}%")
    print(f"  Worst case: ${np.min(final_equities):,.0f}")

    # Strategy is robust if 95th percentile profit > 0
    # and 5th percentile loss is manageable
    return {
        'median': np.median(final_equities),
        'p5': np.percentile(final_equities, 5),
        'p95': np.percentile(final_equities, 95),
        'pct_profitable': (final_equities > 10000).mean()
    }
```

### 7.6 Validation Checklist Before Going Live

1. **Backtest on 2+ years of data** with realistic transaction costs
2. **Out-of-sample performance** within 50% of in-sample performance
3. **Walk-forward analysis** profitable in 4/5+ windows
4. **Monte Carlo simulation** > 80% of shuffles profitable
5. **Parameter stability** - edge survives +/-10% parameter changes
6. **Paper trade for 2-4 weeks** with real-time data
7. **Start with 10-25% of intended capital** for first month live
8. **Sharpe Ratio** between 1.0-3.0 (below 1.0 = weak, above 3.0 = likely overfit)
9. **Max drawdown** < 20% (acceptable), < 10% (good)
10. **Profit factor** > 1.5 (total gross profit / total gross loss)

---

## QUICK REFERENCE: KEY THRESHOLDS

| Metric | Threshold | Meaning |
|--------|-----------|---------|
| RVOL | > 2.0 | Stock is "in play" |
| Gap % | > 3% | Significant gap |
| RSI overbought | > 70 | Potential reversal down |
| RSI oversold | < 30 | Potential reversal up |
| ATR stop multiplier | 2-3x | Standard trailing distance |
| BB squeeze | BB inside KC | Volatility contraction |
| Volume z-score | > 3.0 | Volume spike |
| Spread % | < 0.10% | Acceptable for day trading |
| ADV minimum | > 500K shares | Sufficient liquidity |
| Position size | < 1% of ADV | Avoid market impact |
| Sharpe ratio | 1.0-3.0 | Reasonable expectation |
| Profit factor | > 1.5 | Viable strategy |
| Max drawdown | < 20% | Acceptable risk |
| Short interest | > 15% | Squeeze candidate |
| Float rotation | > 0.5x | High speculative interest |

---

## RECOMMENDED PYTHON LIBRARY STACK

```
# Core
alpaca-py          # Broker API + market data
pandas             # Data manipulation
numpy              # Numerical operations

# Technical Analysis
TA-Lib             # 150+ indicators, 61 candlestick patterns (requires C library)
pandas-ta          # 212+ indicators, pure Python alternative
ta                 # Lightweight alternative

# Scientific Computing
scipy              # Signal processing (argrelextrema for swing points)
scikit-learn       # Clustering for S/R detection

# Backtesting
backtesting.py     # Quick prototyping
backtrader         # Full-featured
vectorbt           # Fast vectorized backtesting

# Data
yfinance           # Fundamentals, historical data
finvizfinance      # Screening data (unofficial)

# Infrastructure
asyncio            # Async WebSocket handling
websockets         # WebSocket client
apscheduler        # Job scheduling
logging            # Production logging
```
