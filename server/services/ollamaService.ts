import axios from "axios";
import { logger } from "../core/Logger";
import { config } from "../core/Config";
import { CircuitBreaker } from "../core/CircuitBreaker";

/**
 * Enterprise Ollama Service.
 * 
 * Provides local LLM inference with circuit breaker protection
 * and automated health checks.
 */
export class OllamaService {
  private isAvailable = false;
  private breaker = new CircuitBreaker({ name: 'Ollama', failureThreshold: 3, resetTimeoutMs: 60000 });

  constructor() {
    this.checkAvailability();
  }

  private async checkAvailability(): Promise<void> {
    try {
      const response = await axios.get(`${config.OLLAMA_URL}/api/tags`, { timeout: 3000 });
      this.isAvailable = response.status === 200;
      if (this.isAvailable) {
        logger.info(`[OllamaService] Connected to ${config.OLLAMA_URL}`, { 
          models: response.data?.models?.map((m: any) => m.name) 
        });
      }
    } catch (error) {
      this.isAvailable = false;
      logger.warn(`[OllamaService] Not available at ${config.OLLAMA_URL}. Fallback to neutral sentiment.`);
    }
  }

  public async isReady(): Promise<boolean> {
    if (!this.isAvailable) {
      await this.checkAvailability();
    }
    return this.isAvailable;
  }

