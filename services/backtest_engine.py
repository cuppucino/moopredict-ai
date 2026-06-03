import vectorbt as vbt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger
from services.mcpt.costs import PER_FLIP_BPS


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
            pf = vbt.Portfolio.from_signals(close, entries, exits, init_cash=10000, fees=PER_FLIP_BPS)

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

            pf = vbt.Portfolio.from_signals(close, entries, exits, init_cash=10000, fees=PER_FLIP_BPS)
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


    def backtest_vwap(self, symbol: str, lookback_days: int = 365) -> Dict:
        """
        Backtest VWAP bounce strategy.
        Buy when price crosses above VWAP, Sell when it crosses below.
        """
        try:
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            data = vbt.YFData.download(symbol, start=start_date)
            close = data.get('Close')
            volume = data.get('Volume')

            # Calculate VWAP
            vwap = (close * volume).cumsum() / volume.cumsum()

            entries = close.vbt.crossed_above(vwap)
            exits = close.vbt.crossed_below(vwap)

            pf = vbt.Portfolio.from_signals(close, entries, exits, init_cash=10000, fees=PER_FLIP_BPS)
            stats = pf.stats(settings=dict(freq='D'))

            return {
                "symbol": symbol,
                "strategy": "VWAP Crossover",
                "total_return_pct": round(float(stats.get('Total Return [%]', 0)), 2),
                "benchmark_return_pct": round(float(stats.get('Benchmark Return [%]', 0)), 2),
                "win_rate_pct": round(float(stats.get('Win Rate [%]', 0)), 2),
                "max_drawdown_pct": round(float(stats.get('Max Drawdown [%]', 0)), 2),
                "sharpe_ratio": round(float(stats.get('Sharpe Ratio', 0)), 2),
                "total_trades": int(stats.get('Total Trades', 0)),
                "success": True
            }
        except Exception as e:
            logger.error(f"[Backtest] VWAP error for {symbol}: {e}")
            return {"error": str(e), "success": False}

    def compare_strategies(self, symbol: str, lookback_days: int = 365) -> List[Dict]:
        """Run multiple backtests and return a comparison."""
        results = []
        results.append(self.backtest_rsi(symbol, lookback_days=lookback_days))
        results.append(self.backtest_ema_crossover(symbol, lookback_days=lookback_days))
        results.append(self.backtest_vwap(symbol, lookback_days=lookback_days))
        
        # Sort by total return desc
        return sorted([r for r in results if r.get("success")], key=lambda x: x["total_return_pct"], reverse=True)

    def walk_forward_validation(self, symbol: str, strategy: str = "rsi", lookback_days: int = 365) -> Dict:
        """
        Simple walk-forward validation:
        Split data into 70% train (to pick best params) and 30% test (to see real performance).
        """
        # Note: Implementation here would optimize parameters on train set.
        # For now, we'll just report a split-period performance.
        try:
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            data = vbt.YFData.download(symbol, start=start_date)
            close = data.get('Close')
            
            split_idx = int(len(close) * 0.7)
            train_close = close.iloc[:split_idx]
            test_close = close.iloc[split_idx:]
            
            # Simple RSI test on both
            def run_rsi(c):
                rsi = vbt.RSI.run(c, window=14)
                e = rsi.rsi_below(30)
                x = rsi.rsi_above(70)
                return vbt.Portfolio.from_signals(c, e, x).total_return()
            
            train_ret = run_rsi(train_close)
            test_ret = run_rsi(test_close)
            
            return {
                "symbol": symbol,
                "train_return_pct": round(float(train_ret * 100), 2),
                "test_return_pct": round(float(test_ret * 100), 2),
                "robustness_ratio": round(float(test_ret / train_ret if train_ret != 0 else 0), 2)
            }
        except Exception as e:
            return {"error": str(e)}

# Singleton instance
backtest_engine = BacktestEngine()
