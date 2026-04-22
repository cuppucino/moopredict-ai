import cron, { ScheduledTask } from "node-cron";
import { eventBus } from "../core/EventBus";
import { sendTelegramMessage } from "./telegramService";
import { newsIntelAgent } from "../agents/pipeline/NewsIntelAgent";
import { socialIntelAgent } from "../agents/pipeline/SocialIntelAgent";
import { executionAgent } from "../agents/pipeline/ExecutionAgent";

export class Scheduler {
  private static tasks: ScheduledTask[] = [];

  public static init(): void {
    console.log("[Scheduler] Initializing MooPredict V4 automated agents...");

    // 1. Team A: Intelligence Layer (Every 2 hours during market windows)
    this.tasks.push(cron.schedule("0 */2 * * 1-5", async () => {
      const hour = new Date().getHours();
      const isHKWindow = (hour >= 7 && hour <= 17);
      const isUSWindow = (hour >= 20 || hour <= 5);

      if (isHKWindow || isUSWindow) {
        try {
          console.log(`[Scheduler] V4 Team A: Starting intelligence scrape (Hour: ${hour})...`);
          const newsBatch = await newsIntelAgent.scrapeBroadNews();
          const socialBatch = await socialIntelAgent.scrapeSocialIntel();

          eventBus.publish('intel:news_batch', newsBatch);
          eventBus.publish('intel:social_batch', { items: socialBatch });
        } catch (error) {
          console.error("[Scheduler] V4 Team A failed:", error);
        }
      }
    }));

    // 2. Team D: Position Monitor (Every 15 minutes during market hours)
    this.tasks.push(cron.schedule("*/15 * * * 1-5", async () => {
      try {
        await executionAgent.monitorPositions();
      } catch (error) {
        console.error("[Scheduler] V4 Team D monitor failed:", error);
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

    // 5. Market Reminders
    this.tasks.push(cron.schedule("20 9 * * 1-5", async () => {
      await sendTelegramMessage("🔔 *HK MARKET OPENING SOON* (10m)", "info");
    }));
    this.tasks.push(cron.schedule("20 21 * * 1-5", async () => {
      await sendTelegramMessage("🔔 *US MARKET OPENING SOON* (10m)", "info");
    }));

    console.log("[Scheduler] V4 agents initialized.");
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
