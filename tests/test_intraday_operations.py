from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock
import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from core.database import (Base, IntradayForecast, ForecastEvaluation, IntradayRun,
                           NotificationOutbox, PaperExperimentAccount, PaperExperimentEvent)
from services.intraday_market import NY, normalize_bars, complete_window, session_window
from services.intraday_forecast import IntradayForecastService, STRATEGY_VERSION, EVALUATOR_VERSION, EXPERIMENT
from services.intraday_operations import IntradayOperations
from services.intraday_paper import IntradayPaperService, ACCOUNT_ID, COMPARATOR_ID
from services.notifications import NotificationQueue, enqueue_in_session

DAY = date(2026, 9, 28)


def instant(clock, day=DAY):
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=NY)


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    # Explicit BEGIN gives SQLite the same outer/savepoint atomicity as PostgreSQL.
    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.isolation_level = None
    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("services.intraday_forecast.REPORT_DIR", tmp_path)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def bars(day=DAY, count=48, minutes=5):
    return pd.DataFrame({"open": [100]*count, "high": [101]*count, "low": [99]*count,
                         "close": [100.5]*count, "volume": [100]*count},
        index=pd.date_range(instant("09:30", day), periods=count, freq=f"{minutes}min"))


def historical():
    frames = []
    for stamp in pd.date_range("2026-08-01", "2026-09-25"):
        try:
            session_window(stamp.date())
            frames.append(bars(stamp.date()))
        except ValueError:
            pass
    result = pd.concat(frames)
    result.attrs["provenance"] = {"sha256": "synthetic-test-source"}
    return result


def assessments():
    return [{"etf": s, "direction": "UP", "confidence": 57.3, "p_up": .573}
            for s in ("SPY", "QQQ", "SMH", "XLE")]


def stored_forecast(db, symbol="SPY", day=DAY, legacy=False):
    row = IntradayForecast(experiment=EXPERIMENT, strategy_version="old" if legacy else STRATEGY_VERSION,
        symbol=symbol, session_date=day, issued_at=instant("09:15", day).astimezone(timezone.utc).replace(tzinfo=None),
        window_start=instant("09:30", day), window_end=instant("13:30", day),
        direction="UP", confidence=57, move_low_pct=.2, move_high_pct=1.2, sample_size=30,
        status="RESOLVED" if legacy else "FORECASTED", result={"original": True} if legacy else None,
        input_snapshot={"historical_bands": {"UP": {"low_pct": .2, "high_pct": 1.2}}})
    db.add(row)
    db.commit()
    return row.id


@pytest.mark.parametrize("missing", [0, 20, 47])
def test_completeness_rejects_missing_open_middle_or_close(missing):
    data = bars()
    with pytest.raises(ValueError, match="incomplete_window"):
        complete_window(data.drop(data.index[missing]), DAY)


def test_end_labels_cover_0930_to_1330_exactly_once():
    raw = bars()
    raw.index += pd.Timedelta(minutes=5)
    normalized = normalize_bars(raw, timestamp_label="end")
    assert len(complete_window(normalized, DAY)) == 48
    assert normalized.index[0] == instant("09:30") and normalized.index[-1] == instant("13:25")
    assert normalize_bars(normalized).equals(normalized)


@pytest.mark.parametrize("kind", ["nan", "infinity", "duplicate", "ohlc"])
def test_invalid_market_rows_are_not_silently_dropped(kind):
    data = bars().astype(float)
    if kind == "duplicate":
        data = pd.concat([data, data.iloc[:1]])
    else:
        data.loc[data.index[1], "high"] = {"nan": float("nan"), "infinity": float("inf"), "ohlc": 1}[kind]
    with pytest.raises(ValueError):
        normalize_bars(data)


def test_holidays_and_early_closes_are_excluded():
    with pytest.raises(ValueError, match="market_closed"):
        session_window(date(2026, 11, 26))
    with pytest.raises(ValueError, match="short_session_excluded"):
        session_window(date(2026, 11, 27))


def test_premarket_generation_is_idempotent_immutable_and_atomic(sessions):
    feed = Mock()
    feed.fetch.return_value = historical()
    svc = IntradayForecastService(feed, sessions, lambda: instant("09:15"), assessments)
    first = svc.generate()
    assert len(first["created"]) == 4 and not first["errors"]
    second = svc.generate()
    assert not second["created"] and len(second["existing"]) == 4
    with sessions() as db:
        assert db.query(NotificationOutbox).count() == 4
        row = db.query(IntradayForecast).first()
        assert row.confidence == 57.3
        assert row.input_snapshot["history_source"]["sha256"] == "synthetic-test-source"
    presented = svc.list()[0]
    assert presented["model_score"] == 57.3
    assert presented["evidence_quality"]["direct_sentiment"]["status"] == "not_recorded"
    svc.clock = lambda: instant("09:30")
    assert svc.generate()["errors"][0]["error"] == "forecast_window_closed"


