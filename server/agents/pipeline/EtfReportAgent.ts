import { eventBus } from '../../core/EventBus';
import { query } from '../../db/postgres';
import { sendTelegramMessage } from '../../services/telegramService';
import { TechnicalSignal } from '../../services/etfTechnicalAnalysis';
import { logger } from '../../core/Logger';

export class EtfReportAgent {
  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    // Listen for new ETF signals
    eventBus.subscribe('etf:signal_ready', async (signal: any) => {
      await this.reportSignal(signal);
    });

    // Listen for morning briefing triggers
    eventBus.subscribe('etf:morning_briefing', async () => {
      await this.sendMorningBriefing();
    });
  }

  /**
   * Formats and sends a single ETF signal to Telegram
   */
  private async reportSignal(signal: any): Promise<void> {
    try {
      const isBuy = signal.signal_type === 'BUY';
      const emoji = isBuy ? '📈' : '📉';
      const typeLabel = isBuy ? 'BUY SIGNAL' : 'SELL SIGNAL';
      
      const risk = signal.entry_price - signal.stop_loss;
      const reward = signal.target_price_short - signal.entry_price;
      const rrRatio = risk > 0 ? (reward / risk).toFixed(1) : 'N/A';

      const message = `
${emoji} *ETF ${typeLabel}: ${signal.symbol}*
━━━━━━━━━━━━━━━━━
💰 *Entry Price:* $${signal.entry_price.toFixed(2)}
🎯 *Target (short):* $${signal.target_price_short} (+${(((signal.target_price_short / signal.entry_price) - 1) * 100).toFixed(1)}%)
🎯 *Target (long):* $${signal.target_price_long} (+${(((signal.target_price_long / signal.entry_price) - 1) * 100).toFixed(1)}%)
🛑 *Stop Loss:* $${signal.stop_loss} (-${((1 - (signal.stop_loss / signal.entry_price)) * 100).toFixed(1)}%)
📊 *Confidence:* ${Math.round(signal.confidence * 100)}%
⏱ *Hold:* ${signal.timeframe}

📝 *Why:* ${signal.reason}

_Risk/Reward: ${rrRatio}:1 (ATR-based)_
      `.trim();

      await sendTelegramMessage(message, 'info');

      // Mark as alerted in DB
      await query(`
        UPDATE etf_signals SET alerted_at = NOW() WHERE id = $1
      `, [signal.id]);

      logger.info(`[EtfReportAgent] Telegram alert sent for ${signal.symbol}`);

    } catch (error: any) {
      logger.error(`[EtfReportAgent] Failed to send report for ${signal.symbol}:`, error.message);
    }
  }

  /**
   * Sends a summary of active ETF signals
   */
  private async sendMorningBriefing(): Promise<void> {
    try {
      // Get active signals
      const activeSignals = await query(`
        SELECT * FROM etf_signals 
        WHERE created_at > NOW() - INTERVAL '48 hours'
        AND signal_type = 'BUY'
        ORDER BY created_at DESC
      `);

      // Get lifetime stats
      const statsResult = await query(`
        SELECT 
          COUNT(*) as total_resolved,
          SUM(CASE WHEN is_correct = true THEN 1 ELSE 0 END) as total_wins
        FROM etf_signals
        WHERE is_correct IS NOT NULL
      `);
      
      const stats = statsResult.rows[0];
      const totalResolved = parseInt(stats.total_resolved) || 0;
      const totalWins = parseInt(stats.total_wins) || 0;
      const winRate = totalResolved > 0 ? Math.round((totalWins / totalResolved) * 100) : 0;
      
      const winRateStr = totalResolved > 0 
        ? `\n_ETF Module lifetime win rate: ${winRate}% across ${totalResolved} signals._\n`
        : `\n_ETF Module lifetime win rate: N/A (no closed signals yet)._\n`;

      if (activeSignals.rows.length === 0) {
        // Only send if there are active signals to keep noise low
        return;
      }

      let signalList = activeSignals.rows.map((s: any) => 
        `• *${s.symbol}*: Buy @ $${parseFloat(s.entry_price).toFixed(2)} (Conf: ${Math.round(s.confidence * 100)}%)`
      ).join("\n");

      const message = `
🌅 *ETF MORNING BRIEFING*
━━━━━━━━━━━━━━━━━${winRateStr}
The following ETF signals are currently active:

${signalList}

_No action needed unless you see a new BUY or SELL alert above._
      `.trim();

      await sendTelegramMessage(message, 'info');
      logger.info(`[EtfReportAgent] Morning briefing sent with ${activeSignals.rows.length} signals`);

    } catch (error: any) {
      logger.error(`[EtfReportAgent] Failed to send morning briefing:`, error.message);
    }
  }
}

export const etfReportAgent = new EtfReportAgent();
