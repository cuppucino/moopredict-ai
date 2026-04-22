import { smartDigestAgent } from '../server/agents/SmartDigestAgent';
import { logger } from '../server/core/Logger';

/**
 * Test script for SmartDigestAgent Phase 1
 */
async function test() {
  logger.info("🧪 Starting SmartDigestAgent test...");

  const signals = [
    {
      symbol: "TSLA",
      action: "BUY",
      confidence: 0.85,
      price: 245.50,
      reason: "Earnings beat + social buzz VIRAL",
      urgency: "NOW",
      socialBuzz: "VIRAL",
      riskLevel: "MEDIUM"
    },
    {
      symbol: "AAPL",
      action: "SELL",
      confidence: 0.72,
      price: 182.10,
      reason: "Guidance cut warning, RSI overbought",
      urgency: "SOON",
      socialBuzz: "LOW",
      riskLevel: "HIGH"
    },
    {
      symbol: "NVDA",
      action: "HOLD",
      confidence: 0.55,
      price: 850.00,
      reason: "Consolidating, volume low",
      urgency: "WATCH",
      socialBuzz: "MEDIUM",
      riskLevel: "LOW"
    }
  ];

  for (const s of signals) {
    logger.info(`Adding test signal for ${s.symbol}...`);
    await smartDigestAgent.addSignal(s);
  }

  logger.info("Flushing digest...");
  await smartDigestAgent.flushDigest();
  
  logger.info("✅ Test complete. Check your Telegram (if configured) or the logs.");
}

test().catch(console.error);
