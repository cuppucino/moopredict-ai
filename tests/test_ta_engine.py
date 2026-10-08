import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
from services.ta_engine import ta_engine

@pytest.fixture(autouse=True)
def isolate_volume_profile(monkeypatch):
    # These synthetic daily candles must not be combined with today's live
    # Yahoo volume profile. The timeout test supplies its own profile stub.
    from services.volume_flow_service import vol_flow_service
    monkeypatch.setattr(vol_flow_service, "calculate_volume_profile",
                        lambda *args, **kwargs: {"success": False})

@pytest.fixture
def mock_kline_data():
    # Create a 250-candle downward trending dataset (Oversold)
    rng = np.random.default_rng(0)
    dates = pd.date_range(start="2023-01-01", periods=250)
    close_prices = np.linspace(500, 100, 250) + rng.normal(0, 0.1, 250)
    high_prices = close_prices + 2
    low_prices = close_prices - 2
    open_prices = close_prices - 1
    volume = rng.integers(1000, 5000, 250)

    df = pd.DataFrame({
        "time_key": dates,
        "open": open_prices,
        "high": high_prices,
        "low": low_prices,
        "close": close_prices,
        "volume": volume
    })
    return df

def test_ta_engine_compute_all(mock_kline_data):
    with patch.object(ta_engine, '_get_kline_data', return_value=mock_kline_data):
        res = ta_engine.compute_all("AAPL")

        assert "error" not in res
        assert res["symbol"] == "AAPL"
        assert "composite_score" in res
        assert 0 <= res["composite_score"] <= 100
        assert "label" in res
        assert "signals" in res
        assert len(res["signals"]) >= 6 # RSI, MACD, ADX, Bollinger, Z-Score, SMA

def test_ta_engine_regime_detection(mock_kline_data):
    # A strong downward trend should also be detected as TRENDING.
    with patch.object(ta_engine, '_get_kline_data', return_value=mock_kline_data):
        res = ta_engine.compute_all("AAPL")
        assert res["regime"] == "TRENDING"

def test_ta_engine_composite_logic(mock_kline_data):
    # The fixture falls from 500 to 100: oversold RSI does not make the
    # overall daily trend bullish. This checks the bearish fixture's behavior.
    with patch.object(ta_engine, '_get_kline_data', return_value=mock_kline_data):
        res = ta_engine.compute_all("AAPL")
        assert res["composite_score"] < 50
        assert res["label"] in ["SELL", "STRONG_SELL"]

def test_legacy_wrapper_compatibility(mock_kline_data):
    from services.technical_analysis import ta_service
    with patch.object(ta_engine, '_get_kline_data', return_value=mock_kline_data), \
         patch('services.ta_engine.moomoo_service.get_volume', return_value={"success": False}):
        res = ta_service.get_full_analysis("AAPL")
        assert "rsi" in res
        assert "moving_averages" in res
        assert "macd" in res
        assert "summary" in res

def test_ta_engine_volume_flow_timeout(mock_kline_data):
    import time
    def slow_volume_profile(*args, **kwargs):
        time.sleep(10.0)
        return {"success": True, "poc": 150}

    with patch.object(ta_engine, '_get_kline_data', return_value=mock_kline_data), \
         patch('services.volume_flow_service.vol_flow_service.calculate_volume_profile', side_effect=slow_volume_profile):

        start_time = time.time()
        res = ta_engine.compute_all("AAPL")
        elapsed = time.time() - start_time

        assert elapsed < 6.0
        assert "error" not in res
        signal_names = [s["name"] for s in res["signals"]]
        assert "Volume Flow" not in signal_names
