import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from services.news_scraper import news_scraper
from services.x_scraper import x_scraper
from services.reddit_scraper import reddit_scraper
from services.daily_briefing import briefing_service
from services.notifications import notification_queue
from services.options_flow import options_service
from services.trailing_stop_service import trailing_stop_service
from services.strategy_service import strategy_service
from core.database import SessionLocal, UserWatchlist
from services.heartbeat_monitor import heartbeat_monitor
from services.macro_calendar_service import macro_service
from services.ml_service import ml_service
from services.pattern_analyzer import pattern_analyzer
from services.watchdog import watchdog_service

class Scheduler:
    def __init__(self):
        # Force UTC to avoid local timezone (e.g. MYT) offsets
        self.scheduler = BackgroundScheduler(timezone=pytz.utc)
        self.is_running = False

    def init(self):
        """Initialize and start cron jobs."""
        if self.is_running:
            return

        logger.info("[Scheduler] Initializing Python cron jobs...")

        # Scrapers
        self.scheduler.add_job(news_scraper.run, CronTrigger.from_crontab("0 * * * *", timezone=pytz.utc), id="news_scrape_job")
        self.scheduler.add_job(x_scraper.run, CronTrigger.from_crontab("*/15 * * * *", timezone=pytz.utc), id="x_scrape_job")
        self.scheduler.add_job(reddit_scraper.run, CronTrigger.from_crontab("*/30 * * * *", timezone=pytz.utc), id="reddit_scrape_job")

        # Briefings (UTC)
        # 02:00 UTC = 10:00 AM MYT
        self.scheduler.add_job(briefing_service.generate_morning_briefing, CronTrigger.from_crontab("0 2 * * *", timezone=pytz.utc), id="morning_briefing_job")
        
        # 13:00 UTC = 09:00 PM MYT (Pre-market prep)
        self.scheduler.add_job(briefing_service.generate_premarket_prep, CronTrigger.from_crontab("0 13 * * *", timezone=pytz.utc), id="premarket_prep_job")
        
        # Market Reminders (UTC)
        # 13:00 UTC = 9:00 PM MYT
        self.scheduler.add_job(lambda: notification_queue.enqueue("🚨 *US Market opens in 30 minutes!* ⏳", "alert", category="news"), CronTrigger.from_crontab("0 13 * * 1-5", timezone=pytz.utc), id="market_open_soon_job")
        
        # 13:30 UTC = 9:30 PM MYT
        self.scheduler.add_job(lambda: notification_queue.enqueue("🔔 *US Market is OPEN!* 📈", "info", category="news"), CronTrigger.from_crontab("30 13 * * 1-5", timezone=pytz.utc), id="market_open_job")
        
        # 20:55 UTC = 04:55 AM MYT (5 mins before close)
        self.scheduler.add_job(lambda: notification_queue.enqueue("🔔 *US Market is closing in 5 minutes!* 📉", "warning", category="news"), CronTrigger.from_crontab("55 20 * * 1-5", timezone=pytz.utc), id="market_close_soon_job")

        # 21:00 UTC = 05:00 AM MYT
        self.scheduler.add_job(briefing_service.generate_eod_summary, CronTrigger.from_crontab("0 21 * * *", timezone=pytz.utc), id="eod_summary_job")

        # Price Alerts (Every 5 minutes)
        from services.alert_service import alert_service
        self.scheduler.add_job(alert_service.check_alerts, CronTrigger.from_crontab("*/5 * * * *", timezone=pytz.utc), id="price_alert_job")

        # Trailing Stops (Every 5 minutes during market hours)
        self.scheduler.add_job(trailing_stop_service.update_stops, CronTrigger.from_crontab("*/5 13-21 * * 1-5", timezone=pytz.utc), id="trailing_stop_job")

        # TA Snapshots (Every hour during market hours)
        from services.ta_engine import ta_engine
        def ta_snapshot_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                for sym in symbols:
                    ta_engine.save_snapshot(sym)
            finally:
                db.close()
        
        self.scheduler.add_job(ta_snapshot_job, CronTrigger.from_crontab("0 13-21 * * 1-5", timezone=pytz.utc), id="ta_snapshot_job")

        # Sentiment Snapshots (Every hour during market hours)
        from services.sentiment_engine import sentiment_engine
        def sentiment_snapshot_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                for sym in symbols:
                    sentiment_engine.save_snapshot(sym)
            finally:
                db.close()
        
        self.scheduler.add_job(sentiment_snapshot_job, CronTrigger.from_crontab("0 13-21 * * 1-5", timezone=pytz.utc), id="sentiment_snapshot_job")

        # Options Snapshots (Every 4 hours during market hours - options data is heavy)
        from services.options_engine import options_engine
        def options_snapshot_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                for sym in symbols:
                    options_engine.save_snapshot(sym)
            finally:
                db.close()
        
        self.scheduler.add_job(options_snapshot_job, CronTrigger.from_crontab("0 14,18,22 * * 1-5", timezone=pytz.utc), id="options_snapshot_job")

        # PCR Monitor (Every hour)
        def pcr_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                if symbols:
                    # options_service.monitor_pcr_spikes(symbols)
                    logger.warning("[Scheduler] monitor_pcr_spikes not implemented. Skipping.")
            finally:
                db.close()
        
        self.scheduler.add_job(pcr_job, CronTrigger.from_crontab("0 * * * *", timezone=pytz.utc), id="pcr_spike_job")

        # Prediction Auto-Resolve (Every 6 hours)
        from services.prediction_service import prediction_service
        self.scheduler.add_job(prediction_service.resolve_pending_predictions, CronTrigger.from_crontab("0 */6 * * *", timezone=pytz.utc), id="prediction_resolve_job")

        # Weekly Outlook (Sunday 02:00 UTC = 10:00 AM MYT)
        from services.outlook_service import outlook_service
        self.scheduler.add_job(outlook_service.generate_weekly_outlook, CronTrigger.from_crontab("0 2 * * 0", timezone=pytz.utc), id="weekly_outlook_job")

        # Strategy Evolution (Sunday 03:00 UTC = 11:00 AM MYT)
        self.scheduler.add_job(strategy_service.evolve, CronTrigger.from_crontab("0 3 * * 0", timezone=pytz.utc), id="strategy_evolution_job")

        # --- Heartbeat Monitoring ---
        # 1. Full Check every 15 min during market hours (13:30-21:00 UTC)
        self.scheduler.add_job(heartbeat_monitor.run_full_check, CronTrigger.from_crontab("*/15 13-21 * * 1-5", timezone=pytz.utc), id="heartbeat_check_job")
        
        # 2. Daily P&L Report (21:00 UTC)
        self.scheduler.add_job(heartbeat_monitor.generate_daily_report, CronTrigger.from_crontab("0 21 * * 1-5", timezone=pytz.utc), id="heartbeat_daily_report")
        
        # 3. Pre-market Scan (13:00 UTC)
        self.scheduler.add_job(heartbeat_monitor.generate_premarket_scan, CronTrigger.from_crontab("0 13 * * 1-5", timezone=pytz.utc), id="heartbeat_premarket_scan")

        # 4. Off-hours Heartbeat (Every hour)
        self.scheduler.add_job(heartbeat_monitor.run_full_check, CronTrigger.from_crontab("0 * * * *", timezone=pytz.utc), id="heartbeat_offhours_job")

        # --- Maintenance & Intelligence Jobs ---
        
        # 1. Daily Accuracy Report (22:00 UTC)
        from core.database import Prediction
        from datetime import timedelta
        def accuracy_report_job():
            db = SessionLocal()
            try:
                from services.prediction_service import prediction_service
                # Resolve pending first for current data
                prediction_service.resolve_pending_predictions()
                
                yesterday = datetime.utcnow() - timedelta(days=1)
                results = db.query(Prediction).filter(Prediction.resolved_at >= yesterday).all()
                
                if not results:
                    return
                
                correct = len([r for r in results if r.outcome == 'RIGHT'])
                total = len(results)
                win_rate = (correct / total * 100) if total > 0 else 0
                
                msg = (
                    f"📊 *Daily Accuracy Report*\n"
                    f"───────────────────────────\n"
                    f"Total Predictions: {total}\n"
                    f"✅ Correct: {correct}\n"
                    f"🎯 Win Rate: {win_rate:.1f}%"
                )
                notification_queue.enqueue(msg, level="info", category="general")
            finally:
                db.close()
        
        from datetime import datetime
        self.scheduler.add_job(accuracy_report_job, CronTrigger.from_crontab("0 22 * * *", timezone=pytz.utc), id="accuracy_report_job")

        # 2. Weekly Macro Calendar (Sunday 04:00 UTC)
        def macro_calendar_job():
            briefing = macro_service.generate_weekly_briefing()
            notification_queue.enqueue(briefing, level="info", category="news")
            
        self.scheduler.add_job(macro_calendar_job, CronTrigger.from_crontab("0 4 * * 0", timezone=pytz.utc), id="macro_calendar_job")

        # 3. Weekly ML Retrain (Sunday 05:00 UTC)
        def model_retrain_job():
            results = ml_service.retrain_all()
            msg = (
                f"🤖 *Weekly ML Retrain*\n"
                f"───────────────────────────\n"
                f"• LSTM: {results.get('lstm')}\n"
                f"• Random Forest: {results.get('random_forest')}\n"
                f"• Data points: {results.get('data_points')}\n"
                f"⏱️ Duration: {results.get('duration_sec')}s"
            )
            notification_queue.enqueue(msg, level="info", category="system")

        self.scheduler.add_job(model_retrain_job, CronTrigger.from_crontab("0 5 * * 0", timezone=pytz.utc), id="model_retrain_job")

        # 4. Weekly Pattern Review (Sunday 06:00 UTC)
        def pattern_review_job():
            analysis = pattern_analyzer.analyze_effectiveness()
            if "error" in analysis: return
            
            msg = (
                f"📈 *Weekly Pattern Review*\n"
                f"───────────────────────────\n"
                f"Total Reviewed: {analysis.get('total')}\n"
                f"✅ Effective: {analysis.get('effective')}\n"
                f"⚠️ Needs Review: {analysis.get('needs_review')}\n"
            )
            notification_queue.enqueue(msg, level="info", category="general")

        self.scheduler.add_job(pattern_review_job, CronTrigger.from_crontab("0 6 * * 0", timezone=pytz.utc), id="pattern_review_job")

        self.scheduler.start()
        watchdog_service.start()
        self.is_running = True
        logger.info("[Scheduler] Jobs scheduled and running.")

    def shutdown(self):
        """Gracefully shutdown the scheduler."""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("[Scheduler] Shutdown complete.")

scheduler = Scheduler()
