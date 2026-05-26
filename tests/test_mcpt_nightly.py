import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from fastapi.testclient import TestClient

from core.database import MCPTResult, get_db
from services.mcpt_nightly import is_trading_day, should_alert, update_mcpt_status
from main import app

def test_is_trading_day():
    """Verify that is_trading_day correctly flags weekends vs weekdays."""
    try:
        # Monday is 0, Saturday is 5, Sunday is 6
        monday = datetime(2026, 5, 25)  # Monday
        saturday = datetime(2026, 5, 23)  # Saturday
        sunday = datetime(2026, 5, 24)  # Sunday
        
        assert is_trading_day(monday) is True, "Monday must be a trading day"
        assert is_trading_day(saturday) is False, "Saturday must not be a trading day"
        assert is_trading_day(sunday) is False, "Sunday must not be a trading day"
    except Exception as error:
        pytest.fail(f"test_is_trading_day failed: {error}")

def test_should_alert():
    """Verify state-transition and significance crossing alert logic."""
    try:
        # 1. State transition (actionable)
        assert should_alert(0.04, 0.04, "WATCHLIST", "LIVE") is True
        
        # 2. Crossed significance gate (0.05)
        assert should_alert(0.04, 0.06, "WATCHLIST", "WATCHLIST") is True
        
        # 3. Crossed watchlist boundary (0.10)
        assert should_alert(0.09, 0.12, "WATCHLIST", "WATCHLIST") is True
        
        # 4. Movement within null/significant regions (no alert)
        assert should_alert(0.12, 0.15, "WATCHLIST", "WATCHLIST") is False
        assert should_alert(0.01, 0.02, "LIVE", "LIVE") is False
        
        # 5. Handle None values safely
        assert should_alert(None, 0.04, "WATCHLIST", "WATCHLIST") is False
    except Exception as error:
        pytest.fail(f"test_should_alert failed: {error}")

def test_mcpt_nightly_bootstrap_freeze():
    """Verify N < 5 bootstrap rule locks strategy status as WATCHLIST."""
    try:
        mock_session = MagicMock()
        
        # Scenario A: 2 historical records (total runs = 3 < 5)
        # Both gates pass (insample_p = 0.005, wf_p = 0.03) -> expect status = WATCHLIST (frozen)
        mock_results = [
            MCPTResult(strategy='signal_aggregator', ticker='MARA', ema_p=0.03, wf_p=0.03, status='WATCHLIST', run_at=datetime.utcnow()),
            MCPTResult(strategy='signal_aggregator', ticker='MARA', ema_p=0.03, wf_p=0.03, status='WATCHLIST', run_at=datetime.utcnow())
        ]
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_results
        
        with patch('services.mcpt_nightly.notification_queue') as mock_queue:
            record = update_mcpt_status(
                session=mock_session,
                strategy='signal_aggregator',
                ticker='MARA',
                insample_p=0.005,
                wf_p=0.03,
                real_pf=1.35
            )
            
            assert record.status == 'WATCHLIST', "Expected frozen WATCHLIST status under N=3 bootstrap"
            # Since total runs is < 5, it should not transition to LIVE even if both gates pass and EMA < 0.04
            assert record.ema_p < 0.04
            
        # Scenario B: 4 historical records (total runs = 5 >= 5)
        # Both gates pass and EMA < 0.04 -> expect status = LIVE
        mock_results_5 = [
            MCPTResult(strategy='signal_aggregator', ticker='MARA', ema_p=0.03, wf_p=0.03, status='WATCHLIST', run_at=datetime.utcnow())
            for _ in range(4)
        ]
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_results_5
        
        with patch('services.mcpt_nightly.notification_queue') as mock_queue:
            record = update_mcpt_status(
                session=mock_session,
                strategy='signal_aggregator',
                ticker='MARA',
                insample_p=0.005,
                wf_p=0.03,
                real_pf=1.35
            )
            
            assert record.status == 'LIVE', "Expected LIVE status since warmup is complete and gates cleared"
    except Exception as error:
        pytest.fail(f"test_mcpt_nightly_bootstrap_freeze failed: {error}")

