import { predict_stock, PredictionResult, StockData } from "./aiService";
import { ollamaService } from "./ollamaService";
import { newsService } from "./newsService";
import { socialService } from "./socialService";
import { mlServiceClient } from "./mlServiceClient";
import { query } from "../db/database";

interface EnsembleInput {
  geminiPrediction: PredictionResult;
  ollamaPrediction?: {
    direction: "UP" | "DOWN" | "FLAT";
    confidence: number;
    reasoning: string;
  };
  mlResults?: any;
  newsSentiment: number;
  socialSentiment: number;
  technicalTrend: string;
}

interface EnsembleWeights {
  chronos: number;
  tft: number;
  xgboost: number;
  gemini: number;
  ollama: number;
  news: number;
  social: number;
  technical?: number;
}

// ── Prediction cache ─────────────────────────────────────────────────────────

interface CacheEntry {
  result: PredictionResult;
  expires: number;
}

const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

// ─────────────────────────────────────────────────────────────────────────────

export class EnsemblePredictor {
  private weights: EnsembleWeights = {
    chronos: 0.25,
    tft: 0.20,
    xgboost: 0.15,
    gemini: 0.15,
    ollama: 0.10,
    news: 0.08,
    social: 0.07
  };

  private useOllama = false;
  private cache = new Map<string, CacheEntry>();

  constructor() {
    // Migration moved to explicit init
  }

  private async loadConfig(): Promise<void> {
    const result = await query('SELECT value FROM system_config WHERE key = $1', ['ollama_enabled']);
    const config = result.rows[0];
    this.useOllama = config?.value === 'true';

    // Load persisted ensemble weights if present
    const weightsRow = await query('SELECT value FROM system_config WHERE key = $1', ['ensemble_weights']);
    if (weightsRow.rows[0]?.value) {
      try {
        const saved = JSON.parse(weightsRow.rows[0].value) as EnsembleWeights;
        this.weights = { ...this.weights, ...saved };
        console.log('[EnsemblePredictor] Loaded calibrated weights from DB:', this.weights);
      } catch {
        // malformed — keep defaults
      }
    }
  }

