"""Sensor entities for Komeco."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .entity import KomecoEntity

_SHADOW_SENSOR_ICONS: dict[str, str] = {
    "consumption_gas": "mdi:fire",
    "consumption_water": "mdi:water",
    "error_code": "mdi:alert-circle-outline",
    "mode": "mdi:tune-variant",
    "temp_current_input": "mdi:thermometer-chevron-down",
    "temp_current_output": "mdi:thermometer-chevron-up",
    "water_flow_current": "mdi:waves-arrow-right",
    "water_productivity": "mdi:water-pump",
}

_SHADOW_SENSOR_NAMES: dict[str, str] = {
    "consumption_gas": "Gas Consumption",
    "consumption_water": "Water Consumption",
    "error_code": "Error Code",
    "mode": "Mode",
    "temp_current_input": "Water Inlet Temperature",
    "temp_current_output": "Water Outlet Temperature",
    "water_flow_current": "Water Flow Current",
    "water_productivity": "Water Productivity",
}

_SHADOW_SENSOR_UNITS: dict[str, str] = {
    "temp_current_input": UnitOfTemperature.CELSIUS,
    "temp_current_output": UnitOfTemperature.CELSIUS,
}

_SHADOW_SENSOR_DEVICE_CLASS: dict[str, SensorDeviceClass] = {
    "temp_current_input": SensorDeviceClass.TEMPERATURE,
    "temp_current_output": SensorDeviceClass.TEMPERATURE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Komeco sensor entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[SensorEntity] = [KomecoLastCommandSensor(coordinator), KomecoShadowTimestampSensor(coordinator)]

    reported = coordinator.data.get("shadow_reported", {})
    if isinstance(reported, dict):
        excluded = {
            "switch",
            "temp_set",
            "zero_cold_water_mode",
            "zero_cold_water_mode_status",
            "connected",
            "state_antifreeze",
            "state_flame",
            "state_motor",
            "state_water",
        }
        for key, value in reported.items():
            if key in excluded or isinstance(value, bool):
                continue
            entities.append(KomecoShadowValueSensor(coordinator, key))

    async_add_entities(entities)


class KomecoLastCommandSensor(KomecoEntity, SensorEntity):
    """Timestamp for last known command event."""

    _attr_name = "Last Command"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.device_id}_last_command"
        self._attr_icon = "mdi:history"

    @property
    def native_value(self) -> datetime | None:
        """Return timestamp value."""
        raw = self.coordinator.data.get("last_command_at")
        if not isinstance(raw, str):
            return None
        parsed = dt_util.parse_datetime(raw)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            return dt_util.as_utc(parsed)
        return parsed


class KomecoShadowTimestampSensor(KomecoEntity, SensorEntity):
    """Timestamp from AWS IoT Shadow document."""

    _attr_name = "Shadow Timestamp"
    _attr_icon = "mdi:clock-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.device_id}_shadow_timestamp"

    @property
    def native_value(self) -> datetime | None:
        """Return shadow timestamp."""
        value = self.coordinator.data.get("shadow_timestamp")
        if not isinstance(value, int):
            return None
        return dt_util.utc_from_timestamp(value)

    @property
    def available(self) -> bool:
        """Return availability state."""
        return super().available and isinstance(self.coordinator.data.get("shadow_timestamp"), int)


class KomecoShadowValueSensor(KomecoEntity, SensorEntity):
    """Expose numeric/string telemetry from shadow reported."""

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.api.device_id}_shadow_{key}"
        self._attr_name = _SHADOW_SENSOR_NAMES.get(key, key.replace("_", " ").title())
        self._attr_icon = _SHADOW_SENSOR_ICONS.get(key, "mdi:gauge")
        self._attr_native_unit_of_measurement = _SHADOW_SENSOR_UNITS.get(key)
        self._attr_device_class = _SHADOW_SENSOR_DEVICE_CLASS.get(key)

    @property
    def native_value(self) -> Any:
        """Return sensor value."""
        reported = self.coordinator.data.get("shadow_reported", {})
        if not isinstance(reported, dict):
            return None
        value = reported.get(self._key)
        if isinstance(value, bool):
            return None
        return value

    @property
    def available(self) -> bool:
        """Return availability state."""
        if not super().available:
            return False
        reported = self.coordinator.data.get("shadow_reported", {})
        return isinstance(reported, dict) and self._key in reported

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose shadow metadata timestamps per key when available."""
        raw = self.coordinator.data.get("shadow_raw", {})
        if not isinstance(raw, dict):
            return {}
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict):
            return {}
        reported_meta = metadata.get("reported")
        if not isinstance(reported_meta, dict):
            return {}
        key_meta = reported_meta.get(self._key)
        if not isinstance(key_meta, dict):
            return {}
        ts = key_meta.get("timestamp")
        attrs: dict[str, Any] = {}
        if isinstance(ts, int):
            attrs["source_timestamp"] = dt_util.utc_from_timestamp(ts)
        version = self.coordinator.data.get("shadow_version")
        if isinstance(version, int):
            attrs["shadow_version"] = version
        thing_name = self.coordinator.data.get("shadow_thing_name")
        if isinstance(thing_name, str) and thing_name:
            attrs["thing_name"] = thing_name
        attrs["shadow_key"] = self._key
        return attrs
