import { logger } from '../core/Logger';

export interface TechnicalSummary {
  support: number[];
  resistance: number[];
  trend: 'BULLISH' | 'BEARISH' | 'SIDEWAYS';
  volatility: number;
  rsi_status: 'OVERBOUGHT' | 'OVERSOLD' | 'NEUTRAL';
  ma_signal: 'CROSSOVER_UP' | 'CROSSOVER_DOWN' | 'STEADY';
}

/**
 * Advanced Technical Analysis Utility
 * Calculates key levels and trends from historical price data
 */
export class TechnicalAnalyzer {
  
  public static analyze(history: any[]): TechnicalSummary {
    if (!history || history.length < 20) {
      return this.getDefaultSummary();
    }

    const prices = history.map(h => h.price);
    const lastPrice = prices[prices.length - 1];
    
    // 1. Calculate Support & Resistance using local minima/maxima
    const levels = this.calculateLevels(prices);
    
    // 2. Determine Trend (Price vs SMA20)
    const sma20 = this.calculateSMA(prices, 20);
    const trend: 'BULLISH' | 'BEARISH' | 'SIDEWAYS' = 
      lastPrice > sma20 * 1.02 ? 'BULLISH' : 
      lastPrice < sma20 * 0.98 ? 'BEARISH' : 'SIDEWAYS';

    // 3. Simple Volatility (Standard Deviation of returns)
    const volatility = this.calculateVolatility(prices);

    // 4. MA Crossover detection (SMA5 vs SMA20)
    const sma5_prev = this.calculateSMA(prices.slice(0, -1), 5);
    const sma5_curr = this.calculateSMA(prices, 5);
    const sma20_prev = this.calculateSMA(prices.slice(0, -1), 20);
    const sma20_curr = this.calculateSMA(prices, 20);

    let ma_signal: 'CROSSOVER_UP' | 'CROSSOVER_DOWN' | 'STEADY' = 'STEADY';
    if (sma5_prev <= sma20_prev && sma5_curr > sma20_curr) ma_signal = 'CROSSOVER_UP';
    if (sma5_prev >= sma20_prev && sma5_curr < sma20_curr) ma_signal = 'CROSSOVER_DOWN';

    return {
      support: levels.support,
      resistance: levels.resistance,
      trend,
      volatility,
      rsi_status: 'NEUTRAL', // Placeholder for actual RSI if not available
      ma_signal
    };
  }

  private static calculateLevels(prices: number[]): { support: number[], resistance: number[] } {
    const support: number[] = [];
    const resistance: number[] = [];
    
    // Look for pivot points (3-point window)
    for (let i = 1; i < prices.length - 1; i++) {
      // Resistance
      if (prices[i] > prices[i-1] && prices[i] > prices[i+1]) {
        resistance.push(parseFloat(prices[i].toFixed(2)));
      }
      // Support
      if (prices[i] < prices[i-1] && prices[i] < prices[i+1]) {
        support.push(parseFloat(prices[i].toFixed(2)));
      }
    }

    // Sort and take latest/most relevant
    return {
      support: [...new Set(support)].sort((a,b) => b-a).slice(0, 3),
      resistance: [...new Set(resistance)].sort((a,b) => a-b).slice(0, 3)
    };
  }

  private static calculateSMA(prices: number[], period: number): number {
    const window = prices.slice(-period);
    return window.reduce((a, b) => a + b, 0) / window.length;
  }

  private static calculateVolatility(prices: number[]): number {
    const returns = [];
    for (let i = 1; i < prices.length; i++) {
      returns.push((prices[i] / prices[i-1]) - 1);
    }
    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const variance = returns.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / returns.length;
    return Math.sqrt(variance);
  }

  private static getDefaultSummary(): TechnicalSummary {
    return {
      support: [],
      resistance: [],
      trend: 'SIDEWAYS',
      volatility: 0,
      rsi_status: 'NEUTRAL',
      ma_signal: 'STEADY'
    };
  }
}
