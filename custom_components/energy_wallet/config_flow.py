"""Config and options flow for Energy Wallet."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    CAPACITY_TIERS,
    CONF_CAPACITY_TIER,
    CONF_CLAMP_NEGATIVE,
    CONF_DEPOSIT_MULTIPLIER,
    CONF_DIST_OFFPEAK,
    CONF_DIST_PEAK,
    CONF_DIST_PEAK2,
    CONF_DSO,
    CONF_DYNAMIC,
    CONF_DYNAMIC_MARKUP,
    CONF_ENERGY_OFFPEAK,
    CONF_ENERGY_PEAK,
    CONF_ENERGY_PEAK2,
    CONF_EXCISE,
    CONF_EXPORT_ENTITY,
    CONF_FIXED_MONTHLY,
    CONF_HOLIDAY_OFFPEAK,
    CONF_IMPORT_ENTITY,
    CONF_OFFPEAK_HOURS,
    CONF_OFFPEAK_HOURS_SUMMER,
    CONF_PEAK2_HOURS,
    CONF_PEAK2_HOURS_SUMMER,
    CONF_PHASES,
    CONF_SALE_MODE,
    CONF_SELLER,
    CONF_TARIFF_GROUP,
    CONF_VAT,
    CONF_WEEKEND_OFFPEAK,
    DEFAULT_CAPACITY_TIER,
    DEFAULT_DEPOSIT_MULTIPLIER,
    DEFAULT_EXCISE,
    DEFAULT_VAT,
    DOMAIN,
    OTHER,
    SALE_MODE_RCE,
    SALE_MODES,
    TARIFF_GROUPS,
    THREE_ZONE_GROUPS,
    TWO_ZONE_GROUPS,
)
from .detect import grid_entities_from_energy_prefs, rank_candidates
from .tariff import catalog_defaults, load_catalog, parse_windows

TARIFF_KEYS = (CONF_SELLER, CONF_TARIFF_GROUP, CONF_DYNAMIC, CONF_DSO, CONF_PHASES, CONF_CAPACITY_TIER)

ENERGY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="energy")
)
PRICE = selector.NumberSelector(
    selector.NumberSelectorConfig(min=-10, max=10, step="any", mode="box", unit_of_measurement="PLN/kWh")
)
MONTHLY = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0, max=1000, step=0.01, mode="box", unit_of_measurement="PLN")
)
RATIO = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=2, step=0.01, mode="box"))


async def detect_grid_entities(hass: HomeAssistant) -> tuple[str | None, str | None]:
    """Prefer the Energy dashboard's grid sensors, then guess from names."""
    imp = exp = None
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
        imp, exp = grid_entities_from_energy_prefs(manager.data)
    except Exception:  # noqa: BLE001 - energy may be missing or its format may change
        pass
    if imp is None or exp is None:
        ranked_imp, ranked_exp = rank_candidates(
            (state.entity_id, state.attributes) for state in hass.states.async_all("sensor")
        )
        imp = imp or (ranked_imp[0].entity_id if ranked_imp else None)
        exp = exp or (ranked_exp[0].entity_id if ranked_exp else None)
    return imp, exp


def _select(options: list[str], key: str | None) -> selector.SelectSelector:
    config: dict[str, Any] = {"options": options, "mode": "dropdown"}
    if key:
        config["translation_key"] = key
    return selector.SelectSelector(selector.SelectSelectorConfig(**config))


def entities_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_IMPORT_ENTITY, **_default(defaults.get(CONF_IMPORT_ENTITY))): ENERGY_SENSOR,
            vol.Required(CONF_EXPORT_ENTITY, **_default(defaults.get(CONF_EXPORT_ENTITY))): ENERGY_SENSOR,
        }
    )


def tariff_schema(defaults: dict[str, Any]) -> vol.Schema:
    catalog = load_catalog(dt_util.now().year)
    sellers = [*catalog.get("sellers", {}), OTHER]
    dsos = [*catalog.get("dsos", {}), OTHER]
    return vol.Schema(
        {
            vol.Required(CONF_SELLER, default=defaults.get(CONF_SELLER, sellers[0])): _select(sellers, "seller"),
            vol.Required(CONF_TARIFF_GROUP, default=defaults.get(CONF_TARIFF_GROUP, "G11")): _select(TARIFF_GROUPS, None),
            vol.Required(CONF_DYNAMIC, default=defaults.get(CONF_DYNAMIC, False)): selector.BooleanSelector(),
            vol.Required(CONF_DSO, default=defaults.get(CONF_DSO, dsos[0])): _select(dsos, "dso"),
            vol.Required(CONF_PHASES, default=str(defaults.get(CONF_PHASES, "3"))): _select(["1", "3"], "phases"),
            vol.Required(CONF_CAPACITY_TIER, default=defaults.get(CONF_CAPACITY_TIER, DEFAULT_CAPACITY_TIER)): _select(
                CAPACITY_TIERS, "capacity_tier"
            ),
            vol.Required(CONF_SALE_MODE, default=defaults.get(CONF_SALE_MODE, SALE_MODE_RCE)): _select(
                SALE_MODES, "sale_mode"
            ),
        }
    )


