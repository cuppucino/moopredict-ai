import { GoogleGenerativeAI } from "@google/generative-ai";
import dotenv from "dotenv";
import * as ti from "technicalindicators";
const { RSI, MACD, BollingerBands, StochasticRSI, ATR } = ti as any;
import { ollamaService } from "./ollamaService";
import { logger } from "../core/Logger";

dotenv.config();

const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY || "");

export interface OHLCVPoint {
  date: string;
  price: number;
  open?: number;
  high?: number;
  low?: number;
  volume?: number;
}

export interface StockData {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  high: number;
  low: number;
  volume: number;
  marketCap: string;
  market: string;
  peRatio: number;
  history: OHLCVPoint[];
}

export interface ComputedIndicators {
  rsi: number;
  stoch_rsi_k: number;
  stoch_rsi_d: number;
  macd_trend: string;
  macd_histogram: number;
  bb_position: string; // "ABOVE_UPPER" | "NEAR_UPPER" | "MIDDLE" | "NEAR_LOWER" | "BELOW_LOWER"
  bb_width: number;
  bb_squeeze: boolean;
  market_structure: 'Bullish' | 'Bearish' | 'Sideways';
  mss_detected: boolean;
  nearest_resistance_pct: number;
  nearest_support_pct: number;
  price_vs_sma20: string;
  price_vs_sma50: string;
  atr: number; // Average True Range — for stop-loss / take-profit sizing
}

export interface PredictionResult {
  symbol: string;
  recommendation: "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL";
  confidence: number;
  targetPrice: number;
  stop_loss: number;   // ATR-based: entry - 2 * ATR
  take_profit: number; // ATR-based: entry + 3 * ATR (3:1 risk/reward)
  price?: number;      // Price at time of prediction
  analysis: string;
  technicalIndicators: {
    rsi: number;
    stoch_rsi: string;
    macd: string;
    movingAverage: string;
    atr: number;
  };
  riskLevel: "LOW" | "MEDIUM" | "HIGH";
  isScalp?: boolean;
}

export interface ConfluenceResult {
  action: 'BUY' | 'SELL' | 'HOLD';
  confidence: number;       // 0.0–1.0, derived from bull/bear score ratio
  bull_score: number;       // raw bullish point count
  bear_score: number;       // raw bearish point count
  signal_count: number;     // total points considered
}

// --- Prediction cache (5-minute TTL, keyed by symbol) ---
interface CacheEntry {
  result: PredictionResult;
  expires_at: number;
}
const prediction_cache = new Map<string, CacheEntry>();
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

const compute_sma = (prices: number[], period: number): number => {
  if (prices.length < period) return prices[prices.length - 1] ?? 0;
  const slice = prices.slice(-period);
  return slice.reduce((sum, p) => sum + p, 0) / period;
};

interface PivotResult {
  swingHighs: number[];
  swingLows: number[];
}

function detect_pivots(history: OHLCVPoint[], lookback: number): PivotResult {
  if (history.length < 10) return { swingHighs: [], swingLows: [] };
  const slice = history.slice(-lookback);
  const highs = slice.map(h => h.high ?? h.price);
  const lows  = slice.map(h => h.low  ?? h.price);
  const swingHighs: number[] = [];
  const swingLows:  number[] = [];
  for (let i = 2; i < slice.length - 2; i++) {
    if (highs[i] > highs[i-2] && highs[i] > highs[i-1] && highs[i] > highs[i+1] && highs[i] > highs[i+2])
      swingHighs.push(highs[i]);
    if (lows[i] < lows[i-2] && lows[i] < lows[i-1] && lows[i] < lows[i+1] && lows[i] < lows[i+2])
      swingLows.push(lows[i]);
  }
  return { swingHighs, swingLows };
}

