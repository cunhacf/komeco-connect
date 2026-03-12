"""Binary sensor entities for Komeco."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KomecoEntity

_SHADOW_BINARY_ICONS: dict[str, str] = {
    "connected": "mdi:lan-connect",
    "state_antifreeze": "mdi:snowflake-alert",
    "state_flame": "mdi:fire",
    "state_motor": "mdi:engine",
    "state_water": "mdi:water",
}

_SHADOW_BINARY_NAMES: dict[str, str] = {
    "connected": "Connected",
    "state_antifreeze": "Antifreeze State",
    "state_flame": "Flame State",
    "state_motor": "Motor State",
    "state_water": "Water State",
}

_SHADOW_BINARY_DEVICE_CLASS: dict[str, BinarySensorDeviceClass] = {
    "connected": BinarySensorDeviceClass.CONNECTIVITY,
}


def _coerce_bool(value: Any) -> bool | None:
    """Convert bool-like values from API/shadow into bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Komeco binary sensor entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[BinarySensorEntity] = [
        KomecoStatusBinarySensor(coordinator),
        KomecoShadowBinarySensor(coordinator, "connected"),
        KomecoShadowBinarySensor(coordinator, "state_antifreeze"),
        KomecoShadowBinarySensor(coordinator, "state_flame"),
        KomecoShadowBinarySensor(coordinator, "state_motor"),
        KomecoShadowBinarySensor(coordinator, "state_water"),
    ]

    async_add_entities(entities)


class KomecoStatusBinarySensor(KomecoEntity, BinarySensorEntity):
    """Read-only zero cold water mode status."""

    _attr_name = "Zero Cold Water Status"
    _attr_icon = "mdi:snowflake-melt"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.device_id}_zero_cold_water_mode_status"

    @property
    def available(self) -> bool:
        """Return availability state."""
        if not super().available:
            return False
        supported = self.coordinator.data.get("supported_command_keys", [])
        return "zero_cold_water_mode_status" in supported

    @property
    def is_on(self) -> bool | None:
        """Return current status."""
        value = self.coordinator.data.get("command_values", {}).get("zero_cold_water_mode_status")
        return _coerce_bool(value)


class KomecoShadowBinarySensor(KomecoEntity, BinarySensorEntity):
    """Expose bool telemetry from shadow reported."""

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.api.device_id}_shadow_{key}"
        self._attr_name = _SHADOW_BINARY_NAMES.get(key, key.replace("_", " ").title())
        self._attr_icon = _SHADOW_BINARY_ICONS.get(key, "mdi:check-network")
        self._attr_device_class = _SHADOW_BINARY_DEVICE_CLASS.get(key)

    @property
    def is_on(self) -> bool | None:
        """Return current bool value."""
        reported = self.coordinator.data.get("shadow_reported", {})
        if not isinstance(reported, dict):
            return None
        return _coerce_bool(reported.get(self._key))

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
        attrs: dict[str, Any] = {}
        ts = key_meta.get("timestamp")
        if isinstance(ts, int):
            attrs["source_timestamp"] = ts
        version = self.coordinator.data.get("shadow_version")
        if isinstance(version, int):
            attrs["shadow_version"] = version
        thing_name = self.coordinator.data.get("shadow_thing_name")
        if isinstance(thing_name, str) and thing_name:
            attrs["thing_name"] = thing_name
        attrs["shadow_key"] = self._key
        return attrs
