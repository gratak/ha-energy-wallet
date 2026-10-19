"""Tariff model: time zones, Polish holidays, buy/sell price math.

Kept free of Home Assistant imports so it can be unit tested on its own.
All prices are in PLN per kWh; ``rce`` values are in PLN per MWh, as PSE
publishes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

TARIFF_DIR = Path(__file__).parent / "tariffs"

PEAK = "peak"
OFFPEAK = "offpeak"
# G13: PEAK is the morning peak, PEAK2 the afternoon peak, OFFPEAK the remaining hours.
PEAK2 = "peak2"

# Some DSOs (PGE Dystrybucja) move the afternoon off-peak window in summer;
# G13 moves its afternoon peak.
SUMMER_MONTHS = range(4, 10)


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=16)
def polish_holidays(year: int) -> frozenset[date]:
    """Public holidays in Poland (days free from work)."""
    easter = easter_sunday(year)
    days = {
        date(year, 1, 1),
        date(year, 1, 6),
        easter,
        easter + timedelta(days=1),
        date(year, 5, 1),
        date(year, 5, 3),
        easter + timedelta(days=49),  # Zielone Świątki
        easter + timedelta(days=60),  # Boże Ciało
        date(year, 8, 15),
        date(year, 11, 1),
        date(year, 11, 11),
        date(year, 12, 25),
        date(year, 12, 26),
    }
    if year >= 2025:
        days.add(date(year, 12, 24))
    return frozenset(days)


_WINDOW_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*$")


def parse_windows(spec: str | list[str] | None) -> list[tuple[int, int]]:
    """Parse "13-15,22-6" or ["13:00-15:00", ...] into minute ranges."""
    if not spec:
        return []
    parts = spec.split(",") if isinstance(spec, str) else list(spec)
    windows = []
    for part in parts:
        if not part.strip():
            continue
        match = _WINDOW_RE.match(part)
        if not match:
            raise ValueError(f"Invalid time window: {part!r}")
        h1, m1, h2, m2 = match.groups()
        start = int(h1) * 60 + int(m1 or 0)
        end = int(h2) * 60 + int(m2 or 0)
        if not (0 <= start <= 1440 and 0 <= end <= 1440):
            raise ValueError(f"Invalid time window: {part!r}")
        windows.append((start % 1440, end % 1440))
    return windows


def format_windows(windows: list[tuple[int, int]]) -> str:
    def fmt(minute: int) -> str:
        h, m = divmod(minute, 60)
        return f"{h}" if m == 0 else f"{h}:{m:02d}"

    return ",".join(f"{fmt(s)}-{fmt(e)}" for s, e in windows)


def _in_windows(minute: int, windows: list[tuple[int, int]]) -> bool:
    for start, end in windows:
        if start == end:
            return True
        if start < end and start <= minute < end:
            return True
        if start > end and (minute >= start or minute < end):
            return True
    return False


def sell_price(rce: float | None, multiplier: float = 1.0, clamp_negative: bool = True) -> float | None:
    """Value of 1 kWh fed into the grid (net-billing deposit), PLN/kWh."""
    if rce is None:
        return None
    value = rce / 1000
    if clamp_negative and value < 0:
        value = 0.0
    return value * multiplier


@dataclass
class PriceConfig:
    """Everything needed to price 1 kWh bought from the grid."""

    tariff_group: str = "G11"
    dynamic: bool = False
    energy_peak: float = 0.0
    energy_offpeak: float = 0.0
    dynamic_markup: float = 0.0
    dist_peak: float = 0.0
    dist_offpeak: float = 0.0
    energy_peak2: float = 0.0
    dist_peak2: float = 0.0
    offpeak_windows: list[tuple[int, int]] = field(default_factory=list)
    offpeak_windows_summer: list[tuple[int, int]] | None = None
    peak2_windows: list[tuple[int, int]] = field(default_factory=list)
    peak2_windows_summer: list[tuple[int, int]] | None = None
    weekend_offpeak: bool = False
    holiday_offpeak: bool = False
    fixed_monthly: float = 0.0
    excise: float = 0.005
    vat: float = 0.23

    @property
    def two_zone(self) -> bool:
        return self.tariff_group != "G11"

    @property
    def three_zone(self) -> bool:
        return self.tariff_group == "G13"

    def zone(self, local_dt: datetime) -> str:
        if not self.two_zone:
            return PEAK
        day = local_dt.date()
        if self.weekend_offpeak and day.weekday() >= 5:
            return OFFPEAK
        if self.holiday_offpeak and day in polish_holidays(day.year):
            return OFFPEAK
        summer = local_dt.month in SUMMER_MONTHS
        minute = local_dt.hour * 60 + local_dt.minute
        if self.three_zone:
            peak2 = self.peak2_windows_summer if self.peak2_windows_summer and summer else self.peak2_windows
            if _in_windows(minute, peak2):
                return PEAK2
        windows = self.offpeak_windows_summer if self.offpeak_windows_summer and summer else self.offpeak_windows
        return OFFPEAK if _in_windows(minute, windows) else PEAK

    def energy_component(self, local_dt: datetime, rce: float | None) -> float | None:
        """Seller's part of the price, net PLN/kWh."""
        if self.dynamic:
            if rce is None:
                return None
            return rce / 1000 + self.dynamic_markup
        zone = self.zone(local_dt)
        if zone == OFFPEAK:
            return self.energy_offpeak
        if zone == PEAK2:
            return self.energy_peak2
        return self.energy_peak

    def distribution_component(self, local_dt: datetime) -> float:
        """Variable distribution charges, net PLN/kWh."""
        zone = self.zone(local_dt)
        if zone == OFFPEAK:
            return self.dist_offpeak
        if zone == PEAK2:
            return self.dist_peak2
        return self.dist_peak

    def buy_price(self, local_dt: datetime, rce: float | None = None) -> float | None:
        """Gross PLN/kWh for energy taken from the grid at ``local_dt``.

        Tariff energy prices are published without excise, which the bill adds
        per kWh before VAT.
        """
        energy = self.energy_component(local_dt, rce)
        if energy is None:
            return None
        return (energy + self.excise + self.distribution_component(local_dt)) * (1 + self.vat)

    @property
    def fixed_monthly_gross(self) -> float:
        return self.fixed_monthly * (1 + self.vat)


