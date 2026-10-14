"""Sensors exposing purchase cost, sale revenue and balance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, PERIODS, SIGNAL_UPDATE
from .manager import WalletManager

CURRENCY = "PLN"
PRICE_UNIT = "PLN/kWh"


@dataclass(frozen=True, kw_only=True)
class WalletSensorDescription(SensorEntityDescription):
    value_fn: Callable[[WalletManager], float | None]
    attrs_fn: Callable[[WalletManager], dict[str, Any]] | None = None
    role: str
    period: str | None = None


def _period_descriptions(period: str) -> list[WalletSensorDescription]:
    def summary(manager: WalletManager) -> dict[str, float]:
        return manager.summary(period)

    money = {
        "device_class": SensorDeviceClass.MONETARY,
        "native_unit_of_measurement": CURRENCY,
        "state_class": SensorStateClass.TOTAL,
        "suggested_display_precision": 2,
    }
    return [
        WalletSensorDescription(
            key=f"purchase_cost_{period}",
            translation_key=f"purchase_cost_{period}",
            role="purchase_cost",
            period=period,
            value_fn=lambda m: round(summary(m)["cost"], 4),
            attrs_fn=lambda m: {
                "energy_kwh": round(summary(m)["imp"], 3),
                "unpriced_kwh": round(summary(m)["imp_unpriced"], 3),
            },
            **money,
        ),
        WalletSensorDescription(
            key=f"sale_revenue_{period}",
            translation_key=f"sale_revenue_{period}",
            role="sale_revenue",
            period=period,
            value_fn=lambda m: round(summary(m)["rev"], 4),
            attrs_fn=lambda m: {
                "energy_kwh": round(summary(m)["exp"], 3),
                "unpriced_kwh": round(summary(m)["exp_unpriced"], 3),
                "sale_mode": m.sale_mode,
            },
            **money,
        ),
        WalletSensorDescription(
            key=f"balance_{period}",
            translation_key=f"balance_{period}",
            role="balance",
            period=period,
            value_fn=lambda m: round(summary(m)["balance"], 4),
            attrs_fn=lambda m: {
                "fixed_monthly_gross": round(m.prices.fixed_monthly_gross, 2),
            },
            **money,
        ),
    ]


DESCRIPTIONS: list[WalletSensorDescription] = [
    *(d for period in PERIODS for d in _period_descriptions(period)),
    WalletSensorDescription(
        key="purchase_cost_total",
        translation_key="purchase_cost_total",
        role="purchase_cost",
        period="total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda m: round(m.lifetime()["cost"], 4),
    ),
    WalletSensorDescription(
        key="sale_revenue_total",
        translation_key="sale_revenue_total",
        role="sale_revenue",
        period="total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda m: round(m.lifetime()["rev"], 4),
    ),
    WalletSensorDescription(
        key="buy_price",
        translation_key="buy_price",
        role="buy_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda m: m.current_buy_price(),
        attrs_fn=lambda m: {"forecast": m.price_forecast()},
    ),
    WalletSensorDescription(
        key="sell_price",
        translation_key="sell_price",
        role="sell_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda m: m.current_sell_price(),
        attrs_fn=lambda m: {"sale_mode": m.sale_mode},
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager: WalletManager = entry.runtime_data
    async_add_entities(WalletSensor(manager, entry, d) for d in DESCRIPTIONS)


class WalletSensor(SensorEntity):
    """A value computed by the WalletManager."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # The forecast attribute is large and changes constantly.
    _unrecorded_attributes = frozenset({"forecast"})
    entity_description: WalletSensorDescription

    def __init__(self, manager: WalletManager, entry: ConfigEntry, description: WalletSensorDescription) -> None:
        self.entity_description = description
        self._manager = manager
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Energy Wallet",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE.format(self._entry.entry_id), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self._manager)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        desc = self.entity_description
        attrs: dict[str, Any] = {
            "energy_wallet": {"entry_id": self._entry.entry_id, "role": desc.role, "period": desc.period}
        }
        if desc.attrs_fn:
            attrs.update(desc.attrs_fn(self._manager))
        return attrs
