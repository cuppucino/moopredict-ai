import yfinance as yf
import pandas as pd
from typing import Dict, List, Optional
from loguru import logger

class OptionsFlowService:
    def get_unusual_activity(self, symbol: str) -> Dict:
        """Scan for high Volume/Open Interest ratios in the options chain."""
        # Clean symbol for yfinance
        yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
        
        try:
            ticker = yf.Ticker(yf_symbol)
            expirations = ticker.options
            
            if not expirations:
                return {"symbol": symbol, "error": "No options found"}
            
            # Use the nearest expiration for scanning high-volume activity
            nearest_exp = expirations[0]
            chain = ticker.option_chain(nearest_exp)
            
            calls = chain.calls
            puts = chain.puts
            
            # Combine and add type
            calls['type'] = 'CALL'
            puts['type'] = 'PUT'
            all_options = pd.concat([calls, puts])
            
            # Filter for significant volume (> 100) and calculate ratio
            # Avoid division by zero with openInterest
            all_options = all_options[all_options['volume'] > 100].copy()
            all_options['vol_oi_ratio'] = all_options['volume'] / all_options['openInterest'].replace(0, 1)
            
            # Sort by ratio descending
            unusual = all_options.sort_values(by='vol_oi_ratio', ascending=False).head(10)
            
            results = []
            for _, row in unusual.iterrows():
                results.append({
                    "strike": float(row['strike']),
                    "type": row['type'],
                    "expiry": nearest_exp,
                    "volume": int(row['volume']),
                    "open_interest": int(row['openInterest']),
                    "ratio": round(float(row['vol_oi_ratio']), 2),
                    "last_price": float(row['lastPrice'])
                })
            
            # Calculate Put/Call ratio for this expiration
            total_call_vol = calls['volume'].sum()
            total_put_vol = puts['volume'].sum()
            pcr = round(total_put_vol / total_call_vol, 2) if total_call_vol > 0 else 0
            
            return {
                "symbol": symbol,
                "expiration": nearest_exp,
                "put_call_ratio": pcr,
                "unusual_activity": results,
                "sentiment": "BULLISH" if pcr < 0.7 else "BEARISH" if pcr > 1.0 else "NEUTRAL"
            }
            
        except Exception as e:
            logger.error(f"[Options] Error scanning {symbol}: {e}")
            return {"symbol": symbol, "error": str(e)}

    def get_pcr(self, symbol: str) -> Dict:
        """Quick fetch for aggregate Put/Call ratio."""
        data = self.get_unusual_activity(symbol)
        if "error" in data: return data
        return {
            "symbol": symbol,
            "pcr": data["put_call_ratio"],
            "sentiment": data["sentiment"]
        }

    def get_implied_move(self, symbol: str) -> Dict:
        """Estimate the implied move based on the nearest ATM straddle."""
        yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info
            current_price = info.get('regularMarketPrice') or info.get('currentPrice')
            
            if not current_price:
                # Fallback to fast_info or history
                hist = ticker.history(period="1d")
                if not hist.empty:
                    current_price = hist['Close'].iloc[-1]
            
            if not current_price:
                return {"error": "Could not fetch current price"}
                
            expirations = ticker.options
            if not expirations:
                return {"error": "No options found"}
            
            # Use nearest expiration
            nearest_exp = expirations[0]
            chain = ticker.option_chain(nearest_exp)
            
            # Find strike closest to current price
            calls = chain.calls
            puts = chain.puts
            
            calls['dist'] = (calls['strike'] - current_price).abs()
            puts['dist'] = (puts['strike'] - current_price).abs()
            
            # Use sort_values and take top instead of idxmin for better handling
            atm_call = calls.sort_values(by='dist').iloc[0]
            atm_put = puts.sort_values(by='dist').iloc[0]
            
            # Use lastPrice or mid price if possible
            straddle_price = atm_call['lastPrice'] + atm_put['lastPrice']
            implied_move_pct = (straddle_price / current_price) * 100
            
            return {
                "symbol": symbol,
                "expiration": nearest_exp,
                "current_price": round(current_price, 2),
                "straddle_price": round(straddle_price, 2),
                "implied_move_pct": round(implied_move_pct, 2),
                "range_upper": round(current_price * (1 + implied_move_pct/100), 2),
                "range_lower": round(current_price * (1 - implied_move_pct/100), 2),
                "success": True
            }
        except Exception as e:
            logger.error(f"[Options] Implied move error for {symbol}: {e}")
            return {"error": str(e), "success": False}

options_service = OptionsFlowService()
