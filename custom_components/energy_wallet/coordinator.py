"""Coordinator fetching RCE prices from PSE."""

from __future__ import annotations

from datetime import timedelta
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .pse import PriceTable, fetch_rce, parse_rce

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=30)
KEEP_DAYS = 7


class RcePriceCoordinator(DataUpdateCoordinator[PriceTable]):
    """Keeps a rolling table of RCE prices (yesterday .. tomorrow and recent history)."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_rce", update_interval=UPDATE_INTERVAL)
        self.table = PriceTable()

    async def _async_update_data(self) -> PriceTable:
        since = (dt_util.now() - timedelta(days=1)).date()
        try:
            records = await fetch_rce(async_get_clientsession(self.hass), since)
        except (aiohttp.ClientError, TimeoutError) as err:
            if len(self.table):
                _LOGGER.warning("PSE API unavailable, keeping cached prices: %s", err)
                return self.table
            raise UpdateFailed(f"PSE API error: {err}") from err

        keep_after = int((dt_util.utcnow() - timedelta(days=KEEP_DAYS)).timestamp())
        self.table.merge(parse_rce(records, dt_util.get_default_time_zone()), keep_after=keep_after)
        return self.table
