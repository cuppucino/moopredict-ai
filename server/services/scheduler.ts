import cron, { ScheduledTask } from "node-cron";
import { eventBus } from "../core/EventBus";
import { sendTelegramMessage } from "./telegramService";
import { newsIntelAgent } from "../agents/pipeline/NewsIntelAgent";
import { socialIntelAgent } from "../agents/pipeline/SocialIntelAgent";
import { executionAgent } from "../agents/pipeline/ExecutionAgent";
import { logger } from "../core/Logger";

export class Scheduler {
  private static tasks: ScheduledTask[] = [];

  public static init(): void {
    logger.info("[Scheduler] Initializing MooPredict V4 automated agents...");

    // 1. Team A: Intelligence Layer (24/7 Session-Aware Scrape)
    this.tasks.push(cron.schedule("0 * * * *", async () => {
      const now = new Date();
      const hour = now.getHours();
      const day = now.getDay(); // 0=Sun, 1=Mon, ..., 6=Sat
      const isWeekend = (day === 0 || day === 6);

      // Full US session: pre-market (7am ET) through post-market (8pm ET)
      // = 19:00 HKT through 08:00 HKT next day
      const isUSSessionWindow = (hour >= 19 || hour <= 8);

      // Weekends: run lighter — only at 09:00, 15:00, 21:00 HKT
      const isWeekendSlot = isWeekend && (hour === 9 || hour === 15 || hour === 21);

      if (isUSSessionWindow || isWeekendSlot) {
        try {
          const context = isWeekend ? 'weekend' : 'standard';
          logger.info(`[Scheduler] V4 Team A: Starting ${context} intelligence scrape (Hour: ${hour})...`);
          const newsBatch = await newsIntelAgent.scrapeBroadNews(isWeekend ? 'weekend' : 'standard');
          const socialBatch = await socialIntelAgent.scrapeSocialIntel();

          eventBus.publish('intel:news_batch', newsBatch);
          eventBus.publish('intel:social_batch', { items: socialBatch });
        } catch (error) {
          logger.error("[Scheduler] V4 Team A failed:", { error });
        }
      }
    }));

    // 2. Targeted Intelligence (Watchlist Sync - Every 30 mins)
    this.tasks.push(cron.schedule("*/30 * * * *", async () => {
        try {
          logger.info("[Scheduler] Starting targeted watchlist news scrape...");
          const watchlistBatch = await newsIntelAgent.scrapeWatchlistNews();
          if (watchlistBatch.headlines.length > 0) {
            eventBus.publish('intel:news_batch', watchlistBatch);

            // On weekends, send a direct Telegram digest since the pipeline won't fire (market gated)
            const day = new Date().getDay();
            const isWeekend = (day === 0 || day === 6);
            if (isWeekend) {
              const lines = watchlistBatch.headlines.slice(0, 8).map((h: string) => `• ${h}`).join('\n');
              const more = watchlistBatch.headlines.length > 8 ? `\n_...and ${watchlistBatch.headlines.length - 8} more_` : '';
              await sendTelegramMessage(`👀 *Watchlist News (Weekend):*\n\n${lines}${more}`, 'info');
            }
          }
        } catch (error) {
          logger.error("[Scheduler] Targeted scrape failed:", { error });
        }
    }));

    // 3. Team D: Position Monitor (Every 15 minutes during market hours)
    this.tasks.push(cron.schedule("*/15 * * * 1-5", async () => {
      try {
        await executionAgent.monitorPositions();
      } catch (error) {
        logger.error("[Scheduler] V4 Team D monitor failed:", { error });
      }
    }));

    // 3. Team E: Daily Report (17:05 HKT Mon-Fri)
    this.tasks.push(cron.schedule("5 17 * * 1-5", async () => {
      eventBus.publish('system:daily_report', {});
    }));

    // 4. Team E: Weekly Report (08:00 Sat)
    this.tasks.push(cron.schedule("0 8 * * 6", async () => {
      eventBus.publish('system:weekly_report', {});
    }));

    // NEW: Post-Market Digest (08:00 HKT Mon-Fri = 4pm ET US Close)
    this.tasks.push(cron.schedule("0 8 * * 1-5", async () => {
      logger.info("[Scheduler] Post-market digest triggered (US market closed)");
      try {
        const newsBatch = await newsIntelAgent.scrapeBroadNews('post-market');
        eventBus.publish('intel:news_batch', newsBatch);
      } catch (error) {
        logger.error("[Scheduler] Post-market digest failed:", { error });
      }
    }));

    // NEW: Weekend Morning Digest (09:00 HKT Sat & Sun)
    this.tasks.push(cron.schedule("0 9 * * 0,6", async () => {
      logger.info("[Scheduler] Weekend digest triggered");
      try {
        const newsBatch = await newsIntelAgent.scrapeBroadNews('weekend');
        if (newsBatch.headlines.length === 0) {
          await sendTelegramMessage("🗞️ *Weekend Brief:* No new major headlines since last check. Markets are quiet.", "info");
        } else {
          eventBus.publish('intel:news_batch', newsBatch);
        }
      } catch (error) {
        logger.error("[Scheduler] Weekend digest failed:", { error });
      }
    }));

    // 5. Market Reminders & Intelligence Summaries

    // US Opening (21:15 HKT)
    this.tasks.push(cron.schedule("15 21 * * 1-5", async () => {
      await sendTelegramMessage("🌃 *US NIGHT INTEL REPORT*\n━━━━━━━━━━━━━━━━━━━━━━\n_Analyzing pre-market social and news..._", "info");
      // Trigger a fresh scrape for the opening
      const newsBatch = await newsIntelAgent.scrapeBroadNews();
      eventBus.publish('intel:news_batch', newsBatch);
    }));

    this.tasks.push(cron.schedule("20 21 * * 1-5", async () => {
      await sendTelegramMessage("🔔 *US MARKET OPENING SOON* (10m)", "info");
    }));

    // 6. ETF Intelligence Module

    // US Pre-market (20:00 HKT)
    this.tasks.push(cron.schedule("0 20 * * 1-5", async () => {
      logger.info("[Scheduler] Triggering ETF US Pre-market scan...");
      eventBus.publish('etf:force_scan', {});
    }));

    // Intraday ETF Checks (10:00, 14:00, 22:00, 1 * * 1-5)
    this.tasks.push(cron.schedule("0 10,14,22,1 * * 1-5", async () => {
      logger.info("[Scheduler] Triggering ETF Intraday scan...");
      eventBus.publish('etf:force_scan', {});
    }));

    // ETF Morning Briefings (20:30 HKT for US)
    this.tasks.push(cron.schedule("30 20 * * 1-5", async () => {
      logger.info("[Scheduler] Triggering ETF US Morning Briefing...");
      eventBus.publish('etf:morning_briefing', {});
    }));

    logger.info("[Scheduler] V4 agents initialized.");
  }

  public static stop(): void {
    this.tasks.forEach(task => task.stop());
    this.tasks = [];
  }

  public static getStatus(): any {
    return { active: this.tasks.length };
  }
}

export const scheduler = Scheduler;
