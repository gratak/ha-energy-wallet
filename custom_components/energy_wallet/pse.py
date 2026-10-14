"""PSE RCE (rynkowa cena energii) client.

Since 14 June 2024 RCE equals the Polish SDAC day-ahead price, i.e. the same
TGE Fixing I price that dynamic retail tariffs are indexed to, so one feed
serves both the sale and the dynamic purchase side. Since October 2025 PSE
publishes it in 15-minute periods; older hourly data is handled too.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

import aiohttp

PSE_RCE_URL = "https://api.raporty.pse.pl/api/rce-pln"
PSE_SELECT = "dtime,period,rce_pln,business_date"
PAGE_SIZE = 500
MAX_PAGES = 10


def _dtime_sort_key(dtime: str) -> str:
    # PSE marks the last period of the day as "24:00:00".
    return dtime.replace(" 24:00:00", " 23:59:60")


def parse_rce(records: list[dict[str, Any]], tz: tzinfo) -> list[tuple[int, int, float]]:
    """Turn PSE records into sorted (start_epoch, end_epoch, PLN/MWh) tuples.

    Periods are laid out evenly between local midnights, which keeps DST days
    (92 or 100 quarter-hours) right without parsing ambiguous wall times.
    """
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("business_date") and record.get("rce_pln") is not None:
            by_day[record["business_date"]].append(record)

    out: list[tuple[int, int, float]] = []
    for day_str, day_records in by_day.items():
        day = date.fromisoformat(day_str)
        day_records.sort(key=lambda r: _dtime_sort_key(str(r.get("dtime", ""))))
        start = datetime.combine(day, time(0), tz).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time(0), tz).astimezone(timezone.utc)
        step = (end - start) / len(day_records)
        for i, record in enumerate(day_records):
            p_start = start + step * i
            out.append((int(p_start.timestamp()), int((p_start + step).timestamp()), float(record["rce_pln"])))
    out.sort()
    return out


class PriceTable:
    """Lookup of RCE by time."""

    def __init__(self, periods: list[tuple[int, int, float]] | None = None) -> None:
        self._starts: list[int] = []
        self._periods: list[tuple[int, int, float]] = []
        if periods:
            self.merge(periods)

    def merge(self, periods: list[tuple[int, int, float]], keep_after: int | None = None) -> None:
        merged = {p[0]: p for p in self._periods}
        merged.update({p[0]: p for p in periods})
        items = sorted(merged.values())
        if keep_after is not None:
            items = [p for p in items if p[1] > keep_after]
        self._periods = items
        self._starts = [p[0] for p in items]

    def at(self, ts: datetime) -> float | None:
        epoch = ts.timestamp()
        idx = bisect_right(self._starts, epoch) - 1
        if idx < 0:
            return None
        start, end, price = self._periods[idx]
        return price if start <= epoch < end else None

    def day_mean(self, day: date, tz: tzinfo) -> float | None:
        """Mean RCE for a local day, only if the whole day is known."""
        start = datetime.combine(day, time(0), tz).timestamp()
        end = datetime.combine(day + timedelta(days=1), time(0), tz).timestamp()
        values = [p for p in self._periods if start <= p[0] < end]
        if not values or sum(p[1] - p[0] for p in values) < end - start:
            return None
        return sum(p[2] for p in values) / len(values)

    def between(self, start: datetime, end: datetime) -> list[tuple[int, int, float]]:
        s, e = start.timestamp(), end.timestamp()
        return [p for p in self._periods if p[1] > s and p[0] < e]

    def __len__(self) -> int:
        return len(self._periods)


async def fetch_rce(session: aiohttp.ClientSession, since: date) -> list[dict[str, Any]]:
    """Fetch raw RCE records for ``since`` and later days, following paging."""
    params: dict[str, Any] | None = {
        "$select": PSE_SELECT,
        "$filter": f"business_date ge '{since.isoformat()}'",
        "$first": PAGE_SIZE,
    }
    url: str | None = PSE_RCE_URL
    records: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        if url is None:
            break
        async with session.get(
            url,
            params=params,
            headers={"Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        records.extend(payload.get("value", []))
        url = payload.get("nextLink")
        params = None
    return records
