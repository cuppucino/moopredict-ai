import "./server/agents/pipeline/NewsIntelAgent.js";
import "./server/agents/pipeline/SocialIntelAgent.js";
import "./server/agents/pipeline/EventAnalystAgent.js";
import "./server/agents/pipeline/StrategyAgent.js";
import "./server/agents/pipeline/ExecutionAgent.js";
import "./server/agents/pipeline/IntelligenceReportAgent.js";
import { newsIntelAgent } from "./server/agents/pipeline/NewsIntelAgent.js";
import { socialIntelAgent } from "./server/agents/pipeline/SocialIntelAgent.js";
import { eventBus } from "./server/core/EventBus.js";
import { init_db } from "./server/db/database.js";

async function force() {
  console.log("🚀 FORCING INTEL SCRAPE...");
  await init_db();
  
  const news = await newsIntelAgent.scrapeBroadNews();
  const social = await socialIntelAgent.scrapeSocialIntel();
  
  console.log("📤 Publishing events to EventBus...");
  eventBus.publish('intel:news_batch', news);
  eventBus.publish('intel:social_batch', { items: social });
  
  console.log("⏳ Waiting 120s for Ollama analysis and Telegram dispatch...");
  setTimeout(() => {
    console.log("🏁 Force scrape script finished.");
    process.exit(0);
  }, 120000);
}

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});

force();
