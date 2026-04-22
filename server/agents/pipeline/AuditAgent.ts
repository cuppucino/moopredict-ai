import { eventBus } from '../../core/EventBus';
import { query } from '../../db/postgres';

export class AuditAgent {
  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    // Audit triggers when a position is closed
    // For now, let's listen for a custom event or tie it to ExecutionAgent closure
    eventBus.subscribe('executor:position_update', async (data) => {
      await this.auditTrade(data);
    });
  }

  /**
   * Audits a closed trade for accuracy and failure classification
   */
  public async auditTrade(data: any): Promise<void> {
    const { symbol, pnl, held_days, event } = data;
    
    console.log(`[AuditAgent] Auditing trade for ${symbol}...`);

    try {
      // Find the last proposal for this symbol
      const proposalResult = await query(`
        SELECT * FROM trade_proposals WHERE symbol = $1 ORDER BY created_at DESC LIMIT 1
      `, [symbol]);
      const proposal = proposalResult.rows[0];
      
      if (!proposal) return;

      const wasAccurate = pnl > 0;
      const actualDirection = pnl > 0 ? 'UP' : 'DOWN';
      const predictedDirection = proposal.action === 'BUY' ? 'UP' : 'DOWN';

      let failureReason = null;
      let lesson = '';

      if (!wasAccurate) {
        // Classification logic using Ollama (can be expanded)
        failureReason = this.classifyFailure(event);
        lesson = await this.generateLesson(symbol, proposal, pnl);
      } else {
        lesson = 'Signal was accurate. Strategy confirmed catalyst impact.';
      }

      // Store in audit_log
      await query(`
        INSERT INTO audit_log (proposal_id, symbol, was_accurate, predicted_direction, actual_direction, actual_pnl_pct, failure_reason, lesson)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      `, [
        proposal.id,
        symbol,
        wasAccurate,
        predictedDirection,
        actualDirection,
        (pnl / (data.qty * data.entry_price)) * 100, // True PnL %
        failureReason,
        lesson
      ]);

      console.log(`[AuditAgent] Audit complete for ${symbol}. Accurate: ${wasAccurate}`);

    } catch (error) {
      console.error('[AuditAgent] Audit failed:', error);
    }
  }

  private classifyFailure(event: string): string {
    if (event === 'STOP_LOSS') return 'TIMING_ERROR';
    if (event === 'TIME_EXPIRATION') return 'INSUFFICIENT_MOMENTUM';
    return 'PATTERN_UNRECOGNIZED';
  }

  private async generateLesson(symbol: string, proposal: any, pnl: number): Promise<string> {
    // In a real implementation, we would ask Ollama to compare catalyst vs result
    return `Trade on ${symbol} resulted in ${pnl.toFixed(2)} PnL. Initial catalyst: ${proposal.event_description}`;
  }
}

export const auditAgent = new AuditAgent();
