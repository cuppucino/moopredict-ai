"""
Stats Reporter — scheduled short stats push to kf's Telegram (2026-08-10).

kf's ask: automatic stats at 9:30am + 8:00pm MYT weekdays, SHORT format (3 lines:
scorecard / active book / system OK-or-problem), delivered to the existing Telegram
chat via notification_queue -> notify_poller.

Deterministic code, not an LLM agent — same reliability lesson as tripwire/auto-draft.
DB session held only around queries (alert_service pool-leak lesson).
"""
from datetime import datetime, timedelta

from loguru import logger

from core.database import SessionLocal, Prediction
from services.notifications import notification_queue



def _scorecard_line() -> str:
    from services.track_record import compute_metrics
    db = SessionLocal()
    try:
        m = compute_metrics(db)
    finally:
        db.close()
    if not m.get("n_resolved"):
        return "📊 no resolved predictions in 30d window"
    vs = m.get("wr_vs_benchmark", 0.0)
    return (f"📊 WR {m['win_rate']*100:.1f}% ({vs*100:+.1f}pp vs "
            f"{m['benchmark_always_up']*100:.1f}%) n={m['n_resolved']} | "
            f"PF {m['profit_factor']:.2f} | +{m['expectancy_after_costs_bps']:.0f}bps/trade"
            if m.get("expectancy_after_costs_bps") is not None else
            f"📊 WR {m['win_rate']*100:.1f}% n={m['n_resolved']} | PF {m['profit_factor']:.2f}")


def _book_line() -> str:
    db = SessionLocal()
    try:
        active = (db.query(Prediction)
                  .filter(Prediction.outcome.is_(None))
                  .order_by(Prediction.id)
                  .all())
        rows = [(p.symbol, p.direction, p.category, p.created_at) for p in active]
    finally:
        db.close()
    if not rows:
        return "🎯 no active predictions"
    arrow = {"UP": "↑", "DOWN": "↓"}
    focus = " ".join(f"{s}{arrow.get(d, '?')}" for s, d, c, _ in rows if c == "focus")
    parts = [f"🎯 {len(rows)} active"]
    if focus:
        parts.append(f"focus {focus}")
    return ": ".join(parts)


def _system_line() -> str:
    problems = []
    try:
        from services.moomoo_service import moomoo_service
        if not moomoo_service.is_connected:
            problems.append("moomoo DISCONNECTED")
    except Exception:
        problems.append("moomoo check failed")

    db = SessionLocal()
    try:
        latest = (db.query(Prediction)
                  .order_by(Prediction.created_at.desc())
                  .first())
        newest_at = latest.created_at if latest else None
    finally:
        db.close()
    # Staleness = "a scheduled engine slot was MISSED", not wall-clock age (audit H3:
    # a fixed 30h bar false-alarms every Monday 9:30am — Friday's book is 61h old and
    # nothing is wrong). Expected slot: most recent WEEKDAY 12:20 UTC (position-watch,
    # the last engine in the evening train). Alarm only if newest prediction predates
    # the most recent such slot that is >=1h in the past.
    now = datetime.utcnow()
    slot = now.replace(hour=12, minute=20, second=0, microsecond=0)
    if slot > now - timedelta(hours=1):
        slot -= timedelta(days=1)
    while slot.weekday() >= 5:
        slot -= timedelta(days=1)
    if newest_at and newest_at < slot:
        age_h = (now - newest_at).total_seconds() / 3600
        problems.append(f"last prediction {age_h:.0f}h old (missed {slot:%a} slot)")

    return "✅ system OK" if not problems else "⚠️ " + "; ".join(problems)


def compose_short_report() -> str:
    return "\n".join([_scorecard_line(), _book_line(), _system_line()])


def send_if_missed_on_startup(grace_hours: float = 2.0) -> bool:
    """App restarts (Mac reboot) lose in-memory missed cron slots — APScheduler
    misfire grace only survives sleep, not process death (seen 2026-08-13: 9:30am
    report lost to a 9:42 boot). Called at scheduler init: if a weekday slot
    (01:30 / 12:00 UTC) passed within grace and the app just started, send late."""
    now = datetime.utcnow()
    if now.weekday() >= 5:
        return False
    for slot_h, slot_m in ((1, 30), (12, 0)):
        slot = now.replace(hour=slot_h, minute=slot_m, second=0, microsecond=0)
        if slot <= now <= slot + timedelta(hours=grace_hours):
            logger.info("[StatsReport] startup catch-up — sending missed slot report")
            return send_report()
    return False


def send_report() -> bool:
    try:
        msg = "📈 *MooPredict stats*\n" + compose_short_report()
        notification_queue.enqueue(msg, level="info", category="stats_report")
        logger.info(f"[StatsReport] sent:\n{msg}")
        return True
    except Exception as e:
        logger.error(f"[StatsReport] failed: {e}")
        return False
