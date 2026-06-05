"""
Unit tests for PoliticalMonitor service.
Tests urgency classification, ticker extraction, effective urgency,
and alert firing logic.
"""

import pytest
from unittest.mock import patch, MagicMock
from services.political_monitor import (
    PoliticalMonitor,
    VIP_ACCOUNTS,
    KEYWORD_TIERS,
    _COMPILED_KEYWORDS,
)


@pytest.fixture
def monitor():
    """Fresh PoliticalMonitor instance for each test."""
    return PoliticalMonitor()


# ─── URGENCY CLASSIFICATION ─────────────────────────────────────


class TestClassifyUrgency:
    def test_p0_tariff(self, monitor):
        assert monitor._classify_urgency("New tariff on China 25%!") == "P0"

    def test_p0_rate_cut(self, monitor):
        assert monitor._classify_urgency("Fed announces rate cut") == "P0"

    def test_p0_executive_order(self, monitor):
        assert monitor._classify_urgency("Signing executive order today") == "P0"

    def test_p0_go_buy(self, monitor):
        assert monitor._classify_urgency("Go buy a Dell!") == "P0"

    def test_p0_debt_ceiling(self, monitor):
        assert monitor._classify_urgency("Debt ceiling crisis looming") == "P0"

    def test_p0_trade_deal(self, monitor):
        assert monitor._classify_urgency("Massive trade deal with EU") == "P0"

    def test_p1_china(self, monitor):
        assert monitor._classify_urgency("Meeting with China leaders") == "P1"

    def test_p1_bitcoin(self, monitor):
        assert monitor._classify_urgency("Bitcoin is the future") == "P1"

    def test_p1_tesla(self, monitor):
        assert monitor._classify_urgency("Tesla is doing great things") == "P1"

    def test_p1_antitrust(self, monitor):
        assert monitor._classify_urgency("Antitrust investigation launched") == "P1"

    def test_p2_general_market(self, monitor):
        assert monitor._classify_urgency("The stock market is strong") == "P2"

    def test_p2_tax_policy(self, monitor):
        assert monitor._classify_urgency("New tax reforms coming") == "P2"

    def test_p2_no_keywords(self, monitor):
        # Totally unrelated content defaults to P2
        assert monitor._classify_urgency("Had a great lunch today!") == "P2"

    def test_p0_overrides_p1(self, monitor):
        # Text containing both P0 and P1 keywords should return P0
        text = "Tariff on China 50% effective immediately"
        assert monitor._classify_urgency(text) == "P0"

    def test_case_insensitive(self, monitor):
        assert monitor._classify_urgency("TARIFF INCOMING!") == "P0"
        assert monitor._classify_urgency("bitcoin moon") == "P1"


# ─── TICKER EXTRACTION ──────────────────────────────────────────


class TestExtractTickers:
    def test_explicit_ticker(self, monitor):
        tickers = monitor._extract_tickers("I love $AAPL and $NVDA")
        assert "AAPL" in tickers
        assert "NVDA" in tickers

    def test_company_name_to_ticker(self, monitor):
        tickers = monitor._extract_tickers("Tesla is doing amazing this quarter")
        assert "TSLA" in tickers

    def test_dell_mention(self, monitor):
        tickers = monitor._extract_tickers("Go buy a Dell computer!")
        assert "DELL" in tickers

    def test_bitcoin_crypto(self, monitor):
        tickers = monitor._extract_tickers("Bitcoin hit new highs")
        assert "BTC" in tickers

    def test_multiple_companies(self, monitor):
        tickers = monitor._extract_tickers("Apple and Microsoft are leading tech")
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_no_tickers(self, monitor):
        tickers = monitor._extract_tickers("Great weather today!")
        assert len(tickers) == 0


# ─── EFFECTIVE URGENCY ──────────────────────────────────────────


