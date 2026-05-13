from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from loguru import logger
import yfinance as yf
from services.earnings_calendar import earnings_service
from services.sentiment_service import sentiment_service
from services.technical_analysis import ta_service
from services.options_flow import options_service

class ValidationService:
    def is_earnings_blocked(self, symbol: str) -> Tuple[bool, str]:
        """
        Determine if trading/predicting is blocked due to upcoming or recent earnings.
        Rule: Block 3 days BEFORE and 1 day AFTER earnings.
        """
        try:
            earnings = earnings_service.get_stock_earnings(symbol)
            if not earnings.get("success"):
                return False, ""

            # Check Upcoming
            if earnings.get("earnings_dates"):
                next_date_str = earnings["earnings_dates"][0]
                next_date = datetime.strptime(next_date_str, "%Y-%m-%d")
                days_to = (next_date - datetime.now()).days
                
                if 0 <= days_to <= 3:
                    return True, f"🔴 EARNINGS BLOCK: Earnings in {days_to} days ({next_date_str})."

            # Check Recent (Cooldown)
            # We'll use yfinance earnings history to find the most recent date
            ticker = yf.Ticker(symbol)
            hist = ticker.earnings_history
            if hist is not None and not hist.empty:
                last_date = hist.index[0]
                # Convert index to datetime if it's not already
                if isinstance(last_date, str):
                    last_date = datetime.strptime(last_date, "%Y-%m-%d")
                
                days_since = (datetime.now() - last_date).days
                if 0 <= days_since <= 1:
                    return True, f"🔴 EARNINGS COOLDOWN: Earnings were {days_since} days ago. Wait for volatility to settle."

            return False, ""
        except Exception as e:
            logger.error(f"[Validation] Earnings check error for {symbol}: {e}")
            return False, ""

    def validate_prediction(self, symbol: str, direction: str) -> Dict:
        """
        Perform a comprehensive safety check before allowing a prediction.
        """
        symbol = symbol.upper().strip()
        direction = direction.upper().strip()
        warnings = []
        scores = {}
        
        try:
            # 1. Earnings Catalyst Check (Refactored)
            is_blocked, message = self.is_earnings_blocked(symbol)
            if is_blocked:
                warnings.append(message)
            
            # Additional metadata for UI
            earnings = earnings_service.get_stock_earnings(symbol)
            if earnings.get("earnings_dates"):
                next_date = datetime.strptime(earnings["earnings_dates"][0], "%Y-%m-%d")
                scores["earnings_days"] = (next_date - datetime.now()).days
            
            # 2. Sentiment Check
            sentiment = sentiment_service.get_sentiment_score(symbol)
            scores["sentiment"] = sentiment["score"]
            if direction == "UP" and sentiment["score"] <= -0.4:
                warnings.append(f"🔴 SENTIMENT CONFLICT: You predicted UP, but sentiment is {sentiment['label']} ({sentiment['score']}). {sentiment['reason']}")
            elif direction == "DOWN" and sentiment["score"] >= 0.4:
                warnings.append(f"🔴 SENTIMENT CONFLICT: You predicted DOWN, but sentiment is {sentiment['label']} ({sentiment['score']}). {sentiment['reason']}")
            
            # Sentiment Shift Detection
            from services.sentiment_engine import sentiment_engine
            shift = sentiment_engine.detect_shift(symbol)
            if shift:
                warnings.append(f"⚠️ SENTIMENT SHIFT: {shift}")
            
            # 3. TA Composite Check (Replacement for RSI-only check)
            from services.ta_engine import ta_engine
            ta = ta_engine.compute_all(symbol)
            if "error" not in ta:
                composite = ta["composite_score"]
                regime = ta["regime"]
                scores["ta_composite"] = composite
                scores["market_regime"] = regime
                
                if direction == "UP" and composite < 35:
                    warnings.append(f"🔴 TA CONFLICT: Predicted UP, but TA Composite is bearish ({composite}/100).")
                elif direction == "DOWN" and composite > 65:
                    warnings.append(f"🔴 TA CONFLICT: Predicted DOWN, but TA Composite is bullish ({composite}/100).")
                
                if regime == "RANGE" and direction in ["UP", "DOWN"]:
                    warnings.append(f"⚠️ RANGE BOUND: Market is sideways. Directional bets are lower probability.")
            
            # 4. Options Flow (Smart Money)
            from services.options_engine import options_engine
            options = options_engine.get_chain_data(symbol)
            if options:
                pcr = options["pcr"]
                max_pain = options["max_pain"]
                spot = options["spot_price"]
                scores["pcr"] = pcr
                scores["max_pain"] = max_pain
                
                # PCR Over-extension
                if pcr > 1.5:
                    warnings.append(f"⚠️ EXTREME FEAR: PCR is {pcr}. Market might be bottoming (Contrarian Buy).")
                elif pcr < 0.5:
                    warnings.append(f"⚠️ EXTREME GREED: PCR is {pcr}. Market might be topping (Contrarian Sell).")
                
                # Max Pain Gravity
                # If price is far from max pain, it tends to pull back towards it near expiration
                pain_diff_pct = (spot - max_pain) / spot
                if abs(pain_diff_pct) > 0.05:
                    direction_str = "ABOVE" if pain_diff_pct > 0 else "BELOW"
                    warnings.append(f"🧲 MAX PAIN GRAVITY: Price is {abs(pain_diff_pct):.1%} {direction_str} Max Pain (${max_pain}). Expect magnet effect.")

            # 5. Insider Activity Check (SMCI fix)
            try:
                from services.sec_filing_service import sec_service
                insider = sec_service.get_insider_activity(symbol)
                if insider["sentiment"] == "BEARISH (Insider Selling)" and direction == "UP":
                    warnings.append(f"🔴 INSIDER CONFLICT: Heavy insider selling detected ({insider['sells']} sells).")
                elif insider["sentiment"] == "BULLISH (Insider Buying)" and direction == "DOWN":
                    warnings.append(f"🔴 INSIDER CONFLICT: Recent insider buying detected ({insider['buys']} buys).")
            except Exception as e:
                logger.warning(f"[Validation] Insider check failed for {symbol}: {e}")

            # 6. Data Freshness Gate
            from services.data_freshness import freshness_registry
            if not freshness_registry.is_fresh("moomoo_quote", 120, symbol):
                age = freshness_registry.get_age_minutes("moomoo_quote", symbol)
                warnings.append(f"🔴 STALE DATA: Price data is {round(age)} min old. Heartbeat may be dead.")

            # 7. Short-Dated Options Check
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