  /**
   * Get enhanced prediction using ensemble of models
   * Priority: XGBoost Python > Gemini > Ollama > Rule-based
   * Results are cached per symbol for CACHE_TTL_MS to avoid redundant API calls.
   */
  public async getPrediction(
    stockData: StockData,
    newsHeadlines?: string[]
  ): Promise<PredictionResult> {
    // Cache hit
    const cached = this.cache.get(stockData.symbol);
    if (cached && Date.now() < cached.expires) {
      console.log(`[EnsemblePredictor] Cache hit for ${stockData.symbol}`);
      return cached.result;
    }

    // Ensure config is loaded
    await this.loadConfig();
    
    // Try Python ML service first (XGBoost)
    const newsSummary = await newsService.getSentimentSummary(stockData.symbol);
    const socialSummary = await socialService.getSentimentSummary(stockData.symbol);
    
    const mlPrediction = await mlServiceClient.predict(
      stockData.symbol,
      stockData,
      newsSummary.avgScore,
      socialSummary.avgScore
    );

    // Get Gemini prediction
    const geminiResult = await predict_stock(stockData, newsHeadlines);

    // Get Ollama prediction if available
    let ollamaResult: EnsembleInput["ollamaPrediction"] | undefined;
    if (this.useOllama && await ollamaService.isReady()) {
      ollamaResult = await ollamaService.predictFromTechnical(
        stockData,
        {
          rsi: geminiResult.technicalIndicators.rsi,
          macd_trend: geminiResult.technicalIndicators.macd,
          bb_position: geminiResult.technicalIndicators.rsi > 70 ? "ABOVE_UPPER" : 
            geminiResult.technicalIndicators.rsi < 30 ? "BELOW_LOWER" : "MIDDLE",
          bb_squeeze: false,
          bb_width: 0,
          market_structure: "Sideways",
          mss_detected: false,
          nearest_resistance_pct: 0,
          nearest_support_pct: 0,
          price_vs_sma20: geminiResult.technicalIndicators.movingAverage
        },
        newsHeadlines || []
      );
    }

    // Build ensemble input
    const ensembleInput: EnsembleInput = {
      geminiPrediction: geminiResult,
      ollamaPrediction: ollamaResult,
      mlResults: mlPrediction?.all_results,
      newsSentiment: newsSummary.avgScore,
      socialSentiment: socialSummary.avgScore,
      technicalTrend: stockData.changePercent > 2 ? "strong_up" : 
        stockData.changePercent > 0 ? "up" : 
        stockData.changePercent > -2 ? "down" : "strong_down"
    };

    // Calculate ensemble score
    const ensembleScore = this.calculateEnsembleScore(ensembleInput);

    // Adjust Gemini result based on ensemble
    const result = this.adjustPrediction(geminiResult, ensembleScore, ensembleInput);

    // --- Bug 1: Correct Adaptive Targets from Chronos ---
    if (mlPrediction?.all_results?.chronos) {
      const chronos = mlPrediction.all_results.chronos;
      const forecast = chronos.forecast;
      const isDown = chronos.direction === "DOWN";
      const price = stockData.price;

      result.targetPrice = isDown ? (forecast?.p10 ?? price * 0.95) : (forecast?.p90 ?? price * 1.05);
      result.stop_loss = isDown ? (forecast?.p90 ?? price * 1.05) : (forecast?.p10 ?? price * 0.95);
      result.take_profit = isDown ? (forecast?.p10 ?? price * 0.90) : (forecast?.p90 ?? price * 1.10);
    }

    // --- Bug 2: Null Guards & Technical Indicators ---
    if (mlPrediction) {
      const features = mlPrediction.features;
      result.technicalIndicators = {
        rsi: features.rsi_14 ?? geminiResult.technicalIndicators.rsi,
        stoch_rsi: "N/A",
        macd: features.momentum_5 != null ? (features.momentum_5 > 0 ? 'Bullish' : 'Bearish') : geminiResult.technicalIndicators.macd,
        movingAverage: features.price_vs_sma20 != null ? (features.price_vs_sma20 > 0 ? 'Above' : 'Below') + ' SMA20' : geminiResult.technicalIndicators.movingAverage,
        atr: features.atr_14 ?? geminiResult.technicalIndicators.atr
      };

      if (features.volatility_20 != null) {
        result.riskLevel = features.volatility_20 > 0.3 ? "HIGH" : 
                           features.volatility_20 > 0.2 ? "MEDIUM" : "LOW";
        result.isScalp = features.volatility_20 > 0.25 || Math.abs(ensembleScore) > 0.8;
      }
    }

    // Log prediction for accuracy tracking
    await this.logPrediction(stockData.symbol, result, ensembleInput);

    this.cache.set(stockData.symbol, { result, expires: Date.now() + CACHE_TTL_MS });
    return result;
  }

  /**
   * Calculate weighted ensemble score (-1 to 1, negative = bearish, positive = bullish)
   */
  private calculateEnsembleScore(input: EnsembleInput): number {
    let score = 0;
    let totalWeight = 0;

    // Gemini contribution
    const geminiScore = this.recommendationToScore(input.geminiPrediction.recommendation);
    score += geminiScore * input.geminiPrediction.confidence * this.weights.gemini;
    totalWeight += this.weights.gemini;

    // Ollama contribution
    if (input.ollamaPrediction) {
      const ollamaScore = input.ollamaPrediction.direction === "UP" ? 1 : 
        input.ollamaPrediction.direction === "DOWN" ? -1 : 0;
      score += ollamaScore * input.ollamaPrediction.confidence * this.weights.ollama;
      totalWeight += this.weights.ollama;
    }

    // News sentiment contribution
    score += input.newsSentiment * this.weights.news;
    totalWeight += this.weights.news;

    // Social sentiment contribution
    score += input.socialSentiment * this.weights.social;
    totalWeight += this.weights.social;

    // Foundation Model: Chronos
    if (input.mlResults?.chronos) {
      const chronosScore = input.mlResults.chronos.direction === "UP" ? 1 : -1;
      score += chronosScore * input.mlResults.chronos.confidence * this.weights.chronos;
      totalWeight += this.weights.chronos;
    }

    // Foundation Model: TFT
    if (input.mlResults?.tft) {
      const tftScore = input.mlResults.tft.direction === "UP" ? 1 : -1;
      score += tftScore * input.mlResults.tft.confidence * this.weights.tft;
      totalWeight += this.weights.tft;
    }

    // Legacy Model: XGBoost
    if (input.mlResults?.xgboost) {
      const xgboostScore = input.mlResults.xgboost.direction === "UP" ? 1 : -1;
      score += xgboostScore * input.mlResults.xgboost.confidence * this.weights.xgboost;
      totalWeight += this.weights.xgboost;
    }

    // Technical trend contribution
    const techScore = input.technicalTrend === "strong_up" ? 1 : 
      input.technicalTrend === "up" ? 0.5 : 
      input.technicalTrend === "down" ? -0.5 : -1;
    const techWeight = this.weights.technical || 0.05;
    score += techScore * techWeight;
    totalWeight += techWeight;

    // Normalize
    return totalWeight > 0 ? score / totalWeight : 0;
  }

