from datetime import datetime
from typing import List, Dict, Optional
from loguru import logger
from core.database import SessionLocal, PatternDB

class PatternService:
    def record_pattern(self, name: str, category: str, observation: str, thesis: str, symbols: str = "") -> int:
        """Record a new market pattern observation."""
        db = SessionLocal()
        try:
            pattern = PatternDB(
                name=name,
                category=category,
                observation=observation,
                thesis=thesis,
                symbols=symbols,
                created_at=datetime.utcnow(),
                last_seen=datetime.utcnow()
            )
            db.add(pattern)
            db.commit()
            db.refresh(pattern)
            logger.info(f"[Pattern] Recorded new pattern #{pattern.id}: {name}")
            return pattern.id
        except Exception as e:
            logger.error(f"[Pattern] Error recording pattern: {e}")
            db.rollback()
            return -1
        finally:
            db.close()

    def confirm_pattern(self, pattern_id: int, confirmation: str) -> bool:
        db = SessionLocal()
        try:
            pattern = db.query(PatternDB).filter(PatternDB.id == pattern_id).first()
            if pattern:
                pattern.confirmed = True
                pattern.confirmation = confirmation
                db.commit()
                return True
            return False
        finally:
            db.close()

    def get_confirmed_lessons(self, symbol: Optional[str] = None) -> str:
        """Fetch all confirmed patterns/lessons as a formatted string for AI context."""
        db = SessionLocal()
        try:
            query = db.query(PatternDB).filter(PatternDB.confirmed == True)
            if symbol:
                query = query.filter(PatternDB.symbols.contains(symbol))
            
            patterns = query.order_by(PatternDB.last_seen.desc()).limit(10).all()
            if not patterns:
                return ""
            
            context = "\n### RELEVANT LESSONS & PATTERNS TO FOLLOW:\n"
            for p in patterns:
                context += f"- [{p.category}] {p.name}: {p.observation} -> {p.thesis}\n"
            return context
        finally:
            db.close()

    def get_all(self, limit: int = 20) -> List[Dict]:
        db = SessionLocal()
        try:
            patterns = db.query(PatternDB).order_by(PatternDB.last_seen.desc()).limit(limit).all()
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "category": p.category,
                    "observation": p.observation,
                    "confirmed": p.confirmed,
                    "times_seen": p.times_seen
                } for p in patterns
            ]
        finally:
            db.close()

pattern_service = PatternService()
