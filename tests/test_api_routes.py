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
