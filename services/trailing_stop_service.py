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
    def create_trailing_stop(self, symbol: str, entry_price: float, trade_id: Optional[int] = None, is_day_trade: bool = False) -> int:
        """Create a new trailing stop based on asset type."""
        db = SessionLocal()
        try:
            # Detect trail pct based on user rules
            # Leveraged: SMCX, ARMG, 2X, 3X, etc. -> 5%
            # Stocks: SMCI, AAPL, etc. -> 10%
            # Options: (detected by length or dots) -> 15%
            
            is_leveraged = any(x in symbol.upper() for x in ["2X", "3X", "SMCX", "ARMG", "LABU", "TQQQ", "SOXL"])
            is_option = len(symbol) > 10 or (symbol.count('.') >= 2) # Crude option detection
            
            trail_pct = 8.0 # Default for stocks
            if is_leveraged:
                trail_pct = 5.0
            elif is_option:
                trail_pct = 10.0 # User decision: 10% for options
                
            stop_price = entry_price * (1 - (trail_pct / 100.0))
            
            ts = TrailingStop(
                symbol=symbol.upper().strip(),
                entry_price=entry_price,
                highest_price=entry_price,
                trail_pct=trail_pct,
                stop_price=stop_price,
                trade_id=trade_id,
                is_day_trade=is_day_trade,
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

                # 2. Check if triggered by price
                if current_price <= ts.stop_price:
                    self._trigger_stop(db, ts, current_price, reason="Price Hit Stop")
                
                # 3. Check if triggered by time (1-hour hold limit for Day Trades)
                elif ts.is_day_trade:
                    hold_time = datetime.utcnow() - ts.created_at
                    if hold_time.total_seconds() > 3600: # 1 hour
                        logger.warning(f"[TrailingStop] {ts.symbol} exceeded 1-hour hold limit for day trade.")
                        self._trigger_stop(db, ts, current_price, reason="1-Hour Hold Limit Exceeded")
            
            db.commit()
        except Exception as e:
            logger.error(f"[TrailingStop] Update error: {e}")
        finally:
            db.close()

    def _trigger_stop(self, db: Session, ts: TrailingStop, current_price: float, reason: str = "Price Triggered"):
        """Handle triggered trailing stop."""
        ts.status = "TRIGGERED"
        ts.triggered_at = datetime.utcnow()
        
        logger.warning(f"[TrailingStop] TRIGGERED: {ts.symbol} @ {current_price} (Stop: {ts.stop_price}) - Reason: {reason}")
        
        # 1. Notify user
        pnl_pct = ((current_price / ts.entry_price) - 1) * 100 if ts.entry_price > 0 else 0.0
        msg = (
            f"🚨 *TRAILING STOP TRIGGERED: {ts.symbol}*\n"
            f"──────────────────\n"
            f"Exit Price: ${current_price:.2f}\n"
            f"Stop Level: ${ts.stop_price:.2f}\n"
            f"Reason: {reason}\n"
            f"P&L: {pnl_pct:+.2f}%"
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

    def get_stop_status(self) -> Dict:
        """Report on stop loss coverage for both journaled trades and real moomoo positions."""
        db = SessionLocal()
        try:
            from core.database import TradeJournal
            # 1. Get Journaled trades
            open_journal = db.query(TradeJournal).filter(TradeJournal.status == "OPEN").all()
            
            # 2. Get Real Moomoo positions
            moomoo_positions = moomoo_service.get_positions()
            
            # 3. Get Active stops
            active_stops = db.query(TrailingStop).filter(TrailingStop.status == "ACTIVE").all()
            active_symbols = {s.symbol for s in active_stops}
            stop_trade_ids = {s.trade_id for s in active_stops if s.trade_id}
            
            # 4. Calculate unprotected
            # Journaled: Check both trade_id and symbol
            unprotected_journal = [
                t.symbol for t in open_journal 
                if (t.id not in stop_trade_ids) and (t.symbol not in active_symbols)
            ]
            # Real
            unprotected_moomoo = [p["symbol"] for p in moomoo_positions if p["symbol"] not in active_symbols]
            
            # Combined
            all_symbols = {t.symbol for t in open_journal} | {p["symbol"] for p in moomoo_positions}
            unprotected = list(set(unprotected_journal) | set(unprotected_moomoo))
            total_count = len(all_symbols)
            protected_count = total_count - len(unprotected)
            
            return {
                "total_open_trades": total_count,
                "protected_trades": protected_count,
                "unprotected_trades": len(unprotected),
                "unprotected_symbols": unprotected,
                "coverage_pct": round((protected_count / total_count * 100), 1) if total_count > 0 else 100.0
            }
        finally:
            db.close()

    def auto_protect_positions(self):
        """Check all real positions and create trailing stops for unprotected ones."""
        logger.info("[TrailingStop] Running auto-protect scan...")
        db = SessionLocal()
        try:
            positions = moomoo_service.get_positions()
            if not positions:
                return

            for p in positions:
                symbol = p["symbol"]
                # Check if an active stop already exists
                exists = db.query(TrailingStop).filter(
                    TrailingStop.symbol == symbol, 
                    TrailingStop.status == "ACTIVE"
                ).first()
                
                if not exists:
                    logger.info(f"[TrailingStop] Auto-protecting {symbol}...")
                    # Create a standard stop based on current price
                    self.create_trailing_stop(symbol, p["current_price"])
                    
            db.commit()
        except Exception as e:
            logger.error(f"[TrailingStop] Auto-protect error: {e}")
        finally:
            db.close()

trailing_stop_service = TrailingStopService()
