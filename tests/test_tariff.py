from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.energy_wallet.tariff import (
    OFFPEAK,
    PEAK,
    PriceConfig,
    catalog_defaults,
    easter_sunday,
    load_catalog,
    parse_windows,
    polish_holidays,
    sell_price,
)

WAW = ZoneInfo("Europe/Warsaw")


def test_easter():
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2025) == date(2025, 4, 20)


def test_holidays_include_moveable_and_christmas_eve():
    days = polish_holidays(2026)
    assert date(2026, 4, 6) in days  # Easter Monday
    assert date(2026, 6, 4) in days  # Corpus Christi
    assert date(2026, 12, 24) in days
    assert date(2024, 12, 24) not in polish_holidays(2024)


def test_parse_windows():
    assert parse_windows("13-15,22-6") == [(780, 900), (1320, 360)]
    assert parse_windows(["13:30-15:00"]) == [(810, 900)]
    assert parse_windows("") == []
    with pytest.raises(ValueError):
        parse_windows("abc")


def g12(**kw):
    return PriceConfig(
        tariff_group=kw.pop("group", "G12"),
        energy_peak=0.6,
        energy_offpeak=0.4,
        dist_peak=0.3,
        dist_offpeak=0.1,
        offpeak_windows=parse_windows("13-15,22-6"),
        vat=0.23,
        **kw,
    )


@pytest.mark.parametrize(
    ("hour", "minute", "zone"),
    [(12, 59, PEAK), (13, 0, OFFPEAK), (14, 59, OFFPEAK), (15, 0, PEAK), (22, 0, OFFPEAK), (5, 59, OFFPEAK), (6, 0, PEAK)],
)
def test_g12_zones(hour, minute, zone):
    assert g12().zone(datetime(2026, 10, 14, hour, minute, tzinfo=WAW)) == zone


def test_g12w_weekend_and_holiday():
    cfg = g12(group="G12w", weekend_offpeak=True, holiday_offpeak=True)
    assert cfg.zone(datetime(2026, 10, 17, 10, tzinfo=WAW)) == OFFPEAK  # Saturday
    assert cfg.zone(datetime(2026, 11, 11, 10, tzinfo=WAW)) == OFFPEAK  # Independence Day
    assert cfg.zone(datetime(2026, 10, 14, 10, tzinfo=WAW)) == PEAK


def test_buy_price_fixed_and_dynamic():
    noon = datetime(2026, 10, 14, 12, tzinfo=WAW)
    assert g12().buy_price(noon) == pytest.approx((0.6 + 0.005 + 0.3) * 1.23)
    dyn = g12(dynamic=True, dynamic_markup=0.05)
    assert dyn.buy_price(noon, rce=400) == pytest.approx((0.4 + 0.05 + 0.005 + 0.3) * 1.23)
    assert dyn.buy_price(noon, rce=-100) == pytest.approx((-0.1 + 0.05 + 0.005 + 0.3) * 1.23)
    assert dyn.buy_price(noon, rce=None) is None


def test_pge_summer_window():
    cfg = g12(offpeak_windows_summer=parse_windows("15-17,22-6"))
    assert cfg.zone(datetime(2026, 7, 14, 14, tzinfo=WAW)) == PEAK
    assert cfg.zone(datetime(2026, 7, 14, 16, tzinfo=WAW)) == OFFPEAK
    assert cfg.zone(datetime(2026, 10, 14, 14, tzinfo=WAW)) == OFFPEAK
    assert cfg.zone(datetime(2026, 10, 14, 16, tzinfo=WAW)) == PEAK


def test_published_gross_matches_model():
    """Tauron G12 day: energy 0.5430 + excise, published gross 0.6740."""
    cfg = PriceConfig(tariff_group="G11", energy_peak=0.5430, vat=0.23)
    assert cfg.buy_price(datetime(2026, 10, 14, 12, tzinfo=WAW)) == pytest.approx(0.6740, abs=1e-4)


def test_g11_ignores_windows():
    cfg = g12(group="G11")
    assert cfg.zone(datetime(2026, 10, 14, 23, tzinfo=WAW)) == PEAK


def test_sell_price():
    assert sell_price(350) == pytest.approx(0.35)
    assert sell_price(-50) == 0
    assert sell_price(-50, clamp_negative=False) == pytest.approx(-0.05)
    assert sell_price(300, multiplier=1.23) == pytest.approx(0.369)
    assert sell_price(None) is None


def test_catalog_defaults_tauron_g11():
    d = catalog_defaults(load_catalog(2026), "tauron", "tauron_dystrybucja", "G11", False, 3, "1200_2800")
    assert d["energy_peak"] == pytest.approx(0.497)
    assert d["energy_offpeak"] == pytest.approx(0.497)
    assert d["dist_peak"] == pytest.approx(0.2464 + 0.0332 + 0.0073 + 0.0030)
    # trade 0 + network 10.86 + subscription 4.56 + transitional 0 + capacity 17.18
    assert d["fixed_monthly"] == pytest.approx(32.60)
    assert d["excise"] == pytest.approx(0.005)
    assert d["verified"] is False
    assert d["sources"]


def test_catalog_defaults_dynamic_and_g12w_zones():
    d = catalog_defaults(load_catalog(2026), "tauron", "tauron_dystrybucja", "G12w", True, 1)
    assert d["energy_peak"] is None
    assert d["dynamic_markup"] == pytest.approx(0.110)
    assert d["offpeak_hours"] == "13-15,22-6"
    assert d["offpeak_hours_summer"] == ""
    assert d["weekend_offpeak"] is True


def test_catalog_pge_summer_and_unknowns_stay_none():
    d = catalog_defaults(load_catalog(2026), "pge", "pge_dystrybucja", "G12", False, 3)
    assert d["offpeak_hours_summer"] == "15-17,22-6"
    assert d["energy_offpeak"] == pytest.approx(0.3718)
    stoen = catalog_defaults(load_catalog(2026), "eon", "stoen_operator", "G12w", False, 3)
    assert stoen["dist_peak"] is None
    assert stoen["energy_peak"] is None


def test_catalog_net_gross_consistency():
    """Every DSO variable rate in the catalog is a plain net number in a sane range."""
    for dso in load_catalog(2026)["dsos"].values():
        for group in dso["groups"].values():
            for value in group["variable"].values():
                assert value is None or 0 < value < 1


def test_catalog_falls_back_to_oldest_for_past_years():
    assert load_catalog(2020)["year"] == 2026