def available_catalog_years() -> list[int]:
    return sorted(int(p.stem) for p in TARIFF_DIR.glob("*.json") if p.stem.isdigit())


@lru_cache(maxsize=4)
def load_catalog(year: int | None = None) -> dict[str, Any]:
    """Load the newest bundled tariff catalog not newer than ``year``."""
    years = available_catalog_years()
    if not years:
        return {"year": None, "sellers": {}, "dsos": {}}
    candidates = [y for y in years if year is None or y <= year] or years[:1]
    path = TARIFF_DIR / f"{candidates[-1]}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _sum_known(*values: float | None) -> float | None:
    """Sum the values, or None if any of them is unknown."""
    if any(v is None for v in values):
        return None
    return round(sum(values), 6)


def catalog_defaults(
    catalog: dict[str, Any],
    seller: str,
    dso: str,
    group: str,
    dynamic: bool,
    phases: int,
    capacity_tier: str | None = None,
) -> dict[str, Any]:
    """Default price settings for a seller/DSO/group combination.

    Unknown values come back as ``None`` so the config flow can ask the user
    instead of silently pricing at zero.
    """
    seller_data = catalog.get("sellers", {}).get(seller, {})
    dso_group = catalog.get("dsos", {}).get(dso, {}).get("groups", {}).get(group, {})
    common = catalog.get("common", {})
    sources: list[str] = []
    verified = True

    if dynamic:
        offer = seller_data.get("dynamic", {})
        energy_peak = energy_offpeak = energy_peak2 = None
        markup = offer.get("markup")
    else:
        offer = seller_data.get("tariffs", {}).get(group, {})
        energy = offer.get("energy", {})
        energy_peak = energy.get("peak")
        energy_offpeak = energy.get("offpeak", energy_peak if group == "G11" else None)
        energy_peak2 = energy.get("peak2")
        markup = None
    if offer:
        sources.extend(offer.get("sources", []))
        verified &= bool(offer.get("verified"))
    else:
        verified = False

    variable = dso_group.get("variable", {})
    # Quality, OZE and cogeneration charges are set nationally; a DSO entry may override them.
    per_kwh = {**common.get("per_kwh", {}), **dso_group.get("per_kwh", {})}
    extras = [per_kwh.get(k) for k in ("quality", "oze", "cogeneration")]
    dist_peak = _sum_known(variable.get("peak"), *extras)
    dist_offpeak = _sum_known(variable.get("offpeak", variable.get("peak") if group == "G11" else None), *extras)
    dist_peak2 = _sum_known(variable.get("peak2"), *extras)
    if dso_group:
        sources.extend(dso_group.get("sources", []))
        verified &= bool(dso_group.get("verified"))
    else:
        verified = False

    fixed_parts = [
        offer.get("fee_monthly"),
        dso_group.get("fixed_monthly", {}).get(str(phases)),
        dso_group.get("subscription_monthly"),
        dso_group.get("transitional_monthly"),
        common.get("capacity_monthly", {}).get(capacity_tier or "none", 0.0) if dso_group else None,
    ]
    # Fixed fees: a partial sum is still a useful starting point for the form.
    known_fixed = [v for v in fixed_parts if v is not None]
    fixed_monthly = round(sum(known_fixed), 2) if known_fixed else None

    windows = dso_group.get("offpeak", [])
    return {
        "offpeak_hours_summer": ",".join(dso_group.get("offpeak_summer", [])),
        "excise": common.get("excise_kwh"),
        "energy_peak": energy_peak,
        "energy_offpeak": energy_offpeak,
        "energy_peak2": energy_peak2,
        "dynamic_markup": markup,
        "dist_peak": dist_peak,
        "dist_offpeak": dist_offpeak,
        "dist_peak2": dist_peak2,
        "offpeak_hours": ",".join(windows),
        "peak2_hours": ",".join(dso_group.get("peak2", [])),
        "peak2_hours_summer": ",".join(dso_group.get("peak2_summer", [])),
        "weekend_offpeak": bool(dso_group.get("weekend_offpeak", False)),
        "holiday_offpeak": bool(dso_group.get("holiday_offpeak", False)),
        "fixed_monthly": fixed_monthly,
        "verified": verified,
        "sources": sources,
    }
