"""Exchange sessions and validated, start-labeled market data for paper research."""
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
ARCHIVE = Path(__file__).resolve().parent.parent / "data" / "intraday_market"


@lru_cache(maxsize=2048)
def market_session(day: date):
    schedule = mcal.get_calendar("NYSE").schedule(day, day)
    if schedule.empty:
        return None
    row = schedule.iloc[0]
    return row.market_open.to_pydatetime().astimezone(NY), row.market_close.to_pydatetime().astimezone(NY)


def session_window(day: date):
    session = market_session(day)
    if session is None:
        raise ValueError("market_closed")
    start, close = session
    end = datetime.combine(day, time(13, 30), NY)
    if close < end:
        raise ValueError("short_session_excluded")
    return start, end


def normalize_bars(frame, *, timestamp_label="start", interval_minutes=5):
    """Adapters explicitly identify end labels. Internal frames always use bar starts.

    Reject bad rows and duplicates instead of silently discarding extrema. This also
    prevents partially malformed sessions from passing completeness validation.
    """
    if timestamp_label not in ("start", "end"):
        raise ValueError("unknown_timestamp_label")
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz=NY))
    bars = frame.copy()
    index = pd.DatetimeIndex(pd.to_datetime(bars.pop("time_key") if "time_key" in bars else bars.index))
    index = index.tz_localize(NY) if index.tz is None else index.tz_convert(NY)
    if timestamp_label == "end":
        index = index - pd.Timedelta(minutes=interval_minutes)
    if index.hasnans or index.has_duplicates:
        raise ValueError("invalid_or_duplicate_bar_timestamp")
    bars.index = index
    columns = ["open", "high", "low", "close"]
    if any(c not in bars for c in columns):
        raise ValueError("missing_ohlc")
    if "volume" in bars:
        columns.append("volume")
    for c in columns:
        bars[c] = pd.to_numeric(bars[c], errors="raise")
    if not np.isfinite(bars[columns].to_numpy()).all():
        raise ValueError("nonfinite_bar")
    if ((bars[["open", "high", "low", "close"]] <= 0).any().any()
            or (bars.high < bars[["open", "close", "low"]].max(axis=1)).any()
            or (bars.low > bars[["open", "close", "high"]].min(axis=1)).any()
            or ("volume" in bars and (bars.volume < 0).any())):
        raise ValueError("invalid_ohlcv")
    bars.attrs.update(timestamp_label="start", interval_minutes=interval_minutes)
    return bars.sort_index()


def complete_window(frame, day, *, end=None, interval_minutes=5):
    start, deadline = session_window(day)
    end = end or deadline
    if end <= start or end > deadline:
        raise ValueError("invalid_window_end")
    bars = normalize_bars(frame, interval_minutes=interval_minutes)
    window = bars[(bars.index >= start) & (bars.index < end)]
    expected = pd.date_range(start, end, freq=f"{interval_minutes}min", inclusive="left")
    if not window.index.equals(expected):
        raise ValueError(f"incomplete_window:expected={len(expected)},observed={len(window)}")
    return window


def archive_bars(frame, metadata):
    payload = {"metadata": metadata,
               "bars": json.loads(frame.to_json(orient="records", date_format="iso", double_precision=15))}
    raw = json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = ARCHIVE / digest[:2] / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Content addressed and atomically published; identical fetches share one file.
    if not path.exists():
        import tempfile
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            f.write(raw)
            temporary = Path(f.name)
        temporary.replace(path)
    return {"sha256": digest, "path": str(path), "received_at": datetime.now(UTC).isoformat(), **metadata}


class FutuIntradayBars:
    """Quote-only historical data adapter; Futu timestamps identify interval ends."""
    def fetch(self, symbol, start, end, interval_minutes=5):
        from futu import KLType, RET_OK, AuType
        from services.moomoo_service import moomoo_service
        if interval_minutes not in (1, 5):
            raise ValueError("unsupported_bar_interval")
        if not moomoo_service.is_connected and not moomoo_service.connect():
            raise RuntimeError("moomoo_unavailable")
        code = symbol if "." in symbol else f"US.{symbol}"
        pages, page_key, seen = [], None, set()
        for _ in range(100):
            ret, data, next_key = moomoo_service.quote_ctx.request_history_kline(
                code, start=start.isoformat(), end=end.isoformat(),
                ktype=KLType.K_1M if interval_minutes == 1 else KLType.K_5M,
                autype=AuType.QFQ, max_count=1000, page_req_key=page_key, extended_time=False,
            )
            if ret != RET_OK:
                raise RuntimeError(f"intraday_bars_failed:{data}")
            pages.append(data)
            if next_key is None:
                break
            if next_key in seen:
                raise RuntimeError("repeated_history_page")
            seen.add(next_key)
            page_key = next_key
        else:
            raise RuntimeError("history_page_limit")
        raw = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()
        manifest = archive_bars(raw, {"provider": "futu", "symbol": code,
            "start": start.isoformat(), "end": end.isoformat(), "interval_minutes": interval_minutes,
            "provider_timestamp_label": "end", "adjustment": "QFQ", "session": "regular"})
        bars = normalize_bars(raw, timestamp_label="end", interval_minutes=interval_minutes)
        bars.attrs["provenance"] = manifest
        return bars


def load_archived_bars(manifest):
    """Verify the exact saved data before replay; never fetch a replacement."""
    raw = Path(manifest["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("archive_hash_mismatch")
    payload = json.loads(raw)
    metadata = payload["metadata"]
    bars = normalize_bars(pd.DataFrame(payload["bars"]),
        timestamp_label=metadata["provider_timestamp_label"], interval_minutes=metadata["interval_minutes"])
    bars.attrs["provenance"] = manifest
    return bars
