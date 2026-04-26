import { eventBus } from '../../core/EventBus';
import { query } from '../../db/postgres';
import { sendTelegramMessage } from '../../services/telegramService';
import { ollamaService } from '../../services/ollamaService';

export class ReportAgent {
  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    // 17:00 HKT - Daily Report Trigger (derived from Scheduler event)
    eventBus.subscribe('system:daily_report', async () => {
      await this.generateDailyReport();
    });

    // 08:00 Sat - Weekly Report Trigger
    eventBus.subscribe('system:weekly_report', async () => {
      await this.generateWeeklyReport();
    });
  }

  /**
   * Generates a daily summary of Paper Trading and Market Intel
   */
  public async generateDailyReport(): Promise<void> {
    console.log('[ReportAgent] Generating daily report...');

    try {
      // 1. Fetch Virtual Portfolio Stats
      const portfolios = await query('SELECT * FROM virtual_portfolio');
      const usd = portfolios.rows.find((r: any) => r.currency === 'USD');

      // 2. Fetch Recent Audit Logs (Today)
      const auditLogs = await query(`
        SELECT * FROM audit_log 
        WHERE closed_at > NOW() - INTERVAL '24 hours'
      `);

      // 3. AI Lesson from Performance
      const lesson = await ollamaService.analyzeDailyPerformance(
        auditLogs.rows.filter((l: any) => l.was_accurate),
        auditLogs.rows.filter((l: any) => !l.was_accurate)
      );

      // 4. Format Message
      const message = `
📊 *MOOPREDICT DAILY PERFORMANCE*
━━━━━━━━━━━━━━━━━━━━━━
💰 *Virtual Balance (USD):*
• Balance: $${usd?.current_balance?.toLocaleString() ?? '0'} (${(usd?.total_pnl ?? 0) >= 0 ? '+' : ''}${usd?.total_pnl?.toLocaleString() ?? '0'})

💹 *Today's Activity:*
• Trades Closed: ${auditLogs.rowCount}
• Win Rate: ${auditLogs.rowCount > 0 ? Math.round((auditLogs.rows.filter((l: any) => l.was_accurate).length / auditLogs.rowCount) * 100) : 0}%

🧠 *AI Coach Lesson:*
"${lesson}"

━━━━━━━━━━━━━━━━━━━━━━
_Tomorrow is another day to learn. Stay disciplined._
      `.trim();

      await sendTelegramMessage(message, 'info');

    } catch (error) {
      console.error('[ReportAgent] Daily report generation failed:', error);
    }
  }

  /**
   * Generates a weekly performance audit and strategy review
   */
  public async generateWeeklyReport(): Promise<void> {
    console.log('[ReportAgent] Generating weekly report...');
    
    try {
      // 1. Fetch Weekly Audit Logs
      const weeklyAudit = await query(`
        SELECT * FROM audit_log 
        WHERE closed_at > NOW() - INTERVAL '7 days'
      `);

      if (weeklyAudit.rowCount === 0) {
        await sendTelegramMessage('📈 *Weekly Audit:* No trades closed this week. Agents are still hunting.', 'info');
        return;
      }

      // 2. Aggregate Stats
      const wins = weeklyAudit.rows.filter((l: any) => l.was_accurate).length;
      const totalTrades = weeklyAudit.rowCount;
      const winRate = Math.round((wins / totalTrades) * 100);
      
      const totalPnlPct = weeklyAudit.rows.reduce((acc: number, l: any) => acc + parseFloat(l.actual_pnl_pct), 0);
      const topSymbol = weeklyAudit.rows.sort((a: any, b: any) => parseFloat(b.actual_pnl_pct) - parseFloat(a.actual_pnl_pct))[0]?.symbol;

      // 3. Weekly Meta-Lesson (Summarizing lessons)
      const weeklyLessons = weeklyAudit.rows.map((l: any) => l.lesson).join('\n');
      const metaLessonPrompt = `
        Summarize these daily trading lessons into one high-level strategy meta-lesson for a beginner trader:
        ${weeklyLessons}
        
        Keep it to 2-3 concise sentences.
      `;
      const metaLesson = await ollamaService.generateJSON(metaLessonPrompt) || "Stay patient and trust the catalysts.";

      // 4. Format Message
      const message = `
🗓️ *WEEKLY PERFORMANCE AUDIT*
━━━━━━━━━━━━━━━━━━━━━━
📊 *Stats:*
• Total Trades: ${totalTrades}
• Win Rate: ${winRate}%
• Total Weekly PnL: ${totalPnlPct >= 0 ? '+' : ''}${totalPnlPct.toFixed(2)}%
• Top Performer: ${topSymbol || 'N/A'}

🧠 *Weekly Meta-Lesson:*
"${typeof metaLesson === 'string' ? metaLesson : JSON.stringify(metaLesson)}"

━━━━━━━━━━━━━━━━━━━━━━
_Reviewing history is how we build the future._
      `.trim();

      await sendTelegramMessage(message, 'info');

    } catch (error) {
      console.error('[ReportAgent] Weekly report generation failed:', error);
    }
  }
}

export const reportAgent = new ReportAgent();
