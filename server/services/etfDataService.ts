import { createRequire } from "module";
import { logger } from "../core/Logger";

const _require = createRequire(import.meta.url);
const yahooFinance = _require("yahoo-finance2").default;

export interface EtfOhlcv {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface EtfSnapshot {
  symbol: string;
  price: number;
  change_percent: number;
  volume: number;
  timestamp: Date;
}

class EtfDataService {
  private cache: Map<string, { data: any; timestamp: number }> = new Map();
  private readonly CACHE_TTL = 15 * 60 * 1000; // 15 minutes

  /**
   * Fetches 90 days of daily OHLCV data for an ETF
   */
  async fetch_etf_data(symbol: string): Promise<EtfOhlcv[]> {
    try {
      const cacheKey = `history:${symbol}`;
      const cached = this.cache.get(cacheKey);
      if (cached && Date.now() - cached.timestamp < this.CACHE_TTL) {
        return cached.data;
      }

      const period1 = new Date(Date.now() - 400 * 24 * 60 * 60 * 1000); // 400 days for backtesting support
      const chart_res = await yahooFinance.chart(symbol, {
        period1,
        period2: new Date(),
        interval: "1d",
      });

      const quotes = chart_res.quotes || [];
      const history: EtfOhlcv[] = quotes
        .filter((q: any) => q.close !== null && q.date !== null)
        .map((q: any) => ({
          date: new Date(q.date).toISOString().split('T')[0],
          open: parseFloat(q.open.toFixed(2)),
          high: parseFloat(q.high.toFixed(2)),
          low: parseFloat(q.low.toFixed(2)),
          close: parseFloat(q.close.toFixed(2)),
          volume: q.volume || 0,
        }))
        .sort((a, b) => a.date.localeCompare(b.date));

      this.cache.set(cacheKey, { data: history, timestamp: Date.now() });
      return history;
    } catch (error: any) {
      logger.error(`[EtfDataService] Failed to fetch history for ${symbol}:`, error.message);
      throw error;
    }
  }

  /**
   * Fetches current snapshot for an ETF
   */
  async fetch_etf_snapshot(symbol: string): Promise<EtfSnapshot> {
    try {
      const quote = await yahooFinance.quote(symbol);
      if (!quote) throw new Error(`No quote found for ${symbol}`);

      return {
        symbol: symbol.toUpperCase(),
        price: parseFloat((quote.regularMarketPrice || 0).toFixed(2)),
        change_percent: parseFloat((quote.regularMarketChangePercent || 0).toFixed(2)),
        volume: quote.regularMarketVolume || 0,
        timestamp: new Date(),
      };
    } catch (error: any) {
      logger.error(`[EtfDataService] Failed to fetch snapshot for ${symbol}:`, error.message);
      throw error;
    }
  }
}

export const etf_data_service = new EtfDataService();
