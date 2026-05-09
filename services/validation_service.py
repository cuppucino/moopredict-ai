from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger
from services.earnings_calendar import earnings_service
from services.sentiment_service import sentiment_service
from services.technical_analysis import ta_service
from services.options_flow import options_service

class ValidationService:
    def validate_prediction(self, symbol: str, direction: str) -> Dict:
        """
        Perform a comprehensive safety check before allowing a prediction.
        Aims to catch 'AMD-style' (bad sentiment) or 'PLTR-style' (priced in) mistakes.
        """
        symbol = symbol.upper().strip()
        direction = direction.upper().strip()
        warnings = []
        scores = {}
        
        try:
            # 1. Earnings Catalyst Check
            earnings = earnings_service.get_stock_earnings(symbol)
            if earnings.get("success") and earnings.get("earnings_dates"):
                next_date_str = earnings["earnings_dates"][0]
                next_date = datetime.strptime(next_date_str, "%Y-%m-%d")
                days_to_earnings = (next_date - datetime.now()).days
                
                if 0 <= days_to_earnings <= 3:
                    if days_to_earnings <= 1:
                        warnings.append(f"🔴 EARNINGS BLOCK: Earnings in {days_to_earnings} days. Rule: Don't predict direction before earnings.")
                    else:
                        warnings.append(f"⚠️ EARNINGS CATALYST: Earnings in {days_to_earnings} days ({next_date_str}). High volatility expected.")
                scores["earnings_days"] = days_to_earnings
            
            # 2. Sentiment Check
            sentiment = sentiment_service.get_sentiment_score(symbol)
            scores["sentiment"] = sentiment["score"]
            if direction == "UP" and sentiment["score"] <= -0.4:
                warnings.append(f"🔴 SENTIMENT CONFLICT: You predicted UP, but sentiment is {sentiment['label']} ({sentiment['score']}). {sentiment['reason']}")
            elif direction == "DOWN" and sentiment["score"] >= 0.4:
                warnings.append(f"🔴 SENTIMENT CONFLICT: You predicted DOWN, but sentiment is {sentiment['label']} ({sentiment['score']}). {sentiment['reason']}")
            
            # 3. "Priced In" Detector (PLTR-style mistake)
            ta = ta_service.get_full_analysis(symbol)
            if "error" not in ta:
                rsi = ta.get("rsi")
                scores["rsi"] = rsi
                if direction == "UP" and rsi and rsi > 70:
                    warnings.append(f"⚠️ PRICED IN? RSI is {rsi} (Overbought). Prediction might be too late (PLTR-style mistake).")
                
                # Check analyst ratings vs price
                if earnings.get("analyst_rating"):
                    # recommendationMean is usually 1 (Strong Buy) to 5 (Strong Sell)
                    rating = earnings["analyst_rating"]
                    scores["analyst_rating"] = rating
                    if direction == "UP" and rating > 3.0: # Neutral or worse
                        warnings.append(f"⚠️ ANALYST SKEPTICISM: Rating is {rating:.2f} (Neutral/Sell). Catalyst might not be strong enough.")
            
            # 4. Options Flow (Smart Money)
            options = options_service.get_pcr(symbol)
            if "error" not in options:
                pcr = options["pcr"]
                scores["put_call_ratio"] = pcr
                if direction == "UP" and pcr > 1.2:
                    warnings.append(f"⚠️ OPTIONS SKEPTICISM: Put/Call Ratio is {pcr} ({options['sentiment']}). Smart money is betting against you.")
                elif direction == "DOWN" and pcr < 0.6:
                    warnings.append(f"⚠️ OPTIONS SKEPTICISM: Put/Call Ratio is {pcr} ({options['sentiment']}). Smart money is betting against you.")

            # 6. Short-Dated Options Check
            if len(symbol) > 10: # Likely an option
                import re
                # Try to extract date like 260508 (YYMMDD)
                match = re.search(r'(\d{6})', symbol)
                if match:
                    date_str = match.group(1)
                    try:
                        expiry = datetime.strptime(date_str, "%y%m%d")
                        days_to_expiry = (expiry - datetime.now()).days
                        scores["dte"] = days_to_expiry
                        if days_to_expiry < 14:
                            warnings.append(f"🔴 HIGH RISK OPTION: Only {days_to_expiry} days to expiry. Time decay (theta) will kill profits. Rule: Avoid < 14 DTE.")
                    except:
                        pass

            passed = len([w for w in warnings if "🔴" in w]) == 0
            
            return {
                "symbol": symbol,
                "passed": passed,
                "warnings": warnings,
                "scores": scores,
                "recommendation": "PROCEED" if passed and not warnings else "PROCEED WITH CAUTION" if passed else "REJECT"
            }
            
        except Exception as e:
            logger.error(f"[Validation] Error for {symbol}: {e}")
            return {"symbol": symbol, "passed": True, "warnings": [f"Validation error: {e}"], "scores": {}}

validation_service = ValidationService()