def test_past_future_and_late_collection_cannot_create_forecast(sessions):
    current = [instant("09:15")]
    feed = Mock()
    def late_fetch(*_):
        current[0] = instant("09:30")
        return historical()
    feed.fetch.side_effect = late_fetch
    svc = IntradayForecastService(feed, sessions, lambda: current[0], assessments)
    assert svc.generate(DAY-timedelta(days=3))["errors"]
    assert svc.generate(DAY+timedelta(days=1))["errors"]
    assert len(svc.generate()["errors"]) == 4
    with sessions() as db:
        assert db.query(IntradayForecast).count() == 0
        assert db.query(NotificationOutbox).count() == 0


def test_regrade_appends_and_never_overwrites_published_old_score(sessions):
    with sessions() as db:
        row_id = stored_forecast(db, legacy=True)
    feed = Mock()
    feed.fetch.return_value = bars()
    svc = IntradayForecastService(feed, sessions, lambda: instant("13:29"))
    assert svc.resolve(regrade=True)["errors"]
    svc.clock = lambda: instant("13:35")
    assert svc.resolve(regrade=True)["resolved"] == [row_id]
    assert svc.resolve(regrade=True)["resolved"] == []
    with sessions() as db:
        assert db.get(IntradayForecast, row_id).result == {"original": True}
        evaluation = db.query(ForecastEvaluation).one()
        assert evaluation.result["bars_observed"] == 48
        assert evaluation.result["retrospective_regrade"]
    assert svc.summary()["n"] == 0 and svc.summary()["legacy_forecasts"] == 1


def test_incomplete_score_remains_pending_and_new_version_scores_separately(sessions):
    with sessions() as db:
        stored_forecast(db)
    feed = Mock()
    feed.fetch.return_value = bars(count=47)
    svc = IntradayForecastService(feed, sessions, lambda: instant("13:35"))
    assert svc.resolve()["errors"]
    assert svc.summary()["pending"] == 1
    feed.fetch.return_value = bars()
    assert len(svc.resolve()["resolved"]) == 1
    summary = svc.summary()
    assert summary["n"] == 1 and summary["distinct_sessions"] == 1
    assert summary["mean_interval_score_50"] >= 1
    assert summary["always_up_baseline"]["endpoint_direction_rate"] == 1


def test_outbox_survives_restart_deduplicates_does_not_drop_and_rolls_back(sessions):
    queue = NotificationQueue(max_size=2, session_factory=sessions)
    first = queue.enqueue("one", dedupe_key="one")
    assert queue.enqueue("one again", dedupe_key="one") == first
    queue.enqueue("two")
    queue.enqueue("three")
    restarted = NotificationQueue(max_size=10, session_factory=sessions)
    assert len(restarted.get_pending()) == 3
    restarted.mark_as_sent(first)
    assert len(restarted.get_pending()) == 2
    with sessions() as db:
        enqueue_in_session(db, "rolled back", dedupe_key="rollback")
        db.rollback()
    assert len(restarted.get_pending()) == 2


def test_missed_forecasts_alert_once_and_never_backfill(sessions):
    forecasts, paper = Mock(), IntradayPaperService(session_factory=sessions)
    ops = IntradayOperations(forecasts, paper, sessions, lambda: instant("09:31"))
    paper.tick = Mock(return_value={"status": "ok", "errors": []})
    ops.tick()
    ops.tick()
    forecasts.generate.assert_not_called()
    with sessions() as db:
        row = db.get(IntradayRun, f"forecast:{DAY}")
        assert row.status == "missed" and len(row.detail["missing_symbols"]) == 4
        assert db.query(NotificationOutbox).filter_by(dedupe_key=f"operation:forecast:{DAY}:missed").count() == 1


def test_failure_is_visible_and_retries_before_open(sessions):
    forecasts = Mock()
    forecasts.generate.side_effect = [RuntimeError("OpenD unavailable"), {"errors": []}]
    ops = IntradayOperations(forecasts, IntradayPaperService(session_factory=sessions), sessions, lambda: instant("09:15"))
    ops.tick()
    with sessions() as db:
        assert db.get(IntradayRun, f"forecast:{DAY}").status == "failed"
    ops.tick()
    with sessions() as db:
        assert db.get(IntradayRun, f"forecast:{DAY}").status == "complete"
        assert db.get(IntradayRun, f"forecast:{DAY}").attempts == 2


