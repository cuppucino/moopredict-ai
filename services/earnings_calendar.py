import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger

class EarningsCalendarService:
    def get_stock_earnings(self, symbol: str) -> Dict:
        """Fetch comprehensive earnings data for a stock."""
        yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
        
        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info
            calendar = ticker.calendar
            
            res = {
                "symbol": symbol,
                "success": True,
                "earnings_dates": [],
                "eps_estimate": info.get("forwardEps"),
                "revenue_estimate": None,
                "previous_eps": info.get("trailingEps"),
                "analyst_rating": info.get("recommendationMean"),
                "history": []
            }

            # 1. Process Calendar (Upcoming)
            if calendar is not None:
                if isinstance(calendar, dict):
                    res["earnings_dates"] = [d.strftime("%Y-%m-%d") for d in calendar.get("Earnings Date", []) if hasattr(d, "strftime")]
                    res["eps_estimate"] = calendar.get("EPS Estimate") or res["eps_estimate"]
                    res["revenue_estimate"] = calendar.get("Revenue Estimate")
                else: # DataFrame
                    cal_dict = calendar.to_dict()
                    dates = cal_dict.get("Earnings Date", {})
                    res["earnings_dates"] = [d.strftime("%Y-%m-%d") for d in dates.values() if hasattr(d, "strftime")]
                    res["eps_estimate"] = list(cal_dict.get("EPS Estimate", {}).values())[0] if cal_dict.get("EPS Estimate") else res["eps_estimate"]
                    res["revenue_estimate"] = list(cal_dict.get("Revenue Estimate", {}).values())[0] if cal_dict.get("Revenue Estimate") else None

            # Fallback for nextEarningsDate if calendar is empty
            if not res["earnings_dates"] and "nextEarningsDate" in info:
                dt = datetime.fromtimestamp(info["nextEarningsDate"])
                res["earnings_dates"] = [dt.strftime("%Y-%m-%d")]

            # 2. Get Earnings History (Beat/Miss)
            try:
                hist = ticker.earnings_history
                if hist is not None and not hist.empty:
                    # Take last 4 quarters
                    for idx, row in hist.head(4).iterrows():
                        res["history"].append({
                            "period": str(idx),
                            "eps_actual": float(row['EPS Actual']) if pd.notnull(row['EPS Actual']) else None,
                            "eps_estimate": float(row['EPS Estimate']) if pd.notnull(row['EPS Estimate']) else None,
                            "surprise_pct": float(row['Surprise(%)']) if pd.notnull(row['Surprise(%)']) else None
                        })
            except Exception as e:
                logger.debug(f"History not available for {symbol}: {e}")

            return res
            
        except Exception as e:
            logger.error(f"[Earnings] Error fetching for {symbol}: {e}")
            return {"symbol": symbol, "success": False, "error": str(e)}

    def get_watchlist_earnings(self, symbols: List[str]) -> List[Dict]:
        """Fetch earnings for a list of symbols."""
        results = []
        for sym in symbols:
            results.append(self.get_stock_earnings(sym))
        return results

earnings_service = EarningsCalendarService()
