import yfinance as yf
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from loguru import logger
from datetime import datetime
from core.database import SessionLocal, OptionsSnapshot

class OptionsEngine:
    def get_chain_data(self, symbol: str) -> Optional[Dict]:
        """Fetch option chain and calculate advanced metrics with circuit breaker protection."""
        from services.circuit_breaker import get_breaker
        breaker = get_breaker("yfinance")

        def _fetch_options():
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            if not expirations:
                raise Exception(f"No options found for {symbol}")
            
            # Robust price lookup
            current_price = ticker.info.get('regularMarketPrice') or ticker.fast_info.get('lastPrice', 0)
            if current_price == 0:
                # Try one more way
                history = ticker.history(period="1d")
                if not history.empty:
                    current_price = history['Close'].iloc[-1]
            
            if current_price == 0:
                raise Exception(f"Could not fetch price for {symbol}")

            agg_call_oi = 0
            agg_put_oi = 0
            whales = []
            
            # Analyze first 3 expirations for Global Sentiment and Whales
            for exp in expirations[:3]:
                try:
                    chain = ticker.option_chain(exp)
                    calls, puts = chain.calls, chain.puts
                    
                    if not calls.empty:
                        agg_call_oi += calls['openInterest'].sum()
                        c_whales = calls[calls['volume'] > (calls['openInterest'] * 1.5)]
                        for _, row in c_whales.iterrows():
                            premium = row['lastPrice'] * row['volume'] * 100
                            if premium > 100000:
                                whales.append({"type": "CALL", "strike": row['strike'], "exp": exp, "premium": round(premium, 0), "vol_oi": round(row['volume']/(row['openInterest'] or 1), 1)})
                    
                    if not puts.empty:
                        agg_put_oi += puts['openInterest'].sum()
                        p_whales = puts[puts['volume'] > (puts['openInterest'] * 1.5)]
                        for _, row in p_whales.iterrows():
                            premium = row['lastPrice'] * row['volume'] * 100
                            if premium > 100000:
                                whales.append({"type": "PUT", "strike": row['strike'], "exp": exp, "premium": round(premium, 0), "vol_oi": round(row['volume']/(row['openInterest'] or 1), 1)})
                except Exception as e:
                    logger.warning(f"[OptionsEngine] Could not fetch chain for {exp}: {e}")
                    continue

            # Primary Expiry for Max Pain and GEX
            exp0 = expirations[0]
            chain0 = ticker.option_chain(exp0)
            calls0, puts0 = chain0.calls, chain0.puts
            
            if calls0.empty or puts0.empty:
                raise Exception(f"Empty chain for primary expiry {exp0}")

            # 1. Max Pain Calculation
            all_strikes = sorted(set(calls0['strike']).union(set(puts0['strike'])))
            pain_levels = []
            for strike in all_strikes:
                c_itm = calls0[calls0['strike'] < strike]
                c_pain = ((strike - c_itm['strike']) * c_itm['openInterest']).sum()
                p_itm = puts0[puts0['strike'] > strike]
                p_pain = ((p_itm['strike'] - strike) * p_itm['openInterest']).sum()
                pain_levels.append({'strike': strike, 'total_pain': c_pain + p_pain})
            
            max_pain = min(pain_levels, key=lambda x: x['total_pain'])['strike'] if pain_levels else 0
            
            # 2. Simple GEX Proxy
            calls0['gex'] = calls0['openInterest'] * (current_price - calls0['strike'])
            puts0['gex'] = puts0['openInterest'] * (puts0['strike'] - current_price)
            total_gex = calls0['gex'].sum() - puts0['gex'].sum()
            
            # 3. Support/Resistance Walls (Max OI)
            gamma_wall = calls0.loc[calls0['openInterest'].idxmax(), 'strike'] if not calls0.empty else 0
            support_wall = puts0.loc[puts0['openInterest'].idxmax(), 'strike'] if not puts0.empty else 0
            
            global_pcr = agg_put_oi / agg_call_oi if agg_call_oi > 0 else 1.0
            
            return {
                "symbol": symbol,
                "spot_price": round(current_price, 2),
                "max_pain": round(max_pain, 2),
                "total_gex": round(total_gex, 0),
                "pcr": round(global_pcr, 2),
                "whale_trades": sorted(whales, key=lambda x: x['premium'], reverse=True)[:5],
                "gamma_wall": gamma_wall,
                "support_wall": support_wall,
                "expiration": exp0,
                "sentiment": "BULLISH" if current_price > max_pain else "BEARISH",
                "timestamp": datetime.now().isoformat()
            }

        return breaker.call(_fetch_options, cache_key=f"options:{symbol}")

    def save_snapshot(self, symbol: str):
        """Save options metrics to DB."""
        data = self.get_chain_data(symbol)
        if not data: return

        db = SessionLocal()
        try:
            snapshot = OptionsSnapshot(
                symbol=symbol,
                max_pain=data["max_pain"],
                total_gex=data["total_gex"],
                pcr=data["pcr"],
                spot_price=data["spot_price"],
                raw_json=data
            )
            db.add(snapshot)
            db.commit()
            logger.info(f"[OptionsEngine] Snapshot saved for {symbol}: GEX={data['total_gex']}")
        except Exception as e:
            logger.error(f"[OptionsEngine] DB Error: {e}")
            db.rollback()
        finally:
            db.close()

options_engine = OptionsEngine()
