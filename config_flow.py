"""Config and options flow for Tüke Busz."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_COUNT,
    CONF_DESTINATION,
    CONF_GTFS_REFRESH_HOURS,
    CONF_LINES,
    CONF_ORIGIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_COUNT,
    DEFAULT_GTFS_REFRESH_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .gtfs import GtfsStore


def _stop_options(store: GtfsStore) -> list[SelectOptionDict]:
    """Stop name groups (all platforms), followed by their individual platforms."""
    options: list[SelectOptionDict] = []
    for name, ids in sorted(store.stop_groups.items(), key=lambda kv: kv[0].lower()):
        options.append(SelectOptionDict(value=name, label=name))
        if len(ids) > 1:
            for sid in sorted(ids):
                desc = store.stops[sid].desc or sid
                options.append(SelectOptionDict(value=sid, label=f"{name} – {desc}"))
    return options


def _settings_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_COUNT, default=defaults.get(CONF_COUNT, DEFAULT_COUNT)):
            NumberSelector(NumberSelectorConfig(min=1, max=20, step=1, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_SCAN_INTERVAL, default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
            NumberSelector(NumberSelectorConfig(min=15, max=3600, step=1, mode=NumberSelectorMode.BOX,
                                                unit_of_measurement="s")),
        vol.Optional(CONF_LINES, description={"suggested_value": defaults.get(CONF_LINES, "")}): TextSelector(),
        vol.Required(CONF_GTFS_REFRESH_HOURS,
                     default=defaults.get(CONF_GTFS_REFRESH_HOURS, DEFAULT_GTFS_REFRESH_HOURS)):
            NumberSelector(NumberSelectorConfig(min=1, max=168, step=1, mode=NumberSelectorMode.BOX,
                                                unit_of_measurement="h")),
    })


def _clean_settings(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_COUNT: int(user_input[CONF_COUNT]),
        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
        CONF_LINES: (user_input.get(CONF_LINES) or "").strip(),
        CONF_GTFS_REFRESH_HOURS: int(user_input[CONF_GTFS_REFRESH_HOURS]),
    }


class TukeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._stops: dict[str, Any] = {}
        self._title = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from . import get_hub

        try:
            store = await get_hub(self.hass).async_ensure_loaded()
        except ConfigEntryNotReady:
            return self.async_abort(reason="cannot_connect")

        errors: dict[str, str] = {}
        if user_input is not None:
            origin = user_input[CONF_ORIGIN]
            destination = user_input.get(CONF_DESTINATION) or ""
            if destination and set(store.resolve_stop(origin)) & set(store.resolve_stop(destination)):
                errors[CONF_DESTINATION] = "same_stop"
            else:
                await self.async_set_unique_id(f"{origin}|{destination}")
                self._abort_if_unique_id_configured()
                self._stops = {CONF_ORIGIN: origin, CONF_DESTINATION: destination}
                label = lambda v: store.stop_label(store.resolve_stop(v))  # noqa: E731
                self._title = f"{label(origin)} → {label(destination)}" if destination else label(origin)
                return await self.async_step_settings()

        options = _stop_options(store)
        selector = lambda: SelectSelector(SelectSelectorConfig(  # noqa: E731
            options=options, mode=SelectSelectorMode.DROPDOWN, sort=False))
        schema = vol.Schema({
            vol.Required(CONF_ORIGIN): selector(),
            vol.Optional(CONF_DESTINATION): selector(),
        })
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(
            schema, user_input or {}), errors=errors)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None and CONF_COUNT in user_input:
            return self.async_create_entry(title=self._title, data=self._stops, options=_clean_settings(user_input))
        return self.async_show_form(step_id="settings", data_schema=_settings_schema({}),
                                    description_placeholders={"route": self._title})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TukeOptionsFlow()


class TukeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_clean_settings(user_input))
        return self.async_show_form(step_id="init", data_schema=_settings_schema(dict(self.config_entry.options)))
