import vectorbt as vbt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger

class BacktestEngine:
    """
    High-performance backtesting using vectorbt.
    Supports:
    - Technical indicator strategies (RSI, MACD, EMA)
    - Multi-symbol comparison
    - Walk-forward validation (conceptually)
    """

    def backtest_rsi(self, symbol: str, period: int = 14, lower: int = 30, upper: int = 70, lookback_days: int = 365) -> Dict:
        """
        Backtest a simple RSI mean-reversion strategy.
        Buy when RSI < lower, Sell when RSI > upper.
        """
        try:
            # Fetch data
            logger.info(f"[Backtest] Starting RSI backtest for {symbol} (lookback: {lookback_days}d)")
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            data = vbt.YFData.download(symbol, start=start_date)
            logger.info(f"[Backtest] Data fetched for {symbol}")
            close = data.get('Close')

            # Calculate RSI
            rsi = vbt.RSI.run(close, window=period)

            # Define Signals
            entries = rsi.rsi_below(lower)
            exits = rsi.rsi_above(upper)

            # Run Portfolio
            pf = vbt.Portfolio.from_signals(close, entries, exits, init_cash=10000, fees=0.001)

            stats = pf.stats(settings=dict(freq='D'))
            
            return {
                "symbol": symbol,
                "strategy": f"RSI ({lower}/{upper})",
                "total_return_pct": round(float(stats.get('Total Return [%]', 0)), 2),
                "benchmark_return_pct": round(float(stats.get('Benchmark Return [%]', 0)), 2),
                "win_rate_pct": round(float(stats.get('Win Rate [%]', 0)), 2),
                "max_drawdown_pct": round(float(stats.get('Max Drawdown [%]', 0)), 2),
                "sharpe_ratio": round(float(stats.get('Sharpe Ratio', 0)), 2),
                "total_trades": int(stats.get('Total Trades', 0)),
                "success": True
            }
        except Exception as e:
            logger.error(f"[Backtest] RSI error for {symbol}: {e}")
            return {"error": str(e), "success": False}


    def backtest_ema_crossover(self, symbol: str, short_window: int = 20, long_window: int = 50, lookback_days: int = 365) -> Dict:
        """
        Backtest EMA crossover (Golden Cross / Death Cross).
        """
        try:
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            data = vbt.YFData.download(symbol, start=start_date)
            close = data.get('Close')

            short_ema = vbt.MA.run(close, window=short_window, ewm=True)
            long_ema = vbt.MA.run(close, window=long_window, ewm=True)

            entries = short_ema.ma_crossed_above(long_ema.ma)
            exits = short_ema.ma_crossed_below(long_ema.ma)

            pf = vbt.Portfolio.from_signals(close, entries, exits, init_cash=10000, fees=0.001)
            stats = pf.stats(settings=dict(freq='D'))

            return {
                "symbol": symbol,
                "strategy": f"EMA {short_window}/{long_window} Crossover",
                "total_return_pct": round(float(stats.get('Total Return [%]', 0)), 2),
                "benchmark_return_pct": round(float(stats.get('Benchmark Return [%]', 0)), 2),
                "win_rate_pct": round(float(stats.get('Win Rate [%]', 0)), 2),
                "max_drawdown_pct": round(float(stats.get('Max Drawdown [%]', 0)), 2),
                "sharpe_ratio": round(float(stats.get('Sharpe Ratio', 0)), 2),
                "total_trades": int(stats.get('Total Trades', 0)),
                "success": True
            }
        except Exception as e:
            logger.error(f"[Backtest] EMA error for {symbol}: {e}")
            return {"error": str(e), "success": False}

# Singleton instance
backtest_engine = BacktestEngine()
