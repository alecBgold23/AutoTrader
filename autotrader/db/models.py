"""SQLAlchemy models for trade logging and portfolio tracking."""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, JSON,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from autotrader.config import DB_PATH


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    side = Column(String(4), nullable=False)  # BUY or SELL
    quantity = Column(Float, nullable=False)
    order_type = Column(String(20), default="market")
    limit_price = Column(Float, nullable=True)
    filled_price = Column(Float, nullable=True)
    status = Column(String(20), default="pending")
    alpaca_order_id = Column(String(64), nullable=True, unique=True)

    # Claude's analysis
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    pattern = Column(String(50), nullable=True)   # Pattern that triggered the trade

    # Performance tracking
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)            # Realized P&L
    r_multiple = Column(Float, nullable=True)      # How many R was gained/lost
    hold_time_minutes = Column(Integer, nullable=True)

    # Scanner context
    scanner_score = Column(Float, nullable=True)
    scanner_flags = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    filled_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Trade {self.side} {self.quantity} {self.symbol} @ {self.filled_price or 'pending'}>"


class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    action = Column(String(4), nullable=False)
    confidence = Column(Float, nullable=False)
    reasoning = Column(Text, nullable=True)
    pattern = Column(String(50), nullable=True)
    indicators = Column(JSON, nullable=True)
    news_summary = Column(Text, nullable=True)
    executed = Column(Boolean, default=False)
    blocked_reason = Column(String(200), nullable=True)

    # Context
    market_phase = Column(String(20), nullable=True)  # open, midday, power_hour
    scanner_score = Column(Float, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Decision {self.action} {self.symbol} conf={self.confidence:.0%}>"


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    total_equity = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    buying_power = Column(Float, nullable=False)
    positions = Column(JSON, nullable=True)
    daily_pnl = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)

    # Day trading stats
    trades_today = Column(Integer, default=0)
    wins_today = Column(Integer, default=0)
    losses_today = Column(Integer, default=0)
    total_r_today = Column(Float, default=0.0)  # Total R earned today

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False)
    details = Column(Text, nullable=True)
    action_taken = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TradingJournal(Base):
    """Daily trading journal — tracks performance and lessons.

    Every successful day trader reviews their day. This table
    stores daily summaries for pattern analysis over time.
    """
    __tablename__ = "trading_journal"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, unique=True, index=True)  # YYYY-MM-DD

    # Performance
    total_trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    total_r = Column(Float, default=0.0)
    largest_win = Column(Float, default=0.0)
    largest_loss = Column(Float, default=0.0)
    avg_winner = Column(Float, default=0.0)
    avg_loser = Column(Float, default=0.0)

    # Pattern performance
    best_pattern = Column(String(50), nullable=True)
    worst_pattern = Column(String(50), nullable=True)
    patterns_traded = Column(JSON, nullable=True)  # {pattern: {wins, losses, pnl}}

    # Market context
    spy_change = Column(Float, nullable=True)
    market_mood = Column(String(50), nullable=True)
    universe_size = Column(Integer, nullable=True)
    hot_list_size = Column(Integer, nullable=True)

    # Portfolio
    starting_equity = Column(Float, nullable=True)
    ending_equity = Column(Float, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── Database setup ─────────────────────────────────────

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Create all tables."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_session():
    """Get a database session."""
    return SessionLocal()
