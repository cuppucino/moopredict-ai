import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from services.news_scraper import news_scraper
from core.database import NewsIntel

@patch("services.news_scraper.SessionLocal")
def test_get_recent_for_ticker_tight_matching(mock_session_cls):
    """Test get_recent_for_ticker applies the tighter matching criteria correctly."""
    mock_db = MagicMock()
    mock_session_cls.return_value = mock_db
    
    # Test cases:
    # 1. Google in first half, only ticker: MATCH
    # 2. Google in suffix only: NO MATCH
    # 3. Google in second half, multiple tickers: NO MATCH
    # 4. Google in first half, other tickers: MATCH
    # 5. Google in first half, only ticker: MATCH
    # 6. Google in second half, only ticker: MATCH
    # 7. Google in summary only: NO MATCH
    fake_articles = [
        NewsIntel(
            headline="Google launches Gemma 4 - Google News",
            summary="Gemma 4 is the new AI model.",
            source="Google News",
            scraped_at=datetime.utcnow() - timedelta(hours=2)
        ),
        NewsIntel(
            headline="Dow jumps 800 points, Nasdaq tumbles - Google News",
            summary="Market updates today.",
            source="Google News",
            scraped_at=datetime.utcnow() - timedelta(hours=3)
        ),
        NewsIntel(
            headline="Markets rise as Apple, Amazon, Nvidia, and Google hit new highs",
            summary="Tech stocks are surging.",
            source="Reuters",
            scraped_at=datetime.utcnow() - timedelta(hours=4)
        ),
        NewsIntel(
            headline="Google rises while Apple tumbles",
            summary="Tech earnings comparisons.",
            source="CNBC",
            scraped_at=datetime.utcnow() - timedelta(hours=5)
        ),
        NewsIntel(
            headline="Latest updates on Google's AI",
            summary="Updates from Google I/O.",
            source="MarketWatch",
            scraped_at=datetime.utcnow() - timedelta(hours=6)
        ),
        NewsIntel(
            headline="Earnings preview for Google",
            summary="Google is expected to report strong earnings.",
            source="CNBC",
            scraped_at=datetime.utcnow() - timedelta(hours=7)
        ),
        NewsIntel(
            headline="Dow jumps 500 points",
            summary="Google is doing great things today.",
            source="BBC",
            scraped_at=datetime.utcnow() - timedelta(hours=8)
        )
    ]
    
    mock_db.query.return_value.filter.return_value.all.return_value = fake_articles
    
    results = news_scraper.get_recent_for_ticker("GOOGL", hours=24)
    
    matched_headlines = [r["headline"] for r in results]
    
    # Check expected matches
    assert "Google launches Gemma 4 - Google News" in matched_headlines
    assert "Google rises while Apple tumbles" in matched_headlines
    assert "Latest updates on Google's AI" in matched_headlines
    assert "Earnings preview for Google" in matched_headlines
    
    # Check expected non-matches
    assert "Dow jumps 800 points, Nasdaq tumbles - Google News" not in matched_headlines
    assert "Markets rise as Apple, Amazon, Nvidia, and Google hit new highs" not in matched_headlines
    assert "Dow jumps 500 points" not in matched_headlines
    
    assert len(results) == 4
