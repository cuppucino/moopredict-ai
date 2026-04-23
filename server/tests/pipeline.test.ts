import { eventBus } from '../core/EventBus';
import { newsIntelAgent } from '../agents/pipeline/NewsIntelAgent';
import { eventAnalystAgent } from '../agents/pipeline/EventAnalystAgent';
import { strategyAgent } from '../agents/pipeline/StrategyAgent';
import { executionAgent } from '../agents/pipeline/ExecutionAgent';
import { ollamaService } from '../services/ollamaService';
import { futu_service } from '../services/futuService';
import { ensemblePredictor } from '../services/ensemblePredictor';
import { query } from '../db/postgres';

async function testV4Pipeline() {
  console.log("\n🧪 Testing MooPredict V4 Pipeline (Event-First)...");

  // 0. Clean DB state for tests
  console.log("...Step 0: Clearing previous test data");
  await query('TRUNCATE positions, trade_proposals RESTART IDENTITY CASCADE');

  // 1. Mock Ollama for Discovery
  (ollamaService as any).generateJSON = async (prompt: string) => {
    if (prompt.includes('analyze these headlines')) {
      return [{
        symbol: 'NVDA',
        reason: 'New AI chip announcement',
        catalyst_type: 'PRODUCT',
        direction: 'BULLISH',
        impact_score: 0.9,
        confidence: 0.95
      }];
    }
    return [];
  };

  // 2. Mock Futu for Price Data
  (futu_service as any).get_stock_data = async (symbol: string) => ({
    symbol,
    price: 100,
    history: [],
    technicals: { rsi: 45 }
  });

  // 3. Mock EnsemblePredictor — bypasses ML service and Gemini
  (ensemblePredictor as any).getPrediction = async (_stockData: any) => ({
    recommendation: 'BUY',
    confidence: 0.82,
    analysis: '[TEST-DATA] Mocked: Strong bullish signal from AI chip catalyst.',
    technicalIndicators: { rsi: 45, atr: 2.0, macd: 'Bullish', movingAverage: 'Above SMA20' },
    all_results: {}
  });

  // 4. Waking up agents (ensure they are imported)
  const agents = { newsIntelAgent, eventAnalystAgent, strategyAgent, executionAgent };

  let analystHeard = false;
  let strategyHeard = false;
  let executionHeard = false;

  eventBus.subscribe('analyst:opportunities', () => analystHeard = true);
  eventBus.subscribe('strategy:trade_plan', () => strategyHeard = true);
  eventBus.subscribe('executor:result', () => executionHeard = true);

  // 4. Trigger Intel Batch
  console.log("...Step 1: Emitting Intel Batch");
  eventBus.publish('intel:news_batch', {
    headlines: ['NVIDIA announces new H200 AI chips with massive performance gain'],
    timestamp: new Date().toISOString()
  });

  // 5. Wait for chain reaction
  let attempts = 0;
  while (attempts < 10 && (!analystHeard || !strategyHeard || !executionHeard)) {
    await new Promise(r => setTimeout(r, 500));
    attempts++;
  }

  if (analystHeard) console.log("✅ Team B (Analyst): Discovered NVDA opportunity.");
  if (strategyHeard) console.log("✅ Team C (Strategist): Generated trade plan.");
  if (executionHeard) console.log("✅ Team D (Executor): Performed paper trade.");

  if (analystHeard && strategyHeard && executionHeard) {
    console.log("\n✨ V4 PIPELINE TESTS PASSED\n");
    process.exit(0);
  } else {
    throw new Error("Pipeline chain broken.");
  }
}

testV4Pipeline().catch(e => {
  console.error("❌ TEST FAILED:", e.message);
  process.exit(1);
});