class TestEffectiveUrgency:
    def test_s_tier_elevates_p1_to_p0(self, monitor):
        assert monitor._effective_urgency("P1", "S") == "P0"

    def test_s_tier_elevates_p2_to_p1(self, monitor):
        assert monitor._effective_urgency("P2", "S") == "P1"

    def test_s_tier_keeps_p0(self, monitor):
        assert monitor._effective_urgency("P0", "S") == "P0"

    def test_a_tier_no_change(self, monitor):
        assert monitor._effective_urgency("P0", "A") == "P0"
        assert monitor._effective_urgency("P1", "A") == "P1"
        assert monitor._effective_urgency("P2", "A") == "P2"

    def test_b_tier_caps_at_p1(self, monitor):
        assert monitor._effective_urgency("P0", "B") == "P1"


# ─── CANONICAL URL ───────────────────────────────────────────────


class TestCanonicalUrl:
    def test_existing_x_url(self, monitor):
        url = "https://x.com/realDonaldTrump/status/123456"
        result = monitor._canonical_url("realDonaldTrump", "test", url)
        assert result == url

    def test_generates_hash_for_missing_url(self, monitor):
        result = monitor._canonical_url("realDonaldTrump", "Some tweet content", "")
        assert result.startswith("https://x.com/realDonaldTrump/status/hash_")

    def test_same_content_same_hash(self, monitor):
        a = monitor._canonical_url("elonmusk", "Bitcoin is great", "")
        b = monitor._canonical_url("elonmusk", "Bitcoin is great", "")
        assert a == b

    def test_different_content_different_hash(self, monitor):
        a = monitor._canonical_url("elonmusk", "Bitcoin is great", "")
        b = monitor._canonical_url("elonmusk", "Dogecoin is great", "")
        assert a != b


# ─── VIP ACCOUNT REGISTRY ───────────────────────────────────────


class TestVIPAccounts:
    def test_trump_is_s_tier(self):
        assert VIP_ACCOUNTS["realDonaldTrump"]["tier"] == "S"

    def test_musk_is_a_tier(self):
        assert VIP_ACCOUNTS["elonmusk"]["tier"] == "A"

    def test_fed_is_b_tier(self):
        assert VIP_ACCOUNTS["federalreserve"]["tier"] == "B"

    def test_all_accounts_have_required_fields(self):
        required = {"display", "tier", "impact", "emoji"}
        for handle, meta in VIP_ACCOUNTS.items():
            for field in required:
                assert field in meta, f"@{handle} missing field: {field}"

    def test_all_tiers_valid(self):
        valid_tiers = {"S", "A", "B"}
        for handle, meta in VIP_ACCOUNTS.items():
            assert meta["tier"] in valid_tiers, f"@{handle} has invalid tier: {meta['tier']}"

    def test_tier_classification_s(self):
        from services.political_monitor import get_tier
        assert get_tier("realDonaldTrump") == "S"
        assert get_tier("jeromehpowell") == "S"
        assert get_tier("SecScottBessent") == "S"

    def test_tier_classification_a(self):
        from services.political_monitor import get_tier
        assert get_tier("JensenHuang") == "A"
        assert get_tier("elonmusk") == "A"

    def test_tier_classification_b(self):
        from services.political_monitor import get_tier
        assert get_tier("jimcramer") == "B"

    def test_tier_classification_unknown(self):
        from services.political_monitor import get_tier
        assert get_tier("randomperson") == "UNKNOWN"

    def test_removed_handles_not_in_vip_tiers(self):
        from services.political_monitor import VIP_TIERS
        all_handles = {h for tier in VIP_TIERS.values() for h in tier.keys()}
        assert "GaryGensler" not in all_handles
        assert "PGelsinger" not in all_handles


# ─── ALERT FIRING ────────────────────────────────────────────────


