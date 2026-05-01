import os
import time
import json
import requests
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

# Load environment variables
load_dotenv()

# Configuration
BASE_URL = os.getenv("MOOPREDICT_URL", "http://127.0.0.1:3001")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_MS", "5000")) / 1000.0

def get_openclaw_config():
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read OpenClaw config: {e}")
    return {}

oc_config = get_openclaw_config()

# Main Credentials
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or oc_config.get("channels", {}).get("telegram", {}).get("botToken")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or "REMOVED_PRIVATE_VALUE"

# News Credentials (New)
BOT_TOKEN_NEWS = os.getenv("TELEGRAM_BOT_TOKEN_NEWS")
CHAT_ID_NEWS = os.getenv("TELEGRAM_CHAT_ID_NEWS")

LEVEL_EMOJI = {
    "info": "ℹ️",
    "warning": "⚠️",
    "alert": "🚨",
    "error": "🚨"
}

def send_telegram(notif):
    category = notif.get("category", "general")
    emoji = LEVEL_EMOJI.get(notif.get("level"), "ℹ️")
    message = notif.get("message", "")
    
    # Decide which bot and chat to use
    target_token = BOT_TOKEN
    target_chat = CHAT_ID
    
    if category == "news" and BOT_TOKEN_NEWS:
        target_token = BOT_TOKEN_NEWS
        target_chat = CHAT_ID_NEWS or CHAT_ID # Fallback if channel ID not set
        logger.info(f"Routing to NEWS bot/channel: {target_chat}")
    
    if not target_token:
        logger.error(f"No token available for category: {category}")
        return False

    # Simple HTML escaping
    escaped_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = f"<b>{emoji} MooPredict</b>\n\n{escaped_message}"
    
    url = f"https://api.telegram.org/bot{target_token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        if not data.get("ok"):
            logger.error(f"Telegram error ({category}): {data.get('description')}")
            return False
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram ({category}): {e}")
        return False

def mark_sent(notification_id):
    try:
        requests.post(f"{BASE_URL}/api/notifications/mark-sent", json={"id": notification_id}, timeout=5)
    except Exception as e:
        logger.error(f"Failed to mark notification {notification_id} as sent: {e}")

def poll():
    logger.info(f"🚀 MooPredict Notification Poller started (Python)")
    logger.info(f"   MooPredict : {BASE_URL}")
    logger.info(f"   Telegram   : chat {CHAT_ID}")
    
    if not BOT_TOKEN:
        logger.error("❌ No Telegram bot token found!")
        return

    while True:
        try:
            response = requests.get(f"{BASE_URL}/api/notifications/pending", timeout=5)
            pending = response.json()
            
            if pending:
                logger.info(f"📬 Found {len(pending)} pending notifications")
                for notif in pending:
                    if send_telegram(notif):
                        mark_sent(notif["id"])
                        logger.info(f"  ✅ Sent & marked #{notif['id']}")
                        
        except Exception as e:
            logger.warning(f"Connection error: {e}")
            
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        poll()
    except KeyboardInterrupt:
        logger.info("Poller stopped by user.")