export const compute_indicators = (history: OHLCVPoint[]): ComputedIndicators => {
  const closes = history.map((h) => h.price);
  const highs  = history.map((h) => h.high ?? h.price);
  const lows   = history.map((h) => h.low  ?? h.price);

  // RSI (14-period)
  let rsi_value = 50;
  try {
    const rsi_results = RSI.calculate({ values: closes, period: 14 });
    rsi_value = rsi_results.length > 0 ? parseFloat(rsi_results[rsi_results.length - 1].toFixed(2)) : 50;
  } catch { /* keep default */ }

  // Stochastic RSI (14/14/3/3)
  let stoch_rsi_k = 50;
  let stoch_rsi_d = 50;
  try {
    const srsi_results = StochasticRSI.calculate({
      values: closes,
      rsiPeriod: 14,
      stochasticPeriod: 14,
      kPeriod: 3,
      dPeriod: 3,
    });
    if (srsi_results.length > 0) {
      const latest_srsi = srsi_results[srsi_results.length - 1];
      stoch_rsi_k = parseFloat((latest_srsi.k ?? 50).toFixed(2));
      stoch_rsi_d = parseFloat((latest_srsi.d ?? 50).toFixed(2));
    }
  } catch { /* keep default */ }

  // ATR (14-period) — used for stop-loss and take-profit
  let atr_value = 0;
  try {
    const atr_results = ATR.calculate({ high: highs, low: lows, close: closes, period: 14 });
    atr_value = atr_results.length > 0 ? parseFloat(atr_results[atr_results.length - 1].toFixed(4)) : 0;
  } catch { /* keep default */ }

  // MACD (12/26/9)
  let macd_trend = "Neutral";
  let macd_histogram = 0;
  try {
    const macd_results = MACD.calculate({
      values: closes,
      fastPeriod: 12,
      slowPeriod: 26,
      signalPeriod: 9,
      SimpleMAOscillator: false,
      SimpleMASignal: false,
    });
    if (macd_results.length > 0) {
      const latest = macd_results[macd_results.length - 1];
      macd_histogram = parseFloat((latest.histogram ?? 0).toFixed(4));
      macd_trend = macd_histogram > 0 ? (macd_histogram > 0.5 ? "Bullish Crossover" : "Bullish") : macd_histogram < -0.5 ? "Bearish Crossover" : "Bearish";
    }
  } catch { /* keep default */ }

  // Bollinger Bands (20-period, 2 std dev)
  let bb_position = "MIDDLE";
  let bb_width_value = 0;
  let bb_squeeze_value = false;
  try {
    const bb_results = BollingerBands.calculate({ values: closes, period: 20, stdDev: 2 });
    if (bb_results.length > 0) {
      const latest_bb = bb_results[bb_results.length - 1];
      const current_price = closes[closes.length - 1];
      const band_range = latest_bb.upper - latest_bb.lower;
      const position_pct = band_range > 0 ? (current_price - latest_bb.lower) / band_range : 0.5;
      if (position_pct > 1.0) bb_position = "ABOVE_UPPER";
      else if (position_pct > 0.8) bb_position = "NEAR_UPPER";
      else if (position_pct < 0.0) bb_position = "BELOW_LOWER";
      else if (position_pct < 0.2) bb_position = "NEAR_LOWER";
      else bb_position = "MIDDLE";

      if (latest_bb.middle > 0) {
        bb_width_value = parseFloat(((latest_bb.upper - latest_bb.lower) / latest_bb.middle).toFixed(4));
        bb_squeeze_value = bb_width_value < 0.04;
      }
    }
  } catch { /* keep default */ }

  let market_structure: 'Bullish' | 'Bearish' | 'Sideways' = 'Sideways';
  let mss_detected = false;
  let nearest_resistance_pct = 5.0;
  let nearest_support_pct = 5.0;
  let pivots: PivotResult | null = null;

  try {
    if (history.length >= 10) {
      pivots = detect_pivots(history, 50);
      const { swingHighs, swingLows } = pivots;
      if (swingHighs.length >= 2 && swingLows.length >= 2) {
        const hh = swingHighs[swingHighs.length - 1] > swingHighs[swingHighs.length - 2];
        const hl = swingLows[swingLows.length - 1]   > swingLows[swingLows.length - 2];
        const lh = swingHighs[swingHighs.length - 1] < swingHighs[swingHighs.length - 2];
        const ll = swingLows[swingLows.length - 1]   < swingLows[swingLows.length - 2];
        if (hh && hl) market_structure = 'Bullish';
        else if (lh && ll) market_structure = 'Bearish';

        if (swingHighs.length >= 3 && swingLows.length >= 3) {
          const prevWasBullish = swingHighs[swingHighs.length - 2] > swingHighs[swingHighs.length - 3]
                              && swingLows[swingLows.length - 2]   > swingLows[swingLows.length - 3];
          const prevWasBearish = swingHighs[swingHighs.length - 2] < swingHighs[swingHighs.length - 3]
                              && swingLows[swingLows.length - 2]   < swingLows[swingLows.length - 3];
          if (prevWasBearish && market_structure === 'Bullish') mss_detected = true;
          if (prevWasBullish && market_structure === 'Bearish') mss_detected = true;
        }
      }
    }
  } catch { /* keep default */ }

  try {
    if (pivots && history.length >= 10) {
      const currentPrice = closes[closes.length - 1];
      const resistanceLevels = pivots.swingHighs.filter(h => h > currentPrice);
      const supportLevels    = pivots.swingLows.filter(l => l < currentPrice);
      if (resistanceLevels.length > 0) {
        const r = Math.min(...resistanceLevels);
        nearest_resistance_pct = parseFloat((((r - currentPrice) / currentPrice) * 100).toFixed(2));
      }
      if (supportLevels.length > 0) {
        const s = Math.max(...supportLevels);
        nearest_support_pct = parseFloat((((currentPrice - s) / currentPrice) * 100).toFixed(2));
      }
    }
  } catch { /* keep default */ }

  // SMA comparison
  const sma20 = compute_sma(closes, 20);
  const sma50 = compute_sma(closes, 50);
  const current = closes[closes.length - 1];
  const price_vs_sma20 = current > sma20 ? `ABOVE ($${sma20.toFixed(2)})` : `BELOW ($${sma20.toFixed(2)})`;
  const price_vs_sma50 = current > sma50 ? `ABOVE ($${sma50.toFixed(2)})` : `BELOW ($${sma50.toFixed(2)})`;

  return { 
    rsi: rsi_value, 
    stoch_rsi_k, 
    stoch_rsi_d, 
    macd_trend, 
    macd_histogram, 
    bb_position, 
    bb_width: bb_width_value,
    bb_squeeze: bb_squeeze_value,
    market_structure,
    mss_detected,
    nearest_resistance_pct,
    nearest_support_pct,
    price_vs_sma20, 
    price_vs_sma50, 
    atr: atr_value 
  };
};

