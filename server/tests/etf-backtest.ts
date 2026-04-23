import { etf_data_service } from '../services/etfDataService';
import { etf_technical_analysis } from '../services/etfTechnicalAnalysis';
import { ETF_WATCHLIST } from '../core/EtfWatchlist';
import { logger } from '../core/Logger';

async function runBacktest() {
  logger.info("🚀 Starting ETF Historical Backtest (1 Year)...");
  
  const results: any[] = [];

  for (const etf of ETF_WATCHLIST) {
    try {
      logger.info(`Analyzing ${etf.symbol}...`);
      await new Promise(resolve => setTimeout(resolve, 5000)); // Delay to avoid rate limit
      
      // Fetch 1 year of data
      const history = await etf_data_service.fetch_etf_data(etf.symbol);
      if (history.length < 100) {
        logger.warn(`Insufficient history for ${etf.symbol}`);
        continue;
      }

      let totalTrades = 0;
      let wins = 0;
      let totalPnL = 0;

      // Sliding window backtest
      // Start from 50 days in to have enough for SMAs
      for (let i = 50; i < history.length - 10; i++) {
        const window = history.slice(0, i + 1);
        const signal = etf_technical_analysis.analyze(etf.symbol, window);

        if (signal.signal_type === 'BUY') {
          totalTrades++;
          
          // Simple outcome check: look ahead 10 days
          const entryPrice = signal.entry_price;
          const stopLoss = signal.stop_loss;
          const target = signal.target_price_short;
          
          let won = false;
          let lost = false;
          let finalPrice = entryPrice;

          for (let j = i + 1; j < Math.min(i + 11, history.length); j++) {
            const current = history[j];
            if (current.high >= target) {
              won = true;
              finalPrice = target;
              break;
            }
            if (current.low <= stopLoss) {
              lost = true;
              finalPrice = stopLoss;
              break;
            }
            finalPrice = current.close;
          }

          if (won) {
            wins++;
          }
          
          totalPnL += ((finalPrice - entryPrice) / entryPrice) * 100;
        }
      }

      const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
      const avgReturn = totalTrades > 0 ? totalPnL / totalTrades : 0;

      results.push({
        symbol: etf.symbol,
        trades: totalTrades,
        winRate: winRate.toFixed(2) + "%",
        avgReturn: avgReturn.toFixed(2) + "%"
      });

    } catch (error: any) {
      logger.error(`Backtest failed for ${etf.symbol}: ${error.message}`);
    }
  }

  console.table(results);
  logger.info("✅ Backtest completed.");
}

runBacktest();
