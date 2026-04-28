import Parser from "rss-parser";
import { query } from "../db/postgres.js";
import { logger } from "../core/Logger.js";
import { send_openclaw_notification } from "./openclaw_service.js";

const parser = new Parser();

const FEEDS = [
  { name: "BBC", url: "http://feeds.bbci.co.uk/news/business/rss.xml" },
  { name: "Reuters", url: "https://feeds.reuters.com/reuters/businessNews" },
  { name: "CNBC", url: "https://www.cnbc.com/id/100003114/device/rss/rss.html" },
  { name: "MarketWatch", url: "https://www.marketwatch.com/rss/topstories" },
  { name: "Google News", url: "https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en" },
];

export const news_scraper = {
  async run() {
    logger.info("[NewsScraper] Starting hourly scrape...");
    const all_headlines: string[] = [];
    let new_count = 0;

    for (const feed of FEEDS) {
      try {
        const feed_data = await parser.parseURL(feed.url);
        const items = feed_data.items.slice(0, 10);

        for (const item of items) {
          const headline = item.title || "";
          const url = item.link || "";
          const summary = item.contentSnippet || item.content || "";

          if (!headline || !url) continue;

          // Check if exists
          const existing = await query("SELECT id FROM news_intel WHERE url = $1", [url]);
          if (existing.rows.length === 0) {
            // Save to DB
            await query(
              "INSERT INTO news_intel (headline, summary, source, url) VALUES ($1, $2, $3, $4)",
              [headline, summary, feed.name, url]
            );
            all_headlines.push(`• [${feed.name}] ${headline}`);
            new_count++;
          }
        }
      } catch (error: any) {
        logger.error(`[NewsScraper] Failed to fetch ${feed.name}: ${error.message}`);
      }
    }

    if (all_headlines.length > 0) {
      const timestamp = new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
      const batch_message = 
        `───────────────────────────\n` +
        `📰 NEWS BATCH — ${timestamp}\n` +
        `───────────────────────────\n` +
        all_headlines.slice(0, 20).join("\n");

      await send_openclaw_notification({
        message: batch_message,
        level: "info"
      });
      logger.info(`[NewsScraper] Scrape complete. Sent ${all_headlines.length} new headlines.`);
    } else {
      logger.info("[NewsScraper] No new articles found.");
    }

    return new_count;
  }
};
