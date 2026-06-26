import uuid
from datetime import datetime
from typing import List, Dict
from loguru import logger

def is_market_active() -> bool:
    """Check if market or pre-market is active (Mon-Fri 12:00 - 21:00 UTC)."""
    import pytz
    try:
        now_utc = datetime.now(pytz.utc)
        # Weekday: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4
        if now_utc.weekday() >= 5:
            return False
        current_hour = now_utc.hour + now_utc.minute / 60.0
        return 12.0 <= current_hour <= 21.0
    except Exception as e:
        logger.error(f"Error checking market active: {e}")
        return True # Default to True (safe fallback) to avoid silent suppression on error

class NotificationQueue:
    def __init__(self, max_size: int = 100):
        self.queue: List[Dict] = []
        self.max_size = max_size

    def enqueue(self, message: str, level: str = "info", category: str = "general"):
        """Add a new notification to the queue with a category for routing."""
        category_lower = category.lower()
        level_lower = level.lower()

        # Suppress only stale_data and heartbeat_warning off-hours (except level='alert')
        if category_lower in ("stale_data", "heartbeat_warning") and level_lower != "alert":
            if not is_market_active():
                logger.info(f"[Queue] Off-hours suppression: Suppressing {level.upper()} notification ({category}): {message[:50]}...")
                return

        notification = {
            "id": str(uuid.uuid4()),
            "message": message,
            "level": level,
            "category": category_lower,
            "sent": False,
            "created_at": datetime.utcnow().isoformat()
        }
        self.queue.append(notification)
        logger.info(f"[Queue] Enqueued {level.upper()} notification ({category}): {message[:50]}...")
        
        # Keep queue size within limits
        if len(self.queue) > self.max_size:
            self.queue.pop(0)

    def get_pending(self) -> List[Dict]:
        """Return notifications that haven't been sent."""
        return [n for n in self.queue if not n["sent"]]

    def mark_as_sent(self, notification_id: str):
        """Mark a notification as sent."""
        for n in self.queue:
            if n["id"] == notification_id:
                n["sent"] = True
                break

    def clear(self):
        """Clear the queue."""
        self.queue = []

# Singleton instance
notification_queue = NotificationQueue()
