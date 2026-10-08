from datetime import datetime, timezone
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from services.paper_diagnostics import explain_trade, account_diagnostics, path_excursions
from services.intraday_paper import initial_state, PaperConfig


def trade():
    return {"symbol": "QQQ", "entry_key": "QQQ:2026-10-02T10:00:00-04:00", "forecast_id": 1,
            "entry_at": "2026-10-02T10:01:00-04:00", "quantity": .0654,
            "entry_raw_price": 751.97, "entry_price": 751.97*1.0005, "entry_cost": .0654*751.97*.0005,
            "stop": 749.1, "target": 758.31, "planned_loss": .24, "exit_raw_price": 751.74,
            "net_pnl": -.064213317, "total_cost": .049171317, "reason": "view_invalidated"}


def test_loss_attribution_reconciles_recorded_october_2_trade():
    p = trade()
    before = deepcopy(p)
    forecast = SimpleNamespace(input_snapshot={}, reference_open=751.38, direction="UP", move_high_pct=.824)
    result = explain_trade(p, {"eligible": True, "reason": "opening_range_breakout_above_session_vwap"}, forecast)
    assert result["gross_price_pnl_usd"] == pytest.approx(-.015042)
    assert result["modeled_costs_usd"] == pytest.approx(.049171317)
    assert result["accounting_reconciles"] and result["loss_attribution"] == "price_and_costs"
    assert result["forecast_alignment"]["target_above_upper_band"]
    assert "Two completed" in result["exit_reason"] and before == p


def test_session_accounting_excludes_prior_session_and_exposes_unresolved():
    current, prior = trade(), {**trade(), "entry_at": "2026-10-01T10:01:00-04:00"}
    exits = [SimpleNamespace(id=i, payload=p, occurred_at=datetime(2026, 10, 2, 15, 1, tzinfo=timezone.utc))
             for i, p in enumerate([current, prior])]
    state = initial_state(PaperConfig())
    state.update(day="2026-10-02", day_start_equity=100.071745837, equity=100.00753252, pending={"key": "pending"})
    result = account_diagnostics(state, exits, [], {})
    assert len(result["trades"]) == 2
    assert result["session"]["closed_trades"] == 1
    assert result["session"]["closed_trade_net_pnl_usd"] == pytest.approx(-.064213317)
    assert result["session"]["cash_benchmark_equity"] == 100.071745837
    assert result["session"]["unresolved"]
    assert result["trades"][0]["excursions"]["status"] == "unverified"


def source(tmp_path, monkeypatch, missing=False):
    from services.intraday_market import archive_bars
    monkeypatch.setattr("services.intraday_market.ARCHIVE", tmp_path)
    bars = pd.DataFrame({"time_key": ["2026-10-02T10:01:00-04:00", "2026-10-02T10:02:00-04:00", "2026-10-02T10:03:00-04:00"],
                         "open": [751.97]*3, "high": [753, 754, 800], "low": [751, 750, 700],
                         "close": [752]*3})
    if missing:
        bars = bars.drop(0)
    return archive_bars(bars, {"provider_timestamp_label": "start", "interval_minutes": 1})


def test_excursions_exclude_prices_after_open_exit_and_require_full_path(tmp_path, monkeypatch):
    verified = path_excursions(trade(), "2026-10-02T10:03:00-04:00", source(tmp_path, monkeypatch))
    assert verified["status"] == "verified"
    assert verified["mfe_pct"] == pytest.approx((754/751.97-1)*100)
    assert verified["mae_pct"] == pytest.approx((1-750/751.97)*100)
    incomplete = path_excursions(trade(), "2026-10-02T10:03:00-04:00", source(tmp_path, monkeypatch, True))
    assert incomplete["status"] == "unverified"


def test_intrabar_exit_reports_lower_bounds_and_tampered_archive_is_unverified(tmp_path, monkeypatch):
    from pathlib import Path
    data = source(tmp_path, monkeypatch)
    result = path_excursions({**trade(), "reason": "stop"}, "2026-10-02T10:03:00-04:00", data)
    assert result["status"] == "lower_bounds_intrabar_exit"
    assert result["mfe_pct"] == pytest.approx((753/751.97-1)*100)
    Path(data["path"]).write_text("tampered")
    assert path_excursions(trade(), "2026-10-02T10:03:00-04:00", data)["status"] == "unverified"
