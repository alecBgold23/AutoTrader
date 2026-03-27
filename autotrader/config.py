"""Central configuration for AutoTrader."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "autotrader.db"
LOG_DIR = BASE_DIR / "logs"

# ── Alpaca ─────────────────────────────────────────────
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

# ── Claude / Anthropic ─────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
CLAUDE_MAX_TOKENS = 1500

# ── Telegram ───────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Market Scanner ────────────────────────────────────
SCANNER = {
    # Universe filters
    "min_price": 5.0,                   # Minimum stock price
    "max_price": 1000.0,                # Maximum stock price
    "min_avg_volume": 500_000,          # Minimum average daily volume
    "min_relative_volume": 1.5,         # Minimum volume vs 20-day avg to be "hot"
    "min_gap_pct": 2.0,                 # Minimum gap % to flag as gapper

    # Watchlist sizes
    "universe_size": 800,               # Max stocks in daily universe
    "hot_list_size": 40,                # Stocks actively monitored intraday
    "claude_analyze_count": 8,          # Top candidates sent to Claude each cycle

    # Refresh intervals
    "universe_refresh_hour": 9,         # Rebuild universe at 9:00 AM ET
    "hot_list_refresh_minutes": 15,     # Re-rank hot list every 15 min
}

# ── Fallback Watchlist (used if scanner hasn't built universe yet) ─
WATCHLIST_FALLBACK = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "SPY", "QQQ", "AMD",
]

# ── Day Trading Parameters ────────────────────────────
DAY_TRADE_MODE = True                   # Enable day trading behavior
CLOSE_ALL_EOD = True                    # Close all positions before market close
EOD_CLOSE_MINUTE = 50                   # Close positions at 3:50 PM ET

# Scan intervals (in minutes)
SCAN_INTERVAL_OPEN = 2                  # First 30 min after open (high volatility)
SCAN_INTERVAL_NORMAL = 5                # Rest of the day
SCAN_INTERVAL_POWER_HOUR = 3            # Last hour (3:00-3:50 PM)

# Market hours (Eastern Time)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

# ── Risk Management ───────────────────────────────────
RISK = {
    # Position sizing — conservative until system is proven
    "max_risk_per_trade_pct": 0.01,      # 1% of portfolio per trade (was 3%)
    "max_position_pct": 0.05,            # 5% of portfolio in one stock (was 8%)
    "max_total_exposure_pct": 0.40,      # 40% of portfolio deployed at once (was 60%)

    # Loss limits — tighter for unproven system
    "max_daily_loss_pct": 0.02,          # 2% daily loss → halt trading (was 4%)
    "max_weekly_loss_pct": 0.05,         # 5% weekly loss → halt trading (was 8%)
    "max_drawdown_pct": 0.10,            # 10% from peak → full stop (was 15%)

    # Stop loss / take profit
    "default_stop_loss_pct": 0.03,       # 3% stop loss (tighter for day trades)
    "trailing_stop_pct": 0.02,           # 2% trailing stop
    "min_risk_reward_ratio": 2.0,        # Minimum 2:1 reward-to-risk

    # Circuit breakers — tighter limits
    "max_consecutive_losses": 3,         # Pause after 3 losses in a row (was 4)
    "cooldown_after_losses_minutes": 30, # 30 min cooldown (faster for day trading)
    "max_trades_per_day": 8,             # Limit churn (was 30)

    # Confidence thresholds
    "min_confidence_to_trade": 0.55,     # 55%+ to trade (let Claude use judgment)
    "min_confidence_full_auto": 0.70,    # 70%+ = no human approval needed
}

# ── Autonomy Levels ───────────────────────────────────
# "full_auto"   - trades execute immediately, alerts sent after
# "notify_first" - sends alert, waits for approval (timeout = auto-execute)
# "require_approval" - must approve every trade via Telegram
AUTONOMY_MODE = os.getenv("AUTONOMY_MODE", "full_auto")
APPROVAL_TIMEOUT_SECONDS = 300  # 5 minutes

# ── Phase-Specific Trading Parameters ────────────────
# Each phase adjusts strategy: confidence requirements, position sizing, pattern selection
PHASE_CONFIG = {
    "premarket": {
        "scan_interval": 5,
        "size_multiplier": 0.0,        # Don't trade premarket
        "min_confidence": 1.0,          # Effectively blocks trades
        "preferred_setups": [],
        "avoid_setups": ["all"],
    },
    "open": {
        "scan_interval": SCAN_INTERVAL_OPEN,   # 2 min — fast scanning
        "size_multiplier": 0.5,                 # Half size — volatility is wild
        "min_confidence": 0.65,                 # Higher bar — lots of noise
        "preferred_setups": [
            "Gap & Go", "Opening Range Breakout", "Red to Green",
        ],
        "avoid_setups": [
            "Mean Reversion",  # Don't fade the open
        ],
    },
    "prime": {
        "scan_interval": SCAN_INTERVAL_NORMAL,  # 5 min
        "size_multiplier": 1.0,                  # Full size — best setups
        "min_confidence": 0.55,                  # Standard bar
        "preferred_setups": [
            "First Pullback", "Bull Flag", "Bear Flag",
            "VWAP Reclaim", "Momentum Continuation",
            "Opening Range Breakout",
        ],
        "avoid_setups": [],
    },
    "lunch": {
        "scan_interval": 10,                     # Slow scan — nothing happening
        "size_multiplier": 0.35,                 # Tiny size — most moves are fake
        "min_confidence": 0.72,                  # High bar — need A+ setups only
        "preferred_setups": [
            "Mean Reversion",  # Range-bound plays work at lunch
        ],
        "avoid_setups": [
            "Opening Range Breakout", "Momentum Continuation",
            "Gap & Go", "HOD Break", "LOD Break",
            "Bull Flag", "Bear Flag",  # Breakouts fail at lunch
        ],
    },
    "afternoon": {
        "scan_interval": SCAN_INTERVAL_NORMAL,  # 5 min
        "size_multiplier": 0.85,                 # Near-full size
        "min_confidence": 0.58,                  # Slightly above standard
        "preferred_setups": [
            "Momentum Continuation", "VWAP Reclaim",
            "Bull Flag", "Bear Flag", "First Pullback",
        ],
        "avoid_setups": [],
    },
    "power_hour": {
        "scan_interval": SCAN_INTERVAL_POWER_HOUR,  # 3 min — fast scanning
        "size_multiplier": 1.0,                       # Full size — real moves
        "min_confidence": 0.55,                       # Standard bar
        "preferred_setups": [
            "Momentum Continuation", "HOD Break", "LOD Break",
            "Volume Climax Reversal", "VWAP Reclaim",
            "Power Hour Breakout",
        ],
        "avoid_setups": [],
    },
    "close": {
        "scan_interval": 5,
        "size_multiplier": 0.0,         # No new entries
        "min_confidence": 1.0,          # Blocks all new trades
        "preferred_setups": [],
        "avoid_setups": ["all"],         # EXIT ONLY
    },
}

# ── Logging ───────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
