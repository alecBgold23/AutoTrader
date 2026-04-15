# AutoTrader - AI Day Trading Platform

## What This Is

An autonomous day trading system that uses Claude as the decision-making brain and Alpaca as the broker. Currently paper trading with $100k. The system scans the entire market, identifies high-probability intraday setups, and executes trades with professional-grade risk management.

**Owner:** alecBgold23 (GitHub)
**Repo:** https://github.com/alecBgold23/AutoTrader.git
**Status:** Active development, paper trading

---

## Architecture Overview

```
run.py                          # Entry point (asyncio.run)
autotrader/
  main.py                       # Orchestrator — scheduler, trading loop, position management
  config.py                     # All configuration (risk params, phase config, scanner settings)
  brain/
    analyst.py                  # ClaudeAnalyst — sends data to Claude, parses JSON responses
    prompts.py                  # System prompt + analysis/ranking prompt templates
  data/
    market.py                   # Alpaca real-time + yfinance historical (get_stock_data, get_current_price, get_intraday_data)
    indicators.py               # Technical indicators (RSI, MACD, Bollinger, VWAP, ATR, Choppiness Index, ADX)
    patterns.py                 # Candlestick + chart pattern detection, key levels (S/R, ORB)
    scanner.py                  # MarketScanner — builds universe, scores stocks, surfaces hot list
    news.py                     # Alpaca News API — catalyst detection for prompt
    regime.py                   # MarketRegime — SPY trend + VIX level → regime-aware sizing
  execution/
    broker.py                   # AlpacaBroker — order execution, position management, portfolio snapshots
    stalker.py                  # EntryStalker — limit order management for patient entries
  risk/
    manager.py                  # RiskManager — all risk checks, position sizing, circuit breakers
    position_manager.py         # PositionManager — trailing stops, scale-outs (live: 1/3 at 1R, 1/3 at 2R; backtest: 1/2 at 1R, close at 1.5R)
  alerts/
    telegram.py                 # TelegramAlerts — trade notifications, daily summaries
  analytics/
    performance.py              # Performance tracking — metrics by pattern, phase, day, rolling 20
  backtest/
    engine.py                   # Backtest engine — dynamic market scanning, bar-by-bar replay with Claude analysis
    data_fetcher.py             # Fetches 5-min bars (Alpaca) + daily bars (yfinance), cached as CSV. Supports batch download via yf.download()
    runner.py                   # CLI: python -m autotrader.backtest.runner --start 2024-12-02 --end 2025-02-28
  db/
    models.py                   # SQLAlchemy models (Trade, Decision, PortfolioSnapshot, RiskEvent, TradingJournal)
scripts/
  setup.py                      # Initial setup script
  test_paper.py                 # Paper trading test
  set_keys.py                   # API key configuration
data/
  autotrader.db                 # SQLite database (auto-created)
  backtest_cache/               # Cached 5m bars (Alpaca) and daily bars (yfinance) as CSV
  claude_cache/                 # Cached Claude API responses (keyed by PROMPT_VERSION + symbol + date + time + price + volume + phase)
  backtest_results/             # Equity curves and full result JSONs per run
logs/                           # Daily log files + launchd output
```

---

## How It Works (Trading Flow)

### 1. Startup (run.py)
- Initializes all components: ClaudeAnalyst, AlpacaBroker, RiskManager, PositionManager, EntryStalker, MarketScanner
- Creates database tables, takes initial portfolio snapshot
- Builds trading universe by scanning all Alpaca-tradeable US equities (~800 liquid stocks)
- Starts the APScheduler with all recurring jobs

