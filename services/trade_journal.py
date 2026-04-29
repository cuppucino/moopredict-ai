import os
from datetime import datetime
from typing import List, Dict, Optional
from loguru import logger
from core.database import SessionLocal, TradeJournal
from services.moomoo_service import moomoo_service

class TradeJournalService:
    def _log_to_file(self, content: str):
        """Append a trade record to trades.md."""
        try:
            file_exists = os.path.exists("trades.md")
            with open("trades.md", "a") as f:
                if not file_exists:
                    f.write("| Timestamp | ID | Symbol | Side | Qty | Price | Status | P&L | Thesis/Lessons |\n")
                    f.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
                f.write(content + "\n")
        except Exception as e:
            logger.error(f"[Journal] File logging error: {e}")

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
            
            # Backup to trades.md
            log_line = f"| {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} | {trade.id} | {symbol} | {side} | {qty} | {price} | OPEN | - | {thesis} |"
            self._log_to_file(log_line)
            
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
            
            # Backup to trades.md
            log_line = f"| {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} | {trade.id} | {trade.symbol} | {trade.side} | {trade.quantity} | {exit_price} | CLOSED | {trade.outcome} ({trade.pnl_percent:.2f}%) | {lessons} |"
            self._log_to_file(log_line)
            
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

    def sync_past_trades(self, days: int = 30) -> Dict:
        """Sync trades from Moomoo to the local journal."""
        from datetime import datetime, timedelta
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        m_trades = moomoo_service.get_trade_history(start_date=start_date)
        if not m_trades:
            return {"count": 0, "status": "No trades found in Moomoo or not connected"}
            
        db = SessionLocal()
        synced_count = 0
        try:
            for mt in m_trades:
                # Check if already exists by external_id
                existing = db.query(TradeJournal).filter(TradeJournal.external_id == mt['order_id']).first()
                if not existing:
                    # Map Moomoo order to TradeJournal entry
                    # Note: We treat every Moomoo order as an entry for now
                    # In a more complex system, we'd match BUY/SELL pairs
                    new_trade = TradeJournal(
                        symbol=mt['symbol'],
                        side=mt['side'],
                        quantity=mt['qty'],
                        entry_price=mt['price'],
                        external_id=mt['order_id'],
                        status="CLOSED" if "FILLED" in mt['status'] else "CANCELLED",
                        entry_time=datetime.strptime(mt['time'].replace('T', ' ').split('.')[0], "%Y-%m-%d %H:%M:%S") if isinstance(mt['time'], str) else mt['time'],
                        order_type=mt['order_type']
                    )
                    db.add(new_trade)
                    synced_count += 1
            
            db.commit()
            logger.info(f"[Journal] Synced {synced_count} new trades from Moomoo.")
            return {"count": synced_count, "status": "Success"}
        except Exception as e:
            logger.error(f"[Journal] Sync error: {e}")
            db.rollback()
            return {"count": 0, "error": str(e)}
        finally:
            db.close()

trade_journal = TradeJournalService()