def test_paper_service_restart_preserves_cash_and_notifications(sessions):
    def frame(rows, start="09:30", minutes=5):
        return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
            index=pd.date_range(instant(start), periods=len(rows), freq=f"{minutes}min"))
    with sessions() as db:
        stored_forecast(db)
    feed = Mock()
    breakout = frame([(100, 101, 99, 100, 100)]*3+[(100, 102, 99.5, 101.5, 100)])
    feed.fetch.return_value = breakout
    svc = IntradayPaperService(feed, sessions, lambda: instant("09:50:10"))
    svc.tick()
    with sessions() as db:
        assert db.get(PaperExperimentAccount, ACCOUNT_ID).state["pending"]
    minute = frame([(101.5, 101.6, 101.4, 101.5, 100)], "09:51", 1)
    feed.fetch.side_effect = lambda s, a, b, interval_minutes=5: minute if interval_minutes == 1 else breakout
    restarted = IntradayPaperService(feed, sessions, lambda: instant("09:52:10"))
    restarted.tick()
    with sessions() as db:
        primary = db.get(PaperExperimentAccount, ACCOUNT_ID).state
        control = db.get(PaperExperimentAccount, COMPARATOR_ID).state
        assert primary["position"] and control["position"]
        assert primary["cash"] == control["cash"] < 100
        assert db.query(PaperExperimentEvent).filter_by(kind="ENTRY").count() == 2
        assert db.query(NotificationOutbox).count() == 1  # only primary account sends.
    # Next tick replays completed bars but cannot debit twice.
    restarted.clock = lambda: instant("09:52:20")
    restarted.tick()
    with sessions() as db:
        assert db.get(PaperExperimentAccount, ACCOUNT_ID).state["cash"] == primary["cash"]
        assert db.query(PaperExperimentEvent).filter_by(kind="ENTRY").count() == 2


def test_report_explains_trade_and_keeps_comparator_separate(sessions):
    from tests.test_paper_diagnostics import trade
    service = IntradayPaperService(session_factory=sessions)
    service.bootstrap()
    with sessions() as db:
        forecast_id = stored_forecast(db)
        p = {**trade(), "forecast_id": forecast_id}
        account = db.get(PaperExperimentAccount, ACCOUNT_ID)
        account.state = {**account.state, "day": "2026-10-02", "day_start_equity": 100.071745837,
                         "cash": 100.00753252, "equity": 100.00753252}
        db.add(PaperExperimentEvent(account_id=ACCOUNT_ID, event_key="view:test", kind="VIEW", symbol="QQQ",
            occurred_at=datetime(2026, 10, 2, 14), payload={"bar_end": "2026-10-02T10:00:00-04:00", "eligible": True}))
        db.add(PaperExperimentEvent(account_id=ACCOUNT_ID, event_key="exit:test", kind="EXIT", symbol="QQQ",
            occurred_at=datetime(2026, 10, 2, 15, 1), payload=p))
        db.commit()
    result = service.report()
    detail = result["accounts"][ACCOUNT_ID]["diagnostics"]
    assert detail["session"]["closed_trades"] == 1
    assert detail["trades"][0]["accounting_reconciles"]
    assert "broke the first 15-minute high" in detail["trades"][0]["entry_reason"]
    assert result["accounts"][COMPARATOR_ID]["diagnostics"]["trades"] == []
    with sessions() as db:
        assert db.get(PaperExperimentAccount, ACCOUNT_ID).state["cash"] == 100.00753252


def test_failed_account_transaction_cannot_leave_a_trade_or_telegram_message(sessions):
    svc = IntradayPaperService(session_factory=sessions)
    svc.bootstrap()
    with sessions() as db:
        account = db.get(PaperExperimentAccount, ACCOUNT_ID)
        account.state = {**account.state, "cash": 75}
        db.add(PaperExperimentEvent(account_id=ACCOUNT_ID, event_key="entry:test", kind="ENTRY",
                                   occurred_at=datetime(2026, 9, 28), payload={}))
        enqueue_in_session(db, "PAPER ENTRY", dedupe_key="entry:test")
        db.rollback()
    with sessions() as db:
        assert db.get(PaperExperimentAccount, ACCOUNT_ID).state["cash"] == 100
        assert db.query(PaperExperimentEvent).count() == 0
        assert db.query(NotificationOutbox).count() == 0


