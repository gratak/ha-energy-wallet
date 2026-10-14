from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.energy_wallet.pse import PriceTable, parse_rce

WAW = ZoneInfo("Europe/Warsaw")


def quarter_records(day: str, n: int, base: float = 100.0):
    out = []
    for i in range(n):
        end = datetime.fromisoformat(f"{day}T00:00") + timedelta(minutes=15 * (i + 1))
        dtime = end.strftime("%Y-%m-%d %H:%M:%S") if end.date().isoformat() == day else f"{day} 24:00:00"
        out.append({"business_date": day, "dtime": dtime, "period": "", "rce_pln": base + i})
    return out


def test_regular_day_has_96_quarters():
    periods = parse_rce(list(reversed(quarter_records("2026-10-14", 96))), WAW)
    assert len(periods) == 96
    table = PriceTable(periods)
    assert table.at(datetime(2026, 10, 14, 0, 7, tzinfo=WAW)) == 100.0
    assert table.at(datetime(2026, 10, 14, 23, 59, tzinfo=WAW)) == 195.0
    assert table.at(datetime(2026, 10, 15, 0, 1, tzinfo=WAW)) is None


def test_dst_fall_back_day_has_100_quarters():
    periods = parse_rce(quarter_records("2026-10-25", 100), WAW)
    starts = [p[0] for p in periods]
    assert all(b - a == 900 for a, b in zip(starts, starts[1:]))
    assert datetime.fromtimestamp(starts[-1], tz=timezone.utc).astimezone(WAW).hour == 23


def test_hourly_data_still_supported():
    records = [{"business_date": "2024-07-01", "dtime": f"2024-07-01 {h + 1:02d}:00:00", "rce_pln": h} for h in range(24)]
    table = PriceTable(parse_rce(records, WAW))
    assert table.at(datetime(2024, 7, 1, 5, 30, tzinfo=WAW)) == 5


def test_day_mean_requires_full_day():
    table = PriceTable(parse_rce(quarter_records("2026-10-14", 96), WAW))
    assert table.day_mean(date(2026, 10, 14), WAW) == pytest.approx(147.5)
    assert table.day_mean(date(2026, 10, 15), WAW) is None


def test_merge_keeps_recent():
    table = PriceTable(parse_rce(quarter_records("2026-10-13", 96), WAW))
    table.merge(parse_rce(quarter_records("2026-10-14", 96), WAW),
                keep_after=int(datetime(2026, 10, 14, tzinfo=WAW).timestamp()))
    assert len(table) == 96
