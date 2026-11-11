"""Coordinator: recomputes the next journeys from the local GTFS store."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COUNT,
    CONF_DESTINATION,
    CONF_LINES,
    CONF_ORIGIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_COUNT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SIGNAL_FEED_UPDATED,
)
from .gtfs import serialize_journey

if TYPE_CHECKING:
    from . import FeedHub

_LOGGER = logging.getLogger(__name__)


class TukeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, hub: "FeedHub") -> None:
        interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {entry.title}",
                         update_interval=timedelta(seconds=interval), config_entry=entry)
        self.hub = hub
        self.origin: str = entry.data[CONF_ORIGIN]
        self.destination: str | None = entry.data.get(CONF_DESTINATION) or None
        self.count = int(entry.options.get(CONF_COUNT, DEFAULT_COUNT))
        self.lines: str | None = entry.options.get(CONF_LINES) or None
        entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_FEED_UPDATED, self._feed_updated))

    @callback
    def _feed_updated(self) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> dict[str, Any]:
        store = self.hub.store
        if store is None:
            raise UpdateFailed("GTFS feed not loaded")
        now = dt_util.now(store.tz)
        try:
            journeys = await self.hass.async_add_executor_job(
                store.next_journeys, self.origin, self.destination, now, self.count, self.lines)
        except KeyError as err:
            raise UpdateFailed(f"{err.args[0]} (not in the current GTFS feed)") from err
        return {
            "now": now,
            "origin": store.stop_label(store.resolve_stop(self.origin)),
            "destination": store.stop_label(store.resolve_stop(self.destination)) if self.destination else None,
            "journeys": journeys,
            "departures": [serialize_journey(j, now) for j in journeys],
        }