### 2. Scheduled Jobs
| Job | Interval | What It Does |
|-----|----------|--------------|
| `_trading_loop` | Dynamic (2-10 min by phase) | Core loop: scan, analyze, trade |
| `_manage_positions` | 2 min | Trailing stops, scale-outs, time exits |
| `_check_stalked_entries` | 30 sec | Monitor pending limit orders |
| `_refresh_hot_list` | 15 min | Re-score universe for movers |
| `_snapshot_portfolio` | 30 min | Save portfolio state to DB |
| `_rebuild_universe` | Daily 9:00 AM ET | Full market rescan |
| `_eod_close_all` | Daily 3:50 PM ET | Close all positions |
| `_daily_summary` | Daily 4:30 PM ET | Journal entry + performance log, then auto-shutdown |
| `_weekly_performance_report` | Friday 4:45 PM ET | Full analytics report via Telegram |

### 3. Trading Loop (`_trading_loop`)
1. Check risk halt status and market hours
2. Determine market phase (premarket/open/prime/lunch/afternoon/power_hour/close)
3. Dynamically adjust scan interval based on phase
4. Get top candidates from scanner hot list
5. For each candidate:
   - `ClaudeAnalyst.analyze()` gathers: daily indicators, 5-min intraday indicators, candlestick/chart patterns, key S/R levels, news
   - Builds a comprehensive prompt and sends to Claude API
   - Claude returns JSON: `{action, confidence, entry_price, stop_loss, take_profit, pattern, reasoning}`
   - `RiskManager.check_trade()` validates against all risk rules
   - If approved: execute via market order or limit order (entry stalking)

