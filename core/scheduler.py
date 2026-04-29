from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from services.news_scraper import news_scraper
from services.x_scraper import x_scraper
from services.reddit_scraper import reddit_scraper
from services.daily_briefing import briefing_service

class Scheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_running = False

    def init(self):
        """Initialize and start cron jobs."""
        if self.is_running:
            return

        logger.info("[Scheduler] Initializing Python cron jobs...")

        # Scrapers
        self.scheduler.add_job(news_scraper.run, CronTrigger.from_crontab("0 * * * *"), id="news_scrape_job")
        self.scheduler.add_job(x_scraper.run, CronTrigger.from_crontab("*/15 * * * *"), id="x_scrape_job")
        self.scheduler.add_job(reddit_scraper.run, CronTrigger.from_crontab("*/30 * * * *"), id="reddit_scrape_job")

        # Briefings (Malaysia Time)
        # 10:00 AM MYT = 02:00 UTC
        self.scheduler.add_job(briefing_service.generate_morning_briefing, CronTrigger.from_crontab("0 2 * * *"), id="morning_briefing_job")
        
        # 04:00 PM MYT = 08:00 UTC
        self.scheduler.add_job(briefing_service.generate_premarket_prep, CronTrigger.from_crontab("0 8 * * *"), id="premarket_prep_job")
        
        # 04:00 AM MYT = 20:00 UTC
        self.scheduler.add_job(briefing_service.generate_eod_summary, CronTrigger.from_crontab("0 20 * * *"), id="eod_summary_job")

        # Price Alerts (Every 5 minutes)
        from services.alert_service import alert_service
        self.scheduler.add_job(alert_service.check_alerts, CronTrigger.from_crontab("*/5 * * * *"), id="price_alert_job")

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
