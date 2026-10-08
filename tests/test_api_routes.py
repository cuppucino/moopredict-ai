import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from main import app

client = TestClient(app)

@pytest.fixture
def mock_moomoo_service():
    with patch("main.moomoo_service") as mock:
        yield mock

def test_get_positions_prefixed(mock_moomoo_service):
    """Test the /api/moomoo/positions endpoint."""
    mock_positions = [{"symbol": "AAPL", "qty": 10}]
    mock_moomoo_service.get_positions.return_value = mock_positions
    
    response = client.get("/api/moomoo/positions")
    assert response.status_code == 200
    assert response.json() == mock_positions
    mock_moomoo_service.get_positions.assert_called_once()

def test_get_balance_prefixed(mock_moomoo_service):
    """Test the /api/moomoo/balance endpoint."""
    mock_balance = {"available_cash": 1000.0}
    mock_moomoo_service.get_balance.return_value = mock_balance
    
    response = client.get("/api/moomoo/balance")
    assert response.status_code == 200
    assert response.json() == mock_balance
    mock_moomoo_service.get_balance.assert_called_once()

def test_get_positions_error_handling(mock_moomoo_service):
    """Test error handling for positions endpoint."""
    mock_moomoo_service.get_positions.side_effect = Exception("Moomoo connection failed")
    
    response = client.get("/api/moomoo/positions")
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to fetch positions"

def test_get_balance_error_handling(mock_moomoo_service):
    """Test error handling for balance endpoint."""
    mock_moomoo_service.get_balance.side_effect = Exception("Moomoo connection failed")
    
    response = client.get("/api/moomoo/balance")
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to fetch balance"


@patch("main.prediction_service")
def test_create_prediction_api_success(mock_prediction_service):
    """Test successful prediction creation via POST /api/predictions."""
    mock_prediction_service.create_prediction.return_value = {
        "success": True,
        "prediction_id": 42
    }
    
    payload = {
        "symbol": "AAPL",
        "direction": "UP",
        "confidence": "80.0",
        "catalyst": "Strong earnings outlook",
        "category": "general",
        "timeframe": 7,
        "force": True
    }
    
    response = client.post("/api/predictions", json=payload)
    assert response.status_code == 200
    assert response.json() == {"success": True, "prediction_id": 42}
    mock_prediction_service.create_prediction.assert_called_once_with(
        symbol="AAPL",
        direction="UP",
        confidence=80.0,
        catalyst="Strong earnings outlook",
        category="general",
        timeframe_days=7,
        target_price=None,
        force=True,
        prediction_tag=None,
    )


def test_create_prediction_api_missing_fields():
    """Test prediction creation with missing fields."""
    payload = {
        "symbol": "AAPL",
        "direction": "UP"
        # missing confidence and catalyst
    }
    response = client.post("/api/predictions", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing required fields"


def test_create_prediction_api_invalid_types():
    """Test prediction creation with invalid parameter types."""
    payload = {
        "symbol": "AAPL",
        "direction": "UP",
        "confidence": "invalid_float",
        "catalyst": "Some catalyst"
    }
    response = client.post("/api/predictions", json=payload)
    assert response.status_code == 400
    assert "Invalid confidence or timeframe parameter" in response.json()["detail"]


@patch("main.prediction_service")
def test_create_prediction_api_safety_failure(mock_prediction_service):
    """Test prediction creation fails safety check and returns 422 with warnings."""
    mock_prediction_service.create_prediction.return_value = {
        "success": False,
        "error": "Safety check failed.",
        "warnings": ["Warning 1", "Warning 2"],
        "can_override": True
    }
    
    payload = {
        "symbol": "AAPL",
        "direction": "UP",
        "confidence": 75.0,
        "catalyst": "Strong earnings outlook",
        "force": False
    }
    
    response = client.post("/api/predictions", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "Safety check failed."
    assert data["warnings"] == ["Warning 1", "Warning 2"]
    assert data["can_override"] is True
    assert "Pass 'force': true" in data["hint"]


@patch("main.prediction_service")
def test_create_prediction_api_timeout(mock_prediction_service):
    """Test prediction creation timeout error handling."""
    mock_prediction_service.create_prediction.return_value = {
        "success": False,
        "error": "quote_timeout"
    }
    
    payload = {
        "symbol": "AAPL",
        "direction": "UP",
        "confidence": 75.0,
        "catalyst": "Strong earnings outlook"
    }
    
    response = client.post("/api/predictions", json=payload)
    assert response.status_code == 504
    assert response.json()["detail"] == "Timeout retrieving stock quote."


@patch("main.prediction_service")
def test_create_prediction_api_generic_failure(mock_prediction_service):
    """Test prediction creation generic failure handling."""
    mock_prediction_service.create_prediction.return_value = {
        "success": False,
        "error": "Some database issue"
    }
    
    payload = {
        "symbol": "AAPL",
        "direction": "UP",
        "confidence": 75.0,
        "catalyst": "Strong earnings outlook"
    }
    
    response = client.post("/api/predictions", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "Some database issue"


@patch("main.prediction_service")
def test_get_predictions_api(mock_prediction_service):
    """Test GET /api/predictions endpoint returns thesis_citations."""
    mock_prediction_service.get_active.return_value = [
        {
            "id": 1,
            "symbol": "GOOGL",
            "direction": "UP",
            "confidence": 95.0,
            "deadline": "2026-06-06T12:00:00",
            "catalyst": "Some thesis",
            "thesis_citations": [{"type": "tweet", "handle": "sundarpichai", "content": "Gemma 4"}]
        }
    ]
    response = client.get("/api/predictions?active=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["thesis_citations"] == [{"type": "tweet", "handle": "sundarpichai", "content": "Gemma 4"}]
