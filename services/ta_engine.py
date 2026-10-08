import pandas as pd
import pandas_ta as ta
from scipy import stats
from typing import Dict, Optional, List
from datetime import datetime, timezone
from loguru import logger
from futu import *
from services.moomoo_service import moomoo_service
from core.database import SessionLocal, TASnapshot
from models.ta_signals import TASignal, CompositeScore

class TAEngine:
    def _get_kline_data(self, symbol: str, num: int = 250) -> Optional[pd.DataFrame]:
        """Fetch K-line data from Moomoo for technical analysis with circuit breaker protection."""
        from services.circuit_breaker import get_breaker
        breaker = get_breaker("moomoo")

        def _fetch_from_moomoo():
            if not moomoo_service.is_connected:
                if not moomoo_service.connect():
                    raise Exception("Not connected to Moomoo")

            moomoo_symbol = symbol
            if "." not in moomoo_symbol:
                if moomoo_symbol.isdigit() and len(moomoo_symbol) >= 4:
                    moomoo_symbol = f"HK.{moomoo_symbol}"
                else:
                    moomoo_symbol = f"US.{moomoo_symbol}"
            
            moomoo_service.quote_ctx.subscribe([moomoo_symbol], [SubType.K_DAY])
            ret, df = moomoo_service.quote_ctx.get_cur_kline(moomoo_symbol, num, SubType.K_DAY, AuType.QFQ)
            if ret != RET_OK:
                raise Exception(f"Failed to get K-lines: {df}")
            df.attrs["provider_retrieved_at"] = datetime.now(timezone.utc).isoformat()
            return df

        return breaker.call(_fetch_from_moomoo, cache_key=f"kline:{symbol}")

    def compute_all(self, symbol: str) -> Dict:
        """Run 10+ indicators and return a detailed analysis and composite score."""
        from services.data_freshness import freshness_registry
        
        df = self._get_kline_data(symbol)
        if df is None or df.empty:
            return {"error": f"Could not fetch data for {symbol}"}

        # Register freshness after successful fetch
        freshness_registry.register_update("ta_engine", symbol)

        try:
            signals = []
            from services.intraday_market import archive_bars
            source = archive_bars(df, {"provider": "futu", "symbol": symbol,
                "timeframe": "daily", "adjustment": "QFQ",
                "last_bar_at": str(df.iloc[-1].get("time_key", df.index[-1])),
                "provider_retrieved_at": df.attrs.get("provider_retrieved_at"),
                "purpose": "premarket_legacy_technical_input"})
            
            # 1. RSI (14) - D-Tier (Lagging)
            rsi = df.ta.rsi(length=14)
            rsi_val = float(rsi.iloc[-1]) if not rsi.empty else 50
            # Reduced weight: max ±5 instead of ±15
            rsi_strength = 5 if rsi_val < 30 else 3 if rsi_val < 45 else 0 if rsi_val < 55 else -3 if rsi_val < 70 else -5
            signals.append(TASignal("RSI", rsi_val, "BULLISH" if rsi_val < 45 else "BEARISH" if rsi_val > 55 else "NEUTRAL", rsi_strength, f"RSI at {rsi_val:.1f}"))

            # 2. MACD (12, 26, 9) - F-Tier (Avoid in score)
            macd = df.ta.macd(fast=12, slow=26, signal=9)
            if not macd.empty:
                hist_val = float(macd.iloc[-1, 1]) # MACDh_12_26_9
                # MACD is F-Tier, so weight is 0
                macd_strength = 0 
                signals.append(TASignal("MACD", hist_val, "BULLISH" if hist_val > 0 else "BEARISH", macd_strength, "MACD Histogram (F-Tier, ignored in score)"))

            # 3. VWAP - S-Tier (Critical)
            # ROLLING 20-day VWAP (audit 2026-08-13 C1: the old cumulative-from-inception
            # VWAP left price permanently above it for anything in a multi-year uptrend —
            # a fixed +25 on every call; tech_sig was never once bearish in 512 records).
            pv = (df['close'] * df['volume']).rolling(20, min_periods=5).sum()
            vv = df['volume'].rolling(20, min_periods=5).sum()
            df['vwap'] = pv / vv
            vwap_val = float(df['vwap'].iloc[-1])
            current_price = float(df['close'].iloc[-1])
            vwap_signal = "BULLISH" if current_price > vwap_val else "BEARISH"
            vwap_strength = 25 if current_price > vwap_val else -25
            signals.append(TASignal("VWAP", vwap_val, vwap_signal, vwap_strength, f"Price {'above' if current_price > vwap_val else 'below'} VWAP (${vwap_val:.2f})"))

            # 3b. Institutional Volume Flow (Volume Profile) - S-Tier
            try:
                from services.volume_flow_service import vol_flow_service
                from concurrent.futures import ThreadPoolExecutor, TimeoutError
                
                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(vol_flow_service.calculate_volume_profile, symbol, resolution="1w")
                    vol_data = future.result(timeout=5.0)
                except TimeoutError:
                    logger.error(f"[TAEngine] Volume flow calculation timed out (5.0s limit) for {symbol}")
                    vol_data = {"error": "ta_timeout"}
                finally:
                    executor.shutdown(wait=False)
                
                if vol_data and vol_data.get("success"):
                    poc_val = vol_data["poc"]
                    dist = vol_data["distance_to_poc_pct"]
                    shape = vol_data["shape"]
                    divergence = vol_data["divergence"]
                    
                    vol_strength = 0
                    if dist < 1.0: 
                        vol_strength = 20
                    elif vol_data["in_value_area"]:
                        vol_strength = 10
                        
                    if "P-shape" in shape: vol_strength += 5
                    elif "b-shape" in shape: vol_strength -= 5
                    
                    if "BULLISH DIVERGENCE" in divergence: vol_strength += 10
                    elif "BEARISH DIVERGENCE" in divergence: vol_strength -= 10

                    signals.append(TASignal("Volume Flow", poc_val, "BULLISH" if current_price > poc_val else "BEARISH", vol_strength, f"POC: ${poc_val:.2f} | Shape: {shape.split(' ')[0]} | {divergence.split(' ')[0]}"))
            except Exception as ve:
                logger.warning(f"[TAEngine] Volume flow integration error: {ve}")

            # 4. ADX (Trend Strength)
            adx = df.ta.adx(length=14)
            if not adx.empty:
                adx_val = float(adx.iloc[-1, 0]) # ADX_14
                di_plus = float(adx.iloc[-1, 1]) # DMP_14
                di_minus = float(adx.iloc[-1, 2]) # DMN_14
                adx_strength = 0
                if adx_val > 25:
                    adx_strength = 10 if di_plus > di_minus else -10
                signals.append(TASignal("ADX", adx_val, "TRENDING" if adx_val > 25 else "RANGE", adx_strength, f"ADX Trend Strength: {adx_val:.1f}"))

            # 5. Bollinger Bands - F-Tier (Avoid in score)
            bbands = df.ta.bbands(length=20, std=2)
            if not bbands.empty:
                pct_b = float(bbands.iloc[-1, 4]) # BBP_20_2.0
                # Bollinger is F-Tier, weight is 0
                bb_strength = 0
                signals.append(TASignal("Bollinger", pct_b, "OVERSOLD" if pct_b < 0.2 else "OVERBOUGHT" if pct_b > 0.8 else "NEUTRAL", bb_strength, f"Bollinger %B (F-Tier, ignored in score)"))

            # 5. Z-Score (Mean Reversion)
            z_score_arr = stats.zscore(df['close'].tail(20))
            z_score = z_score_arr[-1]
            z_strength = 10 if z_score < -2 else -10 if z_score > 2 else 0
            signals.append(TASignal("Z-Score", float(z_score), "MEAN_REV_BUY" if z_score < -2 else "MEAN_REV_SELL" if z_score > 2 else "NEUTRAL", z_strength, f"Z-Score: {z_score:.2f}"))

            # 7. Moving Averages - A-Tier
            sma20 = df.ta.sma(length=20).iloc[-1]
            sma50 = df.ta.sma(length=50).iloc[-1]
            sma200 = df.ta.sma(length=200).iloc[-1] if len(df) >= 200 else sma50
            # SMA is A-Tier, increasing weight
            ma_strength = 15 if current_price > sma200 else 8 if current_price > sma50 else -15
            signals.append(TASignal("SMA", current_price, "ABOVE_200" if current_price > sma200 else "BELOW", ma_strength, f"Price vs 200SMA (A-Tier)"))

            # Composite Calculation
            total_strength = sum(s.strength for s in signals)
            composite_score = max(0, min(100, 50 + total_strength))
            
            label = "STRONG_BUY" if composite_score > 80 else "BUY" if composite_score > 60 else "SELL" if composite_score < 40 else "STRONG_SELL" if composite_score < 20 else "NEUTRAL"
            
            regime = "TRENDING" if (not adx.empty and adx.iloc[-1, 0] > 25) else "RANGE"
            
            return {
                "symbol": symbol,
                "price": current_price,
                "composite_score": int(composite_score),
                "label": label,
                "regime": regime,
                "signals": [s.__dict__ for s in signals],
                "source": source,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"[TAEngine] Calculation error for {symbol}: {e}")
            return {"error": str(e)}

    def save_snapshot(self, symbol: str):
        """Compute and save a TA snapshot to the database."""
        analysis = self.compute_all(symbol)
        if "error" in analysis:
            return

        db = SessionLocal()
        try:
            # Extract specific fields for the table
            rsi_val = next((s['value'] for s in analysis['signals'] if s['name'] == 'RSI'), None)
            vwap_val = next((s['value'] for s in analysis['signals'] if s['name'] == 'VWAP'), None)
            poc_val = next((s['value'] for s in analysis['signals'] if s['name'] == 'Volume Flow'), None)
            macd_hist = next((s['value'] for s in analysis['signals'] if s['name'] == 'MACD'), None)
            adx_val = next((s['value'] for s in analysis['signals'] if s['name'] == 'ADX'), None)
            bb_pct = next((s['value'] for s in analysis['signals'] if s['name'] == 'Bollinger'), None)
            z_val = next((s['value'] for s in analysis['signals'] if s['name'] == 'Z-Score'), None)

            snapshot = TASnapshot(
                symbol=symbol,
                composite_score=analysis['composite_score'],
                rsi=rsi_val,
                vwap=vwap_val,
                poc=poc_val,
                macd_histogram=macd_hist,
                adx=adx_val,
                bollinger_pct_b=bb_pct,
                zscore=z_val,
                regime=analysis['regime'],
                raw_json=analysis
            )
            db.add(snapshot)
            db.commit()
            logger.info(f"[TAEngine] Snapshot saved for {symbol}: {analysis['composite_score']}/100")
        except Exception as e:
            logger.error(f"[TAEngine] Database error: {e}")
            db.rollback()
        finally:
            db.close()

    def get_full_analysis(self, symbol: str) -> Dict:
        """Compatibility wrapper for legacy technical_analysis.py with enhanced day trading metrics."""
        res = self.compute_all(symbol)
        if "error" in res:
            return res
            
        # Fetch volume and gap data (Phase 1B)
        volume_data = moomoo_service.get_volume(symbol)
        
        # Format back to the old structure
        rsi_val = next((s['value'] for s in res['signals'] if s['name'] == 'RSI'), 50)
        ma_info = next((s for s in res['signals'] if s['name'] == 'SMA'), {})
        macd_info = next((s for s in res['signals'] if s['name'] == 'MACD'), {})
        vwap_info = next((s for s in res['signals'] if s['name'] == 'VWAP'), {})
        
        # Calculate gap if possible
        gap_pct = 0.0
        if volume_data.get("success"):
            prev_close = volume_data.get("previous_close", 0)
            open_price = volume_data.get("open_price", 0)
            if prev_close > 0:
                gap_pct = (open_price - prev_close) / prev_close * 100

        return {
            "symbol": symbol,
            "price": res["price"],
            "source": res.get("source"),
            "rsi": round(rsi_val, 2),
            "vwap": round(vwap_info.get("value", 0), 2),
            "composite_score": res["composite_score"],
            "summary": res["label"],
            "regime": res["regime"],
            "volume_info": {
                "ratio": volume_data.get("volume_ratio", 1.0),
                "signal": volume_data.get("volume_signal", "NORMAL"),
                "high": volume_data.get("intraday_high", 0),
                "low": volume_data.get("intraday_low", 0)
            },
            "gap_pct": round(gap_pct, 2),
            "moving_averages": {
                "sma200": ma_info.get("value", 0), # Simplified for compat
                "position": ma_info.get("signal", "UNKNOWN")
            },
            "macd": {
                "trend": macd_info.get("signal", "NEUTRAL"),
                "histogram": macd_info.get("value", 0)
            },
            "timestamp": res["timestamp"]
        }

ta_engine = TAEngine()