def test_archive_hash_and_deterministic_replay(tmp_path, monkeypatch):
    from services.intraday_market import archive_bars, load_archived_bars
    from services.intraday_paper import (PaperConfig, initial_state, transition,
                                       implementation_digest, replay_observation)
    monkeypatch.setattr("services.intraday_market.ARCHIVE", tmp_path)
    raw = bars(count=1, minutes=1).reset_index(names="time_key")
    raw["time_key"] = (raw["time_key"]+pd.Timedelta(minutes=1)).astype(str)
    source = archive_bars(raw, {"provider_timestamp_label": "end", "interval_minutes": 1})
    assert load_archived_bars(source).index[0] == instant("09:30")
    config = PaperConfig()
    prior = initial_state(config)
    result, events = transition(prior, instant("09:35"), {}, [], config)
    payload = {"prior_state": prior, "result_state": result, "views": [],
               "event_keys": [e["event_key"] for e in events], "minute_sources": {"SPY": source},
               "implementation_sha256": implementation_digest()}
    assert replay_observation(payload, instant("09:35"), config) == []
    from pathlib import Path
    Path(source["path"]).write_text("tampered")
    with pytest.raises(ValueError, match="archive_hash_mismatch"):
        replay_observation(payload, instant("09:35"), config)


def test_worker_overlap_is_rejected_and_watchdog_surfaces_hang(sessions):
    ops = IntradayOperations(Mock(), IntradayPaperService(session_factory=sessions), sessions, lambda: instant("09:20"))
    ops.lock.acquire()
    try:
        assert ops.tick()["status"] == "already_running"
        ops.busy_since = instant("09:15")
        ops.watchdog()
        with sessions() as db:
            assert db.get(IntradayRun, f"watchdog:{DAY}").status == "stalled"
            assert db.query(NotificationOutbox).count() == 1
    finally:
        ops.lock.release()


def test_telegram_stats_uses_paper_equity_and_corrected_cohort(sessions, monkeypatch):
    from services import stats_reporter
    from services.intraday_paper import intraday_paper_service
    from services.intraday_forecast import intraday_forecast_service
    from services.intraday_operations import intraday_operations
    paper = IntradayPaperService(session_factory=sessions)
    paper.bootstrap()
    monkeypatch.setattr(intraday_paper_service, "report", paper.report)
    monkeypatch.setattr(intraday_forecast_service, "summary", lambda: {
        "n": 0, "distinct_sessions": 0, "pending": 0, "missing_sessions": [{"symbols": ["SPY", "QQQ", "SMH", "XLE"]}]})
    monkeypatch.setattr(intraday_operations, "last_result", {"status": "ok"})
    text = stats_reporter.compose_short_report()
    assert "PAPER $100: equity $100.000" in text and "net $+0.000" in text
    assert "Forecast v2 n=0" in text and "missing 4" in text
    assert "without forecast $100.000" in text and "Paper worker OK" in text


@pytest.mark.parametrize("last_bar_end,missing_symbol,expected", [
    ("13:05", None, "data INCOMPLETE observation window"),
    ("13:30", "QQQ", "data INCOMPLETE observation window"),
    ("13:30", None, "data checked"),
])
def test_daily_report_requires_complete_observation_window_after_fetch_cutoff(
        sessions, last_bar_end, missing_symbol, expected):
    feed = Mock()
    paper = IntradayPaperService(feed, sessions, lambda: instant("13:33"))
    paper.bootstrap()
    with sessions() as db:
        account = db.get(PaperExperimentAccount, ACCOUNT_ID)
        account.state = {**account.state, "day": str(DAY),
            "last_signals": {s: instant(last_bar_end).isoformat()
                for s in ("SPY", "QQQ", "SMH", "XLE") if s != missing_symbol},
            "market_data_errors": [{"symbol": "SPY", "error": "Network interruption."}]}
        db.commit()
    # A flat account stops fetching after the observation cutoff. An empty current
    # error list therefore cannot establish that the final price bars were received.
    paper.tick()
    feed.fetch.assert_not_called()
    assert paper.report()["accounts"][ACCOUNT_ID]["state"]["market_data_errors"] == []
    forecasts = Mock()
    forecasts.summary.return_value = {"n": 0, "pending": 4}
    ops = IntradayOperations(forecasts, paper, sessions, lambda: instant("13:40"))
    ops._daily_report(DAY)
    with sessions() as db:
        message = db.query(NotificationOutbox).filter_by(dedupe_key=f"paper-daily:{DAY}").one().message
    assert expected in message
