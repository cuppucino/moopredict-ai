import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, JSON, ForeignKey, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, session
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# Database URL adjustment for Docker/Local
raw_url = os.getenv("DATABASE_URL", "postgresql://admin:password@localhost:5432/moopredict")
db_url = raw_url.replace("localhost", "host.docker.internal") if os.path.exists("/.dockerenv") else raw_url

engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserWatchlist(Base):
    __tablename__ = "user_watchlist"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), unique=True, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)

class NewsIntel(Base):
    __tablename__ = "news_intel"
    id = Column(Integer, primary_key=True, index=True)
    headline = Column(Text, nullable=False)
    summary = Column(Text)
    source = Column(String(50))
    url = Column(Text, unique=True, nullable=False)
    scraped_at = Column(DateTime, default=datetime.utcnow)

class ManualPosition(Base):
    __tablename__ = "manual_positions"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(Text, nullable=False)
    entry_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    entry_time = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="OPEN") # OPEN | CLOSED
    exit_price = Column(Float)
    exit_time = Column(DateTime)
    pnl_pct = Column(Float)

class TradeJournal(Base):
    __tablename__ = "trade_journal"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False) # BUY | SELL
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    order_type = Column(String(20)) # MARKET | LIMIT
    status = Column(String(20), default="OPEN") # OPEN | CLOSED | STOPPED_OUT
    thesis = Column(Text)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    tags = Column(Text) # Comma-separated
    outcome = Column(String(20)) # WIN | LOSS | BREAKEVEN
    pnl_amount = Column(Float)
    pnl_percent = Column(Float)
    lessons = Column(Text)
    external_id = Column(String(50), unique=True, index=True)
    entry_time = Column(DateTime, default=datetime.utcnow)
    exit_time = Column(DateTime)
    strategy = Column(String(30)) # MOMENTUM | MEAN_REVERSION | BREAKOUT
    created_at = Column(DateTime, default=datetime.utcnow)

class PatternDB(Base):
    __tablename__ = "pattern_database"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    category = Column(String(50)) # DIVERGENCE | BREAKOUT | etc
    observation = Column(Text)
    thesis = Column(Text)
    symbols = Column(Text) # Comma-separated
    data_snapshot = Column(Text)
    confirmed = Column(Boolean, default=False)
    confirmation = Column(Text)
    lesson = Column(Text)
    times_seen = Column(Integer, default=1)
    last_seen = Column(DateTime, default=datetime.utcnow)
    source = Column(String(50)) # 'auto_lesson', 'manual', 'transcript'
    created_at = Column(DateTime, default=datetime.utcnow)

class SocialPost(Base):
    __tablename__ = "social_posts"
    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(20), nullable=False) # 'X' or 'Reddit'
    author = Column(String(100))
    content = Column(Text, nullable=False)
    post_url = Column(Text, unique=True, nullable=False)
    posted_at = Column(DateTime)
    scraped_at = Column(DateTime, default=datetime.utcnow)

class PriceAlert(Base):
    __tablename__ = "price_alerts"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    price = Column(Float, nullable=False)
    direction = Column(String(10), nullable=False) # ABOVE | BELOW
    action = Column(String(20), default="notify") # notify | sell
    status = Column(String(20), default="ACTIVE") # ACTIVE | TRIGGERED | CANCELLED
    created_at = Column(DateTime, default=datetime.utcnow)
    triggered_at = Column(DateTime)

class PCRHistory(Base):
    __tablename__ = "pcr_history"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    pcr = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

class DrawdownTracker(Base):
    __tablename__ = "drawdown_tracker"
    id = Column(Integer, primary_key=True, index=True)
    peak_value = Column(Float, nullable=False)
    current_value = Column(Float)
    drawdown_pct = Column(Float, default=0.0)
    is_blocked = Column(Boolean, default=False)
    blocked_reason = Column(Text)
    last_updated = Column(DateTime, default=datetime.utcnow)

class TrailingStop(Base):
    __tablename__ = "trailing_stops"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    entry_price = Column(Float, nullable=False)
    highest_price = Column(Float, nullable=False)
    trail_pct = Column(Float, default=5.0)
    stop_price = Column(Float, nullable=False)
    trade_id = Column(Integer, ForeignKey("trade_journal.id"))
    status = Column(String(20), default="ACTIVE") # ACTIVE | TRIGGERED | CANCELLED
    is_day_trade = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    triggered_at = Column(DateTime)

