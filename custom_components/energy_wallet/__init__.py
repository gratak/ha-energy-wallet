"""Energy Wallet: what grid energy cost you and what your exports earned."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_COMPONENT_LOADED, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall
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

    # Prefer a Lovelace resource: dashboards load resources after the frontend has set up
    # its custom element registry. An extra JS module is imported as soon as the page
    # loads, can run before that, and its element is then missing ("Custom element
    # doesn't exist"); the page also misses it when opened while Home Assistant starts.
    # The extra JS URL stays as the fallback for YAML-mode resources.
    loaded = {"frontend", "lovelace"} & hass.config.components
    if "lovelace" in loaded and await _register_lovelace_resource(hass, url):
        return
    if "frontend" in loaded:
        add_extra_js_url(hass, url)
        return

    # The frontend may finish loading after this integration; register then.
    async def _component_loaded(event: Event) -> None:
        if event.data.get("component") != "frontend":
            return
        unsub()
        if "lovelace" not in hass.config.components or not await _register_lovelace_resource(hass, url):
            add_extra_js_url(hass, url)

    unsub = hass.bus.async_listen(EVENT_COMPONENT_LOADED, _component_loaded)


async def _register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add the card to Lovelace resources, or update its version, in storage mode."""
    data = hass.data.get("lovelace")
    if data is None or getattr(data, "resource_mode", None) != "storage":
        return False  # YAML resources are the user's to manage.
    resources = data.resources
    await resources.async_get_info()  # Loads the stored collection.
    for item in resources.async_items():
        if item["url"].split("?")[0] != FRONTEND_URL:
            continue
        if item["url"] != url:
            await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
        return True
    await resources.async_create_item({"res_type": "module", "url": url})
    return True


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