export function score_confluence(t: ComputedIndicators, volume_ratio: number): ConfluenceResult {
  let bull = 0, bear = 0;

  // RSI (weight: 2 — strong reversal signal)
  if (t.rsi < 35)  bull += 2;
  if (t.rsi > 65)  bear += 2;

  // MACD (weight: 1)
  if (t.macd_trend.includes('Bullish')) bull += 1;
  if (t.macd_trend.includes('Bearish')) bear += 1;

  // Bollinger Band position (weight: 1)
  if (t.bb_position === 'NEAR_LOWER' || t.bb_position === 'BELOW_LOWER') bull += 1;
  if (t.bb_position === 'NEAR_UPPER' || t.bb_position === 'ABOVE_UPPER') bear += 1;

  // Market Structure (weight: 2 — trend context is high value)
  if (t.market_structure === 'Bullish') bull += 2;
  if (t.market_structure === 'Bearish') bear += 2;

  // Market Structure Shift — penalize entering on a reversal signal
  if (t.mss_detected) bear += 1;

  // BB Squeeze — breakout loading, slight bullish bias (momentum incoming)
  if (t.bb_squeeze) bull += 1;

  // S&R proximity (weight: 1 each)
  if (t.nearest_support_pct < 1.5)    bull += 1;  // floor very close below
  if (t.nearest_resistance_pct < 1.5) bear += 1;  // wall very close above

  // Volume confirmation (weight: 1)
  if (volume_ratio > 1.5) bull += 1;  // high volume supports move
  if (volume_ratio < 0.5) bear += 1;  // low volume, weak move

  const total = bull + bear;
  const confidence = total > 0
    ? parseFloat((Math.max(bull, bear) / total).toFixed(2))
    : 0.5;

  const MIN_CONFIDENCE = 0.62;  // minimum to act

  if (bull > bear && confidence >= MIN_CONFIDENCE) return { action: 'BUY',  confidence, bull_score: bull, bear_score: bear, signal_count: total };
  if (bear > bull && confidence >= MIN_CONFIDENCE) return { action: 'SELL', confidence, bull_score: bull, bear_score: bear, signal_count: total };
  return { action: 'HOLD', confidence: 0.5, bull_score: bull, bear_score: bear, signal_count: total };
}

