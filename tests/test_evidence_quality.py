from datetime import datetime, timedelta, timezone
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text, inspect

from core.schema_migrations import ensure_news_publication_column
from services.evidence_quality import summarize_evidence
from services.news_scraper import publication_time, NewsScraper
from services.sentiment_engine import sentiment_engine
from services.paper_diagnostics import forecast_evidence

NOW = datetime(2026, 10, 5, 13, 15, tzinfo=timezone.utc)


def quality(news, social=()):
    return summarize_evidence(news, social, observed_at=NOW)


def test_missing_neutral_unknown_and_stale_are_distinct():
    assert quality([])["status"] == "missing"
    assert quality([{"headline": "Flat markets", "scraped_at": NOW}])["status"] == "unknown"
    assert quality([{"headline": "Flat markets", "published_at": NOW}])["status"] == "fresh"
    old = {"headline": "Old story", "published_at": NOW-timedelta(days=4), "scraped_at": NOW}
    assert quality([old])["status"] == "stale"  # A new scrape cannot refresh a stale story.
    assert quality([], [{"scraped_at": NOW}])["status"] == "unknown"


def test_future_dates_and_mixed_coverage_are_not_fresh():
    data = [{"headline": "Fresh", "published_at": NOW}, {"headline": "Undated"},
            {"headline": "Future", "published_at": NOW+timedelta(hours=1)}]
    before = deepcopy(data)
    result = quality(data)
    assert result["status"] == "partial"
    assert result["publication_counts"] == {"fresh": 1, "stale": 0, "unknown": 1, "future": 1}
    assert data == before and not result["affects_trading_policy"]


def test_duplicate_headline_and_tracking_url_do_not_inflate_coverage():
    result = quality([
        {"headline": "Nasdaq rises - Reuters", "url": "https://example.org/story?utm_source=x", "published_at": NOW},
        {"headline": "NASDAQ rises - CNBC", "url": "https://elsewhere.org/story", "published_at": NOW},
        {"headline": "Retitled same URL", "url": "https://example.org/story", "published_at": NOW},
    ])
    assert result["news_count"] == 3 and result["unique_news_count"] == 1
    assert result["duplicate_news_count"] == 2


@pytest.mark.parametrize("entry,expected", [
    ({"published": "Fri, 02 Oct 2026 09:15:00 -0400"}, "2026-10-02T13:15:00+00:00"),
    ({"published": "2026-10-02T13:15:00Z"}, "2026-10-02T13:15:00+00:00"),
    ({"updated": "2026-10-02T13:15:00Z"}, None),
    ({"published": "2026-10-02T13:15:00"}, None),
    ({"published": "invalid"}, None),
])
def test_publication_timestamp_never_invented(entry, expected):
    result = publication_time(entry)
    assert (result.isoformat() if result else None) == expected


def test_migration_preserves_rows_and_is_repeatable():
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.execute(text("CREATE TABLE news_intel (id INTEGER PRIMARY KEY, headline TEXT)"))
        db.execute(text("INSERT INTO news_intel VALUES (1, 'unchanged')"))
    ensure_news_publication_column(engine)
    ensure_news_publication_column(engine)
    assert "published_at" in {c["name"] for c in inspect(engine).get_columns("news_intel")}
    with engine.connect() as db:
        assert db.execute(text("SELECT * FROM news_intel")).one() == (1, "unchanged", None)


def test_empty_sentiment_explicitly_missing_keeps_numeric_fallback():
    with patch("services.sentiment_engine.SessionLocal") as factory:
        factory.return_value.query.return_value.filter.return_value.all.side_effect = [[], []]
        result = sentiment_engine.score_symbol("SMH")
    assert result["label"] == "NO_DATA" and result["score"] == 0
    assert result["evidence_quality"]["status"] == "missing"


def test_historical_evidence_uses_saved_issue_time_and_never_current_news():
    snapshot = {"focus": {"sources": {"news": {
        "sentiment_result": {"score": 0, "count": 0},
        "sentiment_inputs": {"observed_at": NOW.isoformat(), "news": [], "social": []},
        "observed_at": NOW.isoformat(), "driver_news": [{"headline": "Old saved item", "scraped_at": NOW.isoformat()}]
    }}}}
    before = deepcopy(snapshot)
    result = forecast_evidence(snapshot)
    assert result["direct_sentiment"]["status"] == "missing"
    assert result["driver_news"]["status"] == "unknown"
    assert forecast_evidence({})["direct_sentiment"]["status"] == "not_recorded"
    assert before == snapshot


def test_scraper_stores_publication_date_without_rewriting_existing_article():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [None, object()]
    entries = [{"title": "New", "link": "https://example.org/new", "published": "2026-10-02T13:15:00Z"},
               {"title": "Existing", "link": "https://example.org/old", "published": "2026-10-02T13:15:00Z"}]
    with patch("services.news_scraper.SessionLocal", return_value=db), \
         patch("services.news_scraper.FEEDS", [{"name": "test", "url": "https://example.org/rss"}]), \
         patch("requests.get"), patch("services.news_scraper.feedparser.parse", return_value=MagicMock(entries=entries)), \
         patch("services.ai_service.ai_service.summarize_content", return_value="Summary"), \
         patch("services._legacy.pattern_service.pattern_service.get_all", return_value=[]), \
         patch("services.news_scraper.notification_queue.enqueue"):
        assert NewsScraper().run() == 1
    row = db.add.call_args.args[0]
    assert row.published_at == datetime(2026, 10, 2, 13, 15, tzinfo=timezone.utc)
    assert row.headline == "New" and db.add.call_count == 1


def test_quality_diagnostics_do_not_change_frozen_news_signal():
    from core.database import NewsIntel
    from services.focus_engine import _news_signal
    # Both a stale duplicate and an undated article remain in the frozen input weights.
    rows = [NewsIntel(id=i, headline="Nasdaq rises", summary="", source="test", url=f"https://test/{i}",
                      scraped_at=NOW, published_at=NOW-timedelta(days=10) if i == 1 else None)
            for i in (1, 2)]
    with patch("services.focus_engine.SessionLocal") as factory, \
         patch("services.focus_engine.sentiment_engine.score_symbol", return_value={"score": .2, "data_count": 1}):
        factory.return_value.query.return_value.filter.return_value.all.return_value = rows
        plain = _news_signal("QQQ")
        trace = {}
        recorded = _news_signal("QQQ", trace)
    assert recorded == plain
    assert plain[0] == pytest.approx(.6*.2+.4*1)
    assert trace["driver_quality"]["duplicate_news_count"] == 1
