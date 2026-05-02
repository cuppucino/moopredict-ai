import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from services.prediction_service import PredictionService
from core.database import Prediction

@pytest.fixture
def pred_service():
    return PredictionService()

@patch("services.prediction_service.moomoo_service")
@patch("services.prediction_service.SessionLocal")
def test_create_prediction(mock_session_local, mock_moomoo, pred_service):
    # Setup mocks
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    mock_moomoo.get_stock_quote.return_value = {"last_price": 150.0}
    
    # Run test
    res = pred_service.create_prediction("AAPL", "UP", 80.0, "Test Catalyst")
    
    # Assertions
    assert res["success"] is True
    assert res["entry_price"] == 150.0
    assert mock_db.add.called
    assert mock_db.commit.called

@patch("services.prediction_service.moomoo_service")
@patch("services.prediction_service.SessionLocal")
def test_resolve_prediction_right(mock_session_local, mock_moomoo, pred_service):
    # Setup mock DB and Prediction object
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    
    mock_pred = Prediction(
        id=1,
        symbol="AAPL",
        direction="UP",
        entry_price=100.0,
        outcome=None
    )
    mock_db.query.return_value.filter.return_value.first.return_value = mock_pred
    
    # Mock price move up to 110 (RIGHT)
    mock_moomoo.get_stock_quote.return_value = {"last_price": 110.0}
    
    # Run test
    res = pred_service.resolve_prediction(1)
    
    # Assertions
    assert res["success"] is True
    assert res["outcome"] == "RIGHT"
    assert mock_pred.outcome == "RIGHT"
    assert mock_db.commit.called

@patch("services.prediction_service.moomoo_service")
@patch("services.prediction_service.SessionLocal")
def test_resolve_prediction_wrong(mock_session_local, mock_moomoo, pred_service):
    # Setup mock DB and Prediction object
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    
    mock_pred = Prediction(
        id=2,
        symbol="AAPL",
        direction="UP",
        entry_price=100.0,
        outcome=None
    )
    mock_db.query.return_value.filter.return_value.first.return_value = mock_pred
    
    # Mock price move down to 90 (WRONG)
    mock_moomoo.get_stock_quote.return_value = {"last_price": 90.0}
    
    # Run test
    res = pred_service.resolve_prediction(2)
    
    # Assertions
    assert res["success"] is True
    assert res["outcome"] == "WRONG"
    assert mock_pred.outcome == "WRONG"
    assert mock_db.commit.called