export const predict_stock = async (
  stock: StockData,
  news_headlines?: string[],
): Promise<PredictionResult> => {
  // --- Check cache first ---
  const cache_key = stock.symbol.toUpperCase();
  const cached = prediction_cache.get(cache_key);
  if (cached && Date.now() < cached.expires_at) {
    console.log(`[AIService] Cache hit for ${cache_key}`);
    return cached.result;
  }

  let computed: ComputedIndicators;
  try {
    computed = compute_indicators(stock.history);
  } catch (err) {
    computed = {
      rsi: 50,
      stoch_rsi_k: 50,
      stoch_rsi_d: 50,
      macd_trend: "Neutral",
      macd_histogram: 0,
      bb_position: "MIDDLE",
      price_vs_sma20: "N/A",
      price_vs_sma50: "N/A",
      atr: 0,
      bb_width: 0,
      bb_squeeze: false,
      market_structure: 'Sideways' as const,
      mss_detected: false,
      nearest_resistance_pct: 5.0,
      nearest_support_pct: 5.0,
    };
  }

  const atr = computed.atr > 0 ? computed.atr : stock.price * 0.02;

  const compute_levels = (rec: string) => {
    const isBearish = rec === 'SELL' || rec === 'STRONG_SELL';
    return {
      stop_loss:   parseFloat((isBearish ? stock.price + 2 * atr : stock.price - 2 * atr).toFixed(4)),
      take_profit: parseFloat((isBearish ? stock.price - 3 * atr : stock.price + 3 * atr).toFixed(4)),
    };
  };

  // --- Primary Choice: Local Ollama (Llama 3.2) ---
  try {
    const ollamaRaw = await ollamaService.predictFromTechnical(stock, computed, news_headlines || []);
    if (ollamaRaw) {
      console.log(`[AIService] Using LOCAL OLLAMA analysis for ${stock.symbol}`);
      const rec = ollamaRaw.recommendation || "HOLD";
      const { stop_loss, take_profit } = compute_levels(rec);
      const result: PredictionResult = {
        symbol: stock.symbol,
        recommendation: rec,
        confidence: parseFloat(Math.min(0.85, Math.max(0.40, ollamaRaw.confidence || 0.5)).toFixed(2)),
        targetPrice: take_profit,
        stop_loss,
        take_profit,
        price: stock.price,
        analysis: `${ollamaRaw.analysis || 'Analysis complete.'} (Generated by Local Llama 3.2)`,
        technicalIndicators: {
          rsi: computed.rsi,
          stoch_rsi: `K=${computed.stoch_rsi_k} / D=${computed.stoch_rsi_d}`,
          macd: computed.macd_trend,
          movingAverage: computed.price_vs_sma20,
          atr: computed.atr,
        },
        riskLevel: ollamaRaw.riskLevel || "MEDIUM",
      };
      
      prediction_cache.set(cache_key, { result, expires_at: Date.now() + CACHE_TTL_MS });
      return result;
    }
  } catch (err) {
    logger.warn(`[AIService] Local Ollama failed for ${stock.symbol}, falling back to Gemini...`);
  }

  try {
    // QW1: Upgraded to gemini-2.0-flash — faster + cheaper than gemini-1.5-pro
    const model = genAI.getGenerativeModel({ model: "gemini-2.0-flash" });

    const recent_history = stock.history.slice(-30);
    const news_section =
      news_headlines && news_headlines.length > 0
        ? `\nRecent News Headlines:\n${news_headlines.map((h, i) => `  ${i + 1}. ${h}`).join("\n")}`
        : "";

    const prompt = `
You are a quantitative stock analyst. Analyze the following REAL market data and indicators for ${stock.name} (${stock.symbol}) and provide a precise prediction.

## Current Market Snapshot
- Current Price: $${stock.price}
- 24h Change: ${stock.changePercent}% ($${stock.change})
- 24h High / Low: $${stock.high} / $${stock.low}
- Volume: ${stock.volume.toLocaleString()}
- Market Cap: ${stock.marketCap}
- P/E Ratio: ${stock.peRatio}

## Computed Technical Indicators (calculated from real price data)
- RSI (14): ${computed.rsi} — ${computed.rsi > 70 ? "OVERBOUGHT" : computed.rsi < 30 ? "OVERSOLD" : "NEUTRAL"}
- Stochastic RSI: K=${computed.stoch_rsi_k}, D=${computed.stoch_rsi_d} — ${computed.stoch_rsi_k > 80 ? "OVERBOUGHT" : computed.stoch_rsi_k < 20 ? "OVERSOLD" : "NEUTRAL"}
- MACD Trend: ${computed.macd_trend} (histogram: ${computed.macd_histogram})
- Bollinger Band Position: ${computed.bb_position}
- Price vs SMA 20: ${computed.price_vs_sma20}
- Price vs SMA 50: ${computed.price_vs_sma50}
- ATR (14): ${computed.atr}

## Price History (last 30 trading days)
${JSON.stringify(recent_history.map((h) => ({ date: h.date, close: h.price })))}
${news_section}

Based ONLY on the real data above, output a JSON object:
{
  "symbol": "${stock.symbol}",
  "recommendation": "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL",
  "confidence": <0.40-0.85 float — be calibrated, do NOT always return above 0.75>,
  "targetPrice": <realistic 30-day price target as a number>,
  "analysis": "<2-3 sentences referencing the specific RSI, Stochastic RSI, MACD, and BB values above>",
  "technicalIndicators": {
    "rsi": ${computed.rsi},
    "stoch_rsi": "K=${computed.stoch_rsi_k} / D=${computed.stoch_rsi_d}",
    "macd": "${computed.macd_trend}",
    "movingAverage": "<describe price position vs SMA20 and SMA50>",
    "atr": ${computed.atr}
  },
  "riskLevel": "LOW" | "MEDIUM" | "HIGH"
}

Output ONLY valid JSON. No markdown, no extra text.
    `.trim();

    const result = await model.generateContent(prompt);
    const response = await result.response;
    const text = response.text();
    const json_content = text.replace(/```json|```/g, "").trim();
    const parsed = JSON.parse(json_content) as PredictionResult;

    // Always override computed values for accuracy — never trust LLM to recalculate
    parsed.technicalIndicators.rsi = computed.rsi;
    parsed.technicalIndicators.atr = computed.atr;
    const { stop_loss, take_profit } = compute_levels(parsed.recommendation);
    parsed.stop_loss   = stop_loss;
    parsed.take_profit = take_profit;
    parsed.targetPrice = take_profit;
    parsed.price       = stock.price;

    // QW4: Clamp confidence — prevents LLM from hallucinating always-high confidence
    parsed.confidence = parseFloat(Math.min(0.85, Math.max(0.40, parsed.confidence)).toFixed(2));

    // Store in cache
    prediction_cache.set(cache_key, { result: parsed, expires_at: Date.now() + CACHE_TTL_MS });

    return parsed;
  } catch (error) {
    console.error("AI Prediction Error:", error);
    const { stop_loss: fb_sl, take_profit: fb_tp } = compute_levels("HOLD");
    const fallback: PredictionResult = {
      symbol: stock.symbol,
      recommendation: "HOLD",
      confidence: 0.50,
      targetPrice: stock.price,
      stop_loss: fb_sl,
      take_profit: fb_tp,
      price: stock.price,
      analysis: "AI analysis unavailable. Please retry.",
      technicalIndicators: {
        rsi: computed.rsi,
        stoch_rsi: `K=${computed.stoch_rsi_k} / D=${computed.stoch_rsi_d}`,
        macd: computed.macd_trend,
        movingAverage: computed.price_vs_sma20,
        atr: computed.atr,
      },
      riskLevel: "MEDIUM",
    };
    return fallback;
  }
};

