"""Durable Telegram outbox; delivery is at-least-once and acknowledged by the poller."""
import uuid
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from core.database import SessionLocal, NotificationOutbox
from loguru import logger


def is_market_active():
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.weekday() < 5 and 8 <= now.hour < 17


def enqueue_in_session(db, message, level="info", category="general", dedupe_key=None):
    """Commit a notification with the event it describes; reuse its durable identity."""
    if dedupe_key:
        found = db.query(NotificationOutbox).filter_by(dedupe_key=dedupe_key).first()
        if found:
            return found.id
    row = NotificationOutbox(id=str(uuid.uuid4()), dedupe_key=dedupe_key, message=message,
                             level=level.lower(), category=category.lower())
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        if not dedupe_key:
            raise
        return db.query(NotificationOutbox).filter_by(dedupe_key=dedupe_key).one().id
    return row.id


class NotificationQueue:
    def __init__(self, max_size=100, session_factory=SessionLocal):
        self.session_factory = session_factory
        self.batch_size = max_size

    def enqueue(self, message, level="info", category="general", dedupe_key=None):
        if category.lower() in ("stale_data", "heartbeat_warning") and level.lower() != "alert":
            if not is_market_active():
                return None
        with self.session_factory() as db:
            key = enqueue_in_session(db, message, level, category, dedupe_key)
            db.commit()
        logger.info(f"[Outbox] enqueued {key} ({category})")
        return key

    def get_pending(self):
        with self.session_factory() as db:
            rows = (db.query(NotificationOutbox).filter(NotificationOutbox.sent_at.is_(None))
                    .order_by(NotificationOutbox.created_at, NotificationOutbox.id)
                    .limit(self.batch_size).all())
            return [{"id": r.id, "message": r.message, "level": r.level, "category": r.category,
                     "sent": False, "created_at": r.created_at.isoformat()} for r in rows]

    def mark_as_sent(self, notification_id):
        with self.session_factory() as db:
            row = db.get(NotificationOutbox, notification_id)
            if row and row.sent_at is None:
                row.sent_at = datetime.utcnow()
                db.commit()

    def clear(self):
        with self.session_factory() as db:
            db.query(NotificationOutbox).filter(NotificationOutbox.sent_at.is_(None)).update(
                {NotificationOutbox.sent_at: datetime.utcnow()})
            db.commit()


notification_queue = NotificationQueue()
