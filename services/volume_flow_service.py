import yfinance as yf
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from loguru import logger

class VolumeFlowService:
    def calculate_volume_profile(self, symbol: str, resolution: str = "1w", bins: int = 50) -> Dict:
        """
        Calculate Institutional Volume Flow metrics (POC, Midpoint, etc.)
        resolution: '1d', '1w', '1m'
        """
        # Clean symbol for yfinance
        yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
        
        # Determine period based on resolution
        period_map = {
            "1d": "2d", # Get 2 days to ensure we have a full current day
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
                return {"error": f"No data found for {symbol}"}

            # Calculate metrics for the requested segment
            highest_high = float(hist['High'].max())
            lowest_low = float(hist['Low'].min())
            midpoint = (highest_high + lowest_low) / 2
            current_price = float(hist['Close'].iloc[-1])
            
            # Find POC (Point of Control)
            # We create a histogram of volume at each price level
            price_min = hist['Low'].min()
            price_max = hist['High'].max()
            
            # Create price bins
            bin_edges = np.linspace(price_min, price_max, bins + 1)
            bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
            
            # Map each candle to a bin and aggregate volume
            # For more accuracy, we distribute candle volume across its range
            profile = np.zeros(bins)
            for _, row in hist.iterrows():
                # Simple approximation: assign full volume to the bin of the Close price
                # Or more advanced: distribute between High and Low
                bin_idx = np.digitize(row['Close'], bin_edges) - 1
                if 0 <= bin_idx < bins:
                    profile[bin_idx] += row['Volume']
            
            # POC is the center of the bin with the highest volume
            poc_idx = np.argmax(profile)
            poc = float(bin_centers[poc_idx])
            
            # Determine trend and signal based on Big Beluga rules
            trend = "BULLISH" if current_price > midpoint else "BEARISH"
            
            distance_to_poc = abs(current_price - poc) / current_price * 100
            
            signal = "NEUTRAL"
            if distance_to_poc < 1.5: # Within 1.5% of POC
                if current_price > midpoint:
                    signal = "BUY near POC support"
                else:
                    signal = "SHORT near POC resistance"
            
            return {
                "symbol": symbol,
                "resolution": resolution,
                "current_price": round(current_price, 2),
                "poc": round(poc, 2),
                "midpoint": round(midpoint, 2),
                "highest_high": round(highest_high, 2),
                "lowest_low": round(lowest_low, 2),
                "trend": trend,
                "signal": signal,
                "distance_to_poc_pct": round(distance_to_poc, 2),
                "success": True
            }
            
        except Exception as e:
            logger.error(f"[VolFlow] Error calculating for {symbol}: {e}")
            return {"error": str(e), "success": False}

    def get_signal_report(self, symbol: str, resolution: str = "1w") -> str:
        """Generate a formatted report for Telegram."""
        data = self.calculate_volume_profile(symbol, resolution)
        if not data.get("success"):
            return f"❌ *Error calculating Volume Flow:* {data.get('error')}"
        
        trend_emoji = "🟢" if data['trend'] == "BULLISH" else "🔵"
        signal_emoji = "🎯" if "BUY" in data['signal'] or "SHORT" in data['signal'] else "⚖️"
        
        report = (
            f"📊 *Institutional Volume Flow: {data['symbol']}*\n"
            f"Resolution: {data['resolution']}\n"
            f"──────────────────\n"
            f"💵 Price: ${data['current_price']}\n"
            f"🟠 POC: ${data['poc']} ({data['distance_to_poc_pct']}% dist)\n"
            f"📏 Midpoint: ${data['midpoint']}\n"
            f"🏔️ Range: ${data['lowest_low']} - ${data['highest_high']}\n\n"
            f"📈 Trend: {trend_emoji} *{data['trend']}*\n"
            f"{signal_emoji} Signal: *{data['signal']}*\n\n"
            f"_POC = support in uptrend, resistance in downtrend._"
        )
        return report

# Singleton instance
vol_flow_service = VolumeFlowService()
