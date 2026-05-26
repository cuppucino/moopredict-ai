import json
from loguru import logger
from datetime import datetime, date
from core.database import SessionLocal, PaperTrade, PatternDB
from services.ai_service import ai_service

class LessonWriter:
    def write_lesson(self, trade_id: int):
        db = SessionLocal()
        try:
            trade = db.query(PaperTrade).filter(PaperTrade.id == trade_id).first()
            if not trade:
                logger.error(f"[LessonWriter] Trade #{trade_id} not found.")
                return

            logger.info(f"[LessonWriter] Writing lesson for Trade #{trade_id}...")

            prompt = f"""
Analyze the following paper trade and extract a single, actionable lesson or rule.
Trade Details:
Symbol: {trade.symbol}
Side: {trade.side}
Outcome: {trade.outcome} (P&L: {trade.pnl_percent}%)
Entry Reasoning: {trade.reasoning}
Catalyst: {trade.catalyst}
News Context: {trade.news_context}

Return a JSON object:
{{
  "lesson": "One sentence actionable rule.",
  "category": "ENTRY|EXIT|RISK|TIMING|CATALYST",
  "applies_to": "Specific market condition or general",
  "confidence": 0-100
}}
"""
            raw_json = ai_service.query_lesson(prompt)
            if "error" in raw_json:
                logger.error(f"[LessonWriter] AI failed: {raw_json['error']}")
                return

            lesson_text = raw_json.get("lesson")
            if not lesson_text:
                return

            pattern = PatternDB(
                name=f"Trade #{trade_id} Lesson",
                category=raw_json.get("category", "GENERAL"),
                observation=f"Outcome: {trade.outcome}, P&L: {trade.pnl_percent}%",
                thesis=trade.reasoning,
                symbols=trade.symbol,
                confirmed=True,
                lesson=lesson_text,
                source='auto_lesson'
            )
            db.add(pattern)
            db.commit()
            logger.info(f"[LessonWriter] Successfully recorded lesson for Trade #{trade_id}.")
        except Exception as e:
            logger.error(f"[LessonWriter] Error writing lesson: {e}")
            db.rollback()
        finally:
            db.close()

    def write_session_lessons(self, session_date: date):
        db = SessionLocal()
        try:
            # Note: simplified date comparison for SQLite/Postgres compatibility
            date_start = datetime(session_date.year, session_date.month, session_date.day)
            trades = db.query(PaperTrade).filter(
                PaperTrade.status != "OPEN",
                PaperTrade.closed_at >= date_start
            ).all()
            
            trade_ids = [t.id for t in trades]
        finally:
            db.close()
            
        for tid in trade_ids:
            self.write_lesson(tid)

lesson_writer = LessonWriter()
