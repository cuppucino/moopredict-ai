"""Restart-safe ownership of the paper experiment's expected work.

No detached timeout futures: the scheduler owns a tick until it really finishes.
Failures are persisted separately from a scheduler callback returning normally.
"""
from datetime import datetime, timedelta
from threading import Lock
from loguru import logger
import pandas as pd

from core.database import (SessionLocal, IntradayRun, IntradayForecast, ForecastEvaluation,
                           PaperExperimentAccount, NotificationOutbox)
from services.intraday_market import NY, UTC, session_window
from services.intraday_forecast import (intraday_forecast_service, FOCUS_SYMBOLS, EXPERIMENT,
                                        EXPERIMENT_START, EVALUATOR_VERSION)
from services.intraday_paper import intraday_paper_service, ACCOUNT_ID, COMPARATOR_ID
from services.notifications import enqueue_in_session


class IntradayOperations:
    def __init__(self, forecasts=intraday_forecast_service, paper=intraday_paper_service,
                 session_factory=SessionLocal, clock=None):
        self.forecasts, self.paper, self.session_factory = forecasts, paper, session_factory
        self.clock = clock or (lambda: datetime.now(NY))
        self.lock = Lock()
        self.busy_since = None
        self.last_result = None

    def _save(self, kind, day, status, detail=None, attempt=False, alert=None):
        key = f"{kind}:{day}"
        with self.session_factory() as db:
            row = db.get(IntradayRun, key)
            if row is None:
                row = IntradayRun(key=key, kind=kind, session_date=day, attempts=0)
                db.add(row)
            row.status, row.detail = status, detail or {}
            row.attempts += int(attempt)
            row.updated_at = self.clock().astimezone(UTC).replace(tzinfo=None)
            if alert:
                enqueue_in_session(db, alert, level="warning", category="system_error",
                                   dedupe_key=f"operation:{key}:{status}")
            db.commit()

    def _attempt(self, kind, day, work):
        self._save(kind, day, "running", attempt=True)
        try:
            result = work()
            failed = bool(result.get("errors")) or result.get("status") == "degraded"
            status = "degraded" if failed else "complete"
            self._save(kind, day, status, result, alert=(
                f"PAPER {kind} degraded for {day}. Data/processing failed; no success assumed. "
                "See /api/intraday-operations." if failed else None))
            return result
        except Exception as exc:
            logger.exception(f"[IntradayOperations] {kind} failed")
            result = {"errors": [{"error": str(exc)}]}
            self._save(kind, day, "failed", result,
                       alert=f"PAPER {kind} failed for {day}: {type(exc).__name__}. Will retry within its allowed window.")
            return result

    def tick(self):
        if not self.lock.acquire(blocking=False):
            return {"status": "already_running"}
        self.busy_since = self.clock()
        try:
            self.last_result = self._tick()
            return self.last_result
        except Exception as exc:
            # DB may itself be unavailable, in which case an outbox write also fails.
            self.last_result = {"status": "failed", "error": str(exc), "at": self.clock().isoformat()}
            logger.exception("[IntradayOperations] tick failed")
            raise
        finally:
            self.busy_since = None
            self.lock.release()

    def _tick(self):
        now = self.clock().astimezone(NY)
        day = now.date()
        attempted = []
        self.paper.bootstrap()
        with self.session_factory() as db:
            rows = db.query(IntradayForecast).filter_by(experiment=EXPERIMENT).all()
            present = {(r.session_date, r.symbol) for r in rows}
            done = {r.forecast_id for r in db.query(ForecastEvaluation).filter_by(evaluator_version=EVALUATOR_VERSION).all()}
            backlog = sorted({r.session_date for r in rows if r.id not in done})
            runs = {r.key: (r.status, r.updated_at) for r in db.query(IntradayRun).all()}
            active = any(a.state.get("position") or a.state.get("pending") for a in
                db.query(PaperExperimentAccount).filter(PaperExperimentAccount.id.in_([ACCOUNT_ID, COMPARATOR_ID])).all())
        # Missing forecasts stay missing: do not fabricate premarket observations.
        for stamp in pd.date_range(EXPERIMENT_START, day):
            try:
                opening, _ = session_window(stamp.date())
            except ValueError:
                continue
            missing = [s for s in FOCUS_SYMBOLS if (stamp.date(), s) not in present]
            if now >= opening and missing:
                self._save("forecast", stamp.date(), "missed", {"missing_symbols": missing},
                    alert=f"PAPER forecast missing for {stamp.date()}: {', '.join(missing)}. Market already opened; no backfill or entries from missing forecasts.")
        try:
            start, end = session_window(day)
        except ValueError:
            start = end = None
        if start and start-timedelta(minutes=15) <= now < start:
            if any((day,s) not in present for s in FOCUS_SYMBOLS):
                attempted.append(self._attempt("forecast", day, self.forecasts.generate))
        # Handle existing positions first, including late recovery after downtime.
        if active or (start and start <= now <= end+timedelta(minutes=5)):
            attempted.append(self._attempt("paper", day, self.paper.tick))
        for past in backlog:
            try:
                _, deadline = session_window(past)
            except ValueError:
                continue
            run = runs.get(f"evaluation:{past}")
            recent = run and now.astimezone(UTC).replace(tzinfo=None)-run[1] < timedelta(minutes=5)
            if now >= deadline+timedelta(minutes=5) and not recent:
                attempted.append(self._attempt("evaluation", past, lambda d=past: self.forecasts.resolve(d, regrade=True)))
        if end and now >= end+timedelta(minutes=10):
            self._daily_report(day)
        status = "degraded" if any(r.get("errors") or r.get("status") == "degraded" for r in attempted) else "ok"
        self._save("heartbeat", day, status, {"at": self.clock().isoformat()})
        return {"status": status, "at": self.clock().isoformat()}

    def _daily_report(self, day):
        key = f"paper-daily:{day}"
        with self.session_factory() as db:
            if db.query(NotificationOutbox).filter_by(dedupe_key=key).first():
                return
        report = self.paper.report()
        account = report["accounts"][ACCOUNT_ID]["state"]
        comparator = report["accounts"][COMPARATOR_ID]["state"]
        score = self.forecasts.summary()
        # Once fetching stops, an empty error list does not prove the last bars
        # arrived. Keep an incomplete live window visible in the daily report.
        try:
            _, deadline = session_window(day)
            observed_ends = [datetime.fromisoformat(account.get("last_signals", {}).get(s, ""))
                             for s in FOCUS_SYMBOLS]
            observation_complete = account.get("day") == str(day) and all(
                stamp.tzinfo is not None and stamp.astimezone(NY).date() == day and stamp >= deadline
                for stamp in observed_ends)
        except (TypeError, ValueError):
            observation_complete = False
        if account["data_uncertain"] or account.get("market_data_errors"):
            data_status = "UNVERIFIED / equity may be stale"
        else:
            data_status = "checked" if observation_complete else "INCOMPLETE observation window"
        accuracy = (f"Cumulative range coverage {score['magnitude_in_range_rate']:.1%}; endpoint accuracy {score['endpoint_direction_rate']:.1%}; "
                    f"always-UP endpoint {score['always_up_baseline']['endpoint_direction_rate']:.1%}.\n") if score["n"] else ""
        pnl = account["equity"]-account["day_start_equity"] if account["day"] == str(day) else 0
        session = report["accounts"][ACCOUNT_ID].get("diagnostics", {}).get("session", {})
        attribution = (f"Session closed trades {session['closed_trades']}: price movement ${session['gross_price_pnl_usd']:+.4f}; "
                       f"costs ${session['closed_trade_costs_usd']:.4f}; net ${session['closed_trade_net_pnl_usd']:+.4f}.\n"
                       if session.get("session_date") == str(day) else "Session attribution unavailable.\n")
        cash = f"${account['day_start_equity']:.4f}" if account["day"] == str(day) else "unavailable"
        message = (f"PAPER DAILY — {day}\n"
            f"Equity ${account['equity']:.3f}; session change ${pnl:+.3f}; cumulative realized ${account['realized_pnl']:+.3f}.\n"
            f"{attribution}"
            f"Cumulative closed trades {account['closed_trades']}; costs ${account['costs']:.3f}; max observed drawdown ${account['max_drawdown']:.3f}.\n"
            f"No-forecast comparison equity ${comparator['equity']:.3f}; session stay-in-cash {cash}.\n"
            f"Cumulative corrected forecast sample n={score['n']}; pending={score['pending']}.\n"
            f"{accuracy}"
            f"Position {'UNRESOLVED' if account['position'] else 'flat'}; order {'UNRESOLVED' if account['pending'] else 'none'}; data {data_status}.\n"
            "Generic fractional-share simulation; assumed costs 10 bps round trip. No real orders. "
            "Results are experimental; see /api/paper-experiment for cost sensitivity.")
        with self.session_factory() as db:
            enqueue_in_session(db, message, dedupe_key=key)
            db.commit()

    def watchdog(self):
        if self.busy_since and self.clock()-self.busy_since > timedelta(seconds=90):
            self._save("watchdog", self.clock().astimezone(NY).date(), "stalled",
                {"busy_since": self.busy_since.isoformat()},
                alert="PAPER worker has run for over 90 seconds. New ticks cannot overlap it; observations may be delayed. Check OpenD and /api/intraday-operations.")

    def status(self):
        with self.session_factory() as db:
            runs = db.query(IntradayRun).order_by(IntradayRun.updated_at.desc()).limit(50).all()
            return {"busy_since": self.busy_since.isoformat() if self.busy_since else None,
                "last_result": self.last_result,
                "pending_notifications": db.query(NotificationOutbox).filter(NotificationOutbox.sent_at.is_(None)).count(),
                "runs": [{"key": r.key, "status": r.status, "attempts": r.attempts,
                          "detail": r.detail, "updated_at": r.updated_at.isoformat()+"Z"} for r in runs]}


intraday_operations = IntradayOperations()
