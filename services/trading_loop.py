import pytz
from datetime import datetime
from loguru import logger
from core.database import SessionLocal, SystemState
from services.decision_engine import decision_engine
from services.news_scraper import news_scraper

class TradingLoop:
    def __init__(self):
        self.market_open = 13.5 # 13:30 UTC
        self.market_close = 20.0 # 20:00 UTC

    def run(self):
        if not self.is_market_hours():
            logger.info("[TradingLoop] Outside market hours. Skipping cycle.")
            return
            
        if self.is_paused():
            logger.info("[TradingLoop] Autonomous trading is PAUSED. Skipping cycle.")
            return

        decision_engine.run_decision_cycle()

    def is_market_hours(self):
        now_utc = datetime.now(pytz.utc)
        if now_utc.weekday() >= 5:
            return False
            
        current_hour = now_utc.hour + now_utc.minute / 60.0
        return self.market_open <= current_hour <= self.market_close

    def is_paused(self):
        db = SessionLocal()
        try:
            state = db.query(SystemState).filter(SystemState.key == "trading_paused").first()
            if state and state.value == "true":
                return True
            return False
        finally:
            db.close()

    def pause(self):
        self._set_state("trading_paused", "true")

    def resume(self):
        self._set_state("trading_paused", "false")

    def _set_state(self, key, value):
        db = SessionLocal()
        try:
            state = db.query(SystemState).filter(SystemState.key == key).first()
            if state:
                state.value = value
                state.updated_at = datetime.utcnow()
            else:
                state = SystemState(key=key, value=value)
                db.add(state)
            db.commit()
        except Exception as e:
            logger.error(f"[TradingLoop] Failed to set state: {e}")
            db.rollback()
        finally:
            db.close()

    def run_premarket_sweep(self):
        logger.info("[TradingLoop] Running pre-market sweep...")
        try:
            news_scraper.run()
            from services.x_scraper import x_scraper
            x_scraper.run()
        except Exception as e:
            logger.error(f"[TradingLoop] Premarket sweep failed: {e}")

    def run_postmarket_analysis(self):
        logger.info("[TradingLoop] Running post-market analysis...")
        from services.lesson_writer import lesson_writer
        
        today = datetime.utcnow().date()
        lesson_writer.write_session_lessons(today)

trading_loop = TradingLoop()