  /**
   * Convert recommendation string to score
   */
  private recommendationToScore(rec: string): number {
    switch (rec) {
      case "STRONG_BUY": return 1.0;
      case "BUY": return 0.5;
      case "HOLD": return 0;
      case "SELL": return -0.5;
      case "STRONG_SELL": return -1.0;
      default: return 0;
    }
  }

  /**
   * Convert ensemble score back to recommendation
   */
  private scoreToRecommendation(score: number): string {
    if (score >= 0.7) return "STRONG_BUY";
    if (score >= 0.3) return "BUY";
    if (score >= -0.3) return "HOLD";
    if (score >= -0.7) return "SELL";
    return "STRONG_SELL";
  }

  /**
   * Adjust prediction based on ensemble score
   */
  private adjustPrediction(
    base: PredictionResult, 
    ensembleScore: number, 
    input: EnsembleInput
  ): PredictionResult {
    const adjustedRec = this.scoreToRecommendation(ensembleScore);
    
    // Calculate adjusted confidence (combine base + ensemble consensus)
    let adjustedConfidence = base.confidence;
    
    // Boost confidence if models agree
    const geminiScore = this.recommendationToScore(base.recommendation);
    if (Math.sign(geminiScore) === Math.sign(ensembleScore)) {
      adjustedConfidence = Math.min(0.95, adjustedConfidence * 1.1);
    } else {
      // Reduce confidence if models disagree
      adjustedConfidence = adjustedConfidence * 0.8;
    }

    // Build enhanced analysis
    let enhancedAnalysis = base.analysis;
    if (input.newsSentiment > 0.3) {
      enhancedAnalysis += ` Positive news sentiment (${input.newsSentiment.toFixed(2)}) supports bullish outlook.`;
    } else if (input.newsSentiment < -0.3) {
      enhancedAnalysis += ` Negative news sentiment (${input.newsSentiment.toFixed(2)}) suggests caution.`;
    }

    if (input.socialSentiment > 0.4 && input.geminiPrediction.recommendation.includes("BUY")) {
      enhancedAnalysis += ` Strong social media sentiment confirms bullish momentum.`;
    }

    if (input.ollamaPrediction) {
      enhancedAnalysis += ` Local model analysis: ${input.ollamaPrediction.reasoning}`;
    }

    // --- Scalp Mode Logic ---
    const isScalp = input.socialSentiment > 0.5 || (input.geminiPrediction.technicalIndicators && input.geminiPrediction.technicalIndicators.rsi > 75);
    const adjustedAnalysis = (isScalp ? "[SCALP ⚡] " : "") + enhancedAnalysis;

    return {
      ...base,
      recommendation: adjustedRec as any,
      confidence: parseFloat(adjustedConfidence.toFixed(2)),
      analysis: adjustedAnalysis,
      // Pass down scalp flag for template if needed
      isScalp: isScalp
    };

  }

