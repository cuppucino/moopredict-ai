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

    // 1. Team A: Intelligence Layer (Every hour during market windows)
    this.tasks.push(cron.schedule("0 * * * 1-5", async () => {
      const hour = new Date().getHours();
      // HK: 07:00 - 17:00, US: 20:00 - 05:00
      const isHKWindow = (hour >= 7 && hour <= 17);
      const isUSWindow = (hour >= 20 || hour <= 5);

      if (isHKWindow || isUSWindow) {
        try {
          logger.info(`[Scheduler] V4 Team A: Starting intelligence scrape (Hour: ${hour})...`);
          const newsBatch = await newsIntelAgent.scrapeBroadNews();
          const socialBatch = await socialIntelAgent.scrapeSocialIntel();

          eventBus.publish('intel:news_batch', newsBatch);
          eventBus.publish('intel:social_batch', { items: socialBatch });
        } catch (error) {
          logger.error("[Scheduler] V4 Team A failed:", { error });
        }
      }
    }));

    // 2. Team D: Position Monitor (Every 15 minutes during market hours)
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

    // 5. Market Reminders & Intelligence Summaries
    // HK Opening (09:15 HKT)
    this.tasks.push(cron.schedule("15 9 * * 1-5", async () => {
      await sendTelegramMessage("🌅 *HK MORNING INTEL REPORT*\n━━━━━━━━━━━━━━━━━━━━━━\n_Checking watchlist and focus symbols..._", "info");
      // Trigger a fresh scrape for the opening
      const newsBatch = await newsIntelAgent.scrapeBroadNews();
      eventBus.publish('intel:news_batch', newsBatch);
    }));

    // US Opening (21:15 HKT)
    this.tasks.push(cron.schedule("15 21 * * 1-5", async () => {
      await sendTelegramMessage("🌃 *US NIGHT INTEL REPORT*\n━━━━━━━━━━━━━━━━━━━━━━\n_Analyzing pre-market social and news..._", "info");
      // Trigger a fresh scrape for the opening
      const newsBatch = await newsIntelAgent.scrapeBroadNews();
      eventBus.publish('intel:news_batch', newsBatch);
    }));

    this.tasks.push(cron.schedule("20 9 * * 1-5", async () => {
      await sendTelegramMessage("🔔 *HK MARKET OPENING SOON* (10m)", "info");
    }));
    this.tasks.push(cron.schedule("20 21 * * 1-5", async () => {
      await sendTelegramMessage("🔔 *US MARKET OPENING SOON* (10m)", "info");
    }));

    // 6. ETF Intelligence Module
    // HK Pre-market (08:00 HKT)
    this.tasks.push(cron.schedule("0 8 * * 1-5", async () => {
      logger.info("[Scheduler] Triggering ETF HK Pre-market scan...");
      eventBus.publish('etf:force_scan', {});
    }));

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

    // ETF Morning Briefings (08:30 & 20:30 HKT)
    this.tasks.push(cron.schedule("30 8,20 * * 1-5", async () => {
      logger.info("[Scheduler] Triggering ETF Morning Briefing...");
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
