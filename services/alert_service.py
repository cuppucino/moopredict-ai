from datetime import datetime
from loguru import logger
from core.database import SessionLocal, PriceAlert
from services.moomoo_service import moomoo_service
from services.notifications import notification_queue

class AlertService:
    def add_alert(self, symbol: str, price: float, direction: str, action: str = "notify") -> int:
        """Add a new price alert."""
        db = SessionLocal()
        try:
            alert = PriceAlert(
                symbol=symbol.upper().strip(),
                price=float(price),
                direction=direction.upper().strip(), # ABOVE | BELOW
                action=action.lower().strip(), # notify | sell
                status="ACTIVE"
            )
            db.add(alert)
            db.commit()
            db.refresh(alert)
            logger.info(f"[Alerts] Created alert #{alert.id}: {alert.symbol} {alert.direction} {alert.price}")
            return alert.id
        except Exception as e:
            logger.error(f"[Alerts] Error adding alert: {e}")
            db.rollback()
            return -1
        finally:
            db.close()

    def get_active_alerts(self):
        db = SessionLocal()
        try:
            return db.query(PriceAlert).filter(PriceAlert.status == "ACTIVE").all()
        finally:
            db.close()

    def check_alerts(self):
        """Check all active alerts against current prices."""
        db = SessionLocal()
        try:
            active_alerts = db.query(PriceAlert).filter(PriceAlert.status == "ACTIVE").all()
            if not active_alerts:
                return

            # Get current prices for symbols
            # Fallback to technical_analysis service if not in positions
            from services.technical_analysis import ta_service
            
            positions = moomoo_service.get_positions()
            price_map = {p['symbol']: p['current_price'] for p in positions}

            for alert in active_alerts:
                current_price = price_map.get(alert.symbol)
                
                if current_price is None:
                    # Fetch fresh price if not in positions
                    analysis = ta_service.get_full_analysis(alert.symbol)
                    current_price = analysis.get('price')

                if current_price is None:
                    logger.warning(f"[Alerts] Could not fetch price for {alert.symbol}")
                    continue

                triggered = False
                if alert.direction == "ABOVE" and current_price >= alert.price:
                    triggered = True
                elif alert.direction == "BELOW" and current_price <= alert.price:
                    triggered = True

                if triggered:
                    self._trigger_alert(db, alert, current_price)
        except Exception as e:
            logger.error(f"[Alerts] Error checking alerts: {e}")
        finally:
            db.close()

    def _trigger_alert(self, db, alert, current_price):
        """Handle a triggered alert."""
        logger.info(f"[Alerts] ALERT TRIGGERED: {alert.symbol} is {alert.direction} {alert.price} (Current: {current_price})")
        
        # We need to re-query or use the provided session correctly
        # Since 'alert' is from a different session if we are not careful
        db_alert = db.query(PriceAlert).filter(PriceAlert.id == alert.id).first()
        if db_alert:
            db_alert.status = "TRIGGERED"
            db_alert.triggered_at = datetime.utcnow()
            db.commit()

        # Send notification
        msg = (
            f"🚨 *PRICE ALERT: {alert.symbol}*\n"
            f"──────────────────\n"
            f"Condition: {alert.direction} ${alert.price:.2f}\n"
            f"Current: ${current_price:.2f}\n"
            f"Action: {alert.action.upper()}"
        )
        
        notification_queue.add_notification("ALERT", msg)

alert_service = AlertService()
