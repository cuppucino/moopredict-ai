from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger
from sqlalchemy.orm import Session
from core.database import SessionLocal, Prediction
from services.moomoo_service import moomoo_service

from services.validation_service import validation_service
from services.political_monitor import political_monitor
from services.news_scraper import news_scraper

def generate_thesis_from_citations(symbol: str, direction: str, citations: list) -> str:
    """Build a human-readable thesis string citing each catalyst."""
    try:
        lines = [f"{direction} on {symbol}. Catalysts:"]
        tweets = [c for c in citations if c["type"] == "tweet"]
        news = [c for c in citations if c["type"] == "news"]

        # Sort tweets by absolute weight descending, take top 3
        sorted_tweets = sorted(tweets, key=lambda c: -abs(c.get("weight", 0)))[:3]
        for t in sorted_tweets:
            sign = "+" if t.get("weight", 0) > 0 else "-"
            ts = t.get("timestamp", "")[:19]
            lines.append(
                f"  {sign} Tier-{t.get('tier')} @{t.get('handle')} ({ts}): "
                f"{t.get('content', '')[:80]}... ({t.get('sentiment')})"
            )

        # Sort news by absolute weight descending, take top 2
        sorted_news = sorted(news, key=lambda c: -abs(c.get("weight", 0)))[:2]
        for n in sorted_news:
            sign = "+" if n.get("weight", 0) > 0 else "-"
            lines.append(
                f"  {sign} News [{n.get('source')}]: {n.get('headline', '')[:80]}..."
            )

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[Prediction] Error generating thesis: {e}")
        return f"{direction} on {symbol} (Error generating citations thesis)"

