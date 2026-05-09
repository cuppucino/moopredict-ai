import pytest
from unittest.mock import MagicMock, patch
from services.validation_service import validation_service
from services.sentiment_service import sentiment_service

def test_validation_logic_conflicting_sentiment():
    """Test that validation fails when sentiment strongly conflicts with prediction direction."""
    with patch("services.sentiment_service.sentiment_service.get_sentiment_score") as mock_sentiment:
        # Mock bearish sentiment
        mock_sentiment.return_value = {
            "score": -0.8,
            "label": "BEARISH",
            "reason": "Heavy selling pressure detected.",
            "data_count": 5
        }
        
        # Mock other services to pass
        with patch("services.earnings_calendar.earnings_service.get_stock_earnings") as mock_earnings, \
             patch("services.technical_analysis.ta_service.get_full_analysis") as mock_ta, \
             patch("services.options_flow.options_service.get_pcr") as mock_options:
            
            mock_earnings.return_value = {"success": True, "earnings_dates": ["2026-12-31"]}
            mock_ta.return_value = {"rsi": 50}
            mock_options.return_value = {"pcr": 0.8, "sentiment": "NEUTRAL"}
            
            # Predict UP with BEARISH sentiment
            res = validation_service.validate_prediction("AAPL", "UP")
            
            assert res["passed"] is False
            assert any("SENTIMENT CONFLICT" in w for w in res["warnings"])
            assert res["recommendation"] == "REJECT"

def test_validation_logic_priced_in():
    """Test that validation warns when RSI is overbought (Priced In)."""
    with patch("services.technical_analysis.ta_service.get_full_analysis") as mock_ta:
        mock_ta.return_value = {"rsi": 85} # Overbought
        
        with patch("services.sentiment_service.sentiment_service.get_sentiment_score") as mock_sentiment, \
             patch("services.earnings_calendar.earnings_service.get_stock_earnings") as mock_earnings, \
             patch("services.options_flow.options_service.get_pcr") as mock_options:
            
            mock_sentiment.return_value = {"score": 0.1, "label": "NEUTRAL"}
            mock_earnings.return_value = {"success": True, "earnings_dates": []}
            mock_options.return_value = {"pcr": 0.8, "sentiment": "NEUTRAL"}
            
            res = validation_service.validate_prediction("AAPL", "UP")
            
            assert res["passed"] is True # RSI warning is not a hard rejection in this logic
            assert any("PRICED IN" in w for w in res["warnings"])
            assert res["recommendation"] == "PROCEED WITH CAUTION"

def test_validation_logic_earnings_catalyst():
    """Test that validation warns about upcoming earnings."""
    from datetime import datetime, timedelta
    next_week = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    
    with patch("services.earnings_calendar.earnings_service.get_stock_earnings") as mock_earnings:
        mock_earnings.return_value = {
            "success": True, 
            "earnings_dates": [next_week]
        }
        
        with patch("services.sentiment_service.sentiment_service.get_sentiment_score") as mock_sentiment, \
             patch("services.technical_analysis.ta_service.get_full_analysis") as mock_ta, \
             patch("services.options_flow.options_service.get_pcr") as mock_options:
            
            mock_sentiment.return_value = {"score": 0.1, "label": "NEUTRAL"}
            mock_ta.return_value = {"rsi": 50}
            mock_options.return_value = {"pcr": 0.8, "sentiment": "NEUTRAL"}
            
            res = validation_service.validate_prediction("AAPL", "UP")
            
            assert any("EARNINGS CATALYST" in w for w in res["warnings"])
