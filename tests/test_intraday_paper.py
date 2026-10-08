from copy import deepcopy
from datetime import datetime
import pandas as pd
import pytest

from services.intraday_market import NY, normalize_bars
from services.intraday_paper import PaperConfig, initial_state, entry_signal, transition
from services.intraday_forecast import STRATEGY_VERSION


def instant(clock):
    return datetime.fromisoformat(f"2026-09-28T{clock}").replace(tzinfo=NY)


def frame(rows, start="09:30", minutes=5):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range(instant(start), periods=len(rows), freq=f"{minutes}min"))


def forecast(direction="UP"):
    return {"id": 1, "direction": direction, "issued_at": instant("09:15").isoformat(),
            "strategy_version": STRATEGY_VERSION}


def signal(clock="09:50"):
    return {"symbol": "SPY", "bar_end": instant(clock).isoformat(), "close": 101,
            "stop": 100, "eligible": True, "forecast_id": 1, "invalidate_long": False}


def ordered(config=PaperConfig()):
    return transition(initial_state(config), instant("09:50:10"), {}, [signal()], config)[0]


def filled(config=PaperConfig()):
    state = ordered(config)
    bars = frame([(101, 101.2, 100.8, 101.1, 100)], "09:51", 1)
    return transition(state, instant("09:52:10"), {"SPY": bars}, [], config)[0]


def test_signal_uses_complete_session_vwap_and_new_breakout():
    rows = [(100, 101, 99, 100, 100)]*3+[(100, 102, 99.5, 101.5, 100)]
    data = frame(rows)
    result = entry_signal("SPY", data, forecast(), instant("09:50:10"), PaperConfig())
    assert result["eligible"] and result["view"] == "UP"
    assert result["vwap"] == pytest.approx((100*3+(102+99.5+101.5)/3)/4)
    assert not entry_signal("SPY", data, forecast("DOWN"), instant("09:50:10"), PaperConfig())["eligible"]
    assert entry_signal("SPY", data, forecast("DOWN"), instant("09:50:10"), PaperConfig(require_daily_up=False))["eligible"]
    assert not entry_signal("SPY", data, None, instant("09:50:10"), PaperConfig())["eligible"]
    with pytest.raises(ValueError, match="incomplete_window"):
        entry_signal("SPY", data.drop(data.index[1]), forecast(), instant("09:50:10"), PaperConfig())


def test_order_size_is_fixed_before_next_minute_and_debits_cash_with_costs():
    config = PaperConfig()
    state = ordered(config)
    assert state["position"] is None and state["pending"]["quantity"] > 0
    no_fill, _ = transition(state, instant("09:51:10"), {}, [], config)
    assert no_fill["position"] is None  # 09:51 bar is not completed yet.
    actual = filled(config)
    p = actual["position"]
    assert p["entry_at"] == instant("09:51").isoformat()
    assert p["quantity"] == state["pending"]["quantity"]
    assert p["planned_loss"] <= 0.25
    assert actual["cash"] == pytest.approx(100-p["quantity"]*101*1.0005)
    assert actual["equity"] == pytest.approx(actual["cash"]+p["quantity"]*101.1*0.9995)


def test_upward_gap_above_committed_price_cancels_instead_of_resizing():
    state, events = transition(ordered(), instant("09:52:10"),
        {"SPY": frame([(102, 102.1, 101.9, 102, 100)], "09:51", 1)}, [], PaperConfig())
    assert state["position"] is None and state["pending"] is None and state["cash"] == 100
    assert any(e["kind"] == "CANCEL" for e in events)


def test_simultaneous_stop_and_target_uses_stop_and_conserves_money():
    state = filled()
    quantity = state["position"]["quantity"]
    state, events = transition(state, instant("09:53:10"),
        {"SPY": frame([(101.1, 104, 99, 103, 100)], "09:52", 1)}, [], PaperConfig())
    exit_event = next(e["payload"] for e in events if e["kind"] == "EXIT")
    assert exit_event["reason"] == "stop" and exit_event["ambiguous_stop_target"]
    assert state["cash"] == pytest.approx(100+quantity*(100*0.9995-101*1.0005))
    assert state["realized_pnl"] == pytest.approx(state["cash"]-100)
    assert state["costs"] == pytest.approx(quantity*(101+100)*0.0005)
    assert state["closed_trades"] == 1 and state["position"] is None


def test_stop_gap_can_exceed_planned_loss_and_halts_day():
    state, events = transition(filled(), instant("09:53:10"),
        {"SPY": frame([(95, 96, 94, 95, 100)], "09:52", 1)}, [], PaperConfig())
    assert state["realized_pnl"] < -1 and state["halted"]
    assert next(e for e in events if e["kind"] == "EXIT")["payload"]["exit_raw_price"] == 95
    after, _ = transition(state, instant("09:55:10"), {}, [signal("09:55")], PaperConfig())
    assert after["pending"] is None


