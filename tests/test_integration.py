"""End-to-end tests inside a real Home Assistant instance."""

from datetime import datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.energy_wallet.const import DOMAIN
from custom_components.energy_wallet.pse import PSE_RCE_URL

IMP = "sensor.deye_inverter_totalenergybuy"
EXP = "sensor.deye_inverter_totalenergysell"
ATTRS = {"unit_of_measurement": "kWh", "state_class": "total", "device_class": "energy"}

BASE = {
    "import_entity": IMP,
    "export_entity": EXP,
    "seller": "tauron",
    "tariff_group": "G11",
    "dynamic": False,
    "dso": "tauron_dystrybucja",
    "phases": "3",
    "sale_mode": "rce",
    "energy_peak": 0.5,
    "dist_peak": 0.3,
    "fixed_monthly": 20.0,
    "vat": 0.23,
    "deposit_multiplier": 1.0,
    "clamp_negative_rce": True,
}


def rce_payload(day: str, price: float) -> dict:
    values = []
    start = datetime.fromisoformat(f"{day}T00:00")
    for i in range(96):
        end = start + timedelta(minutes=15 * (i + 1))
        dtime = f"{day} 24:00:00" if i == 95 else end.strftime("%Y-%m-%d %H:%M:%S")
        values.append({"business_date": day, "dtime": dtime, "period": "", "rce_pln": price})
    return {"value": values}


