import axios from "axios";
import { StockData } from "./aiService";

const ML_SERVICE_URL = process.env.ML_SERVICE_URL || "http://localhost:8000";

interface MLFeatures {
  sma_5: number;
  sma_20: number;
  price_vs_sma5: number;
  price_vs_sma20: number;
  rsi_14: number;
  momentum_5: number;
  momentum_20: number;
  volatility_20: number;
  atr_14: number;
  volume_sma5: number;
  volume_ratio: number;
  current_price: number;
  price_change_1d: number;
  price_change_5d: number;
  last_price?: number;      // From Chronos
  predicted_price?: number; // From Chronos
}

interface MLPrediction {
  symbol: string;
  recommendation: "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL";
  confidence: number;
  direction: "UP" | "DOWN" | "FLAT";
  model_used: string;
  features: MLFeatures;
  forecast?: {
    median: number;
    p10: number;
    p90: number;
    direction: string;
    confidence: number;
  };
  explanation?: string;
  all_results?: Record<string, {
    direction: string;
    confidence: number;
    recommendation?: string;
    forecast?: any;
    explanation?: string;
  }>;
}

export class MLServiceClient {
  private baseUrl: string;
  private isAvailable = false;

  constructor() {
    this.baseUrl = ML_SERVICE_URL;
    this.checkHealth();
  }

  private async checkHealth(): Promise<void> {
    try {
      const response = await axios.get(`${this.baseUrl}/health`, { timeout: 5000 });
      this.isAvailable = response.data?.status === "healthy";
      console.log(`[MLServiceClient] Connected to Python ML service at ${this.baseUrl}`);
      console.log(`[MLServiceClient] XGBoost available: ${response.data?.xgboost_available}`);
    } catch (error) {
      this.isAvailable = false;
      console.log(`[MLServiceClient] Python ML service not available at ${this.baseUrl}`);
      console.log(`[MLServiceClient] Will use fallback TypeScript models`);
    }
  }

  public async isReady(): Promise<boolean> {
    if (!this.isAvailable) {
      await this.checkHealth();
    }
    return this.isAvailable;
  }

  /**
   * Get ML prediction from Python service
   */
  public async predict(
    symbol: string,
    stockData: StockData,
    newsSentiment: number = 0,
    socialSentiment: number = 0
  ): Promise<MLPrediction | null> {
    if (!await this.isReady()) {
      return null;
    }

    try {
      const response = await axios.post(`${this.baseUrl}/predict`, {
        symbol,
        price_data: {
          symbol,
          prices: stockData.history.map(h => h.price),
          volumes: stockData.history.map(h => h.volume || 0),
          highs: stockData.history.map(h => h.high || h.price),
          lows: stockData.history.map(h => h.low || h.price)
        },
        news_sentiment: newsSentiment,
        social_sentiment: socialSentiment
      }, { timeout: 10000 });

      return response.data as MLPrediction;
    } catch (error: any) {
      console.error(`[MLServiceClient] Prediction failed:`, error.message);
      return null;
    }
  }

  /**
   * Train a model for a specific symbol
   */
  public async trainModel(symbol: string, historicalData: any[]): Promise<boolean> {
    if (!await this.isReady()) {
      return false;
    }

    try {
      const response = await axios.post(`${this.baseUrl}/train/${symbol}`, historicalData, {
        timeout: 60000 // Training can take time
      });
      return response.data?.trained || false;
    } catch (error) {
      console.error(`[MLServiceClient] Training failed:`, error);
      return false;
    }
  }

  /**
   * Get list of cached models
   */
  public async getCachedModels(): Promise<string[]> {
    if (!await this.isReady()) {
      return [];
    }

    try {
      const response = await axios.get(`${this.baseUrl}/models`);
      return response.data?.cached_models || [];
    } catch (error) {
      return [];
    }
  }

  /**
   * Clear cached model for a symbol
   */
  public async clearModel(symbol: string): Promise<void> {
    if (!await this.isReady()) {
      return;
    }

    try {
      await axios.delete(`${this.baseUrl}/models/${symbol}`);
    } catch (error) {
      console.error(`[MLServiceClient] Failed to clear model:`, error);
    }
  }

  /**
   * Get service status
   */
  public getStatus(): { available: boolean; url: string } {
    return {
      available: this.isAvailable,
      url: this.baseUrl
    };
  }
}

export const mlServiceClient = new MLServiceClient();
