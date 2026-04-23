import { createRequire } from "module";
const _require = createRequire(import.meta.url);
const { RSI, MACD, SMA, ATR } = _require('technicalindicators');
import { EtfOhlcv } from './etfDataService';
import { logger } from '../core/Logger';

export interface TechnicalSignal {
  symbol: string;
  signal_type: 'BUY' | 'SELL' | 'WATCH';
  confidence: number;
  reason: string;
  entry_price: number;
  target_price_short: number;
  target_price_long: number;
  stop_loss: number;
  indicators: {
    rsi: number;
    macd: {
      macd: number;
      signal: number;
      histogram: number;
    };
    sma20: number;
    sma50: number;
    volume_ratio: number;
  };
}

export class EtfTechnicalAnalysis {
  /**
   * Runs technical analysis on OHLCV data to generate a signal
   */
  public analyze(symbol: string, history: EtfOhlcv[]): TechnicalSignal {
    const closes = history.map(h => h.close);
    const volumes = history.map(h => h.volume);
    const highs = history.map(h => h.high);
    const lows = history.map(h => h.low);
    
    const lastPrice = closes[closes.length - 1];
    
    // 1. RSI (14-period)
    const rsiValues = RSI.calculate({ values: closes, period: 14 });
    const currentRsi = rsiValues[rsiValues.length - 1] || 50;

    // 2. MACD (12, 26, 9)
    const macdResult = MACD.calculate({
      values: closes,
      fastPeriod: 12,
      slowPeriod: 26,
      signalPeriod: 9,
      SimpleMAOscillator: false,
      SimpleMASignal: false
    });
    const currentMacd = macdResult[macdResult.length - 1] || { macd: 0, signal: 0, histogram: 0 };

    // 3. SMAs (20, 50)
    const sma20Values = SMA.calculate({ values: closes, period: 20 });
    const sma50Values = SMA.calculate({ values: closes, period: 50 });
    const currentSma20 = sma20Values[sma20Values.length - 1] || lastPrice;
    const currentSma50 = sma50Values[sma50Values.length - 1] || lastPrice;

    // 4. Volume Confirmation
    const avgVolume20 = volumes.slice(-20).reduce((a, b) => a + b, 0) / 20;
    const currentVolume = volumes[volumes.length - 1];
    const volumeRatio = currentVolume / (avgVolume20 || 1);

    // 5. ATR (14-period) for Risk Management
    const atrValues = ATR.calculate({ high: highs, low: lows, close: closes, period: 14 });
    const currentAtr = atrValues[atrValues.length - 1] || (lastPrice * 0.02);

    // 6. Signal Composition (Weighted Voting)
    let buyVotes = 0;
    let sellVotes = 0;
    const reasons: string[] = [];

    // RSI Voting
    if (currentRsi < 35) {
      buyVotes++;
      reasons.push("RSI Oversold (<35)");
    } else if (currentRsi > 70) {
      sellVotes++;
      reasons.push("RSI Overbought (>70)");
    }

    // MACD Voting
    if (currentMacd.histogram > 0 && (macdResult[macdResult.length - 2]?.histogram || 0) <= 0) {
      buyVotes++;
      reasons.push("MACD Bullish Crossover");
    } else if (currentMacd.histogram < 0 && (macdResult[macdResult.length - 2]?.histogram || 0) >= 0) {
      sellVotes++;
      reasons.push("MACD Bearish Crossover");
    }

    // SMA Voting
    if (lastPrice > currentSma20 && currentSma20 > currentSma50) {
      buyVotes++;
      reasons.push("Price above SMA20 & SMA20 > SMA50 (Bullish Trend)");
    } else if (lastPrice < currentSma20) {
      sellVotes++;
      reasons.push("Price below SMA20 (Bearish Trend)");
    }

    // Volume Confirmation (Only for BUY signals)
    const volumeConfirmed = volumeRatio > 1.2;
    if (buyVotes >= 2 && !volumeConfirmed) {
      logger.info(`[EtfAnalysis] ${symbol} BUY signal rejected due to low volume ratio: ${volumeRatio.toFixed(2)}`);
      buyVotes = 1; // Demote signal
    }

    // Final Decision
    let signal_type: 'BUY' | 'SELL' | 'WATCH' = 'WATCH';
    if (buyVotes >= 2) signal_type = 'BUY';
    else if (sellVotes >= 2) signal_type = 'SELL';

    const confidence = Math.max(buyVotes, sellVotes) / 3; // Max 3 indicators

    return {
      symbol,
      signal_type,
      confidence,
      reason: reasons.join(", ") || "No significant technical movement",
      entry_price: lastPrice,
      target_price_short: parseFloat((lastPrice + 2 * currentAtr).toFixed(2)),
      target_price_long: parseFloat((lastPrice + 4 * currentAtr).toFixed(2)),
      stop_loss: parseFloat((lastPrice - 1.5 * currentAtr).toFixed(2)),
      indicators: {
        rsi: parseFloat(currentRsi.toFixed(2)),
        macd: {
          macd: parseFloat((currentMacd.macd || 0).toFixed(4)),
          signal: parseFloat((currentMacd.signal || 0).toFixed(4)),
          histogram: parseFloat((currentMacd.histogram || 0).toFixed(4))
        },
        sma20: parseFloat(currentSma20.toFixed(2)),
        sma50: parseFloat(currentSma50.toFixed(2)),
        volume_ratio: parseFloat(volumeRatio.toFixed(2))
      }
    };
  }
}

export const etf_technical_analysis = new EtfTechnicalAnalysis();
