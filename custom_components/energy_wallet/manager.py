"""Glue between Home Assistant state changes and the Wallet."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLAMP_NEGATIVE,
    CONF_DEPOSIT_MULTIPLIER,
    CONF_DIST_OFFPEAK,
    CONF_DIST_PEAK,
    CONF_DIST_PEAK2,
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
    CONF_MAX_POWER_KW,
    CONF_OFFPEAK_HOURS,
    CONF_OFFPEAK_HOURS_SUMMER,
    CONF_PEAK2_HOURS,
    CONF_PEAK2_HOURS_SUMMER,
    CONF_SALE_MODE,
    CONF_TARIFF_GROUP,
    CONF_VAT,
    CONF_WEEKEND_OFFPEAK,
    DEFAULT_DEPOSIT_MULTIPLIER,
    DEFAULT_EXCISE,
    DEFAULT_MAX_POWER_KW,
    DEFAULT_VAT,
    DOMAIN,
    PERIOD_MONTH,
    PERIOD_TODAY,
    PERIOD_YEAR,
    SALE_MODE_RCE,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
)
from .coordinator import RcePriceCoordinator
from .detect import ENERGY_UNITS
from .tariff import PriceConfig, parse_windows, sell_price
from .wallet import EXPORT, IMPORT, Wallet

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 60


def build_price_config(conf: dict[str, Any]) -> PriceConfig:
    return PriceConfig(
        tariff_group=conf.get(CONF_TARIFF_GROUP, "G11"),
        dynamic=bool(conf.get(CONF_DYNAMIC, False)),
        energy_peak=float(conf.get(CONF_ENERGY_PEAK) or 0),
        energy_offpeak=float(conf.get(CONF_ENERGY_OFFPEAK) or conf.get(CONF_ENERGY_PEAK) or 0),
        dynamic_markup=float(conf.get(CONF_DYNAMIC_MARKUP) or 0),
        dist_peak=float(conf.get(CONF_DIST_PEAK) or 0),
        dist_offpeak=float(conf.get(CONF_DIST_OFFPEAK) or conf.get(CONF_DIST_PEAK) or 0),
        energy_peak2=float(conf.get(CONF_ENERGY_PEAK2) or conf.get(CONF_ENERGY_PEAK) or 0),
        dist_peak2=float(conf.get(CONF_DIST_PEAK2) or conf.get(CONF_DIST_PEAK) or 0),
        offpeak_windows=parse_windows(conf.get(CONF_OFFPEAK_HOURS, "")),
        offpeak_windows_summer=parse_windows(conf.get(CONF_OFFPEAK_HOURS_SUMMER, "")) or None,
        peak2_windows=parse_windows(conf.get(CONF_PEAK2_HOURS, "")),
        peak2_windows_summer=parse_windows(conf.get(CONF_PEAK2_HOURS_SUMMER, "")) or None,
        weekend_offpeak=bool(conf.get(CONF_WEEKEND_OFFPEAK, False)),
        holiday_offpeak=bool(conf.get(CONF_HOLIDAY_OFFPEAK, False)),
        fixed_monthly=float(conf.get(CONF_FIXED_MONTHLY) or 0),
        excise=float(conf.get(CONF_EXCISE, DEFAULT_EXCISE)),
        vat=float(conf.get(CONF_VAT, DEFAULT_VAT)),
    )


class WalletManager:
    """Owns the Wallet for one config entry and keeps it fed."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: RcePriceCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.conf: dict[str, Any] = {**entry.data, **entry.options}
        self.prices = build_price_config(self.conf)
        self.wallet = Wallet()
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._unsubs: list[Callable[[], None]] = []

    @property
    def sale_mode(self) -> str:
        return self.conf.get(CONF_SALE_MODE, SALE_MODE_RCE)

    @property
    def multiplier(self) -> float:
        return float(self.conf.get(CONF_DEPOSIT_MULTIPLIER, DEFAULT_DEPOSIT_MULTIPLIER))

    @property
    def clamp_negative(self) -> bool:
        return bool(self.conf.get(CONF_CLAMP_NEGATIVE, True))

    async def async_start(self) -> None:
        self.wallet = Wallet(await self._store.async_load())
        entities = {IMPORT: self.conf[CONF_IMPORT_ENTITY], EXPORT: self.conf[CONF_EXPORT_ENTITY]}
        for kind, entity_id in entities.items():
            if state := self.hass.states.get(entity_id):
                self._ingest(kind, state)
            self._unsubs.append(
                async_track_state_change_event(self.hass, [entity_id], self._make_listener(kind))
            )
        self._unsubs.append(self.coordinator.async_add_listener(self._on_prices))
        self._unsubs.append(
            async_track_time_change(self.hass, self._on_tick, minute=[0, 15, 30, 45], second=2)
        )
        self._on_prices()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self.wallet.as_dict())

    def _make_listener(self, kind: str) -> Callable[[Event[EventStateChangedData]], None]:
        @callback
        def _listener(event: Event[EventStateChangedData]) -> None:
            if (state := event.data["new_state"]) is not None:
                self._ingest(kind, state)

        return _listener

    def _ingest(self, kind: str, state: State) -> None:
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return
        try:
            value = float(state.state)
        except ValueError:
            return
        factor = ENERGY_UNITS.get(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "kWh"))
        if factor is None:
            _LOGGER.warning("%s has unsupported unit, expected Wh/kWh/MWh", state.entity_id)
            return
        now = dt_util.utcnow()
        delta, rejected = self.wallet.ingest_reading(
            kind,
            value * factor,
            now,
            resets_allowed=state.attributes.get("state_class") == "total_increasing",
            max_power_kw=float(self.conf.get(CONF_MAX_POWER_KW, DEFAULT_MAX_POWER_KW)),
        )
        if rejected:
            _LOGGER.warning("Ignoring implausible jump of %s to %s", state.entity_id, state.state)
        if delta > 0:
            self.wallet.add_energy(kind, delta, now, dt_util.as_local(now).date().isoformat())
            self._settle()

    def _cost_fn(self, start: datetime) -> float | None:
        rce = self.coordinator.table.at(start) if self.prices.dynamic else None
        return self.prices.buy_price(dt_util.as_local(start), rce)

    def _rev_fn(self, start: datetime) -> float | None:
        # Stored without the deposit multiplier; summaries apply it.
        return sell_price(self.coordinator.table.at(start), 1.0, self.clamp_negative)

    def _settle(self) -> None:
        self.wallet.settle(self._cost_fn, self._rev_fn, dt_util.utcnow())
        self._store.async_delay_save(self.wallet.as_dict, SAVE_DELAY)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    @callback
    def _on_prices(self) -> None:
        tz = dt_util.get_default_time_zone()
        today = dt_util.now().date()
        for offset in range(-6, 2):
            day = today + timedelta(days=offset)
            if (mean := self.coordinator.table.day_mean(day, tz)) is not None:
                self.wallet.record_rce_day(day.isoformat(), mean)
        self.wallet.prune(today.isoformat())
        self._settle()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._settle()

    def set_rcem(self, month: str, price: float | None) -> None:
        if price is None:
            self.wallet.rcem_override.pop(month, None)
        else:
            self.wallet.rcem_override[month] = price
        self._settle()

    # Values for entities

    def period_prefix(self, period: str) -> str:
        today = dt_util.now().date().isoformat()
        return {PERIOD_TODAY: today, PERIOD_MONTH: today[:7], PERIOD_YEAR: today[:4]}[period]

    def summary(self, period: str) -> dict[str, float]:
        return self.wallet.summary(self.period_prefix(period), self.sale_mode, self.multiplier)

    def lifetime(self) -> dict[str, float]:
        totals = self.wallet.totals
        revenue = (
            totals.get("rev", 0.0) * self.multiplier
            if self.sale_mode == SALE_MODE_RCE
            else self.wallet.summary("", self.sale_mode, self.multiplier)["rev"]
        )
        return {"cost": totals.get("cost", 0.0), "rev": revenue}

    def current_buy_price(self) -> float | None:
        now = dt_util.utcnow()
        return self._cost_fn(now)

    def current_sell_price(self) -> float | None:
        if self.sale_mode == SALE_MODE_RCE:
            return sell_price(self.coordinator.table.at(dt_util.utcnow()), self.multiplier, self.clamp_negative)
        rcem = self.wallet.rcem(dt_util.now().date().isoformat()[:7])
        return None if rcem is None else rcem * self.multiplier

    def price_forecast(self) -> list[dict[str, Any]]:
        """Buy and sell prices from now until the end of known data."""
        now = dt_util.utcnow()
        out = []
        for start, end, rce in self.coordinator.table.between(now - timedelta(hours=1), now + timedelta(days=2)):
            begin = dt_util.utc_from_timestamp(start)
            buy = self.prices.buy_price(dt_util.as_local(begin), rce)
            out.append(
                {
                    "start": dt_util.as_local(begin).isoformat(),
                    "end": dt_util.as_local(dt_util.utc_from_timestamp(end)).isoformat(),
                    "buy": None if buy is None else round(buy, 4),
                    "sell": round(sell_price(rce, self.multiplier, self.clamp_negative) or 0, 4),
                }
            )
        return out
