import yfinance as yf
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from loguru import logger

class VolumeFlowService:
    def calculate_volume_profile(self, symbol: str, resolution: str = "1w", bins: int = 400) -> Dict:
        """
        Calculate Granular Volume Profile metrics (POC, VAH, VAL, Shapes).
        resolution: '1d', '1w', '1m'
        """
        yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
        
        period_map = {
            "1d": "2d",
            "1w": "1mo",
            "1m": "3mo"
        }
        interval_map = {
            "1d": "5m",
            "1w": "30m",
            "1m": "1h"
        }
        
        try:
            ticker = yf.Ticker(yf_symbol)
            hist = ticker.history(period=period_map.get(resolution, "1mo"), interval=interval_map.get(resolution, "30m"))
            
            if hist.empty:
                return {"success": False, "error": f"No data found for {symbol}"}

            current_price = float(hist['Close'].iloc[-1])
            price_min = float(hist['Low'].min())
            price_max = float(hist['High'].max())
            
            # 1. Generate High-Resolution Histogram (400 bins)
            bin_edges = np.linspace(price_min, price_max, bins + 1)
            bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
            profile = np.zeros(bins)
            
            for _, row in hist.iterrows():
                # Distribute volume across the candle range for accuracy
                h, l, v = row['High'], row['Low'], row['Volume']
                if h == l:
                    idx = np.digitize(l, bin_edges) - 1
                    if 0 <= idx < bins: profile[idx] += v
                else:
                    # Find bins overlapping with [l, h]
                    start_idx = np.digitize(l, bin_edges) - 1
                    end_idx = np.digitize(h, bin_edges) - 1
                    num_bins_overlap = max(1, end_idx - start_idx + 1)
                    vol_per_bin = v / num_bins_overlap
                    for i in range(max(0, start_idx), min(bins, end_idx + 1)):
                        profile[i] += vol_per_bin
            
            # 2. Identify POC (Point of Control)
            poc_idx = np.argmax(profile)
            poc = float(bin_centers[poc_idx])
            
            # 3. Calculate Value Area (70% of total volume)
            total_volume = np.sum(profile)
            target_va_volume = total_volume * 0.70
            
            va_vol = profile[poc_idx]
            low_idx = poc_idx
            high_idx = poc_idx
            
            while va_vol < target_va_volume and (low_idx > 0 or high_idx < bins - 1):
                prev_low_vol = profile[low_idx - 1] if low_idx > 0 else 0
                prev_high_vol = profile[high_idx + 1] if high_idx < bins - 1 else 0
                
                if prev_low_vol >= prev_high_vol and low_idx > 0:
                    low_idx -= 1
                    va_vol += prev_low_vol
                elif high_idx < bins - 1:
                    high_idx += 1
                    va_vol += prev_high_vol
                else:
                    break
                    
            vah = float(bin_centers[high_idx])
            val = float(bin_centers[low_idx])
            
            # 4. Identify Profile Shape
            shape = self._identify_shape(profile, poc_idx, bins)
            
            # 5. Detect Divergence
            divergence = self._detect_divergence(hist)
            
            # 6. Value Area Re-entry Rule
            va_reentry = self._detect_va_reentry(hist, current_price, bins)
            
            return {
                "symbol": symbol,
                "resolution": resolution,
                "current_price": round(current_price, 2),
                "poc": round(poc, 2),
                "vah": round(vah, 2),
                "val": round(val, 2),
                "shape": shape,
                "divergence": divergence,
                "va_reentry": va_reentry,
                "distance_to_poc_pct": round(abs(current_price - poc) / current_price * 100, 2),
                "in_value_area": val <= current_price <= vah,
                "success": True
            }
            
        except Exception as e:
            logger.error(f"[VolFlow] Error calculating for {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def _detect_va_reentry(self, hist: pd.DataFrame, current_price: float, bins: int) -> str:
        """
        Check for Value Area Re-entry from previous session.
        Rule: Opened outside yesterday's VA, now re-entering.
        """
        if len(hist) < 20: return "NONE"
        
        # Approximate 'yesterday' by looking at previous session data
        # In a real scenario, we'd slice by date, but this is a good approximation
        mid = len(hist) // 2
        prev_session = hist.iloc[:mid]
        
        # Calculate prev VA
        p_min, p_max = float(prev_session['Low'].min()), float(prev_session['High'].max())
        edges = np.linspace(p_min, p_max, bins + 1)
        p_profile = np.zeros(bins)
        for _, row in prev_session.iterrows():
            idx = np.digitize(row['Close'], edges) - 1
            if 0 <= idx < bins: p_profile[idx] += row['Volume']
            
        p_poc_idx = np.argmax(p_profile)
        total_p_vol = np.sum(p_profile)
        target = total_p_vol * 0.7
        v_vol, l_idx, h_idx = p_profile[p_poc_idx], p_poc_idx, p_poc_idx
        
        while v_vol < target and (l_idx > 0 or h_idx < bins - 1):
            plv = p_profile[l_idx-1] if l_idx > 0 else 0
            phv = p_profile[h_idx+1] if h_idx < bins - 1 else 0
            if plv >= phv and l_idx > 0: l_idx -= 1; v_vol += plv
            elif h_idx < bins - 1: h_idx += 1; v_vol += phv
            else: break
            
        p_vah = float((edges[h_idx] + edges[h_idx+1])/2)
        p_val = float((edges[l_idx] + edges[l_idx+1])/2)
        
        # Re-entry check
        open_price = float(hist['Open'].iloc[-1])
        was_outside = open_price > p_vah or open_price < p_val
        is_inside = p_val <= current_price <= p_vah
        
        if was_outside and is_inside:
            target_extreme = "VAL" if open_price > p_vah else "VAH"
            return f"VA RE-ENTRY DETECTED (Targeting opposite extreme: {target_extreme})"
            
        return "NONE"

    def _identify_shape(self, profile: np.ndarray, poc_idx: int, total_bins: int) -> str:
        """Categorize the volume profile shape: D, P, b, or Thin."""
        one_third = total_bins // 3
        
        if poc_idx < one_third:
            return "b-shape (Bearish Rejection/Bottom Heavy)"
        elif poc_idx > 2 * one_third:
            return "P-shape (Bullish Breakout/Top Heavy)"
        
        std_dev = np.std(profile)
        mean_vol = np.mean(profile)
        
        if std_dev < mean_vol * 0.5:
            return "Thin (Explosive Trend/Spread Wide)"
        
        return "D-shape (Balanced/Range)"

    def _detect_divergence(self, hist: pd.DataFrame) -> str:
        """Detect volume divergence relative to price action."""
        if len(hist) < 5: return "NEUTRAL"
        
        recent = hist.tail(5)
        price_change = recent['Close'].iloc[-1] - recent['Close'].iloc[0]
        vol_change = recent['Volume'].iloc[-1] - recent['Volume'].iloc[0]
        
        if price_change > 0 and vol_change < 0:
            return "BEARISH DIVERGENCE (Price up, Volume down - Running out of fuel)"
        if price_change < 0 and vol_change < 0:
            return "BULLISH DIVERGENCE (Price down, Volume down - Sellers exhausted)"
        
        return "HEALTHY"

    def get_signal_report(self, symbol: str, resolution: str = "1w") -> str:
        """Generate a premium formatted report for Volume Profile analysis."""
        data = self.calculate_volume_profile(symbol, resolution)
        if not data.get("success"):
            return f"❌ *Error:* {data.get('error')}"
        
        va_status = "✅ INSIDE Value Area" if data['in_value_area'] else "⚠️ OUTSIDE Value Area"
        div_emoji = "🚨" if "DIVERGENCE" in data['divergence'] else "✅"
        
        report = (
            f"📊 *Volume Profile Analysis: {data['symbol']}*\n"
            f"Resolution: {data['resolution']} | {data['shape']}\n"
            f"───────────────────────────\n"
            f"💵 Price: ${data['current_price']}\n"
            f"🟠 POC (Point of Control): ${data['poc']}\n"
            f"📏 Value Area: ${data['val']} - ${data['vah']}\n"
            f"📍 Status: {va_status}\n\n"
            f"🔍 *Volume Context:*\n"
            f"{div_emoji} Divergence: {data['divergence']}\n"
            f"🎯 Dist to POC: {data['distance_to_poc_pct']}%\n\n"
            f"_Tip: D-shape = Range, P-shape = Buy Pullback, b-shape = Short Bounce._"
        )
        return report

# Singleton instance
vol_flow_service = VolumeFlowService()
