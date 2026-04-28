import Parser from "rss-parser";
import { query } from "../db/postgres.js";
import { logger } from "../core/Logger.js";
import { send_openclaw_notification } from "./openclaw_service.js";

const parser = new Parser();

const ACCOUNTS = [
  "elonmusk",
  "realDonaldTrump",
  "federalreserve",
  "GaryGensler",
  "SECGov"
];

// Fallback instances if one is down
const NITTER_INSTANCES = [
  "https://nitter.net",
  "https://nitter.it",
  "https://nitter.cz",
  "https://nitter.at"
];

export const x_scraper = {
  async run() {
    logger.info("[XScraper] Starting 15-min check...");
    let new_count = 0;

    for (const account of ACCOUNTS) {
      let success = false;
      for (const instance of NITTER_INSTANCES) {
        try {
          const feed_url = `${instance}/${account}/rss`;
          const feed_data = await parser.parseURL(feed_url);
          
          for (const item of feed_data.items.slice(0, 5)) {
            const content = item.title || item.contentSnippet || "";
            const post_url = item.link || "";
            const posted_at = item.pubDate ? new Date(item.pubDate) : new Date();

            if (!content || !post_url) continue;

            // Check if exists
            const existing = await query("SELECT id FROM x_posts WHERE post_url = $1", [post_url]);
            if (existing.rows.length === 0) {
              // Save to DB
              await query(
                "INSERT INTO x_posts (author, content, post_url, posted_at) VALUES ($1, $2, $3, $4)",
                [account, content, post_url, posted_at]
              );

              // Forward to OpenClaw immediately
              const timestamp = new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
              const message = 
                `───────────────────────────\n` +
                `🐦 @${account} — ${timestamp}\n` +
                `───────────────────────────\n` +
                `"${content}"`;

              await send_openclaw_notification({
                message,
                level: "info"
              });
              new_count++;
            }
          }
          success = true;
          break; // Stop if instance worked
        } catch (error: any) {
          // Silent fail to try next instance
        }
      }
      if (!success) {
        logger.warn(`[XScraper] Failed to fetch @${account} from all Nitter instances.`);
      }
    }

    if (new_count > 0) {
      logger.info(`[XScraper] Scrape complete. Found ${new_count} new posts.`);
    } else {
      logger.info("[XScraper] No new posts found.");
    }

    return new_count;
  }
};
