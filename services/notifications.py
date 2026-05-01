import uuid
from datetime import datetime
from typing import List, Dict

class NotificationQueue:
    def __init__(self, max_size: int = 100):
        self.queue: List[Dict] = []
        self.max_size = max_size

    def enqueue(self, message: str, level: str = "info", category: str = "general"):
        """Add a new notification to the queue with a category for routing."""
        notification = {
            "id": str(uuid.uuid4()),
            "message": message,
            "level": level,
            "category": category.lower(),
            "sent": False,
            "created_at": datetime.utcnow().isoformat()
        }
        self.queue.append(notification)
        
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
