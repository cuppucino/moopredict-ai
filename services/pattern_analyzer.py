from typing import Dict, List
from sqlalchemy import func
from loguru import logger
from core.database import SessionLocal, Prediction

class PatternAnalyzer:
    def __init__(self):
        pass

    def analyze_effectiveness(self) -> Dict:
        """Analyze the performance of different prediction catalysts."""
        db = SessionLocal()
        try:
            # Query stats grouped by category
            stats = db.query(
                Prediction.category,
                func.count(Prediction.id).label("total"),
                func.sum(func.case((Prediction.outcome == 'RIGHT', 1), else_=0)).label("correct"),
                func.avg(func.abs(Prediction.actual_move_pct)).label("avg_move")
            ).filter(Prediction.outcome.isnot(None)).group_by(Prediction.category).all()

            results = {
                "total": 0,
                "effective": 0,
                "needs_review": 0,
                "by_category": {}
            }

            for s in stats:
                cat = s.category or "general"
                correct = s.correct or 0
                total = s.total or 0
                win_rate = (correct / total * 100) if total > 0 else 0
                
                results["total"] += total
                results["effective"] += correct
                
                results["by_category"][cat] = {
                    "total": total,
                    "win_rate": round(win_rate, 1),
                    "avg_move": round(float(s.avg_move or 0), 2)
                }
                
                if win_rate < 50 and total > 5:
                    results["needs_review"] += 1

            return results
        except Exception as e:
            logger.error(f"[PatternAnalyzer] Analysis failed: {e}")
            return {"error": str(e)}
        finally:
            db.close()

pattern_analyzer = PatternAnalyzer()