def value(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    return hass.states.get(entity_id)


@pytest.fixture
async def setup_states(hass: HomeAssistant):
    await hass.config.async_set_time_zone("Europe/Warsaw")
    hass.states.async_set(IMP, "16.5", {**ATTRS, "friendly_name": "Deye Inverter TotalEnergyBuy"})
    hass.states.async_set(EXP, "0.2", {**ATTRS, "friendly_name": "Deye Inverter TotalEnergySell"})
    hass.states.async_set("sensor.deye_daily_energy_purchased", "0.3", {**ATTRS, "friendly_name": "Daily Energy Purchased"})


async def test_config_flow_detects_and_creates(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    schema = {str(k): k.default() for k in result["data_schema"].schema if callable(getattr(k, "default", None))}
    assert schema["import_entity"] == IMP
    assert schema["export_entity"] == EXP

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"import_entity": IMP, "export_entity": EXP})
    assert result["step_id"] == "tariff"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"seller": "tauron", "tariff_group": "G12w", "dynamic": False, "dso": "tauron_dystrybucja", "phases": "3", "capacity_tier": "gt2800", "sale_mode": "rce"},
    )
    assert result["step_id"] == "prices"
    keys = {str(k) for k in result["data_schema"].schema}
    assert {"energy_offpeak", "dist_offpeak", "offpeak_hours", "weekend_offpeak"} <= keys
    assert "⚠️" in result["description_placeholders"]["verified"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"energy_peak": 0.6, "energy_offpeak": 0.4, "dist_peak": 0.3, "dist_offpeak": 0.1, "offpeak_hours": "nonsense",
         "weekend_offpeak": True, "holiday_offpeak": True, "fixed_monthly": 25, "vat": 0.23,
         "deposit_multiplier": 1.0, "clamp_negative_rce": True},
    )
    assert result["errors"] == {"offpeak_hours": "invalid_hours"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"energy_peak": 0.6, "energy_offpeak": 0.4, "dist_peak": 0.3, "dist_offpeak": 0.1, "offpeak_hours": "13-15,22-6",
         "weekend_offpeak": True, "holiday_offpeak": True, "fixed_monthly": 25, "vat": 0.23,
         "deposit_multiplier": 1.0, "clamp_negative_rce": True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["tariff_group"] == "G12w"
    await hass.async_block_till_done()


async def test_config_flow_g13_asks_for_three_zones(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"import_entity": IMP, "export_entity": EXP})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"seller": "tauron", "tariff_group": "G13", "dynamic": False, "dso": "tauron_dystrybucja", "phases": "3", "capacity_tier": "1200_2800", "sale_mode": "rce"},
    )
    assert result["step_id"] == "prices"
    schema = {str(k): k for k in result["data_schema"].schema}
    assert {"energy_peak2", "dist_peak2", "peak2_hours", "peak2_hours_summer", "energy_offpeak", "dist_offpeak"} <= set(schema)
    assert schema["peak2_hours"].default() == "16-21"
    assert schema["peak2_hours_summer"].default() == "19-22"

    prices = {"energy_peak": 0.5, "energy_peak2": 0.8, "energy_offpeak": 0.4, "dist_peak": 0.26, "dist_peak2": 0.43,
              "dist_offpeak": 0.08, "offpeak_hours": "13-16,21-7", "offpeak_hours_summer": "13-19,22-7",
              "peak2_hours": "afternoon", "weekend_offpeak": True, "holiday_offpeak": True, "fixed_monthly": 32.6,
              "vat": 0.23, "deposit_multiplier": 1.0, "clamp_negative_rce": True}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], prices)
    assert result["errors"] == {"peak2_hours": "invalid_hours"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**prices, "peak2_hours": "16-21", "peak2_hours_summer": "19-22"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["tariff_group"] == "G13"
    assert result["data"]["energy_peak2"] == 0.8
    await hass.async_block_till_done()


async def test_wallet_counts_money(hass: HomeAssistant, setup_states, aioclient_mock, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-14T10:02:00+00:00")
    today = dt_util.now().date().isoformat()
    aioclient_mock.get(PSE_RCE_URL, json=rce_payload(today, 400.0))

    entry = MockConfigEntry(domain=DOMAIN, data=BASE, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Bought 1 kWh and sold 2 kWh a few minutes later.
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set(IMP, "17.5", ATTRS)
    hass.states.async_set(EXP, "2.2", ATTRS)
    await hass.async_block_till_done()

    cost = value(hass, entry, "purchase_cost_today")
    revenue = value(hass, entry, "sale_revenue_today")
    balance = value(hass, entry, "balance_today")
    assert float(cost.state) == pytest.approx((0.5 + 0.005 + 0.3) * 1.23, abs=1e-4)
    assert cost.attributes["energy_kwh"] == pytest.approx(1.0)
    assert float(revenue.state) == pytest.approx(2 * 0.4)
    assert float(balance.state) == pytest.approx(0.8 - 0.99015, abs=1e-4)
    assert balance.attributes["fixed_monthly_gross"] == pytest.approx(24.6)
    assert cost.attributes["energy_wallet"]["role"] == "purchase_cost"
    assert float(value(hass, entry, "sell_price").state) == pytest.approx(0.4)
    assert float(value(hass, entry, "buy_price").state) == pytest.approx(0.99015, abs=1e-4)

    # A glitch to zero and back must not double count.
    hass.states.async_set(IMP, "0", ATTRS)
    await hass.async_block_till_done()
    hass.states.async_set(IMP, "17.5", ATTRS)
    await hass.async_block_till_done()
    assert value(hass, entry, "purchase_cost_today").attributes["energy_kwh"] == pytest.approx(1.0)

    # Survives a reload.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(value(hass, entry, "sale_revenue_month").state) == pytest.approx(0.8)

    # Service override switches RCEm estimate.
    hass.config_entries.async_update_entry(entry, options={**BASE, "sale_mode": "rcem"})
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "set_rcem", {"month": today[:7], "price": 0.3}, blocking=True)
    await hass.async_block_till_done()
    assert float(value(hass, entry, "sale_revenue_month").state) == pytest.approx(0.6)

    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_dynamic_waits_for_price(hass: HomeAssistant, setup_states, aioclient_mock, freezer) -> None:
    freezer.move_to("2026-10-14T10:02:00+00:00")
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    entry = MockConfigEntry(domain=DOMAIN, data={**BASE, "dynamic": True, "dynamic_markup": 0.05}, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    freezer.tick(timedelta(minutes=5))
    hass.states.async_set(IMP, "18.5", ATTRS)
    await hass.async_block_till_done()
    cost = value(hass, entry, "purchase_cost_today")
    assert float(cost.state) == 0
    assert cost.attributes["energy_kwh"] == pytest.approx(2.0)
    assert value(hass, entry, "buy_price").state == "unknown"


async def test_options_flow_resets_prices_on_tariff_change(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    entry = MockConfigEntry(domain=DOMAIN, data=BASE, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"import_entity": IMP, "export_entity": EXP, "seller": "tauron", "tariff_group": "G11", "dynamic": True,
         "dso": "tauron_dystrybucja", "phases": "3", "capacity_tier": "none", "sale_mode": "rcem"},
    )
    assert result["step_id"] == "prices"
    keys = {str(k): k for k in result["data_schema"].schema}
    assert "dynamic_markup" in keys and "energy_peak" not in keys
    # After a tariff change the catalog value replaces the old 0.3 typed for the previous tariff.
    assert keys["dist_peak"].default() == pytest.approx(0.2464 + 0.0332 + 0.0073 + 0.0030)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"dynamic_markup": 0.06, "dist_peak": 0.31, "fixed_monthly": 22, "vat": 0.23,
         "deposit_multiplier": 1.0, "clamp_negative_rce": True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["dynamic"] is True
    assert entry.runtime_data.prices.dynamic_markup == pytest.approx(0.06)


async def test_options_flow_g12_to_g13_takes_zone_flags_from_catalog(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    g12 = {**BASE, "tariff_group": "G12", "energy_offpeak": 0.4, "dist_offpeak": 0.1, "offpeak_hours": "13-15,22-6",
           "weekend_offpeak": False, "holiday_offpeak": False}
    entry = MockConfigEntry(domain=DOMAIN, data=g12, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"import_entity": IMP, "export_entity": EXP, "seller": "tauron", "tariff_group": "G13", "dynamic": False,
         "dso": "tauron_dystrybucja", "phases": "3", "capacity_tier": "1200_2800", "sale_mode": "rce"},
    )
    keys = {str(k): k for k in result["data_schema"].schema}
    # The G12 entry had weekends on peak; G13 counts weekends and holidays as off-peak.
    assert keys["weekend_offpeak"].default() is True
    assert keys["holiday_offpeak"].default() is True
    assert keys["offpeak_hours"].default() == "13-16,21-7"
    assert keys["peak2_hours"].default() == "16-21"


async def test_card_registered_when_frontend_loads_later(hass: HomeAssistant, setup_states, aioclient_mock) -> None:
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
    from homeassistant.const import EVENT_COMPONENT_LOADED

    aioclient_mock.get(PSE_RCE_URL, json={"value": []})
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])
    entry = MockConfigEntry(domain=DOMAIN, data=BASE, title="Energy Wallet")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.data[DATA_EXTRA_MODULE_URL].urls

    hass.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "frontend"})
    await hass.async_block_till_done()
    assert hass.data[DATA_EXTRA_MODULE_URL].urls == {"/energy_wallet/energy-wallet-card.js?v=0.1.2"}
