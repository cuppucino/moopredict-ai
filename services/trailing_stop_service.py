import pytz
from datetime import datetime
from typing import List, Dict, Optional
from loguru import logger
from sqlalchemy.orm import Session
from core.database import SessionLocal, TrailingStop, TradeJournal
from services.moomoo_service import moomoo_service
from services.notifications import notification_queue
from services.trade_journal import trade_journal

class TrailingStopService:
    def create_trailing_stop(self, symbol: str, entry_price: float, trade_id: Optional[int] = None) -> int:
        """Create a new trailing stop based on asset type."""
        db = SessionLocal()
        try:
            # Detect trail pct based on user rules
            # Leveraged: SMCX, ARMG, 2X, 3X, etc. -> 5%
            # Stocks: SMCI, AAPL, etc. -> 10%
            # Options: (detected by length or dots) -> 15%
            
            is_leveraged = any(x in symbol.upper() for x in ["2X", "3X", "SMCX", "ARMG", "LABU", "TQQQ", "SOXL"])
            is_option = len(symbol) > 10 or (symbol.count('.') >= 2) # Crude option detection
            
            trail_pct = 10.0
            if is_leveraged:
                trail_pct = 5.0
            elif is_option:
                trail_pct = 15.0
                
            stop_price = entry_price * (1 - (trail_pct / 100.0))
            
            ts = TrailingStop(
                symbol=symbol.upper().strip(),
                entry_price=entry_price,
                highest_price=entry_price,
                trail_pct=trail_pct,
                stop_price=stop_price,
                trade_id=trade_id,
                status="ACTIVE"
            )
            db.add(ts)
            db.commit()
            db.refresh(ts)
            logger.info(f"[TrailingStop] Created for {symbol} at {stop_price} ({trail_pct}% trail)")
            return ts.id
        except Exception as e:
            logger.error(f"[TrailingStop] Error creating: {e}")
            db.rollback()
            return -1
        finally:
            db.close()

    def update_stops(self):
        """Poll current prices and ratchet up trailing stops."""
        db = SessionLocal()
        try:
            active_stops = db.query(TrailingStop).filter(TrailingStop.status == "ACTIVE").all()
            if not active_stops:
                return

            for ts in active_stops:
                # Fetch fresh price
                quote = moomoo_service.get_stock_quote(ts.symbol)
                current_price = quote.get("last_price")
                
                if current_price is None or current_price <= 0:
                    continue

                # 1. Update highest seen price
                if current_price > ts.highest_price:
                    ts.highest_price = current_price
                    # Ratchet up stop price
                    new_stop = current_price * (1 - (ts.trail_pct / 100.0))
                    if new_stop > ts.stop_price:
                        ts.stop_price = new_stop
                        logger.info(f"[TrailingStop] {ts.symbol} ratchet up to {ts.stop_price}")

                # 2. Check if triggered
                if current_price <= ts.stop_price:
                    self._trigger_stop(db, ts, current_price)
            
            db.commit()
        except Exception as e:
            logger.error(f"[TrailingStop] Update error: {e}")
        finally:
            db.close()

    def _trigger_stop(self, db: Session, ts: TrailingStop, current_price: float):
        """Handle triggered trailing stop."""
        ts.status = "TRIGGERED"
        ts.triggered_at = datetime.utcnow()
        
        logger.warning(f"[TrailingStop] TRIGGERED: {ts.symbol} @ {current_price} (Stop: {ts.stop_price})")
        
        # 1. Notify user
        msg = (
            f"🚨 *TRAILING STOP TRIGGERED: {ts.symbol}*\n"
            f"──────────────────\n"
            f"Exit Price: ${current_price:.2f}\n"
            f"Stop Level: ${ts.stop_price:.2f}\n"
            f"P&L: {((current_price/ts.entry_price)-1)*100:.2f}%"
        )
        notification_queue.enqueue(msg, level="alert", category="news")
        
        # 2. Auto-close journal entry if linked
        if ts.trade_id:
            trade_journal.close_trade(ts.trade_id, current_price, "Trailing stop triggered.")

    def get_active(self) -> List[Dict]:
        db = SessionLocal()
        try:
            stops = db.query(TrailingStop).filter(TrailingStop.status == "ACTIVE").all()
            return [
                {
                    "id": s.id,
                    "symbol": s.symbol,
                    "stop_price": round(s.stop_price, 2),
                    "highest": round(s.highest_price, 2),
                    "trail": s.trail_pct
                } for s in stops
            ]
        finally:
            db.close()

    def delete_stop(self, stop_id: int) -> bool:
        db = SessionLocal()
        try:
            ts = db.query(TrailingStop).filter(TrailingStop.id == stop_id).first()
            if ts:
                ts.status = "CANCELLED"
                db.commit()
                return True
            return False
        finally:
            db.close()

trailing_stop_service = TrailingStopService()
