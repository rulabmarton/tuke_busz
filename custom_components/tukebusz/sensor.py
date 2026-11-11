"""Sensors: next A→B departure (with upcoming list) and GTFS feed status."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SIGNAL_FEED_UPDATED, VERSION
from .coordinator import TukeCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: TukeCoordinator = entry.runtime_data
    async_add_entities([TukeDepartureSensor(coordinator, entry), TukeFeedSensor(coordinator, entry)])


def _device(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=f"Tüke Busz {entry.title}",
                      manufacturer="Tüke Busz Zrt. (GTFS)", entry_type=DeviceEntryType.SERVICE,
                      sw_version=VERSION)


class TukeDepartureSensor(CoordinatorEntity[TukeCoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus-clock"
    _attr_has_entity_name = True
    _attr_name = None  # entity takes the device name -> sensor.tuke_busz_<origin>_<destination>

    def __init__(self, coordinator: TukeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_departures"
        self._attr_device_info = _device(entry)

    @property
    def native_value(self) -> datetime | None:
        journeys = (self.coordinator.data or {}).get("journeys") or []
        return journeys[0]["departure"] if journeys else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        hub = self.coordinator.hub
        first = (data.get("departures") or [None])[0]
        return {
            "origin": data.get("origin"),
            "destination": data.get("destination"),
            "line": first["line"] if first else None,
            "relative": first["relative"] if first else None,
            "departures": data.get("departures", []),
            "generated_at": data["now"].isoformat() if data.get("now") else None,
            "feed_downloaded_at": hub.downloaded_at.isoformat() if hub.downloaded_at else None,
            "attribution": "Menetrend: Tüke Busz Zrt. / Biokom Mobilitási Központ (GTFS)",
        }


class TukeFeedSensor(CoordinatorEntity[TukeCoordinator], SensorEntity):
    """When the GTFS feed was last downloaded; attributes show validity range."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timetable"
    _attr_has_entity_name = True
    _attr_translation_key = "feed"

    def __init__(self, coordinator: TukeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_feed"
        self._attr_device_info = _device(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_FEED_UPDATED, self._feed_updated))

    @callback
    def _feed_updated(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.hub.downloaded_at

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        hub = self.coordinator.hub
        start: date | None = None
        end: date | None = None
        if hub.store is not None:
            start, end = hub.store.feed_range()
        return {
            "valid_from": start.isoformat() if start else None,
            "valid_to": end.isoformat() if end else None,
            "last_error": hub.last_error,
        }