def _default(value: Any) -> dict[str, Any]:
    """Only pass a default when one is known, so unknown prices must be typed in."""
    return {} if value is None or value == "" else {"default": value}


def prices_schema(conf: dict[str, Any], defaults: dict[str, Any]) -> vol.Schema:
    two_zone = conf.get(CONF_TARIFF_GROUP) in TWO_ZONE_GROUPS
    three_zone = conf.get(CONF_TARIFF_GROUP) in THREE_ZONE_GROUPS
    fields: dict[Any, Any] = {}
    if conf.get(CONF_DYNAMIC):
        fields[vol.Required(CONF_DYNAMIC_MARKUP, **_default(defaults.get(CONF_DYNAMIC_MARKUP)))] = PRICE
    else:
        fields[vol.Required(CONF_ENERGY_PEAK, **_default(defaults.get(CONF_ENERGY_PEAK)))] = PRICE
        if three_zone:
            fields[vol.Required(CONF_ENERGY_PEAK2, **_default(defaults.get(CONF_ENERGY_PEAK2)))] = PRICE
        if two_zone:
            fields[vol.Required(CONF_ENERGY_OFFPEAK, **_default(defaults.get(CONF_ENERGY_OFFPEAK)))] = PRICE
    fields[vol.Required(CONF_DIST_PEAK, **_default(defaults.get(CONF_DIST_PEAK)))] = PRICE
    if three_zone:
        fields[vol.Required(CONF_DIST_PEAK2, **_default(defaults.get(CONF_DIST_PEAK2)))] = PRICE
    if two_zone:
        fields[vol.Required(CONF_DIST_OFFPEAK, **_default(defaults.get(CONF_DIST_OFFPEAK)))] = PRICE
        fields[vol.Required(CONF_OFFPEAK_HOURS, **_default(defaults.get(CONF_OFFPEAK_HOURS)))] = (
            selector.TextSelector()
        )
        fields[vol.Optional(CONF_OFFPEAK_HOURS_SUMMER, **_default(defaults.get(CONF_OFFPEAK_HOURS_SUMMER)))] = (
            selector.TextSelector()
        )
        if three_zone:
            fields[vol.Required(CONF_PEAK2_HOURS, **_default(defaults.get(CONF_PEAK2_HOURS)))] = selector.TextSelector()
            fields[vol.Optional(CONF_PEAK2_HOURS_SUMMER, **_default(defaults.get(CONF_PEAK2_HOURS_SUMMER)))] = (
                selector.TextSelector()
            )
        fields[vol.Required(CONF_WEEKEND_OFFPEAK, default=defaults.get(CONF_WEEKEND_OFFPEAK, False))] = (
            selector.BooleanSelector()
        )
        fields[vol.Required(CONF_HOLIDAY_OFFPEAK, default=defaults.get(CONF_HOLIDAY_OFFPEAK, False))] = (
            selector.BooleanSelector()
        )
    fields[vol.Required(CONF_FIXED_MONTHLY, **_default(defaults.get(CONF_FIXED_MONTHLY)))] = MONTHLY
    fields[vol.Required(CONF_EXCISE, default=defaults.get(CONF_EXCISE) or DEFAULT_EXCISE)] = PRICE
    fields[vol.Required(CONF_VAT, default=defaults.get(CONF_VAT, DEFAULT_VAT))] = RATIO
    fields[vol.Required(CONF_DEPOSIT_MULTIPLIER, default=defaults.get(CONF_DEPOSIT_MULTIPLIER, DEFAULT_DEPOSIT_MULTIPLIER))] = RATIO
    fields[vol.Required(CONF_CLAMP_NEGATIVE, default=defaults.get(CONF_CLAMP_NEGATIVE, True))] = (
        selector.BooleanSelector()
    )
    return vol.Schema(fields)