  /**
   * Log ML prediction for accuracy tracking
   */
  private async logMLPrediction(symbol: string, result: PredictionResult, mlPrediction: any): Promise<void> {
    await query(`
      INSERT INTO model_predictions (model_name, symbol, predicted_direction, confidence, features_used)
      VALUES ($1, $2, $3, $4, $5)
    `, [
      'xgboost',
      symbol.toUpperCase(),
      mlPrediction.direction,
      result.confidence,
      JSON.stringify(mlPrediction.features)
    ]);
  }

  /**
   * Log prediction for accuracy tracking
   */
  private async logPrediction(symbol: string, result: PredictionResult, input: EnsembleInput): Promise<void> {
    await query(`
      INSERT INTO model_predictions (model_name, symbol, predicted_direction, confidence, features_used)
      VALUES ($1, $2, $3, $4, $5)
    `, [
      'ensemble',
      symbol.toUpperCase(),
      result.recommendation.includes('BUY') ? 'UP' : 
        result.recommendation.includes('SELL') ? 'DOWN' : 'FLAT',
      result.confidence,
      JSON.stringify({
        gemini: input.geminiPrediction.recommendation,
        ollama: input.ollamaPrediction?.direction,
        news: input.newsSentiment,
        social: input.socialSentiment
      })
    ]);
  }

  /**
   * Update ensemble weights based on recent accuracy
   */
  public async calibrateWeights(): Promise<void> {
    // Get recent predictions and their outcomes
    const result = await query(`
      SELECT * FROM model_predictions 
      WHERE predicted_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
      AND actual_direction IS NOT NULL
    `);
    const predictions = result.rows;

    if (predictions.length < 10) {
      console.log("[EnsemblePredictor] Not enough data to calibrate weights yet");
      return;
    }

    // Calculate accuracy per model
    const accuracy: Record<string, { correct: number; total: number }> = {};
    
    for (const pred of predictions) {
      const model = pred.model_name;
      if (!accuracy[model]) {
        accuracy[model] = { correct: 0, total: 0 };
      }
      accuracy[model].total++;
      if (pred.correct) accuracy[model].correct++;
    }

    // Calculate accuracy rate per model
    const rates: Record<string, number> = {};
    for (const [model, stats] of Object.entries(accuracy)) {
      rates[model] = stats.total > 0 ? stats.correct / stats.total : 0.5;
    }

    console.log("[EnsemblePredictor] Model accuracy report:", rates);

    // Re-weight proportionally to accuracy, keeping existing defaults for absent models
    const totalRate =
      (rates['xgboost'] ?? rates['ensemble'] ?? 0.5) +
      (rates['gemini']  ?? 0.5) +
      (rates['ollama']  ?? 0.5);

    if (totalRate === 0) return;

    // Allocate 85% of weight across the three LLM/ML models, keep news/social/technical fixed
    const llmShare = 0.85;
    this.weights.gemini   = parseFloat(((rates['gemini']  ?? 0.5) / totalRate * llmShare).toFixed(3));
    this.weights.ollama   = parseFloat(((rates['ollama']  ?? 0.5) / totalRate * llmShare).toFixed(3));
    // xgboost accuracy feeds back via the 'technical' slot (it already short-circuits the pipeline)
    this.weights.technical = parseFloat(((rates['xgboost'] ?? rates['ensemble'] ?? 0.5) / totalRate * llmShare).toFixed(3));

    // Persist to DB so weights survive restarts
    await query(`
      INSERT INTO system_config (key, value, updated_at)
      VALUES ('ensemble_weights', $1, CURRENT_TIMESTAMP)
      ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = CURRENT_TIMESTAMP
    `, [JSON.stringify(this.weights)]);

    console.log("[EnsemblePredictor] Weights updated and persisted:", this.weights);
  }

  /**
   * Get ensemble status
   */
  public async getStatus(): Promise<{
    ollamaEnabled: boolean;
    mlServiceAvailable: boolean;
    weights: EnsembleWeights;
    models: string[];
  }> {
    const mlAvailable = await mlServiceClient.isReady();
    return {
      ollamaEnabled: this.useOllama,
      mlServiceAvailable: mlAvailable,
      weights: this.weights,
      models: mlAvailable ? ['xgboost', 'gemini', 'ollama'] : ['gemini', 'ollama']
    };
  }
}

export const ensemblePredictor = new EnsemblePredictor();
