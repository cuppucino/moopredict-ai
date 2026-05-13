import os
import json
import time
import requests
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv
from services.moomoo_service import moomoo_service
from services.earnings_calendar import earnings_service
from services.sector_analysis import sector_service
from services.notifications import notification_queue
from services.prediction_service import prediction_service
from core.database import SessionLocal, UserWatchlist

class HeartbeatMonitor:
    def __init__(self, state_path: str = "/Users/admin/moopredict-ai/data/heartbeat_state.json"):
        self.state_path = state_path
        self.state = self._load_state()
        self.config = {
            "position_alert_pct": 3.0,
            "position_alert_pct_high": 5.0,
            "earnings_warn_days": 3,
            "cooldown_seconds": 3600,  # 1 hour
            "dead_threshold_min": 30
        }
        load_dotenv()
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.last_dead_alert = 0

    def _load_state(self):
        """Load heartbeat state from JSON file."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"[Heartbeat] Failed to load state: {e}")
        
        # Default state
        return {
            "last_check_utc": None,
            "alerts": {},  # { "type:id": timestamp }
            "positions": {}, # { "symbol": last_price }
            "sectors": {},   # { "symbol": last_quadrant }
            "health": {"opend_connected": True}
        }

    def _save_state(self):
        """Save heartbeat state to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"[Heartbeat] Failed to save state: {e}")

    def _is_on_cooldown(self, alert_key: str):
        """Check if an alert is currently on cooldown."""
        last_alert = self.state.get("alerts", {}).get(alert_key)
        if last_alert:
            elapsed = time.time() - last_alert
            if elapsed < self.config["cooldown_seconds"]:
                return True
        return False

    def _update_alert_timestamp(self, alert_key: str):
        """Update the timestamp for a specific alert."""
        if "alerts" not in self.state:
            self.state["alerts"] = {}
        self.state["alerts"][alert_key] = time.time()

    def check_positions(self):
        """Check all positions for significant moves (±3% or ±5%)."""
        logger.info("[Heartbeat] Checking positions...")
        try:
            positions = moomoo_service.get_positions()
            if not positions:
                return

            if "positions" not in self.state:
                self.state["positions"] = {}

            for pos in positions:
                symbol = pos["symbol"]
                current_price = pos["current_price"]
                avg_price = pos["avg_price"]
                pnl_pct = ((current_price - avg_price) / avg_price) * 100 if avg_price > 0 else 0.0
                
                # Store in state
                self.state["positions"][symbol] = {
                    "price": current_price,
                    "pnl": pnl_pct
                }

                if avg_price <= 0:
                    continue
                    
                abs_pnl = abs(pnl_pct)
                
                if abs_pnl >= self.config["position_alert_pct"]:
                    level = "warning" if abs_pnl < self.config["position_alert_pct_high"] else "alert"
                    alert_key = f"pos_move:{symbol}"
                    
                    if not self._is_on_cooldown(alert_key):
                        emoji = "🚀" if pnl_pct > 0 else "📉"
                        msg = f"{emoji} *Position Alert: {symbol}*\nMove: {pnl_pct:+.2f}%\nPrice: ${current_price:.2f}\nAvg: ${avg_price:.2f}"
                        notification_queue.enqueue(msg, level=level, category="risk")
                        self._update_alert_timestamp(alert_key)
        except Exception as e:
            logger.error(f"[Heartbeat] Position check error: {e}")

    def check_balance(self):
        """Check and update account balance to ensure data freshness."""
        logger.info("[Heartbeat] Checking balance...")
        try:
            moomoo_service.get_balance()
        except Exception as e:
            logger.error(f"[Heartbeat] Balance check error: {e}")

    def check_earnings_proximity(self):
        """Check for earnings reports within the warning window (3 days)."""
        logger.info("[Heartbeat] Checking earnings proximity...")
        db = SessionLocal()
        try:
            watchlist = db.query(UserWatchlist).all()
            symbols = [s.symbol for s in watchlist]
            
            # Also check held positions
            positions = moomoo_service.get_positions()
            for p in positions:
                if p["symbol"] not in symbols:
                    symbols.append(p["symbol"])
            
            
            for symbol in symbols:
                earnings = earnings_service.get_stock_earnings(symbol)
                if earnings.get("success") and earnings.get("earnings_dates"):
                    next_date_str = earnings["earnings_dates"][0]
                    try:
                        next_date = datetime.strptime(next_date_str, "%Y-%m-%d")
                        days_to = (next_date - datetime.now()).days
                        
                        if 0 <= days_to <= self.config["earnings_warn_days"]:
                            alert_key = f"earnings:{symbol}:{next_date_str}"
                            if not self._is_on_cooldown(alert_key):
                                msg = f"📅 *Earnings Reminder: {symbol}*\nDate: {next_date_str} ({days_to} days away)\nRule: BLOCKED from trading."
                                notification_queue.enqueue(msg, level="warning", category="news")
                                self._update_alert_timestamp(alert_key)
                    except Exception as e:
                        logger.error(f"[Heartbeat] Date parse error for {symbol}: {e}")
        except Exception as e:
            logger.error(f"[Heartbeat] Earnings check error: {e}")
        finally:
            db.close()

    def check_sector_rotation(self):
        """Track changes in sector rotation quadrants (RRG)."""
        logger.info("[Heartbeat] Checking sector rotation...")
        try:
            rrg_data = sector_service.calculate_rrg()
            if not rrg_data:
                return

            for sector in rrg_data:
                symbol = sector["symbol"]
                current_quadrant = sector["quadrant"]
                
                if "sectors" not in self.state:
                    self.state["sectors"] = {}
                    
                last_quadrant = self.state["sectors"].get(symbol)
                
                if last_quadrant and current_quadrant != last_quadrant:
                    alert_key = f"sector:{symbol}:{current_quadrant}"
                    if not self._is_on_cooldown(alert_key):
                        msg = f"🌀 *Sector Rotation: {symbol}*\nMoved from {last_quadrant} ➡️ *{current_quadrant}*"
                        notification_queue.enqueue(msg, level="info", category="news")
                        self._update_alert_timestamp(alert_key)
                
                # Update state
                self.state["sectors"][symbol] = current_quadrant
        except Exception as e:
            logger.error(f"[Heartbeat] Sector rotation check error: {e}")

    def check_predictions(self):
        """Auto-resolve predictions and report outcomes."""
        logger.info("[Heartbeat] Checking predictions...")
        try:
            resolved = prediction_service.resolve_pending_predictions()
            for res in resolved:
                if res.get("success"):
                    emoji = "✅" if res["outcome"] == "RIGHT" else "❌"
                    msg = f"{emoji} *Prediction Resolved: {res['symbol']}*\nOutcome: *{res['outcome']}*\nMove: {res['move_pct']:+.2f}%"
                    notification_queue.enqueue(msg, level="info", category="general")
        except Exception as e:
            logger.error(f"[Heartbeat] Prediction check error: {e}")

    def check_data_freshness(self):
        """Check the age of all data sources and alert if any are stale."""
        logger.info("[Heartbeat] Checking data freshness...")
        from services.data_freshness import freshness_registry
        report = freshness_registry.get_staleness_report()
        
        stale_threshold = 120 # 2 hours
        stale_sources = []
        for source, age in report.items():
            if age > stale_threshold:
                stale_sources.append(f"{source} ({round(age/60, 1)}h)")
        
        if stale_sources:
            msg = f"⚠️ *DATA STALENESS WARNING*\nThe following sources are > {stale_threshold} min old:\n" + "\n".join([f"• {s}" for s in stale_sources])
            notification_queue.enqueue(msg, level="warning", category="system")
            logger.warning(f"[Heartbeat] Stale data detected: {stale_sources}")

    def _send_dead_heartbeat_alert(self, message: str):
        """Directly send a Telegram message if the heartbeat system is failing."""
        # Rate limit to once per hour
        if time.time() - self.last_dead_alert < 3600:
            return

        if not self.bot_token or not self.chat_id:
            return

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": f"🚨 *HEARTBEAT CRITICAL*\n───────────────────\n{message}",
                "parse_mode": "Markdown"
            }
            requests.post(url, json=payload, timeout=10)
            self.last_dead_alert = time.time()
        except Exception as e:
            logger.error(f"[Heartbeat] Dead alert error: {e}")

    def check_system_health(self):
        """Check connection status of Moomoo OpenD."""
        logger.info("[Heartbeat] Checking system health...")
        try:
            is_connected = moomoo_service.is_connected
            
            if "health" not in self.state:
                self.state["health"] = {"opend_connected": True}
                
            last_status = self.state["health"].get("opend_connected", True)
            
            if is_connected != last_status:
                status_text = "✅ RECONNECTED" if is_connected else "❌ DISCONNECTED"
                level = "info" if is_connected else "error"
                notification_queue.enqueue(f"🖥️ *System Health: Moomoo OpenD*\nStatus: {status_text}", level=level, category="system")
                self.state["health"]["opend_connected"] = is_connected
        except Exception as e:
            logger.error(f"[Heartbeat] System health check error: {e}")

    def run_full_check(self):
        """Perform all monitoring checks and persist state with auto-reconnect logic."""
        # 1. Resilient Connection
        if not moomoo_service.is_connected:
            logger.warning("[Heartbeat] OpenD disconnected. Attempting auto-reconnect...")
            if not moomoo_service.auto_reconnect():
                self._send_dead_heartbeat_alert("OpenD is DOWN and auto-reconnect failed. Monitoring is impaired.")
        
        # 2. Monitoring Tasks
        self.check_system_health()
        self.check_data_freshness()
        self.check_balance()
        self.check_positions()
        self.check_earnings_proximity()
        self.check_sector_rotation()
        self.check_predictions()
        
        # 3. State Persistence
        self.state["last_check_utc"] = datetime.utcnow().isoformat()
        self._save_state()

    def generate_daily_report(self):
        """Generate end-of-day summary report."""
        logger.info("[Heartbeat] Generating daily P&L report...")
        try:
            balance = moomoo_service.get_balance()
            positions = moomoo_service.get_positions()
            
            assets = f"${balance['total_assets']:,.2f}" if balance else "N/A"
            cash = f"${balance['available_cash']:,.2f}" if balance else "N/A"
            
            pos_lines = []
            for p in positions:
                pos_lines.append(f"• {p['symbol']}: {p['qty']} @ ${p['avg_price']} | P&L: {p['pnl_percent']}")
            
            pos_str = "\n".join(pos_lines) if pos_lines else "No open positions."
            
            msg = (
                f"📊 *DAILY PERFORMANCE SUMMARY*\n"
                f"───────────────────────────\n"
                f"💰 Total Assets: {assets}\n"
                f"💵 Available Cash: {cash}\n\n"
                f"*Current Positions:*\n{pos_str}\n\n"
                f"📅 Markets closed. See you tomorrow!"
            )
            notification_queue.enqueue(msg, level="info", category="general")
        except Exception as e:
            logger.error(f"[Heartbeat] Daily report error: {e}")

    def generate_premarket_scan(self):
        """Generate pre-market outlook briefing."""
        logger.info("[Heartbeat] Generating pre-market scan...")
        try:
            # We can use briefing_service or generate a specific heartbeat scan
            msg = "☕ *PRE-MARKET SCAN*\n───────────────────────────\nCheck !briefing for today's catalysts."
            notification_queue.enqueue(msg, level="info", category="news")
        except Exception as e:
            logger.error(f"[Heartbeat] Pre-market scan error: {e}")

# Singleton instance
heartbeat_monitor = HeartbeatMonitor()