class TestFireAlert:
    @patch("services.political_monitor.notification_queue")
    def test_p0_alert_uses_alert_level(self, mock_queue, monitor):
        meta = VIP_ACCOUNTS["realDonaldTrump"]
        monitor._fire_alert(
            "realDonaldTrump", meta, "Tariff on China!", "P0", ["BABA"]
        )
        mock_queue.enqueue.assert_called_once()
        call_args = mock_queue.enqueue.call_args
        assert call_args[1]["level"] == "alert" or call_args[0][1] == "alert"

    @patch("services.political_monitor.notification_queue")
    def test_p1_alert_uses_info_level(self, mock_queue, monitor):
        meta = VIP_ACCOUNTS["elonmusk"]
        monitor._fire_alert(
            "elonmusk", meta, "Bitcoin is awesome", "P1", ["BTC"]
        )
        mock_queue.enqueue.assert_called_once()
        call_args = mock_queue.enqueue.call_args
        # Level should be "info" for P1
        assert "info" in str(call_args)

    @patch("services.political_monitor.notification_queue")
    def test_alert_contains_tickers(self, mock_queue, monitor):
        meta = VIP_ACCOUNTS["realDonaldTrump"]
        monitor._fire_alert(
            "realDonaldTrump", meta, "Go buy a Dell", "P0", ["DELL"]
        )
        message = mock_queue.enqueue.call_args[0][0]
        assert "DELL" in message

    @patch("services.political_monitor.notification_queue")
    def test_alert_contains_account_name(self, mock_queue, monitor):
        meta = VIP_ACCOUNTS["federalreserve"]
        monitor._fire_alert(
            "federalreserve", meta, "Rate decision coming", "P1", []
        )
        message = mock_queue.enqueue.call_args[0][0]
        assert "Federal Reserve" in message


# ─── KEYWORD PATTERN COVERAGE ────────────────────────────────────


class TestKeywordCoverage:
    def test_compiled_patterns_exist(self):
        assert "P0" in _COMPILED_KEYWORDS
        assert "P1" in _COMPILED_KEYWORDS
        assert "P2" in _COMPILED_KEYWORDS

    def test_p0_has_tariff_patterns(self):
        assert _COMPILED_KEYWORDS["P0"].search("tariff")

    def test_p1_has_geopolitical_patterns(self):
        assert _COMPILED_KEYWORDS["P1"].search("summit with China")

    def test_p2_has_economic_patterns(self):
        assert _COMPILED_KEYWORDS["P2"].search("GDP growth numbers")


# ─── SENTIMENT SCORING (mocked DB) ──────────────────────────────


class TestGetRecentPoliticalSentiment:
    @patch("services.political_monitor.SessionLocal")
    def test_returns_no_data_when_empty(self, mock_session_cls, monitor):
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = monitor.get_recent_political_sentiment()
        assert result["label"] == "NO_DATA"
        assert result["post_count"] == 0

    @patch("services.political_monitor.SessionLocal")
    @patch("services.sentiment_engine.sentiment_engine")
    def test_returns_scored_data(self, mock_sent_engine, mock_session_cls, monitor):
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        # Create fake posts
        mock_post = MagicMock()
        mock_post.content = "Tariff on China 25%!"
        mock_post.author = "realDonaldTrump"
        mock_post.scraped_at = MagicMock()

        mock_db.query.return_value.filter.return_value.all.return_value = [mock_post]
        mock_sent_engine.score_text.return_value = -0.8

        result = monitor.get_recent_political_sentiment()
        assert result["post_count"] == 1
        assert isinstance(result["score"], (int, float))


def test_matches_ticker_cases():
    from services.political_monitor import matches_ticker
    # Direct dollar sign matches
    assert matches_ticker("Check out $GOOGL today!", "GOOGL") is True
    assert matches_ticker("Check out $googl today!", "GOOGL") is True
    # Word boundaries matches
    assert matches_ticker("We have GOOGL in our sights", "GOOGL") is True
    assert matches_ticker("googl is doing well", "GOOGL") is True
    # Alias matches
    assert matches_ticker("Sundar Pichai announces Gemma 4", "GOOGL") is True
    assert matches_ticker("Google launches a new AI model called Gemma", "GOOGL") is True
    assert matches_ticker("YouTube Premium has new updates", "GOOGL") is True
    assert matches_ticker("DeepMind is leading research", "GOOGL") is True
    # Case insensitivity for aliases
    assert matches_ticker("gemma is great", "GOOGL") is True
    assert matches_ticker("pichai is here", "GOOGL") is True
    # Non-match cases
    assert matches_ticker("We are going to the mall", "GOOGL") is False
    assert matches_ticker("GoogleNews is a merged word", "GOOGL") is False
    assert matches_ticker("Gemma4 is merged", "GOOGL") is False
