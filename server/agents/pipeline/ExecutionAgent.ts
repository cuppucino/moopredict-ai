import { eventBus } from '../../core/EventBus';
import { query } from '../../db/postgres';
import { tradeStore } from '../../services/tradeStore';
import { sendTelegramMessage } from '../../services/telegramService';
import { futu_service } from '../../services/futuService';
import { logger } from '../../core/Logger';
import { ETF_WATCHLIST } from '../../core/EtfWatchlist';

export class ExecutionAgent {
  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    eventBus.subscribe('strategy:trade_plan', async (plan) => {
      await this.handleTradeExecution(plan);
    });

    eventBus.subscribe('system:hourly_flush', async () => {
      await this.monitorPositions();
    });
  }

  /**
   * Handles trade execution (Paper Mode, US market only)
   */
  public async handleTradeExecution(plan: any): Promise<void> {
    const { symbol, action, entry_price, correlation_id } = plan;
    const currency = 'USD';

    try {
      if (entry_price <= 0 || entry_price === 100.00) {
        logger.error(`[ExecutionAgent] ABORTED: Invalid price $${entry_price} for ${symbol}.`);
        return;
      }

      const balanceResult = await query(`
        SELECT current_balance FROM virtual_portfolio WHERE currency = $1
      `, [currency]);
      const balance = parseFloat(balanceResult.rows[0].current_balance);

      const maxAllocation = 1500;
      const tradeValue = Math.min(balance * 0.3, maxAllocation);
      const qty = Math.floor(tradeValue / entry_price);

      if (qty <= 0) {
        logger.info(`[ExecutionAgent] Insufficient virtual balance for ${symbol}. Skipping.`);
        return;
      }

      const totalCost = qty * entry_price;

      if (action === 'BUY') {
        await query(`
          UPDATE virtual_portfolio
          SET current_balance = current_balance - $1, updated_at = NOW()
          WHERE currency = $2
        `, [totalCost, currency]);

        await tradeStore.addPosition({
          symbol,
          mode: 'PAPER',
          qty,
          entry_price,
          side: 'LONG',
          stop_loss_pct: 5,
          take_profit_pct: 10
        });

        await query(`
          UPDATE trade_proposals SET status = 'EXECUTED'
          WHERE symbol = $1 AND status = 'PENDING'
        `, [symbol.toUpperCase()]);

        logger.info(`[ExecutionAgent] Paper BUY executed: ${qty} ${symbol} at $${entry_price}`);
      }

      const message = `
✅ *TRADE EXECUTED (Paper Mode)*
Symbol: ${symbol}
Action: ${action}
Qty: ${qty}
Price: $${entry_price.toFixed(2)}
Value: USD ${totalCost.toFixed(2)}
━━━━━━━━━━━━━━━━━━━━━━
      `.trim();
      await sendTelegramMessage(message, 'info');

      eventBus.publish('executor:result', {
        correlation_id,
        symbol,
        action,
        qty,
        price: entry_price,
        success: true,
        timestamp: new Date()
      });

    } catch (error) {
      logger.error(`[ExecutionAgent] Execution failed for ${symbol}:`, { error });
    }
  }

  /**
   * Monitors open positions and triggers exits based on multi-tier strategy
   */
  public async monitorPositions(): Promise<void> {
    const positions = await tradeStore.getPositions();

    for (const pos of positions) {
      const proposalResult = await query(`
        SELECT * FROM trade_proposals WHERE symbol = $1 AND status = 'EXECUTED' ORDER BY created_at DESC LIMIT 1
      `, [pos.symbol]);

      const proposal = proposalResult.rows[0];
      if (!proposal) continue;

      try {
        const stockData = await futu_service.get_stock_data(pos.symbol);
        const currentPrice = stockData.price;

        const isEtf = ETF_WATCHLIST.some(e => e.symbol === pos.symbol);
        let shouldExit = false;
        let reason = '';
        let isTargetHit = false;

        if (currentPrice >= proposal.best_target) {
          isTargetHit = true;
          reason = '🥇 BEST TARGET HIT';
        } else if (currentPrice >= proposal.safe_target) {
          isTargetHit = true;
          reason = '🥈 SAFE TARGET HIT';
        } else if (currentPrice <= proposal.stop_loss) {
          shouldExit = true;
          reason = '🛑 STOP LOSS HIT';
        }

        if (shouldExit) {
          await this.closePosition(pos, currentPrice, reason);
        } else if (isTargetHit) {
          if (isEtf) {
            logger.info(`[ExecutionAgent] ${pos.symbol} hit target ${currentPrice}, sending recommendation.`);
            await sendTelegramMessage(`🎯 *ETF TARGET HIT: ${pos.symbol}*\nPrice: $${currentPrice.toFixed(2)}\nAction: Recommended to take profit or set trailing stop.`, 'info');
          } else {
            await this.closePosition(pos, currentPrice, reason);
          }
        }
      } catch (err) {
        logger.error(`[ExecutionAgent] Error monitoring ${pos.symbol}:`, { error: err });
      }
    }
  }

  private async closePosition(pos: any, exitPrice: number, reason: string): Promise<void> {
    const currency = 'USD';
    const proceeds = pos.qty * exitPrice;
    const pnl = proceeds - (pos.qty * pos.entry_price);

    logger.info(`[ExecutionAgent] Closing ${pos.symbol} at $${exitPrice} (${reason})`);

    await query(`
      UPDATE virtual_portfolio
      SET current_balance = current_balance + $1, total_pnl = total_pnl + $2, updated_at = NOW()
      WHERE currency = $3
    `, [proceeds, pnl, currency]);

    await tradeStore.removePosition(pos.symbol, 'PAPER');

    const message = `
🏁 *POSITION CLOSED (Paper Mode)*
Symbol: ${pos.symbol}
Reason: ${reason}
Exit Price: $${exitPrice.toFixed(2)}
PnL: USD ${pnl.toFixed(2)} (${((pnl / (pos.qty * pos.entry_price)) * 100).toFixed(2)}%)
━━━━━━━━━━━━━━━━━━━━━━
    `.trim();
    await sendTelegramMessage(message, 'info');

    await query(`
      UPDATE active_focus SET status = 'CLOSED', removed_at = NOW()
      WHERE symbol = $1 AND status = 'ACTIVE'
    `, [pos.symbol.toUpperCase()]);

    eventBus.publish('executor:position_update', {
      symbol: pos.symbol,
      pnl,
      qty: pos.qty,
      entry_price: pos.entry_price,
      exit_price: exitPrice,
      event: reason.includes('STOP') ? 'STOP_LOSS' : 'TAKE_PROFIT',
      timestamp: new Date()
    });
  }
}

export const executionAgent = new ExecutionAgent();
