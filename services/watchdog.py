import os
import time
import json
import threading
import requests
from loguru import logger
from datetime import datetime
from dotenv import load_dotenv

class WatchdogService:
    def __init__(self, state_path: str = "/Users/admin/moopredict-ai/data/heartbeat_state.json"):
        load_dotenv()
        self.state_path = state_path
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.is_running = False
        self.thread = None
        self.check_interval_sec = 300 # 5 minutes
        self.alert_threshold_min = 75 # Alert if heartbeat > 75m stale (allows for 1h off-hour interval)

    def _send_critical_alert(self, message: str):
        """Directly send a Telegram message bypassing the notification queue."""
        if not self.bot_token or not self.chat_id:
            logger.error("[Watchdog] Missing Telegram credentials.")
            return

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": f"🚨 *SYSTEM WATCHDOG ALERT*\n───────────────────\n{message}",
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("[Watchdog] Critical alert sent to Telegram.")
            else:
                logger.error(f"[Watchdog] Failed to send alert: {response.text}")
        except Exception as e:
            logger.error(f"[Watchdog] Alert error: {e}")

    def _check_heartbeat(self):
        """Check the age of the last heartbeat."""
        if not os.path.exists(self.state_path):
            logger.warning("[Watchdog] Heartbeat state file missing.")
            return

        try:
            with open(self.state_path, "r") as f:
                state = json.load(f)
            
            last_check_str = state.get("last_check_utc")
            if not last_check_str:
                return

            last_check = datetime.fromisoformat(last_check_str)
            now = datetime.utcnow()
            diff_min = (now - last_check).total_seconds() / 60

            if diff_min > self.alert_threshold_min:
                msg = f"Heartbeat is STALE ({round(diff_min)} minutes).\nLast check: {last_check_str}\n\nSystem monitoring may be DEAD."
                self._send_critical_alert(msg)
                logger.error(f"[Watchdog] Heartbeat stale: {diff_min} min")
            else:
                logger.debug(f"[Watchdog] Heartbeat is healthy (age: {round(diff_min, 1)} min)")

        except Exception as e:
            logger.error(f"[Watchdog] Check error: {e}")

    def _run_loop(self):
        """Background loop for watchdog monitoring."""
        logger.info("[Watchdog] Background monitor started.")
        while self.is_running:
            self._check_heartbeat()
            time.sleep(self.check_interval_sec)

    def start(self):
        """Start the watchdog in a daemon thread."""
        if self.is_running:
            return
        
        self.is_running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("[Watchdog] Watchdog service initialized.")

    def stop(self):
        """Stop the watchdog thread."""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("[Watchdog] Watchdog service stopped.")

# Singleton instance
watchdog_service = WatchdogService()