def test_missing_minute_preserves_unresolved_position_then_recovers():
    state = filled()
    missing, _ = transition(state, instant("09:54:10"),
        {"SPY": frame([(101, 101.2, 100.8, 101, 100)], "09:53", 1)}, [], PaperConfig())
    assert missing["data_uncertain"] and missing["position"] == state["position"]
    recovered, _ = transition(missing, instant("09:54:10"),
        {"SPY": frame([(101, 101.2, 100.8, 101, 100)]*2, "09:52", 1)}, [], PaperConfig())
    assert not recovered["data_uncertain"]
    assert recovered["position"]["processed_until"] == instant("09:54").isoformat()


def test_time_exit_includes_final_minute_and_duplicate_replay_no_second_exit():
    state = filled()
    minute = frame([(101, 101.2, 100.8, 101, 100)]*218, "09:52", 1)
    state, events = transition(state, instant("13:30:10"), {"SPY": minute}, [], PaperConfig())
    assert state["position"] is None and state["closed_trades"] == 1
    assert next(e for e in events if e["kind"] == "EXIT")["payload"]["reason"] == "time_exit"
    again, events = transition(state, instant("13:31:10"), {"SPY": minute}, [], PaperConfig())
    assert again["cash"] == state["cash"] and not any(e["kind"] == "EXIT" for e in events)


def test_unobserved_pending_order_is_not_erased_duplicate_signal_and_entry_cap():
    state = ordered()
    duplicate, events = transition(state, instant("09:50:30"), {}, [signal()], PaperConfig())
    assert duplicate["pending"] == state["pending"] and not events
    unobserved, _ = transition(state, instant("09:54:10"), {}, [], PaperConfig())
    assert unobserved["pending"] and unobserved["data_uncertain"] and unobserved["cash"] == 100
    capped = initial_state(PaperConfig())
    capped.update(day="2026-09-28", entries_today=4)
    capped, _ = transition(capped, instant("09:50:10"), {}, [signal()], PaperConfig())
    assert capped["pending"] is None


def test_view_exit_is_requested_now_and_fills_next_minute_open():
    state = filled()
    view = {**signal(), "eligible": False, "invalidate_long": True, "bar_end": instant("09:52").isoformat()}
    state, events = transition(state, instant("09:52:10"), {}, [view], PaperConfig())
    assert state["position"]["exit_requested_at"] == instant("09:52:10").isoformat()
    # The request must not exit at the already-past 09:52 open.
    minutes = frame([(101, 101.2, 100.8, 101, 100), (101.2, 101.3, 100.9, 101, 100)], "09:52", 1)
    state, events = transition(state, instant("09:54:10"), {"SPY": minutes}, [], PaperConfig())
    exit_event = next(e for e in events if e["kind"] == "EXIT")
    assert exit_event["payload"]["exit_raw_price"] == 101.2
    assert exit_event["occurred_at"].startswith("2026-09-28T13:53")


def test_stale_or_expensive_or_tiny_signals_do_not_create_order():
    for clock, view in (("09:52", signal()), ("13:00", signal("13:00")),
                        ("09:50:10", {**signal(), "stop": 100.999})):
        state, _ = transition(initial_state(PaperConfig()), instant(clock), {}, [view], PaperConfig())
        assert state["pending"] is None


def test_transition_never_mutates_replay_inputs():
    state, view = initial_state(PaperConfig()), signal()
    before = deepcopy((state, view))
    transition(state, instant("09:50:10"), {}, [view], PaperConfig())
    assert (state, view) == before


def test_submission_exactly_on_minute_boundary_cannot_fill_that_same_open():
    config = PaperConfig()
    state, _ = transition(initial_state(config), instant("09:50"), {}, [signal()], config)
    state, events = transition(state, instant("09:51"),
        {"SPY": frame([(101, 101.2, 100.8, 101, 100)], "09:50", 1)}, [], config)
    assert state["pending"] and state["position"] is None
    assert not any(e["kind"] == "ENTRY" for e in events)


def test_pending_order_that_filled_during_outage_cannot_hide_a_loss():
    state = ordered()
    minutes = frame([(101, 101.2, 99, 100, 100)]*5, "09:51", 1)
    recovered, events = transition(state, instant("09:56:10"), {"SPY": minutes}, [], PaperConfig())
    assert recovered["closed_trades"] == 1 and recovered["cash"] < 100
    assert [e["kind"] for e in events] == ["ENTRY", "EXIT"]


def test_pending_order_cannot_be_erased_by_next_day_reset():
    state = ordered()
    next_day = datetime(2026, 9, 29, 9, 35, tzinfo=NY)
    unresolved, events = transition(state, next_day, {}, [], PaperConfig())
    assert unresolved["pending"] and unresolved["data_uncertain"]
    assert unresolved["day"] == "2026-09-28"
    assert not any(e["kind"] == "CANCEL" for e in events)
