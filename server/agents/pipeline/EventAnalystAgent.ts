import { eventBus } from '../../core/EventBus';
import { query } from '../../db/postgres';
import { ollamaService } from '../../services/ollamaService';
import { NewsIntelBatch } from './NewsIntelAgent';

export interface Opportunity {
  symbol: string;
  reason: string;
  catalyst_type: string;
  direction: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  impact_score: number;
  confidence: number;
}

export class EventAnalystAgent {
  private minConfidence = 0.5;

  constructor() {
    this.setupListeners();
  }

  private setupListeners(): void {
    // Listen for news and social batches from Team A
    eventBus.subscribe('intel:news_batch', async (data: NewsIntelBatch) => {
      await this.analyzeEvents(data.headlines, 'NEWS');
    });

    eventBus.subscribe('intel:social_batch', async (data: { items: any[] }) => {
      const headlines = data.items.map(i => i.content);
      await this.analyzeEvents(headlines, 'SOCIAL');
    });
  }

  /**
   * Analyzes a batch of headlines to discover stock opportunities
   */
  public async analyzeEvents(headlines: string[], type: 'NEWS' | 'SOCIAL'): Promise<void> {
    if (headlines.length === 0) return;

    console.log(`[EventAnalystAgent] Analyzing ${headlines.length} ${type} events...`);

    try {
      // Step 1: Use Ollama to discover and evaluate stocks
      const discoveries = await this.discoverOpportunities(headlines);
      
      if (discoveries.length === 0) {
        console.log('[EventAnalystAgent] No clear opportunities discovered in this batch.');
        return;
      }

      // Step 2: Filter by confidence and rank top 3
      const qualified = discoveries
        .filter(d => d.confidence >= this.minConfidence && d.direction !== 'NEUTRAL')
        .sort((a, b) => (b.impact_score * b.confidence) - (a.impact_score * a.confidence))
        .slice(0, 3);

      if (qualified.length === 0) {
        console.log('[EventAnalystAgent] No opportunities met the confidence threshold (0.5).');
        return;
      }

      console.log(`[EventAnalystAgent] Qualified ${qualified.length} top opportunities.`);

      // Step 3: Manage dynamic watchlist (active_focus table)
      for (const opt of qualified) {
        await this.storeOpportunity(opt);
      }

      // Step 4: Emit opportunities for Team C
      eventBus.publish('analyst:opportunities', {
        opportunities: qualified,
        source: type,
        timestamp: new Date()
      });

    } catch (error) {
      console.error('[EventAnalystAgent] Analysis failed:', error);
    }
  }

  /**
   * Calls Ollama to map headlines to stocks and score them
   */
  private async discoverOpportunities(headlines: string[]): Promise<Opportunity[]> {
    // We send headlines in batches to avoid overwhelming the LLM and to provide context
    const batchSize = 15;
    const allDiscoveries: Opportunity[] = [];

    for (let i = 0; i < headlines.length; i += batchSize) {
      const batch = headlines.slice(i, i + batchSize);
      const prompt = `
        As a senior stock analyst, analyze these headlines and identify which publicly traded stocks (US or HK) are directly or indirectly affected.
        
        Headlines:
        ${batch.map((h, idx) => `${idx + 1}. ${h}`).join('\n')}
        
        For each affected stock, provide:
        - symbol (Ticker e.g. AAPL, 0700.HK)
        - reason (Why is it affected? Indirect links are okay e.g. "TSMC news affects AAPL supply chain")
        - catalyst_type (EARNINGS | PRODUCT | REGULATION | MACRO | SOCIAL)
        - direction (BULLISH | BEARISH | NEUTRAL)
        - impact_score (0.0 to 1.0, how big is the price impact?)
        - confidence (0.0 to 1.0, how sure are you about this stock connection?)

        Respond with ONLY a JSON array of objects:
        [
          {
            "symbol": "TICKER",
            "reason": "short explanation",
            "catalyst_type": "TYPE",
            "direction": "BULLISH|BEARISH|NEUTRAL",
            "impact_score": 0.8,
            "confidence": 0.9
          }
        ]
        
        If no clear stock impact is found, return an empty array [].
      `.trim();

      try {
        const response = await ollamaService.generateJSON(prompt);
        if (Array.isArray(response)) {
          allDiscoveries.push(...response);
        }
      } catch (error) {
        console.error('[EventAnalystAgent] Ollama discovery failed for batch:', error);
      }
    }

    return allDiscoveries;
  }

  /**
   * Stores discovered opportunity in active_focus table
   */
  private async storeOpportunity(opt: Opportunity): Promise<void> {
    try {
      await query(`
        INSERT INTO active_focus (symbol, reason, catalyst_type, direction, impact_score)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT DO NOTHING
      `, [
        opt.symbol.toUpperCase(),
        opt.reason,
        opt.catalyst_type,
        opt.direction,
        opt.impact_score
      ]);
    } catch (error) {
      console.error(`[EventAnalystAgent] Error storing ${opt.symbol}:`, error);
    }
  }
}

export const eventAnalystAgent = new EventAnalystAgent();
