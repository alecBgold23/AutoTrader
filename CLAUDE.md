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
    indicators.py               # Technical indicators (RSI, MACD, Bollinger, VWAP, ATR, etc.)
    patterns.py                 # Candlestick + chart pattern detection, key levels (S/R, ORB)
    scanner.py                  # MarketScanner — builds universe, scores stocks, surfaces hot list
    news.py                     # Alpaca News API — catalyst detection for prompt
    regime.py                   # MarketRegime — SPY trend + VIX level → regime-aware sizing
  execution/
    broker.py                   # AlpacaBroker — order execution, position management, portfolio snapshots
    stalker.py                  # EntryStalker — limit order management for patient entries
  risk/
    manager.py                  # RiskManager — all risk checks, position sizing, circuit breakers
    position_manager.py         # PositionManager — trailing stops, scale-outs (1/3 at 1R, 1/3 at 2R)
  alerts/
    telegram.py                 # TelegramAlerts — trade notifications, daily summaries
  analytics/
    performance.py              # Performance tracking — metrics by pattern, phase, day, rolling 20
  backtest/
    engine.py                   # Backtest engine — replay historical days through trading logic
    runner.py                   # CLI: python -m autotrader.backtest.runner --start 2025-01-02 --end 2025-03-01
  db/
    models.py                   # SQLAlchemy models (Trade, Decision, PortfolioSnapshot, RiskEvent, TradingJournal)
scripts/
  setup.py                      # Initial setup script
  test_paper.py                 # Paper trading test
  set_keys.py                   # API key configuration
data/
  autotrader.db                 # SQLite database (auto-created)
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
Six groups defined in `manager.py`: mega_tech, semis, ev_auto, banking, energy, biotech.
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
- `data_fetcher.py` — Fetches 5-min bars from Alpaca, daily bars from yfinance, cached as CSV in `data/backtest_cache/`
- `engine.py` — Core replay engine: bar-by-bar simulation with Claude analysis, position management, risk checks
- `runner.py` — CLI interface

### What it simulates
1. Builds universe and scores candidates using prior-day data (no look-ahead)
2. Steps through each trading day in 5-minute increments starting at 9:30 AM ET
3. At scheduled analysis times, feeds Claude the same indicators, patterns, key levels, VWAP from 5-min data
4. Applies phase-specific confidence thresholds, position sizing multipliers, and pattern preferences
5. Applies regime-aware sizing (SPY trend + VIX level from prior close)
6. Fills buy orders at next bar's open + $0.02/share slippage
7. Runs full PositionManager: scale-out 1/3 at 1R, 1/3 at 2R, 2% trailing stop on remainder
8. Time exits: half at 30 min to close, full at 15 min, force-close at 3:50 PM ET
9. Enforces all risk limits: 2% daily loss halt, 3-loss cooldown, sector concentration, 40% max exposure

### Cost management
- Claude responses cached in `data/claude_cache/` (keyed by symbol + date + time + price + volume)
- `--model` flag: defaults to `claude-haiku-4-5-20251001` for cheap bulk runs
- `--max-cycles-per-day` flag: defaults to 6 (open, 10:00, 10:30, 11:30, 2:00, 3:15)

### Usage
```bash
# 20-day test on Haiku (verify engine works)
python -m autotrader.backtest.runner --start 2025-03-01 --end 2025-03-28 --model claude-haiku-4-5-20251001

# Full 60-day validation
python -m autotrader.backtest.runner --start 2025-01-02 --end 2025-03-28 --model claude-haiku-4-5-20251001 --max-cycles-per-day 6

# Final Sonnet run
python -m autotrader.backtest.runner --start 2025-01-02 --end 2025-03-28 --model claude-sonnet-4-20250514
```

### PASS/FAIL thresholds
- 200+ trades: required
- Profit factor > 1.3: required
- Max drawdown < 8%: required
- Sharpe > 0.75: required

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
CLAUDE_MODEL=claude-sonnet-4-20250514
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

6. **Scale-out, don't exit all-at-once** — Professional 1/3-1/3-1/3 scaling with breakeven stop after first profit.

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

### News / Catalyst (`autotrader/data/news.py`)
Uses Alpaca's built-in News API (no extra API key needed — uses broker credentials).
Formats output as CATALYST DETECTED or NO RECENT CATALYST for Claude's analysis.

### Trade Budget
Claude sees "{trades_today}/8 trades used" and is taught to pace itself.

---

## Known Issues / Not Yet Configured

- **Telegram** not configured — alerts are silently skipped
- **No shorting** — System is long-only. SELL signals on unowned stocks are skipped.
- **Scanner uses yfinance batch downloads** — Can be slow on initial universe build (~2-3 min for 800 stocks)

---

## Dependencies

```
alpaca-py>=0.30.0           # Broker API + News API
anthropic>=0.39.0           # Claude API
pandas>=2.2.0               # Data manipulation
ta>=0.11.0                  # Technical indicators
yfinance>=0.2.40            # Market data
python-dotenv>=1.0.0        # Env vars
sqlalchemy>=2.0.30          # ORM
python-telegram-bot>=21.0   # Alerts
apscheduler>=3.10.4         # Job scheduling
aiohttp>=3.9.0              # Async HTTP
```

Python 3.14, venv at `./venv/`
