from datetime import datetime
from typing import List, Dict, Optional
from loguru import logger
from core.database import SessionLocal, TradeJournal

class TradeJournalService:
    def log_trade(self, symbol: str, side: str, qty: float, price: float, order_type: str = "MARKET", thesis: str = "", stop: float = None, target: float = None) -> int:
        """Create a new trade journal entry."""
        db = SessionLocal()
        try:
            trade = TradeJournal(
                symbol=symbol,
                side=side,
                quantity=qty,
                entry_price=price,
                order_type=order_type,
                status="OPEN",
                thesis=thesis,
                stop_loss=stop,
                take_profit=target,
                entry_time=datetime.utcnow()
            )
            db.add(trade)
            db.commit()
            db.refresh(trade)
            logger.info(f"[Journal] Logged trade #{trade.id}: {side} {qty} {symbol} @ {price}")
            return trade.id
        except Exception as e:
            logger.error(f"[Journal] Error logging trade: {e}")
            db.rollback()
            return -1
        finally:
            db.close()

    def close_trade(self, trade_id: int, exit_price: float, lessons: str = "") -> bool:
        """Close an open trade and calculate P&L."""
        db = SessionLocal()
        try:
            trade = db.query(TradeJournal).filter(TradeJournal.id == trade_id).first()
            if not trade or trade.status != "OPEN":
                return False
            
            trade.exit_price = exit_price
            trade.exit_time = datetime.utcnow()
            trade.status = "CLOSED"
            trade.lessons = lessons
            
            # Calculate P&L
            # For BUY: (Exit - Entry) * Qty
            # For SELL: (Entry - Exit) * Qty
            multiplier = 1 if trade.side == "BUY" else -1
            trade.pnl_amount = (exit_price - trade.entry_price) * trade.quantity * multiplier
            trade.pnl_percent = ((exit_price / trade.entry_price) - 1) * 100 * multiplier
            
            if trade.pnl_amount > 0:
                trade.outcome = "WIN"
            elif trade.pnl_amount < 0:
                trade.outcome = "LOSS"
            else:
                trade.outcome = "BREAKEVEN"
                
            db.commit()
            logger.info(f"[Journal] Closed trade #{trade_id} with {trade.outcome} (${trade.pnl_amount:.2f})")
            return True
        except Exception as e:
            logger.error(f"[Journal] Error closing trade: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def update_thesis(self, trade_id: int, thesis: str) -> bool:
        db = SessionLocal()
        try:
            trade = db.query(TradeJournal).filter(TradeJournal.id == trade_id).first()
            if trade:
                trade.thesis = thesis
                db.commit()
                return True
            return False
        finally:
            db.close()

    def add_lesson(self, trade_id: int, lesson: str) -> bool:
        db = SessionLocal()
        try:
            trade = db.query(TradeJournal).filter(TradeJournal.id == trade_id).first()
            if trade:
                new_lessons = (trade.lessons + "\n" + lesson) if trade.lessons else lesson
                trade.lessons = new_lessons
                db.commit()
                return True
            return False
        finally:
            db.close()

    def get_stats(self) -> Dict:
        db = SessionLocal()
        try:
            closed = db.query(TradeJournal).filter(TradeJournal.status == "CLOSED").all()
            if not closed:
                return {"total": 0, "win_rate": 0, "total_pnl": 0}
            
            wins = [t for t in closed if t.outcome == "WIN"]
            total_pnl = sum(t.pnl_amount for t in closed)
            
            return {
                "total": len(closed),
                "win_rate": f"{(len(wins) / len(closed)) * 100:.1f}%",
                "total_pnl": f"${total_pnl:.2f}",
                "avg_pnl_pct": f"{sum(t.pnl_percent for t in closed) / len(closed):.2f}%"
            }
        finally:
            db.close()

    def get_recent(self, limit: int = 10) -> List[Dict]:
        db = SessionLocal()
        try:
            trades = db.query(TradeJournal).order_by(TradeJournal.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "qty": t.quantity,
                    "entry": t.entry_price,
                    "exit": t.exit_price,
                    "status": t.status,
                    "pnl": f"${t.pnl_amount:.2f}" if t.pnl_amount else "N/A"
                } for t in trades
            ]
        finally:
            db.close()

trade_journal = TradeJournalService()
