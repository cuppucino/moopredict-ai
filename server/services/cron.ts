import cron from "node-cron";
import { news_scraper } from "./newsScraper.js";
import { x_scraper } from "./xScraper.js";
import { logger } from "../core/Logger.js";

export const scheduler = {
  init() {
    logger.info("[Scheduler] Initializing simplified cron jobs...");

    // News: every 60 minutes
    cron.schedule("0 * * * *", async () => {
      try {
        await news_scraper.run();
      } catch (e: any) {
        logger.error(`[Scheduler] News scrape job failed: ${e.message}`);
      }
    });

    // X: every 15 minutes
    cron.schedule("*/15 * * * *", async () => {
      try {
        await x_scraper.run();
      } catch (e: any) {
        logger.error(`[Scheduler] X scrape job failed: ${e.message}`);
      }
    });

    logger.info("[Scheduler] Jobs scheduled.");
  }
};
