"""Data update coordinator for Komeco Connect."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KomecoApiClient, KomecoApiError, KomecoAuthError, _as_bool, _as_int
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SUB,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
COMMAND_OVERRIDE_SECONDS = 180.0


class KomecoDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Komeco API polling."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: KomecoApiClient,
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
        )
        self.entry = entry
        self.api = api
        self._command_overrides: dict[str, tuple[Any, float]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Coordinator polling update started entry_id=%s", self.entry.entry_id)
        try:
            data = await self.api.async_fetch_state()
        except KomecoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KomecoApiError as err:
            raise UpdateFailed(str(err)) from err

        previous = self.data if isinstance(self.data, dict) else {}
        previous_values = previous.get("command_values", {})
        new_values = data.get("command_values", {})
        if isinstance(previous_values, dict) and isinstance(new_values, dict):
            for key, old_value in previous_values.items():
                if new_values.get(key) is None and old_value is not None:
                    new_values[key] = old_value
        if data.get("current_temperature") is None and previous.get("current_temperature") is not None:
            data["current_temperature"] = previous.get("current_temperature")
        if data.get("last_command_at") is None and previous.get("last_command_at") is not None:
            data["last_command_at"] = previous.get("last_command_at")
        self._apply_command_overrides(data)

        self._persist_tokens_if_needed()
        _LOGGER.debug(
            "Coordinator polling update complete temp=%s thing=%s shadow_error=%s",
            data.get("current_temperature"),
            data.get("shadow_thing_name"),
            data.get("shadow_error"),
        )
        return data

    async def async_send_command(self, payload: dict[str, Any]) -> None:
        """Send device command and refresh data."""
        _LOGGER.debug("Coordinator sending command payload=%s", payload)
        try:
            await self.api.async_send_command(payload)
        except KomecoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KomecoApiError as err:
            raise UpdateFailed(str(err)) from err

        if isinstance(self.data, dict):
            merged = dict(self.data)
            command_values = dict(merged.get("command_values", {}))
            for key in ("switch", "temp_set", "zero_cold_water_mode", "zero_cold_water_mode_status"):
                if key in payload:
                    command_values[key] = payload[key]
            merged["command_values"] = command_values
            self.async_set_updated_data(merged)
        self._set_command_overrides(payload)

        await self.async_request_refresh()
        _LOGGER.debug("Coordinator command flow complete payload=%s", payload)

    def _set_command_overrides(self, payload: dict[str, Any]) -> None:
        expires_at = monotonic() + COMMAND_OVERRIDE_SECONDS
        for key in ("switch", "temp_set", "zero_cold_water_mode", "zero_cold_water_mode_status"):
            if key in payload:
                self._command_overrides[key] = (payload[key], expires_at)
                _LOGGER.debug("Applied override key=%s value=%s", key, payload[key])

    def _apply_command_overrides(self, data: dict[str, Any]) -> None:
        now = monotonic()
        values = data.get("command_values")
        if not isinstance(values, dict):
            values = {}
            data["command_values"] = values

        for key, (override, expires_at) in list(self._command_overrides.items()):
            if expires_at <= now:
                self._command_overrides.pop(key, None)
                _LOGGER.debug("Expired override key=%s", key)
                continue

            polled_value = values.get(key)
            if polled_value == override:
                self._command_overrides.pop(key, None)
                _LOGGER.debug("Override converged key=%s value=%s", key, override)
                continue

            values[key] = override
            if key == "temp_set":
                current_temp = data.get("current_temperature")
                if current_temp is None or current_temp == polled_value:
                    data["current_temperature"] = override

    def _persist_tokens_if_needed(self) -> None:
        token_data = self.api.token_data
        changed = False
        new_data = dict(self.entry.data)

        for key_map in (
            (CONF_ID_TOKEN, "id_token"),
            (CONF_ACCESS_TOKEN, "access_token"),
            (CONF_REFRESH_TOKEN, "refresh_token"),
            (CONF_SUB, "sub"),
        ):
            config_key, token_key = key_map
            token_value = token_data.get(token_key)
            if token_value and token_value != self.entry.data.get(config_key):
                new_data[config_key] = token_value
                changed = True

        if changed:
            _LOGGER.debug("Persisting updated token fields in config entry entry_id=%s", self.entry.entry_id)
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    def handle_realtime_shadow_update(
        self,
        *,
        reported: dict[str, Any],
        raw: dict[str, Any] | None = None,
        thing_name: str | None = None,
    ) -> None:
        """Apply a realtime shadow update from MQTT."""
        if not isinstance(reported, dict) or not reported:
            return
        _LOGGER.debug(
            "Realtime shadow update received thing=%s keys=%s",
            thing_name,
            sorted(reported.keys()),
        )

        data = dict(self.data) if isinstance(self.data, dict) else {}

        shadow_reported = data.get("shadow_reported")
        if not isinstance(shadow_reported, dict):
            shadow_reported = {}
        shadow_reported = dict(shadow_reported)
        shadow_reported.update(reported)
        data["shadow_reported"] = shadow_reported

        if isinstance(raw, dict):
            data["shadow_raw"] = raw
            timestamp = _as_int(raw.get("timestamp"))
            if timestamp is not None:
                data["shadow_timestamp"] = timestamp
            version = _as_int(raw.get("version"))
            if version is not None:
                data["shadow_version"] = version

        if thing_name:
            data["shadow_thing_name"] = thing_name

        command_values = data.get("command_values")
        if not isinstance(command_values, dict):
            command_values = {}
        command_values = dict(command_values)

        if "switch" in shadow_reported:
            command_values["switch"] = _as_bool(shadow_reported.get("switch"))
        if "temp_set" in shadow_reported:
            command_values["temp_set"] = _as_int(shadow_reported.get("temp_set"))
        if "zero_cold_water_mode" in shadow_reported:
            command_values["zero_cold_water_mode"] = _as_int(shadow_reported.get("zero_cold_water_mode"))
        if "zero_cold_water_mode_status" in shadow_reported:
            command_values["zero_cold_water_mode_status"] = _as_bool(
                shadow_reported.get("zero_cold_water_mode_status")
            )

        data["command_values"] = command_values

        supported = set(data.get("supported_command_keys", []))
        for key in ("switch", "temp_set", "zero_cold_water_mode", "zero_cold_water_mode_status"):
            if key in shadow_reported:
                supported.add(key)
        supported.add("switch")
        supported.add("temp_set")
        data["supported_command_keys"] = sorted(supported)

        for temp_key in (
            "current_temp",
            "temp_current_output",
            "temp_current_input",
            "temp",
            "temp_current",
            "temperature",
            "water_temp",
        ):
            temp_value = _as_int(shadow_reported.get(temp_key))
            if temp_value is not None:
                data["current_temperature"] = temp_value
                break

        self._apply_command_overrides(data)
        self.async_set_updated_data(data)
