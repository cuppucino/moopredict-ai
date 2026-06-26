from typing import Dict, List
from loguru import logger
from services.ta_engine import ta_engine
from services.sentiment_engine import sentiment_engine
from services._legacy.options_engine import options_engine
from services.volume_flow_service import vol_flow_service
from services.sector_analysis import sector_service
from services.political_monitor import political_monitor

class SignalAggregator:
    def get_consensus(self, symbol: str) -> Dict:
        """Aggregate all signals into a final confidence score."""
        try:
            # 1. Fetch all signals
            ta = ta_engine.compute_all(symbol)
            sentiment = sentiment_engine.score_symbol(symbol)
            options = options_engine.get_chain_data(symbol)
            vol_flow = vol_flow_service.calculate_volume_profile(symbol)
            sector_influence = sector_service.get_rrg_influence(symbol)
            
            # 2. Normalize and Weight
            # TA: 0-100 (Scale to -50 to +50)
            ta_val = ta.get("composite_score", 50) - 50
            
            # Sentiment: -1 to +1 (Scale to -50 to +50)
            sent_val = sentiment.get("score", 0) * 50
            
            # Options: PCR and GEX
            # BULLISH if PCR < 0.6 or GEX > 0
            opt_score = 0
            if options:
                if options.get("pcr", 1.0) < 0.6: opt_score += 25
                elif options.get("pcr", 1.0) > 1.2: opt_score -= 25
                if options.get("total_gex", 0) > 0: opt_score += 25
                else: opt_score -= 25
            
            # 3. Insider Activity (SEC Filings)
            insider_score = 0
            try:
                from services._legacy.sec_filing_service import sec_service
                insider = sec_service.get_insider_activity(symbol)
                if "BULLISH" in insider["sentiment"]:
                    insider_score = 25
                elif "BEARISH" in insider["sentiment"]:
                    insider_score = -25
            except Exception as e:
                logger.warning(f"[SignalAggregator] Insider data error for {symbol}: {e}")

            # 4. News Sentiment (Scraped Intel)
            sent_val = 0
            try:
                from services.sentiment_engine import sentiment_engine
                sent_res = sentiment_engine.score_symbol(symbol)
                if sent_res.get("data_count", 0) > 0:
                    sent_val = sent_res["score"] * 50 # Normalize -1..1 to -50..50
            except Exception as e:
                logger.warning(f"[SignalAggregator] Sentiment error for {symbol}: {e}")

            # 4b. Political Sentiment (VIP Twitter/X)
            political_val = 0
            try:
                pol_data = political_monitor.get_recent_political_sentiment()
                if pol_data.get("post_count", 0) > 0:
                    political_val = pol_data["score"]  # Already -50..+50
            except Exception as e:
                logger.warning(f"[SignalAggregator] Political sentiment error for {symbol}: {e}")

            # 5. Volume Flow Score Derivation
            vol_val = 0
            if vol_flow and vol_flow.get("success"):
                v_score = 5 # Neutral base
                if vol_flow["trend"] == "BULLISH": v_score += 2
                else: v_score -= 2
                if "BUY" in vol_flow["signal"]: v_score += 2
                elif "SHORT" in vol_flow["signal"]: v_score -= 2
                vol_val = (v_score - 5) * 10 

            # 6. Final Calculation (Weighted Average)
            # Weights: TA (0.22), Sentiment (0.13), Options (0.13), Vol (0.10), Sector (0.10), Insider (0.12), Political (0.10), News (0.10)
            total_norm = (ta_val * 0.22) + (sent_val * 0.13) + (opt_score * 0.13) + (vol_val * 0.10) + (sector_influence * 0.10) + (insider_score * 0.12) + (political_val * 0.10)
            
            # Freshness Penalty: degrade confidence if data is stale
            from services.data_freshness import freshness_registry
            freshness = freshness_registry.get_staleness_report()
            stale_sources = [s for s, age in freshness.items() if age > 120] # 2 hours
            freshness_penalty = len(stale_sources) * 5 # -5 confidence per stale source
            
            # Convert back to 0-100 range
            confidence = 50 + total_norm - freshness_penalty
            confidence = max(0, min(100, confidence))
            
            # 5. Consensus Label
            label = "STRONG_BUY" if confidence > 75 else "BUY" if confidence > 60 else "STRONG_SELL" if confidence < 25 else "SELL" if confidence < 40 else "NEUTRAL"
            
            return {
                "symbol": symbol,
                "confidence_score": round(confidence, 1),
                "label": label,
                "components": {
                    "ta": ta,
                    "sentiment": sentiment,
                    "options": options,
                    "volume": vol_flow,
                    "sector_influence": sector_influence,
                    "political": political_val
                }
            }
            
        except Exception as e:
            logger.error(f"[SignalAggregator] Error aggregating signals for {symbol}: {e}")
            return {"error": str(e)}

signal_aggregator = SignalAggregator()