  /**
   * Analyze sentiment of text using local Ollama
   */
  public async analyzeSentiment(text: string): Promise<{ score: number; label: string }> {
    if (!await this.isReady()) {
      return { score: 0, label: "neutral" };
    }

    const prompt = `Analyze the sentiment of this financial text. Rate from -1 (very negative) to 1 (very positive).

Text: "${text.substring(0, 1000)}"

Respond with ONLY a JSON object:
{
  "score": <number between -1 and 1>,
  "label": "<positive|negative|neutral>"
}

JSON:`;

    try {
      const response = await axios.post(`${config.OLLAMA_URL}/api/generate`, {
        model: config.OLLAMA_MODEL,
        prompt,
        stream: false,
        options: { temperature: 0.1, num_predict: 100 }
      }, { timeout: 60000 });

      const content = response.data?.response || "";
      const jsonMatch = content.match(/\{[^}]+\}/);
      
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        return {
          score: Math.max(-1, Math.min(1, parsed.score || 0)),
          label: parsed.label || "neutral"
        };
      }
    } catch (error) {
      console.error("[OllamaService] Sentiment analysis failed:", error);
    }

    return { score: 0, label: "neutral" };
  }

  /**
   * Predict stock movement based on technical data using Ollama
   */
  public async predictFromTechnical(
    stock: any,
    computed: any,
    newsHeadlines: string[]
  ): Promise<any> {
    if (!await this.isReady()) {
      return null;
    }

    const newsSection = newsHeadlines && newsHeadlines.length > 0
      ? `\nRecent News Headlines:\n${newsHeadlines.map((h, i) => `  ${i + 1}. ${h}`).join("\n")}`
      : "";

    const prompt = `
Analyze the following REAL market data for ${stock.name} (${stock.symbol}) and provide a concise market snapshot paragraph for a trading dashboard.

## Rules:
- Keep the "analysis" under 60 words.
- Be specific and grounded only in the supplied metrics.
- Mention the strongest support and strongest risk.
- Do not use bullet points.
- Do not mention AI, models, or uncertainty disclaimers.

## Market Data:
- Symbol: ${stock.symbol}
- Current Price: $${stock.price}
- Price Change: ${stock.changePercent}%
- RSI (14): ${computed.rsi}
- MACD: ${computed.macd_trend}
- Bollinger Band: ${computed.bb_position}
- BB Squeeze: ${computed.bb_squeeze ? 'YES — breakout likely' : 'NO'}
- Market Structure: ${computed.market_structure}
- Support: ${computed.nearest_support_pct}% below
- Resistance: ${computed.nearest_resistance_pct}% above
- Trend vs SMA20: ${computed.price_vs_sma20}
${newsSection}

## Output Format (JSON):
{
  "symbol": "${stock.symbol}",
  "recommendation": "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL",
  "confidence": <float>,
  "targetPrice": <realistic target>,
  "analysis": "Grounded paragraph under 60 words",
  "riskLevel": "LOW" | "MEDIUM" | "HIGH",
  "macro_signal": "BULLISH" | "BEARISH" | "NEUTRAL"
}

Respond with ONLY valid JSON.
`.trim();

    try {
      const response = await axios.post(`${config.OLLAMA_URL}/api/generate`, {
        model: config.OLLAMA_MODEL,
        prompt,
        stream: false,
        format: "json",
        options: { temperature: 0.1, num_predict: 300 }
      }, { timeout: 60000 });

      const content = response.data?.response || "";
      return JSON.parse(content);
    } catch (error) {
      logger.error("[OllamaService] Full prediction failed:", error);
      return null;
    }
  }

  /**
   * Generate a trading strategy based on news and technicals
   */
  public async generateStrategy(
    symbol: string,
    technicalSummary: string,
    newsHeadlines: string[],
    riskLevel: string
  ): Promise<{ action: "BUY" | "SELL" | "HOLD"; rationale: string; confidence: number }> {
    if (!await this.isReady()) {
      return { action: "HOLD", rationale: "Ollama unavailable", confidence: 0.5 };
    }

    const newsSection = newsHeadlines.length > 0 
      ? `\nRecent News:\n${newsHeadlines.slice(0, 5).map((h, i) => `${i + 1}. ${h}`).join("\n")}`
      : "";

    const prompt = `You are a senior portfolio manager. Make a trading decision.

Stock: ${symbol}
Technical Analysis: ${technicalSummary}
Current Risk Environment: ${riskLevel}${newsSection}

Should we BUY, SELL, or HOLD this position?

Respond with ONLY a JSON object:
{
  "action": "BUY|SELL|HOLD",
  "confidence": <0.0 to 1.0>,
  "rationale": "<concise explanation>"
}

JSON:`;

    try {
      const response = await axios.post(`${config.OLLAMA_URL}/api/generate`, {
        model: config.OLLAMA_MODEL,
        prompt,
        stream: false,
        options: { temperature: 0.3, num_predict: 200 }
      }, { timeout: 60000 });

      const content = response.data?.response || "";
      const jsonMatch = content.match(/\{[^}]+\}/);
      
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        return {
          action: ["BUY", "SELL", "HOLD"].includes(parsed.action) ? parsed.action : "HOLD",
          confidence: Math.max(0, Math.min(1, parsed.confidence || 0.5)),
          rationale: parsed.rationale || "No rationale provided"
        };
      }
    } catch (error) {
      console.error("[OllamaService] Strategy generation failed:", error);
    }

    return { action: "HOLD", rationale: "Strategy generation failed", confidence: 0.5 };
  }

  /**
   * Pull a model from Ollama registry
   */
  public async pullModel(modelName: string): Promise<boolean> {
    if (!await this.isReady()) return false;

    try {
      logger.info(`[OllamaService] Pulling model: ${modelName}...`);
      await axios.post(`${config.OLLAMA_URL}/api/pull`, {
        name: modelName,
        stream: false
      }, { timeout: 300000 }); // 5 min timeout for large models
      
      console.log(`[OllamaService] Model ${modelName} pulled successfully`);
      return true;
    } catch (error) {
      console.error(`[OllamaService] Failed to pull model ${modelName}:`, error);
      return false;
    }
  }

  /**
   * List locally available models
   */
  public async listModels(): Promise<string[]> {
    if (!await this.isReady()) return [];

    try {
      const response = await axios.get(`${config.OLLAMA_URL}/api/tags`, { timeout: 5000 });
      return response.data?.models?.map((m: any) => m.name) || [];
    } catch (error) {
      return [];
    }
  }

  /**
   * High-precision analysis of Scout recap data.
   * This is the core "Brain" of the Analyst Agent.
   */
  public async analyzeRecap(recap: any, decidedAction?: string): Promise<any> {
    const contextText = decidedAction 
      ? `The quantitative system has decided on a ${decidedAction} signal. Explain the signals mapping to this decision.`
      : `Analyze this 1-5 day trading opportunity and decide on an action.`;

    const prompt = `
You are the Lead Analyst at MooPredict. ${contextText}

STOCK: ${recap.symbol} @ $${recap.price_data.current}

INDICATORS:
- RSI: ${recap.technicals.rsi}
- MACD: ${recap.technicals.macd_trend}
- BB: ${recap.technicals.bb_position}
- BB Squeeze: ${recap.technicals.bb_squeeze ? 'YES — breakout likely imminent' : 'NO'} (width: ${recap.technicals.bb_width})
- Market Structure: ${recap.technicals.market_structure}${recap.technicals.mss_detected ? ' — WARNING: STRUCTURE SHIFT DETECTED' : ''}
- Nearest Resistance: ${recap.technicals.nearest_resistance_pct}% above current price
- Nearest Support: ${recap.technicals.nearest_support_pct}% below current price
- Volume Ratio: ${recap.price_data.volume_ratio}x

SENTIMENT:
- News Score: ${recap.news_sentiment.score} (${recap.news_sentiment.article_count} articles)
- Social Buzz: ${recap.social_buzz.mention_count} mentions, Virality: ${recap.social_buzz.avg_virality}

Respond with ONLY valid JSON:
{
  "action": "${decidedAction || "BUY | SELL | HOLD"}",
  "confidence": 0.0 to 1.0,
  "reasoning": "2 sentences max explaining the signals",
  "conviction": "NORMAL" | "HIGH"
}
`.trim();

    return this.breaker.execute(async () => {
      const response = await axios.post(`${config.OLLAMA_URL}/api/generate`, {
        model: config.OLLAMA_MODEL,
        prompt,
        stream: false,
        format: "json",
        options: { temperature: 0.1, num_predict: 200 }
      }, { timeout: 60000 });

      const content = response.data?.response || "";
      return JSON.parse(content);
    });
  }

  /**
   * Long-term fundamental analysis (6-12 month horizon).
   * Thinks like a long-term investor, not a day trader.
   */
  public async analyzeLongTerm(recap: {
    symbol: string;
    name: string;
    price: number;
    pe_ratio: number;
    market_cap: string;
    fundamentals: {
      sma50: number;
      sma200: number;
      golden_cross: boolean;
      week52_high: number;
      week52_low: number;
      week52_position_pct: number;
      year_return_pct: number;
    };
  }): Promise<{
    action: 'ACCUMULATE' | 'HOLD' | 'REDUCE';
    confidence: number;
    conviction: 'NORMAL' | 'HIGH';
    reasoning: string;
    catalysts: string;
    risks: string;
  }> {
    if (!await this.isReady()) {
      return { action: 'HOLD', confidence: 0.5, conviction: 'NORMAL', reasoning: 'Ollama unavailable', catalysts: 'N/A', risks: 'N/A' };
    }

    const f = recap.fundamentals;
    const crossLabel = f.golden_cross
      ? 'Golden Cross (SMA50 > SMA200 — Bullish structure)'
      : 'Death Cross (SMA50 < SMA200 — Bearish structure)';
    const posLabel = f.week52_position_pct < 30
      ? `${f.week52_position_pct}% — Near 52-week LOW (potential entry zone)`
      : f.week52_position_pct > 80
        ? `${f.week52_position_pct}% — Near 52-week HIGH (caution on entry)`
        : `${f.week52_position_pct}% — Mid-range`;

    const prompt = `You are a senior long-term equity analyst with a 6-12 month investment horizon.
Think like Warren Buffett, not a day trader. Ignore short-term noise.

STOCK: ${recap.name} (${recap.symbol})
Price: $${recap.price} | PE: ${recap.pe_ratio > 0 ? recap.pe_ratio : 'N/A'} | Cap: ${recap.market_cap}

LONG-TERM STRUCTURE:
- SMA50: $${f.sma50} | SMA200: $${f.sma200}
- Trend: ${crossLabel}
- 52-Week Range: $${f.week52_low} — $${f.week52_high}
- Position: ${posLabel}
- 1-Year Return: ${f.year_return_pct > 0 ? '+' : ''}${f.year_return_pct}%

DECISIONS:
- ACCUMULATE: Good business at a reasonable price, worth building a position
- HOLD: Decent but not ideal entry, or not enough conviction
- REDUCE: Overvalued, broken trend, or fundamental concern

Respond with ONLY valid JSON:
{
  "action": "ACCUMULATE" | "HOLD" | "REDUCE",
  "confidence": 0.0 to 1.0,
  "conviction": "NORMAL" | "HIGH",
  "reasoning": "2-3 sentences referencing the specific numbers above",
  "catalysts": "1-2 sentences on what could drive price higher in 6-12 months",
  "risks": "1 sentence on the main risk to this view"
}`.trim();

    return this.breaker.execute(async () => {
      const response = await axios.post(`${config.OLLAMA_URL}/api/generate`, {
        model: config.OLLAMA_MODEL,
        prompt,
        stream: false,
        format: 'json',
        options: { temperature: 0.2, num_predict: 350 },
      }, { timeout: 60000 });

      const content = response.data?.response || '{}';
      const parsed = JSON.parse(content);
      return {
        action: ['ACCUMULATE', 'HOLD', 'REDUCE'].includes(parsed.action) ? parsed.action : 'HOLD',
        confidence: Math.max(0, Math.min(1, parsed.confidence ?? 0.5)),
        conviction: ['NORMAL', 'HIGH'].includes(parsed.conviction) ? parsed.conviction : 'NORMAL',
        reasoning: parsed.reasoning || 'No reasoning provided',
        catalysts: parsed.catalysts || 'N/A',
        risks: parsed.risks || 'N/A',
      };
    });
  }

  /**
   * Generates a high-level AI review of the past week's performance.
   * Acts as a "Chief Investment Officer" reporting to the user.
   */
  public async analyzeWeeklyPerformance(
    stats: any, 
    watchlist: string[], 
    bestReasoning?: string, 
    worstReasoning?: string
  ): Promise<string> {
    if (!await this.isReady()) return "AI Performance Review unavailable: Ollama is offline.";

    const prompt = `
      You are the Chief Investment Officer (CIO) for MooPredict AI.
      Review the performance of our automated trading bot over the past 7 days and provide a "chill" but professional executive summary.

      WEEKLY PERFORMANCE DATA:
      - Total Signals: ${stats.total_signals}
      - Record: ${stats.wins} Wins, ${stats.losses} Losses, ${stats.scratches} Scratches
      - Win Rate: ${stats.win_rate.toFixed(1)}%
      - Total PnL across all signals: ${stats.total_pnl.toFixed(2)}%
      - Average PnL per signal: ${stats.avg_pnl.toFixed(2)}%
      
      TOP PERFORMER:
      - Symbol: ${stats.best_trade ? stats.best_trade.symbol : 'N/A'}
      - PnL: ${stats.best_trade ? stats.best_trade.pnl_pct : '0'}%
      - Original Rationale: ${bestReasoning || 'No data available'}

      WORST PERFORMER:
      - Symbol: ${stats.worst_trade ? stats.worst_trade.symbol : 'N/A'}
      - PnL: ${stats.worst_trade ? stats.worst_trade.pnl_pct : '0'}%
      - Original Rationale: ${worstReasoning || 'No data available'}

      CURRENT WATCHLIST:
      [${watchlist.join(', ')}]

      YOUR TASK:
      1. Briefly comment on the win rate and PnL.
      2. Perform a "Post-Mortem" on the Top and Worst trades. Compare the "Original Rationale" to the actual result.
      3. Recommend whether to KEEP or CLEAN the current watchlist for Monday's open.
      4. Suggest 1 tactical improvement for next week based on why the worst trade failed.

      TONE: "Chill", "Confident", "Encouraging". Use 1-2 emojis. 
      Limit to 250 words.
    `.trim();

    try {
      const response = await this.breaker.execute(async () => {
        return axios.post(`${config.OLLAMA_URL}/api/generate`, {
          model: config.OLLAMA_MODEL,
          prompt,
          stream: false,
          options: { temperature: 0.7, num_predict: 600 }
        }, { timeout: 60000 });
      });

      return response.data?.response || "AI could not generate a review summary.";
    } catch (error: any) {
      logger.error("[OllamaService] Weekly performance review failed", { error: error.message });
      return "AI was too busy at the virtual beach to finish the report. 🏖️";
    }
  }

  /**
   * Generate a beginner-friendly lesson based on US paper trading performance.
   */
  public async analyzeDailyPerformance(
    gains: any[],
    losses: any[]
  ): Promise<string> {
    if (!await this.isReady()) return "AI Coach is currently offline. Keep practicing and stay disciplined!";

    const prompt = `
      You are a friendly, encouraging Stock Trading Coach for a beginner.
      Your task is to review yesterday's US Paper Trading results and provide ONE clear, easy-to-understand lesson.

      YESTERDAY'S RESULTS:
      - Profitable Trades: ${gains.length > 0 ? gains.map(g => `${g.symbol} (+$${g.pnl})`).join(', ') : 'None'}
      - Losing Trades: ${losses.length > 0 ? losses.map(l => `${l.symbol} (-$${Math.abs(l.pnl)})`).join(', ') : 'None'}

      YOUR TASK:
      1. Analyze the pattern of why we won or lost (e.g., chasing breakouts, tight stops, market volatility).
      2. Write ONE clear lesson (max 2 sentences) in a "Coach" tone. 
      3. Use simple words. Avoid jargon like 'stochastic' or 'fibonacci'.
      4. Make it feel like a "Daily Learning".

      Example: "Today I learned that even if a stock looks like a rocket ship, we should wait for a small pull-back before buying. This helps us avoid buying at the very top!"

      Respond with ONLY the lesson text.
    `.trim();

    try {
      const response = await this.breaker.execute(async () => {
        return axios.post(`${config.OLLAMA_URL}/api/generate`, {
          model: config.OLLAMA_MODEL,
          prompt,
          stream: false,
          options: { temperature: 0.7, num_predict: 150 }
        }, { timeout: 60000 });
      });

      return response.data?.response?.trim() || "Every day is a learning day. Keep a cool head and stick to the strategy!";
    } catch (error: any) {
      logger.error("[OllamaService] HK daily lesson generation failed", { error: error.message });
      return "Sometimes the best lesson is patience. Keep observing the markets!";
    }
  }

  /**
   * Evaluate whether to exit (SELL) or hold an open position.
   */
  public async evaluateExitPosition(
    stock: any,
    entryContext: any,
    computed: any
  ): Promise<{ action: 'HOLD' | 'SELL'; confidence: number; reason: string }> {
    if (!await this.isReady()) {
      return { action: 'HOLD', confidence: 0.5, reason: 'Ollama unavailable' };
    }

    const prompt = `You are a risk management agent. Evaluate if this open trade should be CLOSED or held.

## Entry Context
- Entry Price: $${entryContext.snapshot_price || 'N/A'}
- Recommendation at entry: ${entryContext.recommendation || 'N/A'}
- Entry RSI: ${entryContext.indicators?.rsi || 'N/A'}
- Entry MACD: ${entryContext.indicators?.macd_trend || 'N/A'}

## Current Market
- Symbol: ${stock.symbol}
- Current Price: $${stock.price}
- RSI (14): ${computed.rsi}
- MACD Trend: ${computed.macd_trend} (histogram: ${computed.macd_histogram})
- BB Position: ${computed.bb_position}
- Price vs SMA 20: ${computed.price_vs_sma20}

If indicators significantly deteriorated (RSI < 40, MACD turned bearish, price broke below SMA20), recommend SELL. Otherwise HOLD.

Respond with ONLY valid JSON:
{"action":"HOLD"|"SELL","confidence":0.5,"reason":"1-2 sentences"}`.trim();

    return this.breaker.execute(async () => {
      const response = await axios.post(`${config.OLLAMA_URL}/api/generate`, {
        model: config.OLLAMA_MODEL,
        prompt,
        stream: false,
        format: 'json',
        options: { temperature: 0.1, num_predict: 150 },
      }, { timeout: 60000 });

      const content = response.data?.response || '{}';
      const parsed = JSON.parse(content);
      return {
        action: parsed.action === 'SELL' ? 'SELL' : 'HOLD',
        confidence: Math.max(0.5, Math.min(0.9, parsed.confidence ?? 0.5)),
        reason: parsed.reason || 'No reason provided',
      };
    });
  }

  /**
   * Generic JSON generator for agents
   */
  public async generateJSON(prompt: string, timeoutMs: number = 60000): Promise<any> {
    if (!await this.isReady()) {
      return null;
    }

    try {
      const response = await axios.post(`${config.OLLAMA_URL}/api/generate`, {
        model: config.OLLAMA_MODEL,
        prompt,
        stream: false,
        format: "json",
        options: { temperature: 0.1, num_predict: 500 }
      }, { timeout: 120000 });

      const content = response.data?.response || "";
      return JSON.parse(content);
    } catch (error) {
      logger.error("[OllamaService] generateJSON failed:", error);
      return null;
    }
  }

  public getStatus(): { available: boolean; url: string; defaultModel: string } {
    return {
      available: this.isAvailable,
      url: config.OLLAMA_URL,
      defaultModel: config.OLLAMA_MODEL
    };
  }
}

export const ollamaService = new OllamaService();