### 4. Entry Stalking (EntryStalker)
Instead of chasing with market orders, the system places limit orders at Claude's suggested entry price when:
- Entry is >0.3% below current price
- Confidence is <85% (high confidence = don't miss it)
- Entry is above the stop loss

Limit orders have a 10-minute timeout. If price drops 0.5% below stop loss, the order is cancelled (setup invalidated).

### 5. Position Management (PositionManager)
Professional scale-out strategy:
- **1R profit**: Sell 1/3 of position, move stop to breakeven
- **2R profit**: Sell another 1/3, begin ATR-based trailing stop
- **Remaining 1/3**: Trail with 1.5x ATR or 2% fixed trailing stop
- **Time exits**: Half position at 30 min to close, full close at 15 min to close
- **EOD**: Force close everything at 3:50 PM ET

### 6. Sells
The system sells when:
- Claude issues a SELL signal (momentum fading, stop hit, thesis changed)
- PositionManager triggers a stop loss, scale-out, or time exit
- EOD close-all fires

**Critical implementation detail:** Simple market orders are used for buys (NOT bracket orders). Bracket orders lock shares via child orders, preventing subsequent sells. The `close_position()` method cancels all pending orders for a symbol first, then closes.

---

## Market Phases (PHASE_CONFIG)

Time-of-day awareness is built into every layer — prompts, risk thresholds, position sizing, and scan frequency.

| Phase | Time (ET) | Scan Interval | Size Mult | Min Confidence | Strategy |
|-------|-----------|---------------|-----------|----------------|----------|
| premarket | <9:30 | 5 min | 0% | 100% (blocked) | No trades |
| open | 9:30-10:00 | 2 min | 50% | 65% | Gap & Go, ORB, Red-to-Green |
| prime | 10:00-11:00 | 5 min | 100% | 55% | First Pullback, Flags, VWAP Reclaim |
| lunch | 11:00-1:30 | 10 min | 35% | 72% | Mean reversion only |
| afternoon | 1:30-3:00 | 5 min | 85% | 58% | Continuations, VWAP tests |
| power_hour | 3:00-3:50 | 3 min | 100% | 55% | HOD/LOD breaks, momentum |
| close | 3:50+ | 5 min | 0% | 100% (blocked) | Exit only |

---

## Risk Management

### Hard Limits (config.py → RISK dict)
- **Max risk per trade**: 1% of equity
- **Max single position**: 5% of equity
- **Max total exposure**: 40% of equity
- **Max daily loss**: 2% → halts trading
- **Max drawdown**: 10% from peak → full stop
- **Min R:R ratio**: 2:1 (slippage-adjusted)
- **Max trades/day**: 8
- **Consecutive loss limit**: 3 → 30 min cooldown
- **Daily R limit**: -3R → stop trading
- **Max sector concentration**: 2 positions per correlated group

### Sector/Correlation Concentration
Prevents concentrated bets on correlated stocks (e.g., AAPL + MSFT + GOOGL = one tech bet).
Six groups defined in `manager.py` (live) and inlined as `_CORRELATED_GROUPS` in `engine.py` (backtest): mega_tech, semis, ev_auto, banking, energy, biotech.
Max 2 positions per group. Unknown-sector stocks are unrestricted.

### Slippage Modeling
All R:R checks and position sizing account for estimated slippage (0.1% per side, 0.2% round trip).
- R:R check: risk widened, reward narrowed by slippage before comparing to 2:1 minimum
- Position sizing: stop distance widened by 0.2% → slightly fewer shares → real-world-accurate risk

### Broker-Side Stop Losses
Every position has a corresponding Alpaca stop order placed immediately after fill. This provides crash protection — if the system goes down, stops are enforced by the broker. Stop orders are:
- Placed after every BUY (market or limit fill)
- Updated (cancel + re-place) when trailing stops move up
- Adjusted for remaining quantity after scale-outs
- Reconciled on startup: any position without a broker stop gets one at 3% below current price

### Position Sizing
Fixed-fractional: `risk_amount = equity * 0.01 * phase_multiplier * regime_multiplier`
Then: `shares = risk_amount / (price * (stop_distance + slippage))`
Capped by buying power and max position concentration.

### Graceful Degradation on API Failure
- Claude API calls retry 3x with exponential backoff (2s, 4s, 8s)
- After 5+ consecutive failures: new entries halted, Telegram alert sent
- Position management (trailing stops, scale-outs) continues without Claude
- Price fetches fall back to last known cached price if API fails
- Normal operation resumes automatically when API recovers

---

## Market Data Strategy

- **Real-time prices** (`get_current_price`): Alpaca snapshots — no delay, broker-native, falls back to yfinance for non-Alpaca symbols (e.g., ^VIX)
- **Intraday bars** (`get_intraday_data`): Alpaca 5-min bars — consistent, real-time, falls back to yfinance
- **Historical daily** (`get_stock_data`): yfinance — free, good for 3mo+ lookbacks for indicator calculation
- **Batch prices** (`get_batch_prices`): yfinance — efficient for 800+ stock universe scans

---

## Performance Analytics (`autotrader/analytics/performance.py`)

Calculates running metrics from the trade database:
- **Overall**: win rate, profit factor, Sharpe ratio, max drawdown, expectancy, avg R per trade
- **By pattern**: win rate and avg R for each setup type — identifies which patterns Claude is good/bad at
- **By phase**: win rate and avg R for each market phase — validates time-of-day strategy
- **By day of week**: systematic performance differences
- **Rolling 20-trade window**: recent trend (improving vs degrading)
- **Auto-flagging**: patterns with WR < 35% + negative R (10+ trades) flagged for removal

Reports run daily (logged) and weekly (Telegram).

---

## Backtesting (`autotrader/backtest/`)

**Intraday 5-minute bar replay** — simulates exactly what the live system sees at each point in time.

### Architecture
- `data_fetcher.py` — Fetches 5-min bars from Alpaca (28-day chunks), daily bars from yfinance (individual or batch via `yf.download()`), VIX data. All cached as CSV in `data/backtest_cache/`. Includes `get_trading_days()` which uses cached SPY data to avoid rate limits.
- `engine.py` — Core replay engine: dynamic market scanning, bar-by-bar simulation with Claude analysis, position management, risk checks. Computes Choppiness Index and ADX from SPY for regime context (stored in regime dict but not used for trade filtering).
- `runner.py` — CLI interface

### Dynamic Market Scanning
The backtest simulates real market scanning — not a hardcoded stock list:
1. Pulls ALL ~12,000 Alpaca tradeable US equities
2. Filters by price ($5-$1000) and avg volume (500k+) using data from the backtest period (not today) → ~800 liquid stocks
3. Each trading day, scores all ~800 using prior-day data: RVOL (35%), Gap (25%), Momentum (15%), ATR (10%), Trend (8%), Key levels (7%)
4. Selects top 15 movers per day — different stocks every day, just like a real scanner
5. Lazy-fetches 5m data only for symbols that make the daily hot list
6. Universe cache is keyed by date range so different backtest periods get different universes

### What it simulates
1. Builds period-aware universe and scores candidates using prior-day data (no look-ahead)
2. Steps through each trading day in 5-minute increments starting at 9:30 AM ET
3. At 10 scheduled analysis times, feeds Claude the indicators, patterns, key levels, VWAP from 5-min data
4. Applies phase-specific confidence thresholds, position sizing multipliers, and pattern preferences
5. Applies regime-aware sizing (SPY trend + VIX level from prior close)
6. Fills buy orders at next bar's open + $0.01/share slippage
7. Close-based stops (bar close below stop, not wick touching)
8. Scale-out: 1/3 at 1R (move stop to BE), 1/3 at 2R; trailing stop 0.5R from high, floor at breakeven
9. Adaptive time stops: 45-min losers with MFE ≥ 0.3% get BE lock instead of hard close; 90-min hard close only if underwater; winners trail until stopped or EOD
10. Time exits: half at 30 min to close, full at 15 min, force-close at 3:50 PM ET
11. Lunch + afternoon phases blocked from new entries (structural: low volume, choppy action)
12. Enforces all risk limits: 2% daily loss halt, 3-loss cooldown, sector concentration, 80% max exposure

### Backtest-Specific Risk Parameters
These are tuned separately from live (pending sync after validation):
- Max risk per trade: 15% of equity (day trading, flat by EOD)
- Max single position: 20% of equity ($20k on $100k)
- Max total exposure: 80%
- Min confidence: 0.65
- Min R:R: 1.5:1
- Slippage: $0.01/share (realistic for liquid large/mid-caps)
- Regime multiplier flattened for day trading (bear_quiet: 0.85x vs live 0.50x)
- Open phase 1.5x sizing boost
- Analyze count: 10 symbols per cycle (filtered by technical pre-filter)

### Cost management
- Claude responses cached in `data/claude_cache/` (keyed by `PROMPT_VERSION + symbol + date + time + price + volume + phase`)
- `PROMPT_VERSION = "v2"` — changing this invalidates all cached responses
- `--model` flag: defaults to `claude-haiku-4-5-20251001` for cheap bulk runs
- `--max-cycles-per-day` flag: defaults to 10
- First run for a new date range is slow (fresh API calls); subsequent re-runs are near-instant (fully cached)

### Usage
```bash
# 3-month test
./venv/bin/python -m autotrader.backtest.runner --start 2024-04-01 --end 2024-06-28

# Different period
./venv/bin/python -m autotrader.backtest.runner --start 2024-09-01 --end 2024-11-30

# With specific model
./venv/bin/python -m autotrader.backtest.runner --start 2024-12-02 --end 2025-02-28 --model claude-sonnet-4-20250514
```

### Latest Results (Apr 2026, deterministic engines + adaptive time stops + new signal patterns)

Uses deterministic SignalEngine + ShortSignalEngine (no Claude API calls). Key improvements:
- 0.5R trailing stop (A/B tested: +$1,283 vs 0.7R across 8 periods)
- **Adaptive time stops**: At 45 min, if a losing position had MFE ≥ 0.3% (was profitable at some point), lock breakeven instead of hard-closing. At 90 min, hard-close only if still underwater; winners trail until stopped or EOD.
- **New signal patterns**: PDH Reclaim (long), Failed Breakout unblocked via `was_at_hod` bug fix (short), score bonuses for three_white_soldiers/three_black_crows/inside_bar
- 4x PDT margin (accurate Alpaca modeling, negligible P&L impact)

8 overlapping 3-month periods:

| Period | P&L | Trades | WR | PF |
|--------|-----|--------|----|----|
| Apr-Jun 2024 | +$13,426 | 246 | 60.6% | 1.96 |
| May-Jul 2024 | +$2,127 | 238 | 49.2% | 1.12 |
| Jun-Aug 2024 | +$17,730 | 314 | 62.4% | 1.87 |
| Jul-Sep 2024 | +$14,666 | 197 | 68.0% | 2.30 |
| Aug-Oct 2024 | +$9,226 | 214 | 61.7% | 1.85 |
| Sep-Nov 2024 | +$3,176 | 133 | 54.1% | 1.43 |
| Oct-Dec 2024 | +$4,308 | 86 | 59.3% | 2.24 |
| Dec-Feb 2025 | +$3,166 | 63 | 60.3% | 1.85 |
| **Total** | **+$67,825** | **1,491** | | |

**All 8 periods profitable.** Average ~$2,826/month (~2.8%/month on $100K). Previous baseline (same yfinance data): +$28,340 total — **+$39,485 improvement (139%)**.

#### Changes tested and rejected (Apr 2026)
- **Leveraged ETFs** (TQQQ/SOXL/TNA/UPRO/SPXL force-include): -$1,335 net. Amplifies losses as much as gains, drops WR 2%.
- **Afternoon trading re-enabled**: -$2,124. 38.5% WR on 96 trades even with adaptive stops. Structural low quality.
- **4x margin alone**: +$94. Buying power is never the binding constraint (risk params are).
- **BE lock at 0.5R**: Neutral. Protects some blown winners but prematurely stops real winners. Kept at 0.7R.
- **Scale 1/2 at 1R**: Worse. Reduces runner position too much. Kept at 1/3.
- **Red to Green** (long pattern): 0/3 trades across 2 periods (0% WR). Detection fires too late — move already extended.
- **Green to Red** (short pattern): 0/1 trades. Same issue as Red-to-Green — detection fires too late.

**Important caveat:** Results are sensitive to which yfinance daily data is cached. Data downloaded on different dates can produce slightly different adjusted prices, which changes scanner scores and symbol selection. Always establish a fresh baseline before comparing changes.

### PASS/FAIL thresholds
- 200+ trades: required
- Profit factor > 1.3: required
- Max drawdown < 8%: required
- Sharpe > 0.75: required

### Backtesting Principles (MUST follow when tuning)

**DO NOT overfit to the backtest period.** Every change must be justified by structural logic, not by "symbol X lost money in March." The system trades dynamically across all symbols, all market conditions, all time periods. Specifically:

1. **Never filter by specific symbol.** Symbols rotate. A loser in March may be the best setup in April. The scanner and Claude handle symbol selection — don't hardcode winners/losers.
2. **Never tune parameters to a specific date range.** If a change helps March but would hurt January, it's overfitting. Changes must be structurally sound (e.g., "close-based stops reduce noise false-outs" is structural; "remove RIOT because it lost $158" is overfitting).
3. **Position management changes must be mathematically justified.** Show the win-rate / payoff-ratio math. If the system has 55% position WR, position management must be profitable at 55% WR — not just at 67%.
4. **Phase/time-of-day rules should reflect market microstructure**, not backtest P&L. "Midday has lower volume and wider spreads" is structural. "11 AM lost money in March" is noise.
5. **Always re-run after changes.** Cached Claude responses make this fast. If a change doesn't improve results on cached data, it won't help live either.
6. **Market regime (bull/bear, choppy/trending) matters less for day trading.** Day trading is pattern-based — you're flat every night. Regime-based trade suppression (CHOP/ADX gates, afternoon momentum blocks) was tested and found to help bad periods at the cost of destroying good ones. Focus on trade mechanics (entries, exits, position management) not market conditions.
7. **Verify your baseline before iterating.** yfinance data changes over time (adjustments, splits). A "baseline" from last week may not be reproducible today. Always re-run the committed code to establish a current baseline before measuring the effect of changes.

### Output
- Console: full metrics, pattern breakdown, phase breakdown, PASS/FAIL
- `data/backtest_results/equity_YYYYMMDD_HHMMSS.csv` — equity curve
- `data/backtest_results/backtest_YYYYMMDD_HHMMSS.json` — full results with all trades

---

## Database (SQLite via SQLAlchemy)

Located at `data/autotrader.db`. Five tables:

- **trades**: Every executed trade with confidence, reasoning, pattern, fill price, P&L, R-multiple
- **decisions**: Every Claude analysis (including HOLDs and blocked trades) with market_phase
- **portfolio_snapshots**: Periodic equity/cash/positions snapshots
- **risk_events**: Halts, cooldowns, circuit breaker activations
- **trading_journal**: Daily summaries (win rate, P&L, R earned, best/worst patterns)

---

## Environment Variables (.env)

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true
ANTHROPIC_API_KEY=...
CLAUDE_MODEL=claude-haiku-4-5-20251001
TELEGRAM_BOT_TOKEN=         # Optional
TELEGRAM_CHAT_ID=           # Optional
AUTONOMY_MODE=full_auto     # full_auto | notify_first | require_approval
LOG_LEVEL=INFO
```

---

## Auto-Start (macOS launchd)

Plist at `~/Library/LaunchAgents/com.autotrader.daily.plist`
- Starts Mon-Fri at 8:00 AM CT (9:00 AM ET, 30 min before market open)
- Uses venv python directly
- Logs to `logs/launchd_stdout.log` and `logs/launchd_stderr.log`
- Auto-shuts down after daily summary at 4:30 PM ET

Load/unload:
```bash
launchctl load ~/Library/LaunchAgents/com.autotrader.daily.plist
launchctl unload ~/Library/LaunchAgents/com.autotrader.daily.plist
```

---

## Running Manually

```bash
cd ~/Desktop/AutoTrader
source venv/bin/activate
python run.py
```

---

## Key Design Decisions

1. **No bracket orders for buys** — They create child orders that lock shares, preventing sells. Standalone stop orders are placed after each fill instead, and managed (cancel/replace) as trailing stops move.

2. **Cancel orders before closing positions** — `cancel_orders_for_symbol()` is always called before `close_position()` to free any held shares.

3. **Claude is the trader, not a signal generator** — The system prompt treats Claude as a thinking trader, not a rule-following bot. It gets full market context and makes judgment calls.

4. **Entry stalking over market orders** — When Claude identifies an entry below current price, a limit order is placed instead of chasing. Better entries = better R:R.

5. **Phase-aware everything** — Time of day affects scan intervals, position sizing, confidence thresholds, and which patterns Claude prioritizes.

6. **Scale-out, don't exit all-at-once** — Professional 1/2 scaling at 1R with breakeven stop after first profit, then trailing stop.

7. **Patterns over regime** — Day trading is fundamentally pattern-based. The system should work in any market condition. Regime-based trade suppression was tested and found to be net-neutral (helps bad periods, hurts good ones equally). Claude should detect pattern quality from the actual price action, not from top-down regime filters.

---

## Market Awareness (regime + catalyst + trade budget)

### Market Regime (`autotrader/data/regime.py`)
Detects market conditions using SPY trend (above/below 50-day SMA) + VIX level:
- **bull_quiet**: VIX < 16, SPY above 50SMA → full size (1.0x)
- **bull_volatile**: VIX > 22, SPY above 50SMA → reduced size (0.70x)
- **bear_quiet**: VIX < 22, SPY below 50SMA → half size (0.50x)
- **bear_volatile**: VIX > 30, SPY below 50SMA → minimal size (0.25x)

Regime multiplier stacks with the phase multiplier in position sizing:
`risk_amount = equity * 1% * phase_multiplier * regime_multiplier`

Updated once per trading loop cycle. Blocks all new trades when VIX extreme + bearish SPY.

The backtest engine also computes Choppiness Index and ADX from SPY daily data (`indicators.py: calculate_choppiness_index`, `calculate_adx`) and stores them in the regime dict. These are available for future use but are NOT currently used for trade filtering (tested and found counterproductive for day trading).

### News / Catalyst (`autotrader/data/news.py`)
Uses Alpaca's built-in News API (no extra API key needed — uses broker credentials).
Formats output as CATALYST DETECTED or NO RECENT CATALYST for Claude's analysis.

### Trade Budget
Claude sees "{trades_today}/8 trades used" and is taught to pace itself.

---

## Known Issues / Not Yet Configured

- **Deterministic mode** — System uses SignalEngine + ShortSignalEngine for all decisions. No Claude API calls needed. Claude cache is unused in deterministic mode.
- **Live ≈ Backtest parameters** — Live now uses same risk params (5% per trade, 15% max position, 80% exposure), same signal engines (long + short), same position management (1/3 at 1R, 1/3 at 2R, 0.5R trailing stop). Minor differences: live uses broker-side stops (GTC) for crash protection; backtest uses simulated close-based stops. **Pending sync to live**: adaptive time stops (MFE-based BE lock), MFE tracking in live PositionManager, new signal patterns (PDH Reclaim, Failed Breakout was_at_hod fix, pattern bonuses).
- **Long engine selectivity** — Long MIN_SCORE raised to 65 (vs short's 62). Long confidence death zone (0.80-0.85) completely blocked: 28% WR, -$1,592 across 53 trades. Short death zone kept at 50% size reduction (58% WR, neutral P&L).
- **Dual Thrust dynamic range** — `calculate_dual_thrust_range()` in indicators.py computes adaptive ORB thresholds from 5-day price action. Confirmed ORB breakouts (exceeding DT level) get +8 score bonus. Infrastructure available for future use as standalone setup.
- **launchd import error** — `alpaca.data.news.NewsClient` module not found on launchd restart. The Friday process (manual `run.py`) works fine. Need to fix the launchd plist Python/venv path.
- **Telegram** not configured — alerts are silently skipped
- **Shorting enabled in both live and backtest** — `ShortSignalEngine` (`signals/short_engine.py`) integrated into both `backtest/engine.py` and `main.py` with direction-aware P&L, stops, scale-outs, trailing stops, and broker stop orders. Patterns: ORB Breakdown, VWAP Rejection, Bear Flag, LOD Break, Failed Breakout (via was_at_hod). Gap & Fade, Green-to-Red blocked. Score bonuses: three_black_crows (+12), inside_bar bearish (+8). Controlled via `ENABLE_SHORT` in `config.py`.
- **Scanner uses yfinance batch downloads** — Can be slow on initial universe build (~2-3 min for 800 stocks)
- **Backtest results not reproducible across sessions** — yfinance adjusts historical prices over time. Daily data cached on different dates produces different scanner scores and symbol selection.

---

## Dependencies

```
alpaca-py>=0.30.0           # Broker API + News API
anthropic>=0.39.0           # Claude API
pandas>=2.2.0               # Data manipulation
numpy                       # Numerical (used in Choppiness Index calc)
ta>=0.11.0                  # Technical indicators (includes ADXIndicator)
yfinance>=0.2.40            # Market data
python-dotenv>=1.0.0        # Env vars
sqlalchemy>=2.0.30          # ORM
python-telegram-bot>=21.0   # Alerts
apscheduler>=3.10.4         # Job scheduling
aiohttp>=3.9.0              # Async HTTP
```

Python 3.14, venv at `./venv/`
