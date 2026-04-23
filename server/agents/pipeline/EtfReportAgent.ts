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

_Risk/Reward: 2:1 ratio (ATR-based)_
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
      const activeSignals = await query(`
        SELECT * FROM etf_signals 
        WHERE created_at > NOW() - INTERVAL '48 hours'
        AND signal_type = 'BUY'
        ORDER BY created_at DESC
      `);

      if (activeSignals.rows.length === 0) {
        // Only send if there are active signals to keep noise low
        return;
      }

      let signalList = activeSignals.rows.map((s: any) => 
        `• *${s.symbol}*: Buy @ $${parseFloat(s.entry_price).toFixed(2)} (Conf: ${Math.round(s.confidence * 100)}%)`
      ).join("\n");

      const message = `
🌅 *ETF MORNING BRIEFING*
━━━━━━━━━━━━━━━━━
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
