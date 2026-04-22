import { ollamaService } from "./ollamaService";
import { logger } from "../core/Logger";
import { query } from "../db/postgres";
import { sendTelegramMessage } from "./telegramService";

export class MacroEventAnalyzer {
  public async processNews(headline: string, source: string): Promise<void> {
    logger.info(`[MacroEventAnalyzer] Analyzing headline: ${headline}`);
    
    try {
      const prompt = `
        Analyze this market headline for broad market impact and severity.
        Return a JSON object with:
        {
          "category": "ECONOMIC" | "POLITICAL" | "TECH" | "REGULATORY",
          "severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
          "impact_score": 0-1,
          "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
          "summary": "1 sentence summary"
        }
        
        Headline: ${headline}
      `;
      
      const analysis = await ollamaService.generateJSON(prompt);
      
      if (analysis) {
        await query(`
          INSERT INTO macro_events (headline, source, category, severity, analysis_json)
          VALUES ($1, $2, $3, $4, $5)
        `, [headline, source, analysis.category, analysis.severity, JSON.stringify(analysis)]);
        
        if (analysis.severity === 'HIGH' || analysis.severity === 'CRITICAL') {
          await sendTelegramMessage(`🚨 *MACRO ALERT:* ${headline}\nCategory: ${analysis.category}\nSeverity: ${analysis.severity}\nAnalysis: ${analysis.summary}`, 'critical');
        }
      }
    } catch (error: any) {
      logger.error(`[MacroEventAnalyzer] Analysis failed: ${error.message}`);
    }
  }

  public async isReady(): Promise<boolean> {
    return await ollamaService.isReady();
  }
}

export const macroEventAnalyzer = new MacroEventAnalyzer();
