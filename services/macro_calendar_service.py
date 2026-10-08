import calendar
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from loguru import logger

NY_TZ = ZoneInfo("America/New_York")
FF_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "macro_calendar_cache.json"
HTTP_TIMEOUT_SEC = 15
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (moopredict macro calendar)"}
REFRESH_HOURS = 6
FF_IMPACT_MAP = {"High": "HIGH", "Medium": "MEDIUM"}
QUAD_WITCHING_MONTHS = {3, 6, 9, 12}
FRIDAY = 4
FOMC_KEYWORDS = ("FOMC", "Federal Funds Rate")

_YEAR_SECTION_RE = re.compile(r"(\d{4}) FOMC Meetings")
_MEETING_RE = re.compile(
    r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<",
    re.S,
)
_MONTHS = {name.lower()[:3]: i for i, name in enumerate(calendar.month_name) if name}


def _event(name: str, impact: str, day: date, time: str) -> Dict:
    return {"event": name, "impact": impact, "date": day.isoformat(), "time": time}


def _decision_day(year: int, month_label: str, date_text: str) -> Optional[date]:
    """Last day of a meeting; 'Apr/May' + '30-1' resolves to May 1."""
    days = [int(d) for d in re.findall(r"\d+", date_text)]
    months = [m for m in (_MONTHS.get(p.strip().lower()[:3]) for p in month_label.split("/")) if m]
    if not days or not months:
        return None
    month = months[-1] if len(days) > 1 and days[-1] < days[0] else months[0]
    try:
        return date(year, month, days[-1])
    except ValueError:
        return None


def parse_fomc_html(html: str) -> List[Dict]:
    """FOMC decision days from the Fed's official meeting calendar page."""
    headers = list(_YEAR_SECTION_RE.finditer(html))
    events = []
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(html)
        year = int(header.group(1))
        for month_label, date_text in _MEETING_RE.findall(html[header.end():end]):
            day = _decision_day(year, month_label, date_text)
            if day:
                events.append(_event("FOMC Rate Decision", "HIGH", day, "14:00 ET"))
    return events


def parse_ff_feed(items: List[Dict]) -> List[Dict]:
    """US high/medium-impact releases from the ForexFactory weekly JSON feed."""
    events = []
    for item in items:
        impact = FF_IMPACT_MAP.get(item.get("impact", ""))
        if item.get("country") != "USD" or not impact:
            continue
        try:
            when = datetime.fromisoformat(item["date"]).astimezone(NY_TZ)
        except (KeyError, TypeError, ValueError):
            logger.warning(f"[Macro] Skipping feed item with bad date: {item.get('title')}")
            continue
        events.append(_event(item.get("title", "Unknown"), impact, when.date(), f"{when:%H:%M} ET"))
    return events


def opex_events(start: date, end: date) -> List[Dict]:
    """Monthly options expiry (third Friday); quarterly ones are quad witching."""
    events = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        fridays = [d for d in calendar.Calendar().itermonthdates(year, month)
                   if d.month == month and d.weekday() == FRIDAY]
        third_friday = fridays[2]
        if start <= third_friday <= end:
            if month in QUAD_WITCHING_MONTHS:
                events.append(_event("Quad Witching (quarterly OPEX + index rebalance)",
                                     "HIGH", third_friday, "Close"))
            else:
                events.append(_event("Monthly Options Expiry", "MEDIUM", third_friday, "Close"))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return events


def _fetch_ff() -> List[Dict]:
    resp = requests.get(FF_FEED_URL, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.json()


def _fetch_fomc_html() -> str:
    resp = requests.get(FOMC_CALENDAR_URL, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.text


def _today_ny() -> date:
    return datetime.now(NY_TZ).date()


class MacroCalendarService:
    def __init__(
        self,
        cache_path: Path = DEFAULT_CACHE_PATH,
        fetch_ff: Callable[[], List[Dict]] = _fetch_ff,
        fetch_fomc_html: Callable[[], str] = _fetch_fomc_html,
        today_fn: Callable[[], date] = _today_ny,
    ):
        self._cache_path = Path(cache_path)
        self._fetch_ff = fetch_ff
        self._fetch_fomc_html = fetch_fomc_html
        self._today_fn = today_fn
        self._sources: Optional[Dict] = None

    def _load_cache(self) -> Dict:
        try:
            return json.loads(self._cache_path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as ex:
            logger.error(f"[Macro] Unreadable calendar cache {self._cache_path}: {ex}")
            return {}

    def _write_cache(self, sources: Dict) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(sources, indent=2))
        except OSError as ex:
            logger.error(f"[Macro] Could not write calendar cache: {ex}")

    def _fetch_source(self, name: str, fetch: Callable, parse: Callable, cached: Dict) -> List[Dict]:
        try:
            events = parse(fetch())
            if events:
                return events
            logger.warning(f"[Macro] {name} returned no events; using cache")
        except Exception as ex:
            logger.error(f"[Macro] {name} fetch failed, using cache: {ex}")
        return cached.get(f"{name}_events", [])

    def refresh(self) -> Dict:
        cached = self._load_cache()
        sources = {
            "fetched_at": datetime.now(NY_TZ).isoformat(),
            "ff_events": self._fetch_source("ff", self._fetch_ff, parse_ff_feed, cached),
            "fomc_events": self._fetch_source("fomc", self._fetch_fomc_html, parse_fomc_html, cached),
        }
        self._write_cache(sources)
        self._sources = sources
        return sources

    def _current_sources(self) -> Dict:
        if self._sources:
            age = datetime.now(NY_TZ) - datetime.fromisoformat(self._sources["fetched_at"])
            if age < timedelta(hours=REFRESH_HOURS):
                return self._sources
        return self.refresh()

    def get_upcoming_events(self, days: int = 7) -> List[Dict]:
        """Events from today (New York date) through today + days, inclusive."""
        start = self._today_fn()
        end = start + timedelta(days=days)
        sources = self._current_sources()
        feed = sources.get("ff_events", [])
        feed_fomc_days = {e["date"] for e in feed if any(k in e["event"] for k in FOMC_KEYWORDS)}
        fomc = [e for e in sources.get("fomc_events", []) if e["date"] not in feed_fomc_days]
        merged = feed + fomc + opex_events(start, end)
        in_window = [e for e in merged if start.isoformat() <= e["date"] <= end.isoformat()]
        return sorted(in_window, key=lambda e: (e["date"], e["time"]))

    def generate_weekly_briefing(self) -> str:
        """Generate a formatted briefing for the upcoming week."""
        upcoming = self.get_upcoming_events(7)
        if not upcoming:
            return "📅 *Macro Calendar*: No high-impact events scheduled for the coming week."

        lines = ["🗓️ *Weekly Macro Calendar*"]
        for e in upcoming:
            impact_emoji = "🔴" if e["impact"] == "HIGH" else "🟡"
            lines.append(f"{impact_emoji} *{e['event']}*")
            lines.append(f"  • Date: {e['date']} | {e['time']}")
        return "\n".join(lines)


macro_service = MacroCalendarService()
