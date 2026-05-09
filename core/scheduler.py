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

        # PCR Monitor (Every hour)
        def pcr_job():
            db = SessionLocal()
            try:
                symbols = [s.symbol for s in db.query(UserWatchlist).all()]
                if symbols:
                    options_service.monitor_pcr_spikes(symbols)
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

        self.scheduler.start()
        self.is_running = True
        logger.info("[Scheduler] Jobs scheduled and running.")

    def shutdown(self):
        """Gracefully shutdown the scheduler."""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("[Scheduler] Shutdown complete.")

scheduler = Scheduler()
