from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger
from sqlalchemy.orm import Session
from core.database import SessionLocal, Prediction
from services.moomoo_service import moomoo_service

class PredictionService:
    def create_prediction(
        self, 
        symbol: str, 
        direction: str, 
        confidence: float, 
        catalyst: str, 
        category: str = "general", 
        timeframe_days: int = 7,
        target_price: Optional[float] = None
    ) -> Dict:
        """
        Record a new market prediction.
        """
        db = SessionLocal()
        try:
            symbol = symbol.upper().strip()
            direction = direction.upper().strip()
            
            # Fetch current price for entry
            price_data = moomoo_service.get_stock_quote(symbol)
            entry_price = price_data.get("last_price", 0.0)
            
            deadline = datetime.utcnow() + timedelta(days=timeframe_days)
            
            prediction = Prediction(
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                catalyst=catalyst,
                category=category,
                timeframe_days=timeframe_days,
                entry_price=entry_price,
                target_price=target_price,
                deadline=deadline,
                created_at=datetime.utcnow()
            )
            
            db.add(prediction)
            db.commit()
            db.refresh(prediction)
            
            logger.info(f"[Prediction] Recorded #{prediction.id} for {symbol} ({direction})")
            return {"success": True, "prediction_id": prediction.id, "entry_price": entry_price}
            
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
