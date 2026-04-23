import { eventBus } from '../../core/EventBus';
import { sendTelegramMessage } from '../../services/telegramService';
import { logger } from '../../core/Logger';

/**
 * V4 Intelligence Report Agent
 * Consolidates news, social, and analyst findings into clean Telegram reports.
 */
export class IntelligenceReportAgent {
  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    // Listen for opportunities discovered by EventAnalystAgent
    eventBus.subscribe('analyst:opportunities', async (data: any) => {
      await this.sendOpportunityReport(data.opportunities, data.source);
    });

    // We can also add market-specific "Opening Bell" summaries here
  }

  private async sendOpportunityReport(opportunities: any[], source: string): Promise<void> {
    if (!opportunities || opportunities.length === 0) return;

    logger.info(`[IntelligenceReportAgent] Formatting report for ${opportunities.length} opportunities from ${source}`);

    const marketEmoji = source === 'NEWS' ? '🚀' : '🔥';
    let message = `${marketEmoji} *MOOPREDICT INTEL: ${source}*\n`;
    message += `───────────────────\n`;

    for (const opt of opportunities) {
      const directionEmoji = opt.direction === 'BULLISH' ? '📈' : '📉';
      const score = Math.round(opt.impact_score * 100);
      
      message += `${directionEmoji} *${opt.symbol}* | ${score}% IMPACT\n`;
      message += `> _${opt.reason}_\n`;
      message += `• *Type:* ${opt.catalyst_type} | *Conf:* ${Math.round(opt.confidence * 100)}%\n\n`;
    }

    message += `───────────────────\n`;
    message += `_Team B Analysts are monitoring these tickers._`;

    await sendTelegramMessage(message, 'info');
  }
}

export const intelligenceReportAgent = new IntelligenceReportAgent();