def defaults_from_catalog(conf: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Catalog defaults plus description placeholders about their provenance."""
    if conf.get(CONF_SELLER) == OTHER and conf.get(CONF_DSO) == OTHER:
        return {}, {"verified": "—", "sources": "—"}
    defaults = catalog_defaults(
        load_catalog(dt_util.now().year),
        conf.get(CONF_SELLER, OTHER),
        conf.get(CONF_DSO, OTHER),
        conf.get(CONF_TARIFF_GROUP, "G11"),
        bool(conf.get(CONF_DYNAMIC)),
        int(conf.get(CONF_PHASES, 3)),
        conf.get(CONF_CAPACITY_TIER, DEFAULT_CAPACITY_TIER),
    )
    placeholders = {
        "verified": "✅" if defaults.pop("verified") else "⚠️",
        "sources": "\n".join(f"- {s}" for s in defaults.pop("sources")) or "—",
    }
    return defaults, placeholders


def validate_prices(user_input: dict[str, Any]) -> dict[str, str]:
    for key in (CONF_OFFPEAK_HOURS, CONF_OFFPEAK_HOURS_SUMMER, CONF_PEAK2_HOURS, CONF_PEAK2_HOURS_SUMMER):
        try:
            parse_windows(user_input.get(key))
        except ValueError:
            return {key: "invalid_hours"}
    return {}


class EnergyWalletConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a wallet for one grid connection."""

    VERSION = 1

    def __init__(self) -> None:
        self._conf: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_IMPORT_ENTITY] == user_input[CONF_EXPORT_ENTITY]:
                errors["base"] = "same_entity"
            else:
                await self.async_set_unique_id(user_input[CONF_IMPORT_ENTITY])
                self._abort_if_unique_id_configured()
                self._conf.update(user_input)
                return await self.async_step_tariff()

        imp, exp = await detect_grid_entities(self.hass)
        defaults = user_input or {CONF_IMPORT_ENTITY: imp, CONF_EXPORT_ENTITY: exp}
        return self.async_show_form(step_id="user", data_schema=entities_schema(defaults), errors=errors)

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._conf.update(user_input)
            return await self.async_step_prices()
        return self.async_show_form(step_id="tariff", data_schema=tariff_schema(self._conf))

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        defaults, placeholders = defaults_from_catalog(self._conf)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_prices(user_input)
            if not errors:
                self._conf.update(user_input)
                return self.async_create_entry(title="Energy Wallet", data=self._conf)
            defaults.update(user_input)
        return self.async_show_form(
            step_id="prices",
            data_schema=prices_schema(self._conf, defaults),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EnergyWalletOptionsFlow()


class EnergyWalletOptionsFlow(OptionsFlow):
    """Change entities, tariff and prices after setup."""

    def __init__(self) -> None:
        self._conf: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            tariff_changed = any(str(user_input[k]) != str(current.get(k)) for k in TARIFF_KEYS)
            self._conf = {**current, **user_input}
            if tariff_changed:
                # Prices from the previous tariff would be misleading.
                for key in (CONF_ENERGY_PEAK, CONF_ENERGY_OFFPEAK, CONF_ENERGY_PEAK2, CONF_DYNAMIC_MARKUP,
                            CONF_DIST_PEAK, CONF_DIST_OFFPEAK, CONF_DIST_PEAK2, CONF_OFFPEAK_HOURS,
                            CONF_OFFPEAK_HOURS_SUMMER, CONF_PEAK2_HOURS, CONF_PEAK2_HOURS_SUMMER,
                            CONF_WEEKEND_OFFPEAK, CONF_HOLIDAY_OFFPEAK, CONF_FIXED_MONTHLY):
                    self._conf.pop(key, None)
            return await self.async_step_prices()
        schema = vol.Schema({**entities_schema(current).schema, **tariff_schema(current).schema})
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        defaults, placeholders = defaults_from_catalog(self._conf)
        defaults.update({k: v for k, v in self._conf.items() if v is not None})
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_prices(user_input)
            if not errors:
                data = {**self._conf, **user_input}
                for key in (CONF_OFFPEAK_HOURS_SUMMER, CONF_PEAK2_HOURS_SUMMER):
                    if key not in user_input:
                        data.pop(key, None)
                return self.async_create_entry(data=data)
            defaults.update(user_input)
        return self.async_show_form(
            step_id="prices",
            data_schema=prices_schema(self._conf, defaults),
            errors=errors,
            description_placeholders=placeholders,
        )
