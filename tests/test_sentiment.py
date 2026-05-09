import pytest
from unittest.mock import MagicMock, patch
from services.sentiment_service import sentiment_service

def test_sentiment_service_bullish():
    """Test sentiment analysis with mocked AI response."""
    with patch("services.ai_service.ai_service.query_json") as mock_ai:
        mock_ai.return_value = {
            "score": 0.85,
            "reason": "Very positive news regarding breakthrough technology."
        }
        
        # Mock DB queries to return some data
        with patch("core.database.SessionLocal") as mock_db_session:
            mock_db = MagicMock()
            mock_db_session.return_value = mock_db
            
            # Mock news and social results
            mock_news = [MagicMock(headline="AAPL announces record profits", scraped_at="2024-01-01")]
            mock_db.query().filter().order_by().limit().all.side_effect = [mock_news, []]
            
            res = sentiment_service.get_sentiment_score("AAPL")
            
            assert res["score"] == 0.85
            assert res["label"] == "BULLISH"
            assert "positive news" in res["reason"]

def test_sentiment_service_insufficient_data():
    """Test sentiment service when no data is found."""
    with patch("core.database.SessionLocal") as mock_db_session:
        mock_db = MagicMock()
        mock_db_session.return_value = mock_db
        
        # Mock empty results
        mock_db.query().filter().order_by().limit().all.return_value = []
        
        res = sentiment_service.get_sentiment_score("UNKNOWN_TICKER")
        
        assert res["score"] == 0
        assert res["label"] == "NEUTRAL"
        assert "Insufficient" in res["reason"]
