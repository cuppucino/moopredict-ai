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

    def delete_alert(self, alert_id: int) -> bool:
        """Delete an alert (mark as DELETED or remove)."""
        db = SessionLocal()
        try:
            alert = db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()
            if alert:
                db.delete(alert)
                db.commit()
                logger.info(f"[Alerts] Deleted alert #{alert_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"[Alerts] Error deleting alert: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def check_alerts(self):
        """Check all active alerts against current prices.

        DB session lifecycle matters here: this job runs every 5 min under a 30s
        timeout, and moomoo calls can wedge indefinitely. Holding a session across
        those calls leaked one 'idle in transaction' connection per run until the
        pool died (2026-08-03: 50 leaked in ~4h, all engines down). Same fix as the
        heartbeat: snapshot alert rows, CLOSE the session, then do slow network
        calls, and reopen a fresh session only to write triggers.
        """
        db = SessionLocal()
        try:
            active_alerts = db.query(PriceAlert).filter(PriceAlert.status == "ACTIVE").all()
            alert_rows = [
                {"id": a.id, "symbol": a.symbol, "price": a.price, "direction": a.direction}
                for a in active_alerts
            ]
        except Exception as e:
            logger.error(f"[Alerts] Error loading alerts: {e}")
            return
        finally:
            db.close()

        if not alert_rows:
            return

        # Network calls with NO session held — a wedge here can no longer leak.
        try:
            from services.technical_analysis import ta_service

            positions = moomoo_service.get_positions()
            price_map = {p['symbol']: p['current_price'] for p in positions}

            triggered_rows = []
            for row in alert_rows:
                current_price = price_map.get(row["symbol"])

                if current_price is None:
                    analysis = ta_service.get_full_analysis(row["symbol"])
                    current_price = analysis.get('price')

                if current_price is None:
                    logger.warning(f"[Alerts] Could not fetch price for {row['symbol']}")
                    continue

                if row["direction"] == "ABOVE" and current_price >= row["price"]:
                    triggered_rows.append((row["id"], current_price))
                elif row["direction"] == "BELOW" and current_price <= row["price"]:
                    triggered_rows.append((row["id"], current_price))
        except Exception as e:
            logger.error(f"[Alerts] Error checking alerts: {e}")
            return

        if not triggered_rows:
            return

        db = SessionLocal()
        try:
            for alert_id, current_price in triggered_rows:
                alert = db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()
                if alert and alert.status == "ACTIVE":
                    self._trigger_alert(db, alert, current_price)
        except Exception as e:
            logger.error(f"[Alerts] Error triggering alerts: {e}")
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
        
        notification_queue.enqueue(msg, level="alert", category="news")

alert_service = AlertService()