class PredictionService:
    def create_prediction(
        self, 
        symbol: str, 
        direction: str, 
        confidence: float, 
        catalyst: str, 
        category: str = "general", 
        timeframe_days: int = 7,
        target_price: Optional[float] = None,
        pattern_id: Optional[int] = None,
        force: bool = False
    ) -> Dict:
        """
        Record a new market prediction with safety checks.
        """
        db = SessionLocal()
        try:
            symbol = symbol.upper().strip()
            direction = direction.upper().strip()
            
            # ─── Safety Checks ──────────────────────────────────────────────────
            if not force:
                v_res = validation_service.validate_prediction(symbol, direction)
                if not v_res["passed"]:
                    logger.warning(f"[Prediction] Safety check FAILED for {symbol}: {v_res['warnings']}")
                    return {
                        "success": False, 
                        "error": "Safety check failed.", 
                        "warnings": v_res["warnings"],
                        "can_override": True
                    }
                elif v_res["warnings"]:
                    logger.info(f"[Prediction] Safety check WARNINGS for {symbol}: {v_res['warnings']}")
                    # We still proceed but include warnings in the result
            
            # --- Signal Aggregation ---
            from services.signal_aggregator import signal_aggregator
            consensus = signal_aggregator.get_consensus(symbol)
            auto_conf = consensus.get("confidence_score", 50)
            
            # 🛡️ Rule: Minimum 40% Confidence Threshold
            if not force and auto_conf < 40:
                logger.warning(f"[Prediction] Low confidence REJECT for {symbol}: {auto_conf}% (Min: 40%)")
                return {
                    "success": False,
                    "error": f"Confidence score too low: {auto_conf}% (Minimum 40% required)",
                    "can_override": True
                }

            # Fetch current price for entry
            price_data = moomoo_service.get_stock_quote(symbol)
            entry_price = price_data.get("last_price", 0.0)
            
            from services.data_freshness import freshness_registry
            freshness_snapshot = freshness_registry.stamp_prediction_context(symbol)
            
            # --- Catalyst Extraction (Tweets + News) ---

            
            tweets = political_monitor.get_recent_ticker_tweets(symbol, hours=24)
            news = news_scraper.get_recent_for_ticker(symbol, hours=24)
            
            confidence_delta = 0.0
            citations = []
            
            # VIP Tweets modifier
            for tweet in tweets:
                tier = tweet.get("tier")
                sent = tweet.get("sentiment")
                weight = {"S": 0.20, "A": 0.10, "B": 0.05}.get(tier, 0.0)
                
                if sent == "neutral":
                    signed_weight = 0.0
                else:
                    align = (sent == "bullish" and direction == "UP") or (sent == "bearish" and direction == "DOWN")
                    signed_weight = weight if align else -weight
                
                confidence_delta += signed_weight * 100
                citations.append({
                    "type": "tweet",
                    "handle": tweet.get("handle"),
                    "tier": tier,
                    "timestamp": tweet.get("timestamp"),
                    "content": tweet.get("content"),
                    "sentiment": sent,
                    "weight": signed_weight
                })
                
            # News Headlines modifier
            for headline in news:
                sent = headline.get("sentiment")
                if sent == "neutral":
                    signed_weight = 0.0
                else:
                    align = (sent == "bullish" and direction == "UP") or (sent == "bearish" and direction == "DOWN")
                    signed_weight = 0.05 if align else -0.05
                    
                confidence_delta += signed_weight * 100
                citations.append({
                    "type": "news",
                    "source": headline.get("source"),
                    "timestamp": headline.get("timestamp"),
                    "headline": headline.get("headline"),
                    "sentiment": sent,
                    "weight": signed_weight
                })
                
            # No catalyst penalty
            if not tweets and not news:
                confidence_delta -= 5.0
                
            # Apply bounds
            final_confidence = max(20.0, min(95.0, confidence + confidence_delta))
            
            # Build cited thesis
            if citations:
                thesis = generate_thesis_from_citations(symbol, direction, citations)
            else:
                thesis = f"{direction} on {symbol} from TA composite only (no catalysts in 24h)"
            
            prediction = Prediction(
                symbol=symbol,
                direction=direction,
                confidence=final_confidence,
                confidence_score=auto_conf,
                signal_summary=consensus,
                catalyst=thesis,
                category=category,
                timeframe_days=timeframe_days,
                entry_price=entry_price,
                target_price=target_price,
                pattern_id=pattern_id,
                deadline=datetime.utcnow() + timedelta(days=timeframe_days),
                created_at=datetime.utcnow(),
                notes=f"Freshness: {freshness_snapshot}",
                thesis_citations=citations
            )
            
            db.add(prediction)
            db.commit()
            db.refresh(prediction)
            
            logger.info(f"[Prediction] Recorded #{prediction.id} for {symbol} ({direction})")
            
            # If there were warnings even though it passed, return them
            v_res = validation_service.validate_prediction(symbol, direction) if not force else {"warnings": []}
            
            return {
                "success": True, 
                "prediction_id": prediction.id, 
                "entry_price": entry_price,
                "warnings": v_res.get("warnings", [])
            }
            
        except Exception as e:
            logger.error(f"[Prediction] Create error: {e}")
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def resolve_pending_predictions(self) -> List[Dict]:
        """
        Check all pending predictions whose deadline has passed and resolve them.
        """
        db = SessionLocal()
        resolved_list = []
        try:
            now = datetime.utcnow()
            pending = db.query(Prediction).filter(
                Prediction.outcome == None,
                Prediction.deadline <= now
            ).all()
            
            for pred in pending:
                result = self.resolve_prediction(pred.id)
                if result.get("success"):
                    resolved_list.append(result)
            
            return resolved_list
        except Exception as e:
            logger.error(f"[Prediction] Auto-resolve error: {e}")
            return []
        finally:
            db.close()

    def resolve_prediction(self, prediction_id: int, manual_notes: str = "") -> Dict:
        """
        Evaluate a single prediction against current market data.
        """
        db = SessionLocal()
        try:
            prediction = db.query(Prediction).filter(Prediction.id == prediction_id).first()
            if not prediction:
                return {"success": False, "error": "Prediction not found"}
            
            if prediction.outcome:
                return {"success": False, "error": "Already resolved"}

            # Get current price
            price_data = moomoo_service.get_stock_quote(prediction.symbol)
            exit_price = price_data.get("last_price", 0.0)
            
            if exit_price <= 0:
                return {"success": False, "error": "Could not fetch exit price"}

            move_pct = ((exit_price - prediction.entry_price) / prediction.entry_price) * 100 if prediction.entry_price > 0 else 0
            
            outcome = "WRONG"
            if prediction.direction == "UP" and move_pct > 0.5: # 0.5% buffer
                outcome = "RIGHT"
            elif prediction.direction == "DOWN" and move_pct < -0.5:
                outcome = "RIGHT"
            elif prediction.direction == "FLAT" and abs(move_pct) <= 0.5:
                outcome = "RIGHT"
            
            prediction.outcome = outcome
            prediction.exit_price = exit_price
            prediction.actual_move_pct = move_pct
            prediction.resolved_at = datetime.utcnow()
            prediction.notes = manual_notes
            
            # --- Auto Post-Mortem ---
            try:
                from services.prediction_postmortem import postmortem_service
                prediction.postmortem = postmortem_service.analyze(prediction)
            except Exception as pe:
                logger.error(f"[Prediction] Postmortem failed: {pe}")
            
            db.commit()
            logger.info(f"[Prediction] Resolved #{prediction.id} as {outcome} ({move_pct:.2f}%)")
            
            return {
                "success": True, 
                "id": prediction.id, 
                "symbol": prediction.symbol, 
                "outcome": outcome, 
                "move_pct": move_pct
            }
            
        except Exception as e:
            logger.error(f"[Prediction] Resolve error: {e}")
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    def get_stats(self) -> Dict:
        """
        Calculate accuracy statistics.
        """
        db = SessionLocal()
        try:
            total = db.query(Prediction).filter(Prediction.outcome != None).count()
            right = db.query(Prediction).filter(Prediction.outcome == "RIGHT").count()
            
            accuracy = (right / total * 100) if total > 0 else 0
            
            # By category
            categories = db.query(Prediction.category).distinct().all()
            cat_stats = {}
            for (cat,) in categories:
                c_total = db.query(Prediction).filter(Prediction.category == cat, Prediction.outcome != None).count()
                if c_total > 0:
                    c_right = db.query(Prediction).filter(Prediction.category == cat, Prediction.outcome == "RIGHT").count()
                    cat_stats[cat] = round((c_right / c_total * 100), 2)

            return {
                "total_resolved": total,
                "accuracy_pct": round(accuracy, 2),
                "by_category": cat_stats
            }
        except Exception as e:
            logger.error(f"[Prediction] Stats error: {e}")
            return {}
        finally:
            db.close()

    def get_active(self, limit: int = 10) -> List[Dict]:
        db = SessionLocal()
        try:
            preds = db.query(Prediction).filter(Prediction.outcome == None).order_by(Prediction.deadline.asc()).limit(limit).all()
            return [
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "confidence": p.confidence,
                    "deadline": p.deadline.isoformat(),
                    "catalyst": p.catalyst
                } for p in preds
            ]
        finally:
            db.close()

prediction_service = PredictionService()
