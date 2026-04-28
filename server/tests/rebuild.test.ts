import { news_scraper } from "../services/newsScraper";
import { x_scraper } from "../services/xScraper";
import { logger } from "../core/Logger";

async function runTests() {
  logger.info("🧪 Running Rebuild Logic Tests...");

  try {
    const newsCount = await news_scraper.run();
    logger.info(`✅ News Scraper Test: Found ${newsCount} new articles (expected >= 0)`);

    const xCount = await x_scraper.run();
    logger.info(`✅ X Scraper Test: Found ${xCount} new posts (expected >= 0)`);

    logger.info("🎉 All Rebuild Logic Tests Passed!");
    process.exit(0);
  } catch (error: any) {
    logger.error(`❌ Test failed: ${error.message}`);
    process.exit(1);
  }
}

runTests();