/**
 * Specialized AI logic for evaluating an open position vs its entry context.
 */
export const get_exit_advice = async (
  stock: StockData,
  entryContext: any,
): Promise<{ action: 'HOLD' | 'SELL'; confidence: number; reason: string }> => {
  const computed = compute_indicators(stock.history);

  // Try Ollama first (local, no quota limits)
  try {
    if (await ollamaService.isReady()) {
      const result = await ollamaService.evaluateExitPosition(stock, entryContext, computed);
      logger.info(`[AIService] Exit advice via Ollama for ${stock.symbol}: ${result.action}`);
      return result;
    }
  } catch (ollamaErr) {
    logger.warn(`[AIService] Ollama exit advice failed for ${stock.symbol}, falling back to Gemini...`);
  }

  try {
    const model = genAI.getGenerativeModel({ model: "gemini-2.0-flash" });

    const prompt = `
You are a risk management agent for a professional trader. Evaluate if the following trade should be CLOSED or held.

## Trade Entry Context (The "Then")
- Entry Price: $${entryContext.snapshot_price || 'N/A'}
- Recommendation at entry: ${entryContext.recommendation || 'N/A'}
- Entry RSI: ${entryContext.indicators?.rsi || 'N/A'}
- Entry MACD: ${entryContext.indicators?.macd_trend || 'N/A'}

## Current Market Reality (The "Now")
- Symbol: ${stock.symbol}
- Current Price: $${stock.price}
- Current RSI (14): ${computed.rsi}
- Current MACD Trend: ${computed.macd_trend} (histogram: ${computed.macd_histogram})
- Current BB Position: ${computed.bb_position}
- Price vs SMA 20: ${computed.price_vs_sma20}

## Task
Compare the "Then" vs the "Now". 
If the technical indicators have significantly deteriorated (e.g. RSI fell below 40, MACD turned bearish, or price broke below SMA20), or if the price is near a logical resistance zone, recommend SELL.
Otherwise, recommend HOLD.

Output ONLY a JSON object:
{
  "action": "HOLD" | "SELL",
  "confidence": <0.5-0.9 float>,
  "reason": "<1-2 sentences explaining why the original thesis is still valid or has broken>"
}
    `.trim();

    const result = await model.generateContent(prompt);
    const response = await result.response;
    const text = response.text().replace(/```json|```/g, "").trim();
    return JSON.parse(text);
  } catch (error) {
    logger.error("AI Exit Advice Error:", error);
    return { action: 'HOLD', confidence: 0.5, reason: "AI reasoning failed. Defaulting to HOLD." };
  }
};
