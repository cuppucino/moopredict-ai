import { eventBus } from '../../core/EventBus';
import { query } from '../../db/postgres';
import { etf_data_service } from '../../services/etfDataService';
import { etf_technical_analysis, TechnicalSignal } from '../../services/etfTechnicalAnalysis';
import { ETF_WATCHLIST } from '../../core/EtfWatchlist';
import { logger } from '../../core/Logger';

export class EtfIntelAgent {
  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    // Listen for manual scan requests
    eventBus.subscribe('etf:force_scan', async () => {
      await this.runAnalysis();
    });
  }

  /**
   * Main analysis loop for all ETFs in the watchlist
   */
  public async runAnalysis(): Promise<void> {
    logger.info(`[EtfIntelAgent] Starting ETF intelligence scan for ${ETF_WATCHLIST.length} symbols...`);
    
    for (const etf of ETF_WATCHLIST) {
      try {
        // 1. Fetch historical data
        const history = await etf_data_service.fetch_etf_data(etf.symbol);
        if (history.length < 20) {
          logger.warn(`[EtfIntelAgent] Insufficient history for ${etf.symbol}. Skipping.`);
          continue;
        }

        // 2. Run Technical Analysis
        const signal = etf_technical_analysis.analyze(etf.symbol, history);
        logger.info(`[EtfIntelAgent] Analysis result for ${etf.symbol}: ${signal.signal_type} (Conf: ${Math.round(signal.confidence * 100)}%)`);

        // 3. Persist and Emit if signal is valid
        if (signal.signal_type !== 'WATCH') {
          await this.saveAndEmitSignal(signal, etf.preferred_timeframe);
        } else {
          // Optional: Log watch signals to DB with low confidence for historical tracking
          logger.debug(`[EtfIntelAgent] Neutral stance on ${etf.symbol}.`);
        }

      } catch (error: any) {
        logger.error(`[EtfIntelAgent] Failed to analyze ${etf.symbol}:`, error.message);
      }
    }

    logger.info(`[EtfIntelAgent] ETF scan completed.`);
  }

  private async saveAndEmitSignal(signal: TechnicalSignal, timeframe: string): Promise<void> {
    try {
      // 1. Check for duplicate today (symbol + type + date)
      const existing = await query(`
        SELECT id FROM etf_signals 
        WHERE symbol = $1 AND signal_type = $2 AND created_at::DATE = CURRENT_DATE
      `, [signal.symbol, signal.signal_type]);

      if (existing.rows.length > 0) {
        logger.info(`[EtfIntelAgent] Signal ${signal.signal_type} for ${signal.symbol} already exists for today. Skipping save.`);
        return;
      }

      // 2. Save to DB
      const result = await query(`
        INSERT INTO etf_signals (
          symbol, signal_type, entry_price, target_price, stop_loss, 
          confidence, reason, timeframe
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
      `, [
        signal.symbol,
        signal.signal_type,
        signal.entry_price,
        signal.target_price_short, // Default to short target in DB, can expand later
        signal.stop_loss,
        signal.confidence,
        signal.reason,
        timeframe
      ]);

      const signalId = result.rows[0].id;

      // 3. Emit for ReportAgent
      eventBus.publish('etf:signal_ready', {
        id: signalId,
        ...signal,
        timeframe
      });

      // 4. Auto-execute in Paper Mode if confidence is high (>= 70%)
      if (signal.signal_type === 'BUY' && signal.confidence >= 0.7) {
        logger.info(`[EtfIntelAgent] High confidence BUY signal for ${signal.symbol}. Triggering paper trade.`);
        
        // Save as a pending proposal first so ExecutionAgent can find it
        await query(`
          INSERT INTO trade_proposals (
            symbol, action, entry_price, best_target, safe_target, 
            stop_loss, confidence, status, event_description
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'PENDING', $8)
        `, [
          signal.symbol,
          'BUY',
          signal.entry_price,
          signal.target_price_long,
          signal.target_price_short,
          signal.stop_loss,
          signal.confidence,
          `[ETF Module] ${signal.reason}`
        ]);

        eventBus.publish('strategy:trade_plan', {
          correlation_id: `etf-${signalId}`,
          symbol: signal.symbol,
          action: 'BUY',
          entry_price: signal.entry_price,
          best_target: signal.target_price_long,
          safe_target: signal.target_price_short,
          stop_loss: signal.stop_loss,
          confidence: signal.confidence,
          reasoning: `[ETF Module] ${signal.reason}`
        });
      }

      logger.info(`[EtfIntelAgent] New ${signal.signal_type} signal persisted for ${signal.symbol} (ID: ${signalId})`);

    } catch (error: any) {
      logger.error(`[EtfIntelAgent] Error saving signal for ${signal.symbol}:`, error.message);
    }
  }
}

export const etfIntelAgent = new EtfIntelAgent();
