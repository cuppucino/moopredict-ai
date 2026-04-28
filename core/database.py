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

class SocialPost(Base):
    __tablename__ = "social_posts"
    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(20), nullable=False) # 'X' or 'Reddit'
    author = Column(String(100))
    content = Column(Text, nullable=False)
    post_url = Column(Text, unique=True, nullable=False)
    posted_at = Column(DateTime)
    scraped_at = Column(DateTime, default=datetime.utcnow)

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
