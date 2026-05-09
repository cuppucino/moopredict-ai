from typing import List, Dict
from loguru import logger
from sqlalchemy import func
from core.database import SessionLocal, TradeJournal
from services.notifications import notification_queue

class StrategyService:
    def __init__(self):
        self.strategies = ["MOMENTUM", "MEAN_REVERSION", "BREAKOUT"]

    def tag_trade(self, trade_id: int, strategy: str) -> bool:
        """Assign a strategy to a trade."""
        if strategy.upper() not in self.strategies:
            return False
            
        db = SessionLocal()
        try:
            trade = db.query(TradeJournal).filter(TradeJournal.id == trade_id).first()
            if trade:
                trade.strategy = strategy.upper()
                db.commit()
                return True
            return False
        finally:
            db.close()

    def get_strategy_stats(self) -> List[Dict]:
        """Calculate performance stats for each strategy."""
        db = SessionLocal()
        try:
            stats = []
            for strat in self.strategies:
                trades = db.query(TradeJournal).filter(
                    TradeJournal.strategy == strat,
                    TradeJournal.status == "CLOSED"
                ).all()
                
                if not trades:
                    stats.append({
                        "name": strat,
                        "total": 0,
                        "win_rate": 0,
                        "avg_pnl": 0
                    })
                    continue
                
                wins = len([t for t in trades if t.outcome == "WIN"])
                total = len(trades)
                avg_pnl = sum(t.pnl_percent for t in trades) / total
                
                stats.append({
                    "name": strat,
                    "total": total,
                    "win_rate": round((wins / total) * 100, 2),
                    "avg_pnl": round(avg_pnl, 2)
                })
            
            # Sort by win rate desc
            return sorted(stats, key=lambda x: x["win_rate"], reverse=True)
        finally:
            db.close()

    def evolve(self):
        """Weekly report on strategy performance."""
        stats = self.get_strategy_stats()
        if not any(s["total"] > 0 for s in stats):
            logger.info("[Strategy] No trade data for weekly evolution.")
            return

        lines = []
        for s in stats:
            lines.append(f"• *{s['name']}*: {s['win_rate']}% WR ({s['total']} trades, {s['avg_pnl']}% avg)")

        best = stats[0]
        worst = stats[-1] if stats[-1]["total"] > 0 else None
        
        advice = f"\n💡 *Evolution Advice:*\nStrategy *{best['name']}* is winning. "
        if worst and worst != best:
            advice += f"Strategy *{worst['name']}* is lagging, consider reducing its allocation."

        report = (
            f"🧬 *Weekly Strategy Evolution*\n"
            f"──────────────────\n"
            + "\n".join(lines) + "\n"
            + advice
        )
        
        notification_queue.enqueue(report, level="info", category="news")
        logger.info("[Strategy] Weekly evolution report sent.")

strategy_service = StrategyService()
