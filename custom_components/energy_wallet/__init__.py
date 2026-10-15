"""Energy Wallet: what grid energy cost you and what your exports earned."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_COMPONENT_LOADED, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import DOMAIN, FRONTEND_URL
from .coordinator import RcePriceCoordinator
from .manager import WalletManager
from .tariff import load_catalog

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SET_RCEM = "set_rcem"
SET_RCEM_SCHEMA = vol.Schema(
    {
        vol.Required("month"): vol.Match(r"^\d{4}-\d{2}$"),
        vol.Optional("price"): vol.Any(None, vol.Coerce(float)),
        vol.Optional("config_entry_id"): cv.string,
    }
)

type EnergyWalletConfigEntry = ConfigEntry[WalletManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await _register_card(hass)

    async def set_rcem(call: ServiceCall) -> None:
        entry_id = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry_id in (None, entry.entry_id):
                entry.runtime_data.set_rcem(call.data["month"], call.data.get("price"))

    hass.services.async_register(DOMAIN, SERVICE_SET_RCEM, set_rcem, schema=SET_RCEM_SCHEMA)
    return True


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and load it on every dashboard."""
    card = Path(__file__).parent / "frontend" / "energy-wallet-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_URL, str(card), True)])
    # The version query busts the browser cache after an update.
    version = (await async_get_integration(hass, DOMAIN)).version
    url = f"{FRONTEND_URL}?v={version}"

    if "frontend" in hass.config.components:
        add_extra_js_url(hass, url)
        return

    # The frontend may finish loading after this integration; register then.
    @callback
    def _frontend_loaded(event: Event) -> None:
        if event.data.get("component") == "frontend":
            add_extra_js_url(hass, url)
            unsub()

    unsub = hass.bus.async_listen(EVENT_COMPONENT_LOADED, _frontend_loaded)


async def async_setup_entry(hass: HomeAssistant, entry: EnergyWalletConfigEntry) -> bool:
    coordinator = RcePriceCoordinator(hass)
    await coordinator.async_refresh()

    manager = WalletManager(hass, entry, coordinator)
    await manager.async_start()
    entry.runtime_data = manager

    _check_catalog_age(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyWalletConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: EnergyWalletConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _check_catalog_age(hass: HomeAssistant) -> None:
    """Raise a repair issue when the bundled tariffs are from an earlier year."""
    year = dt_util.now().year
    catalog_year = load_catalog(year).get("year")
    if catalog_year is not None and catalog_year < year:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "tariffs_outdated",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="tariffs_outdated",
            translation_placeholders={"catalog_year": str(catalog_year), "year": str(year)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "tariffs_outdated")