class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)  # UP | DOWN | FLAT
    confidence = Column(Float, nullable=False)  # 0-100
    catalyst = Column(Text, nullable=False)
    category = Column(String(30), default="general")  # earnings | sector | technical | fundamental
    timeframe_days = Column(Integer, default=7)
    entry_price = Column(Float)  # price at time of prediction
    target_price = Column(Float)  # optional price target
    deadline = Column(DateTime)  # when to auto-resolve
    outcome = Column(String(10))  # RIGHT | WRONG | PARTIAL | None (pending)
    actual_move_pct = Column(Float)  # actual % move at resolution
    exit_price = Column(Float)  # price at resolution
    notes = Column(Text)  # post-resolution notes / lessons
    pattern_id = Column(Integer, ForeignKey("pattern_database.id"))
    resolved_at = Column(DateTime)
    confidence_score = Column(Float)     # 0-100
    signal_summary = Column(JSON)        # Breakdown of all signals
    postmortem = Column(JSON)            # Post-resolution analysis
    created_at = Column(DateTime, default=datetime.utcnow)

class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10))         # BUY | SHORT
    quantity = Column(Float)
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)         # Mandatory
    take_profit = Column(Float)
    status = Column(String(20), default="OPEN")       # OPEN | CLOSED | STOPPED_OUT
    outcome = Column(String(20))      # WIN | LOSS | BREAKEVEN
    pnl_amount = Column(Float)
    pnl_percent = Column(Float)
    strategy = Column(String(30))     # Which signal triggered this
    window_type = Column(String(20), default="SWING")  # DAY_TRADE | SWING | SCALP
    reasoning = Column(Text)           # GLM's full reasoning
    catalyst = Column(Text)            # Short catalyst summary
    decision_id = Column(Integer)      # FK to decision_log.id
    news_context = Column(Text)        # News snapshot at entry
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

class TASnapshot(Base):
    __tablename__ = "ta_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    composite_score = Column(Float)
    rsi = Column(Float)
    vwap = Column(Float)
    poc = Column(Float)
    macd_histogram = Column(Float)
    adx = Column(Float)
    bollinger_pct_b = Column(Float)
    zscore = Column(Float)
    regime = Column(String(20))
    raw_json = Column(JSON)  # Full indicator dump
    timestamp = Column(DateTime, default=datetime.utcnow)

class SentimentSnapshot(Base):
    __tablename__ = "sentiment_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    score = Column(Float)                # -1 to +1
    label = Column(String(20))           # BULLISH | BEARISH | NEUTRAL
    news_score = Column(Float)           # News-only component
    social_score = Column(Float)         # Social-only component
    analyst_score = Column(Float)        # Analyst rating component
    data_count = Column(Integer)         # Number of data points
    raw_json = Column(JSON)              # Full breakdown
    timestamp = Column(DateTime, default=datetime.utcnow)

class OptionsSnapshot(Base):
    __tablename__ = "options_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    max_pain = Column(Float)
    total_gex = Column(Float)            # Total Gamma Exposure
    pcr = Column(Float)                  # Put/Call Ratio
    spot_price = Column(Float)
    raw_json = Column(JSON)              # Full chain breakdown
    timestamp = Column(DateTime, default=datetime.utcnow)

class TradingKnowledge(Base):
    __tablename__ = "trading_knowledge"
    id = Column(Integer, primary_key=True, index=True)
    rule = Column(Text, nullable=False)
    category = Column(String(20))       # ENTRY, EXIT, RISK, TIMING, CATALYST
    importance = Column(Integer)         # 1-10
    source = Column(String(50))          # 'transcript', 'manual', 'lesson'
    active = Column(Boolean, default=True)
    times_applied = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class DecisionLog(Base):
    __tablename__ = "decision_log"
    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(10))          # OPEN, CLOSE, HOLD, SKIP
    symbol = Column(String(20))
    side = Column(String(5))             # BUY, SHORT, null
    confidence = Column(Integer)
    catalyst = Column(Text)
    reasoning = Column(Text)
    position_size_pct = Column(Integer)
    news_context = Column(Text)
    market_state = Column(Text)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class DailyPerformance(Base):
    __tablename__ = "daily_performance"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, unique=True)
    trades_won = Column(Integer, default=0)
    trades_lost = Column(Integer, default=0)
    trades_open = Column(Integer, default=0)
    session_pnl = Column(Float, default=0)
    cumulative_pnl = Column(Float, default=0)
    capital_end = Column(Float)
    win_rate = Column(Float)
    decisions_made = Column(Integer, default=0)
    lessons_written = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class SystemState(Base):
    __tablename__ = "system_state"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(50), unique=True, nullable=False)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)

class MCPTResult(Base):
    __tablename__ = "mcpt_results"
    id = Column(Integer, primary_key=True, index=True)
    strategy = Column(String(50), index=True)
    ticker = Column(String(20), index=True)
    run_at = Column(DateTime, default=datetime.utcnow)
    insample_p = Column(Float)
    wf_p = Column(Float)
    ema_p = Column(Float)
    real_pf = Column(Float)
    status = Column(String(20)) # 'LIVE', 'WATCHLIST', 'DISABLED'

def init_db():
    """Initialize the database tables."""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("[PostgreSQL] Tables initialized successfully (SQLAlchemy)")
    except Exception as e:
        logger.error(f"[PostgreSQL] Initialization error: {e}")

def get_db():
    """Dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
