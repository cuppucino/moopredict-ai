import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, JSON, ForeignKey
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
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

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
