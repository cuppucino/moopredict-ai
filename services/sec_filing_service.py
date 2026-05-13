import os
import requests
import yfinance as yf
import pandas as pd
from loguru import logger
from typing import Dict, List, Optional

class SECFilingService:
    def __init__(self):
        self._cik_cache = {}
        self.user_agent = "MooPredict Intelligence Engine (admin@moopredict.ai)"

    def _get_cik(self, symbol: str) -> Optional[str]:
        """Map ticker to CIK using SEC's company_tickers.json."""
        symbol = symbol.upper()
        if symbol in self._cik_cache:
            return self._cik_cache[symbol]
        
        try:
            url = "https://www.sec.gov/files/company_tickers.json"
            response = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=10)
            data = response.json()
            
            for entry in data.values():
                if entry['ticker'] == symbol:
                    cik = str(entry['cik_str']).zfill(10)
                    self._cik_cache[symbol] = cik
                    return cik
            return None
        except Exception as e:
            logger.error(f"[SEC] CIK lookup failed for {symbol}: {e}")
            return None

    def get_recent_filings(self, symbol: str) -> List[Dict]:
        """Fetch last 10 filings via SEC EDGAR."""
        cik = self._get_cik(symbol)
        if not cik: return []
        
        try:
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            response = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=10)
            data = response.json()
            
            filings = data.get("filings", {}).get("recent", {})
            results = []
            count = len(filings.get("form", []))
            for i in range(min(count, 10)):
                results.append({
                    "date": filings["filingDate"][i],
                    "form": filings["form"][i],
                    "description": filings["primaryDocDescription"][i]
                })
            return results
        except Exception as e:
            logger.error(f"[SEC] Filings fetch failed for {symbol}: {e}")
            return []

    def get_insider_activity(self, symbol: str) -> Dict:
        """Analyze insider transactions using yfinance."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.insider_transactions
            
            if df is None or df.empty:
                return {"sentiment": "NEUTRAL", "buys": 0, "sells": 0}
            
            # Filter for last 6 months
            # df['Start Date'] exists
            df['Transaction Date'] = pd.to_datetime(df.index)
            
            # yfinance insider data columns: 'Shares', 'Value', 'Transaction', etc.
            # We look for "Sale" or "Buy" or "Option Exercise"
            buys = df[df['Transaction'].str.contains('Buy', case=False, na=False)]
            sells = df[df['Transaction'].str.contains('Sale', case=False, na=False)]
            
            buy_count = len(buys)
            sell_count = len(sells)
            
            sentiment = "NEUTRAL"
            if buy_count > sell_count: sentiment = "BULLISH (Insider Buying)"
            elif sell_count > buy_count: sentiment = "BEARISH (Insider Selling)"
            
            return {
                "sentiment": sentiment,
                "buys": buy_count,
                "sells": sell_count,
                "recent_trades": df.head(5).to_dict('records')
            }
        except Exception as e:
            logger.error(f"[SEC] Insider analysis failed for {symbol}: {e}")
            return {"sentiment": "ERROR", "buys": 0, "sells": 0}

    def get_summary(self, symbol: str) -> Dict:
        """Unified institutional context (Filings + Insiders)."""
        insiders = self.get_insider_activity(symbol)
        filings = self.get_recent_filings(symbol)
        
        return {
            "symbol": symbol,
            "success": True,
            "insider_sentiment": insiders["sentiment"],
            "buy_count": insiders["buys"],
            "sell_count": insiders["sells"],
            "filings_count": len(filings),
            "recent_filings": filings[:5]
        }

sec_service = SECFilingService()
