"""Tüke Busz (Pécs) timetable integration, based on the official static GTFS feed."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    CARD_URL,
    CONF_GTFS_REFRESH_HOURS,
    DATA_HUB,
    DEFAULT_GTFS_REFRESH_HOURS,
    DOMAIN,
    GTFS_DIR,
    GTFS_FILE,
    GTFS_URL,
    SIGNAL_FEED_UPDATED,
    VERSION,
)
from .coordinator import TukeCoordinator
from .gtfs import GtfsStore, serialize_departure, serialize_journey

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


class FeedHub:
    """Owns the shared GTFS store: download, cache on disk, periodic refresh."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.path = Path(hass.config.path(GTFS_DIR)) / GTFS_FILE
        self.store: GtfsStore | None = None
        self.downloaded_at: datetime | None = None
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._unsub_timer = None
        self._interval: timedelta | None = None

    async def async_ensure_loaded(self) -> GtfsStore:
        async with self._lock:
            if self.store is not None:
                return self.store
            if self.path.exists():
                try:
                    self.store = await self.hass.async_add_executor_job(GtfsStore.load, self.path)
                    mtime = await self.hass.async_add_executor_job(lambda: self.path.stat().st_mtime)
                    self.downloaded_at = dt_util.utc_from_timestamp(mtime)
                    return self.store
                except Exception as err:  # corrupt cache: fall through to download
                    _LOGGER.warning("Cached GTFS feed unreadable, re-downloading: %s", err)
            await self._async_download_locked()
            if self.store is None:
                raise ConfigEntryNotReady(f"Could not download Tüke Busz GTFS: {self.last_error}")
            return self.store

    async def async_refresh(self, *_args) -> bool:
        async with self._lock:
            return await self._async_download_locked()

    async def _async_download_locked(self) -> bool:
        session = async_get_clientsession(self.hass)
        tmp = self.path.with_suffix(".tmp")
        try:
            async with asyncio.timeout(120):
                async with session.get(GTFS_URL) as resp:
                    resp.raise_for_status()
                    content = await resp.read()

            def _write_and_load() -> GtfsStore:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(content)
                store = GtfsStore.load(tmp)  # validate before replacing the good copy
                tmp.replace(self.path)
                return store

            self.store = await self.hass.async_add_executor_job(_write_and_load)
        except Exception as err:  # keep serving the previous feed
            self.last_error = str(err) or type(err).__name__
            _LOGGER.warning("Tüke Busz GTFS download failed: %s", self.last_error)
            return False
        self.downloaded_at = dt_util.utcnow()
        self.last_error = None
        _LOGGER.info("Tüke Busz GTFS feed updated (%d trips)", len(self.store.trips))
        async_dispatcher_send(self.hass, SIGNAL_FEED_UPDATED)
        return True

    @callback
    def async_reschedule(self) -> None:
        """Refresh interval = smallest configured value across entries."""
        entries = [e for e in self.hass.config_entries.async_entries(DOMAIN)
                   if e.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)]
        hours = min((e.options.get(CONF_GTFS_REFRESH_HOURS, DEFAULT_GTFS_REFRESH_HOURS) for e in entries),
                    default=DEFAULT_GTFS_REFRESH_HOURS)
        interval = timedelta(hours=float(hours))
        if interval == self._interval and self._unsub_timer:
            return
        self.async_stop()
        self._interval = interval
        self._unsub_timer = async_track_time_interval(self.hass, self.async_refresh, interval)
        if self.downloaded_at and dt_util.utcnow() - self.downloaded_at > interval:
            self.hass.async_create_background_task(self.async_refresh(), f"{DOMAIN}_gtfs_refresh")

    @callback
    def async_stop(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        self._interval = None


def get_hub(hass: HomeAssistant) -> FeedHub:
    data = hass.data.setdefault(DOMAIN, {})
    if DATA_HUB not in data:
        data[DATA_HUB] = FeedHub(hass)
    return data[DATA_HUB]


# ---------------------------------------------------------------------------- setup

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await _async_register_card(hass)
    _register_services(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    if hass.http is None:
        return
    card = Path(__file__).parent / "frontend" / "tukebusz-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = get_hub(hass)
    await hub.async_ensure_loaded()

    coordinator = TukeCoordinator(hass, entry, hub)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    hub.async_reschedule()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hub = get_hub(hass)
        others = [e for e in hass.config_entries.async_entries(DOMAIN)
                  if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED]
        if others:
            hub.async_reschedule()
        else:
            hub.async_stop()
    return unloaded


# ---------------------------------------------------------------------------- services

LINES_SCHEMA = vol.Any(cv.string, [cv.string])

SCHEMA_TIMETABLE = vol.Schema({
    vol.Optional("mode", default="stop"): vol.In(["stop", "line"]),
    vol.Optional("stop"): cv.string,
    vol.Optional("line"): cv.string,
    vol.Optional("date"): cv.date,
    vol.Optional("start"): cv.time,
    vol.Optional("end"): cv.time,
    vol.Optional("direction"): cv.string,
    vol.Optional("lines"): LINES_SCHEMA,
})

SCHEMA_JOURNEYS = vol.Schema({
    vol.Required("origin"): cv.string,
    vol.Optional("destination"): cv.string,
    vol.Optional("count", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional("datetime"): cv.datetime,
    vol.Optional("lines"): LINES_SCHEMA,
})


def _register_services(hass: HomeAssistant) -> None:
    async def _store() -> GtfsStore:
        hub = get_hub(hass)
        if hub.store is None:
            try:
                await hub.async_ensure_loaded()
            except ConfigEntryNotReady as err:
                raise ServiceValidationError(str(err)) from err
        return hub.store

    async def get_metadata(call: ServiceCall) -> ServiceResponse:
        store = await _store()
        return await hass.async_add_executor_job(store.metadata)

    async def get_timetable(call: ServiceCall) -> ServiceResponse:
        store = await _store()
        d = call.data
        day = d.get("date") or dt_util.now(store.tz).date()
        try:
            if d["mode"] == "line":
                if not d.get("line"):
                    raise ServiceValidationError("'line' is required in line mode")
                result = await hass.async_add_executor_job(
                    store.line_timetable, d["line"], day, d.get("start"), d.get("end"), d.get("direction"))
                return {"mode": "line", **result}
            if not d.get("stop"):
                raise ServiceValidationError("'stop' is required in stop mode")
            stop_ids = store.resolve_stop(d["stop"])
            deps = await hass.async_add_executor_job(
                store.stop_timetable, stop_ids, day, d.get("start"), d.get("end"), d.get("lines"))
        except KeyError as err:
            raise ServiceValidationError(str(err.args[0])) from err
        return {"mode": "stop", "stop": store.stop_label(stop_ids), "date": day.isoformat(),
                "departures": [serialize_departure(x) for x in deps]}

    async def get_journeys(call: ServiceCall) -> ServiceResponse:
        store = await _store()
        d = call.data
        now = d.get("datetime") or dt_util.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=store.tz)
        try:
            items = await hass.async_add_executor_job(
                store.next_journeys, d["origin"], d.get("destination"), now, d["count"], d.get("lines"))
        except KeyError as err:
            raise ServiceValidationError(str(err.args[0])) from err
        return {"now": now.isoformat(), "journeys": [serialize_journey(j, now) for j in items]}

    async def refresh_feed(call: ServiceCall) -> None:
        if not await get_hub(hass).async_refresh():
            raise ServiceValidationError(f"GTFS download failed: {get_hub(hass).last_error}")

    hass.services.async_register(DOMAIN, "get_metadata", get_metadata, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "get_timetable", get_timetable, schema=SCHEMA_TIMETABLE,
                                 supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "get_journeys", get_journeys, schema=SCHEMA_JOURNEYS,
                                 supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "refresh_feed", refresh_feed)
