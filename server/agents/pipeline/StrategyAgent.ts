import { eventBus } from '../../core/EventBus';
import { query } from '../../db/postgres';
import { ensemblePredictor } from '../../services/ensemblePredictor';
import { futu_service } from '../../services/futuService';
import { sendTelegramMessage } from '../../services/telegramService';
import { Opportunity } from './EventAnalystAgent';
import { v4 as uuidv4 } from 'uuid';

export class StrategyAgent {
  private maxPositionsPerMarket = 3;
  private maxTotalPositions = 6;
  private maxAllocationPct = 0.30;

  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    eventBus.subscribe('analyst:opportunities', async (data: { opportunities: Opportunity[], source: string }) => {
      await this.processOpportunities(data.opportunities, data.source);
    });
  }

  /**
   * Processes opportunities from Team B to create full trade plans
   */
  public async processOpportunities(opportunities: Opportunity[], source: string): Promise<void> {
    console.log(`[StrategyAgent] Processing ${opportunities.length} opportunities from ${source}...`);

    for (const opt of opportunities) {
      try {
        // 1. Check current position count limits
        if (!await this.canOpenMorePositions(opt.symbol)) {
          console.log(`[StrategyAgent] Limit reached for ${opt.symbol}. Skipping.`);
          continue;
        }

        // 2. Fetch price and technical data
        const stockData = await futu_service.get_stock_data(opt.symbol);
        if (!stockData) {
          console.error(`[StrategyAgent] Could not fetch data for ${opt.symbol}`);
          continue;
        }

        // 3. Run Ensemble Analysis
        const prediction = await ensemblePredictor.getPrediction(stockData);
        
        // 4. Combine with Team B catalyst score
        const combinedConfidence = (prediction.confidence + opt.confidence) / 2;
        
        if (prediction.recommendation === 'HOLD' || combinedConfidence < 0.5) {
          console.log(`[StrategyAgent] Low confidence/HOLD for ${opt.symbol} (${combinedConfidence.toFixed(2)}). Skipping.`);
          continue;
        }

        // 5. Create Multi-Tier Exit Strategy
        const atr = prediction.technicalIndicators?.atr || (stockData.price * 0.02);
        const bestTarget = stockData.price * (1 + 0.10); // +10% target
        const safeTarget = stockData.price * (1 + 0.05); // +5% target
        const stopLoss = stockData.price * (1 - 0.05);   // -5% stop

        const tradePlan = {
          symbol: opt.symbol,
          action: (prediction.recommendation.includes('BUY') ? 'BUY' : 'SELL') as 'BUY' | 'SELL',
          entry_price: stockData.price,
          best_target: bestTarget,
          safe_target: safeTarget,
          stop_loss: stopLoss,
          max_hold_days: 5,
          confidence: combinedConfidence,
          reasoning: `${opt.reason}. Ensemble: ${prediction.analysis}`,
          catalyst_id: null // Will link if found in active_focus
        };

        // 6. Store and Send to Telegram
        await this.storeAndNotify(tradePlan, opt);

        // 7. Emit trade plan for Team D
        eventBus.publish('strategy:trade_plan', {
          correlation_id: uuidv4(),
          ...tradePlan,
          timestamp: new Date()
        });

      } catch (error) {
        console.error(`[StrategyAgent] Error processing ${opt.symbol}:`, error);
      }
    }
  }

  /**
   * Checks if we can open more positions based on market and total limits
   */
  private async canOpenMorePositions(symbol: string): Promise<boolean> {
    const isHK = symbol.endsWith('.HK') || /^\d+$/.test(symbol);
    const market = isHK ? 'HK' : 'US';

    const result = await query(`
      SELECT COUNT(*) FROM positions WHERE qty > 0
    `);
    const totalCount = parseInt(result.rows[0].count);

    if (totalCount >= this.maxTotalPositions) return false;

    // Filter by market (requires positions table to have a market column, or derive from symbol)
    // For now, simple symbol check
    const marketPositions = await query(`
      SELECT symbol FROM positions WHERE qty > 0
    `);
    const currentMarketCount = marketPositions.rows.filter((p: any) => {
      const pIsHK = p.symbol.endsWith('.HK') || /^\d+$/.test(p.symbol);
      return (isHK && pIsHK) || (!isHK && !pIsHK);
    }).length;

    return currentMarketCount < this.maxPositionsPerMarket;
  }

  private async storeAndNotify(plan: any, opt: Opportunity): Promise<void> {
    // Link to catalyst in active_focus
    const focusResult = await query(`
      SELECT id FROM active_focus WHERE symbol = $1 AND status = 'ACTIVE' LIMIT 1
    `, [plan.symbol.toUpperCase()]);
    const catalystId = focusResult.rows[0]?.id || null;

    // Store proposal
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

    // Format Telegram Message
    const isHK = plan.symbol.endsWith('.HK') || /^\d+$/.test(plan.symbol);
    const message = `
━━━━━━━━━━━━━━━━━━━━━━
📐 *TRADE PROPOSAL: ${plan.action} ${plan.symbol}*
━━━━━━━━━━━━━━━━━━━━━━
📰 *Catalyst:* ${opt.reason}
📊 *Confidence:* ${Math.round(plan.confidence * 100)}% | Impact: ${opt.impact_score > 0.7 ? 'HIGH' : 'MEDIUM'}

📋 *PLAN:*
• Entry: $${plan.entry_price.toFixed(2)}
• 🥇 Best Target: $${plan.best_target.toFixed(2)} (+${Math.round(((plan.best_target / plan.entry_price) - 1) * 100)}%)
• 🥈 Safe Target: $${plan.safe_target.toFixed(2)} (+${Math.round(((plan.safe_target / plan.entry_price) - 1) * 100)}%)
• 🛑 Stop Loss: $${plan.stop_loss.toFixed(2)} (-${Math.round((1 - (plan.stop_loss / plan.entry_price)) * 100)}%)
• ⏱️ Max Hold: 5 days
• 📰 Event Hold: ${opt.direction === 'BULLISH' ? 'YES' : 'NO'}

${isHK ? '🤖 *HK: Paper mode (virtual balance)*' : '📱 *US: Manual action required — open Moomoo app*'}
━━━━━━━━━━━━━━━━━━━━━━
    `.trim();

    await sendTelegramMessage(message, 'info');
  }
}

export const strategyAgent = new StrategyAgent();
