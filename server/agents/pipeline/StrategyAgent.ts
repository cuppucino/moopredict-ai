import { eventBus } from '../../core/EventBus';
import { query } from '../../db/postgres';
import { ensemblePredictor } from '../../services/ensemblePredictor';
import { futu_service } from '../../services/futuService';
import { sendTelegramMessage } from '../../services/telegramService';
import { Opportunity } from './EventAnalystAgent';
import { v4 as uuidv4 } from 'uuid';
import { logger } from '../../core/Logger';

export class StrategyAgent {
  private maxTotalPositions = 6;

  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    eventBus.subscribe('analyst:opportunities', async (data: { opportunities: Opportunity[], source: string }) => {
      await this.processOpportunities(data.opportunities, data.source);
    });
  }

  public async processOpportunities(opportunities: Opportunity[], source: string): Promise<void> {
    logger.info(`[StrategyAgent] Processing ${opportunities.length} opportunities from ${source}...`);

    for (const opt of opportunities) {
      try {
        if (!await this.canOpenMorePositions()) {
          logger.info(`[StrategyAgent] Limit reached for ${opt.symbol}. Skipping.`);
          continue;
        }

        const stockData = await futu_service.get_stock_data(opt.symbol);
        if (!stockData || stockData.price <= 0) {
          logger.error(`[StrategyAgent] Invalid market data for ${opt.symbol}`, { price: stockData?.price });
          continue;
        }

        if (stockData.price === 100.00) {
          logger.error(`[StrategyAgent] REJECTED: Mocked price (100.00) detected for ${opt.symbol}. Discarding opportunity.`);
          continue;
        }

        logger.info(`[StrategyAgent] Analysis for ${opt.symbol}: Price $${stockData.price} (${stockData.market})`);

        const prediction = await ensemblePredictor.getPrediction(stockData);
        const combinedConfidence = (prediction.confidence + opt.confidence) / 2;

        if (prediction.recommendation === 'HOLD' || combinedConfidence < 0.5) {
          logger.info(`[StrategyAgent] Low confidence/HOLD for ${opt.symbol} (${combinedConfidence.toFixed(2)}). Skipping.`);
          continue;
        }

        const tradePlan = {
          symbol: opt.symbol,
          action: (prediction.recommendation.includes('BUY') ? 'BUY' : 'SELL') as 'BUY' | 'SELL',
          entry_price: stockData.price,
          best_target: prediction.targetPrice || (stockData.price * 1.10),
          safe_target: stockData.price * 1.05,
          stop_loss: prediction.stop_loss || (stockData.price * 0.95),
          max_hold_days: 5,
          confidence: combinedConfidence,
          reasoning: prediction.analysis || opt.reason,
          technical_summary: `Trend: ${prediction.technicalIndicators?.macd || 'N/A'}, RSI: ${prediction.technicalIndicators?.rsi || 'N/A'}`
        };

        await this.storeAndNotify(tradePlan, opt, prediction);

        eventBus.publish('strategy:trade_plan', {
          correlation_id: uuidv4(),
          catalyst_id: null,
          ...tradePlan,
          timestamp: new Date()
        });

      } catch (error) {
        logger.error(`[StrategyAgent] Error processing ${opt.symbol}:`, { error });
      }
    }
  }

  private async canOpenMorePositions(): Promise<boolean> {
    const result = await query(`SELECT COUNT(*) FROM positions WHERE qty > 0`);
    return parseInt(result.rows[0].count) < this.maxTotalPositions;
  }

  private async storeAndNotify(plan: any, opt: Opportunity, prediction: any): Promise<void> {
    const focusResult = await query(`
      SELECT id FROM active_focus WHERE symbol = $1 AND status = 'ACTIVE' LIMIT 1
    `, [plan.symbol.toUpperCase()]);
    const catalystId = focusResult.rows[0]?.id || null;

    await query(`
      INSERT INTO trade_proposals (symbol, action, entry_price, best_target, safe_target, stop_loss, confidence, catalyst_id, event_description)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    `, [
      plan.symbol,
      plan.action,
      plan.entry_price,
      plan.best_target,
      plan.safe_target,
      plan.stop_loss,
      plan.confidence,
      catalystId,
      plan.reasoning
    ]);

    const riskEmoji = prediction.riskLevel === 'HIGH' ? '🔴' : prediction.riskLevel === 'MEDIUM' ? '🟡' : '🟢';

    const message = `
━━━━━━━━━━━━━━━━━━━━━━
🎯 *TRADE CONSENSUS: ${plan.symbol}*
━━━━━━━━━━━━━━━━━━━━━━
🏁 *Action:* ${plan.action} (${prediction.recommendation})
📊 *Confidence:* ${Math.round(plan.confidence * 100)}% | Risk: ${riskEmoji} ${prediction.riskLevel}
🌍 *Macro Signal:* ${prediction.macro_signal || 'NEUTRAL'}

📝 *CONSENSUS SNAPSHOT:*
_${plan.reasoning}_

📐 *PLAN:*
• Entry: $${plan.entry_price.toFixed(2)}
• 🛑 Stop Loss: $${plan.stop_loss.toFixed(2)} (-${Math.round((1 - (plan.stop_loss / plan.entry_price)) * 100)}%)
• ⏱️ Max Hold: 5 days
• 📰 Event Hold: ${opt.direction === 'BULLISH' ? 'YES' : 'NO'}

📱 *US: Manual action required — open Moomoo app*
━━━━━━━━━━━━━━━━━━━━━━
    `.trim();

    await sendTelegramMessage(message, 'info');
  }
}

export const strategyAgent = new StrategyAgent();
