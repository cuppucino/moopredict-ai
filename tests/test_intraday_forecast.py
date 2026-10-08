from datetime import date

import pandas as pd
import pytest

from services.intraday_forecast import (
    bars_in_window,
    estimate_range,
    evaluate_forecast,
    excursion_history,
    format_forecast_notification,
    format_resolution_notification,
    range_error,
    session_window,
)


DAY = date(2026, 9, 22)


def bars(prices, day=DAY):
    """Five-minute New York bars; each tuple is open/high/low/close."""
    index = pd.date_range(f"{day.isoformat()} 09:30", periods=len(prices), freq="5min")
    return pd.DataFrame(prices, index=index, columns=["open", "high", "low", "close"])


def test_session_window_tracks_new_york_daylight_saving():
    summer_start, summer_end = session_window(date(2026, 9, 22))
    winter_start, winter_end = session_window(date(2026, 12, 22))

    assert summer_start.isoformat().endswith("-04:00")
    assert winter_start.isoformat().endswith("-05:00")
    assert (summer_end - summer_start).total_seconds() == 4 * 3600
    assert (winter_end - winter_start).total_seconds() == 4 * 3600


def test_range_estimate_requires_enough_prior_sessions():
    with pytest.raises(ValueError, match="insufficient_history"):
        estimate_range([0.2] * 19)

    result = estimate_range([float(value) / 10 for value in range(1, 41)])
    assert result["sample_size"] == 40
    assert result["low_pct"] < result["median_pct"] < result["high_pct"]


def test_excursion_history_never_uses_forecast_day():
    earlier = bars([(100, 101, 99, 100.5)] * 48, date(2026, 9, 21))
    forecast_day = bars([(100, 110, 90, 100)] * 3, DAY)
    history = excursion_history(pd.concat([earlier, forecast_day]), before=DAY)

    assert len(history["UP"]) == 1
    assert history["UP"][0] == pytest.approx(1.0)
    assert history["DOWN"][0] == pytest.approx(1.0)


def test_down_forecast_reaches_and_fits_range_even_if_endpoint_recovers():
    frame = bars([
        (100.0, 100.2, 99.8, 100.1),
        (100.1, 100.1, 95.0, 96.0),
        (96.0, 99.2, 95.5, 99.0),
    ])
    result = evaluate_forecast("DOWN", 4.0, 6.0, frame, DAY)

    assert result["actual_excursion_pct"] == pytest.approx(5.0)
    assert result["target_reached"] is True
    assert result["magnitude_in_range"] is True
    assert result["endpoint_direction_correct"] is True
    assert result["zone_low_price"] == pytest.approx(94.0)
    assert result["zone_high_price"] == pytest.approx(96.0)


def test_overshoot_counts_as_reached_but_not_magnitude_accurate():
    frame = bars([(100.0, 100.1, 99.9, 100.0), (100.0, 100.0, 93.0, 94.0)])
    result = evaluate_forecast("DOWN", 4.0, 6.0, frame, DAY)

    assert result["actual_excursion_pct"] == pytest.approx(7.0)
    assert result["target_reached"] is True
    assert result["magnitude_in_range"] is False


def test_up_forecast_undershoot_is_not_a_hit():
    frame = bars([(100.0, 100.5, 99.5, 100.2), (100.2, 103.0, 100.0, 102.5)])
    result = evaluate_forecast("UP", 4.0, 6.0, frame, DAY)

    assert result["actual_excursion_pct"] == pytest.approx(3.0)
    assert result["target_reached"] is False
    assert result["magnitude_in_range"] is False


def test_only_bars_before_1330_are_scored():
    index = pd.DatetimeIndex(["2026-09-22 09:30", "2026-09-22 13:25", "2026-09-22 13:30"])
    frame = pd.DataFrame(
        [(100, 101, 99, 100), (100, 102, 98, 101), (101, 120, 80, 110)],
        index=index,
        columns=["open", "high", "low", "close"],
    )
    window = bars_in_window(frame, DAY)
    result = evaluate_forecast("UP", 1.0, 3.0, frame, DAY)

    assert len(window) == 2
    assert result["actual_excursion_pct"] == pytest.approx(2.0)
    assert result["endpoint_return_pct"] == pytest.approx(1.0)


def test_missing_session_data_is_unverifiable():
    with pytest.raises(ValueError, match="no_bars_in_window"):
        evaluate_forecast("UP", 1.0, 2.0, pd.DataFrame(), DAY)


def test_range_error_penalizes_under_and_overshoot():
    assert range_error(3.0, 4.0, 6.0) == pytest.approx(1.0)
    assert range_error(5.0, 4.0, 6.0) == pytest.approx(0.0)
    assert range_error(7.5, 4.0, 6.0) == pytest.approx(1.5)


def test_live_resolution_rejects_an_incomplete_window():
    frame = bars([(100.0, 101.0, 99.0, 100.0)] * 3)
    with pytest.raises(ValueError, match="incomplete_window"):
        evaluate_forecast("UP", 1.0, 2.0, frame, DAY, require_complete=True)


def test_telegram_messages_show_ranges_and_audit_results():
    forecast = format_forecast_notification({
        "session_date": DAY.isoformat(),
        "forecasts": [{"symbol": "SPY", "direction": "DOWN", "confidence": 61.0,
                       "move_range_pct": [0.4, 0.8], "sample_size": 42}],
        "errors": [],
    })
    audit = format_resolution_notification({
        "session_date": DAY.isoformat(),
        "outcomes": [{"symbol": "SPY", "direction": "DOWN",
                      "expected_range_pct": [0.4, 0.8], "actual_excursion_pct": 0.6,
                      "target_reached": True, "magnitude_in_range": True}],
    })

    assert "SPY ↓ DOWN | model score 61.0/100" in forecast
    assert "not a probability of success" in forecast
    assert "direct sentiment not_recorded" in forecast
    assert "expected excursion 0.40–0.80%" in forecast
    assert "actual 0.60% | ✓ reached | ✓ in range" in audit
