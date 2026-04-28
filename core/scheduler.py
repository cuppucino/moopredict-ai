from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from services.news_scraper import news_scraper
from services.x_scraper import x_scraper
from services.reddit_scraper import reddit_scraper

class Scheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_running = False

    def init(self):
        """Initialize and start cron jobs."""
        if self.is_running:
            return

        logger.info("[Scheduler] Initializing Python cron jobs...")

        # News: every 60 minutes
        self.scheduler.add_job(
            news_scraper.run,
            CronTrigger.from_crontab("0 * * * *"),
            id="news_scrape_job"
        )

        # X: every 15 minutes
        self.scheduler.add_job(
            x_scraper.run,
            CronTrigger.from_crontab("*/15 * * * *"),
            id="x_scrape_job"
        )

        # Reddit: every 30 minutes
        self.scheduler.add_job(
            reddit_scraper.run,
            CronTrigger.from_crontab("*/30 * * * *"),
            id="reddit_scrape_job"
        )

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
