from datetime import datetime
from typing import List, Dict
from loguru import logger
from sqlalchemy.orm import Session
from core.database import SessionLocal, Prediction, PatternDB

class PatternStatsService:
    def get_pattern_accuracy(self) -> List[Dict]:
        """Calculate win/loss statistics for each pattern in the database."""
        db = SessionLocal()
        try:
            patterns = db.query(PatternDB).all()
            stats = []
            
            for p in patterns:
                # Find all resolved predictions linked to this pattern
                resolved = db.query(Prediction).filter(
                    Prediction.pattern_id == p.id,
                    Prediction.outcome != None
                ).all()
                
                total = len(resolved)
                right = len([pr for pr in resolved if pr.outcome == "RIGHT"])
                
                accuracy = (right / total * 100) if total > 0 else 0
                
                stats.append({
                    "id": p.id,
                    "name": p.name,
                    "category": p.category,
                    "total_predictions": total,
                    "wins": right,
                    "accuracy_pct": round(accuracy, 2),
                    "last_used": p.last_seen.isoformat() if p.last_seen else None
                })
                
            # Sort by accuracy descending
            return sorted(stats, key=lambda x: x["accuracy_pct"], reverse=True)
            
        except Exception as e:
            logger.error(f"[PatternStats] Error calculating stats: {e}")
            return []
        finally:
            db.close()

    def update_pattern_usage(self, pattern_id: int):
        """Increment the usage counter for a pattern."""
        db = SessionLocal()
        try:
            pattern = db.query(PatternDB).filter(PatternDB.id == pattern_id).first()
            if pattern:
                pattern.times_seen += 1
                pattern.last_seen = datetime.utcnow()
                db.commit()
        except Exception as e:
            logger.error(f"[PatternStats] Error updating usage for {pattern_id}: {e}")
        finally:
            db.close()

pattern_stats_service = PatternStatsService()
