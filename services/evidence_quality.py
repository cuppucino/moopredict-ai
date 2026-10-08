"""Observational input checks. These never reweight scores or gate a paper trade."""
from datetime import datetime, timezone
from html import unescape
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UTC = timezone.utc
VERSION = "evidence-quality-v1"


def utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def news_record(row):
    """Keep publication and collection timestamps separate, including legacy nulls."""
    def stamp(name):
        value = utc(getattr(row, name, None))
        return value.isoformat() if value else None
    return {"id": row.id, "headline": row.headline, "summary": row.summary,
            "source": row.source, "url": row.url,
            "published_at": stamp("published_at"), "scraped_at": stamp("scraped_at")}


def _headline(text):
    text = re.sub(r"<[^>]*>", " ", unescape(text or "")).casefold()
    text = re.sub(r"\s+[-|–]\s+(google news|reuters|cnbc|marketwatch|bbc news|bbc|yahoo finance|bloomberg|wsj)$", "", text)
    return " ".join(re.findall(r"\w+", text))


def _url(value):
    try:
        parts = urlsplit(value or "")
        if not parts.netloc:
            return ""
        query = [(k, v) for k, v in parse_qsl(parts.query)
                 if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                           urlencode(sorted(query)), ""))
    except ValueError:
        return ""


def summarize_evidence(news, social=(), *, observed_at, lookback_hours=48):
    """Deduplicate coverage only; retain the frozen model's original input weighting.

    'fresh' means published within the stated lookback, not relevant or predictive.
    Missing publication times (including social posts) never become fresh on scrape.
    """
    now = utc(observed_at)
    if now is None:
        raise ValueError("evidence_observation_time_required")
    seen_urls, seen_titles, unique = set(), set(), []
    for item in news:
        url, title = _url(item.get("url")), _headline(item.get("headline"))
        duplicate = (url and url in seen_urls) or (title and title in seen_titles)
        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)
        if not duplicate:
            unique.append(item)
    counts = {"fresh": 0, "stale": 0, "unknown": 0, "future": 0}
    ages = []
    for item in [*unique, *social]:
        published = utc(item.get("published_at"))
        if published is None:
            counts["unknown"] += 1
            continue
        age = (now-published).total_seconds()/3600
        if age < 0:
            counts["future"] += 1
        else:
            ages.append(age)
            counts["fresh" if age <= lookback_hours else "stale"] += 1
    total = len(unique)+len(social)
    status = ("missing" if not total else "unknown" if counts["unknown"] == total
              else "fresh" if counts["fresh"] == total else "stale" if counts["stale"] == total
              else "partial")
    warnings = []
    if not total:
        warnings.append("No matching evidence; a zero sentiment fallback is not neutral evidence.")
    if counts["unknown"]:
        warnings.append("Publication time unavailable; collection time does not establish freshness.")
    if counts["stale"]:
        warnings.append("Some evidence was published before the lookback window.")
    if counts["future"]:
        warnings.append("Future publication timestamp; freshness is unverified.")
    if len(news) > len(unique):
        warnings.append("Repeated headlines/URLs detected; model weighting is unchanged.")
    return {"version": VERSION, "status": status, "observed_at": now.isoformat(),
            "lookback_hours": lookback_hours, "news_count": len(news),
            "unique_news_count": len(unique), "duplicate_news_count": len(news)-len(unique),
            "source_count": len({n.get("source") for n in unique if n.get("source")}),
            "social_count": len(social), "publication_counts": counts,
            "newest_publication_age_hours": min(ages) if ages else None,
            "oldest_publication_age_hours": max(ages) if ages else None,
            "warnings": warnings, "affects_trading_policy": False}