def test_mcpt_nightly_hysteresis_rules():
    """Verify hysteresis limits (staying LIVE below 0.08, demoting above 0.08)."""
    try:
        mock_session = MagicMock()
        
        # Scenario A: Previously LIVE, EMA becomes 0.075 (<= 0.08) -> expect status stays LIVE
        mock_results = [
            MCPTResult(strategy='signal_aggregator', ticker='MARA', ema_p=0.03, wf_p=0.03, status='LIVE', run_at=datetime.utcnow())
            for _ in range(5)
        ]
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_results
        
        with patch('services.mcpt_nightly.notification_queue') as mock_queue:
            record = update_mcpt_status(
                session=mock_session,
                strategy='signal_aggregator',
                ticker='MARA',
                insample_p=0.005,
                # wf_p is high to drag EMA up, but EMA stays <= 0.08
                wf_p=0.25, 
                real_pf=1.15
            )
            
            # EMA: 0.2 * 0.25 + 0.8 * 0.03 = 0.074 <= 0.08 -> stays LIVE
            assert record.status == 'LIVE'
            
        # Scenario B: Previously LIVE, EMA becomes 0.085 (> 0.08) -> expect demotion to WATCHLIST
        mock_results_demote = [
            MCPTResult(strategy='signal_aggregator', ticker='MARA', ema_p=0.07, wf_p=0.07, status='LIVE', run_at=datetime.utcnow())
            for _ in range(5)
        ]
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_results_demote
        
        with patch('services.mcpt_nightly.notification_queue') as mock_queue:
            record = update_mcpt_status(
                session=mock_session,
                strategy='signal_aggregator',
                ticker='MARA',
                insample_p=0.005,
                wf_p=0.20,
                real_pf=1.05
            )
            # EMA: 0.2 * 0.20 + 0.8 * 0.07 = 0.096 > 0.08 -> WATCHLIST
            assert record.status == 'WATCHLIST'
    except Exception as error:
        pytest.fail(f"test_mcpt_nightly_hysteresis_rules failed: {error}")

def test_api_mcpt_strategies_endpoint():
    """Verify that FastAPI endpoint correctly queries latest records and lists bootstrap_pending."""
    try:
        client = TestClient(app)
        
        # Mock database session query results
        mock_latest_record = MCPTResult(
            id=12,
            strategy='signal_aggregator',
            ticker='MARA',
            insample_p=0.005,
            wf_p=0.06,
            ema_p=0.06,
            real_pf=1.36,
            status='WATCHLIST',
            run_at=datetime(2026, 5, 26, 12, 0, 0)
        )
        
        mock_session = MagicMock()
        mock_join_query = MagicMock()
        mock_join_query.all.return_value = [mock_latest_record]
        
        # Set up hierarchical queries:
        mock_query_obj = MagicMock()
        mock_query_obj.join.return_value = mock_join_query
        
        # For the subquery group_by subquery chain:
        mock_subq_chain = MagicMock()
        mock_query_obj.group_by.return_value = mock_subq_chain
        
        mock_filtered = MagicMock()
        mock_filtered.count.return_value = 3
        mock_query_obj.filter.return_value = mock_filtered
        
        mock_session.query.return_value = mock_query_obj
        
        def mock_get_db():
            yield mock_session
            
        app.dependency_overrides[get_db] = mock_get_db
        try:
            response = client.get("/api/v1/mcpt/strategies")
            assert response.status_code == 200
            
            data = response.json()
            assert len(data) == 1
            strategy_info = data[0]
            
            assert strategy_info["strategy"] == "signal_aggregator"
            assert strategy_info["ticker"] == "MARA"
            assert strategy_info["bootstrap_pending"] is True, "Expected bootstrap_pending to be True since total_runs = 3 < 5"
            assert strategy_info["total_runs"] == 3
            assert strategy_info["status"] == "WATCHLIST"
        finally:
            app.dependency_overrides.clear()
    except Exception as error:
        pytest.fail(f"test_api_mcpt_strategies_endpoint failed: {error}")
