"""Sensor entities for Komeco."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .entity import KomecoEntity

_SHADOW_SENSOR_ICONS: dict[str, str] = {
    "error_code": "mdi:alert-circle-outline",
    "mode": "mdi:tune-variant",
    "temp_current_input": "mdi:thermometer-chevron-down",
    "temp_current_output": "mdi:thermometer-chevron-up",
    "water_flow_current": "mdi:waves-arrow-right",
    "water_productivity": "mdi:water-pump",
}

_SHADOW_SENSOR_NAMES: dict[str, str] = {
    "error_code": "Error Code",
    "mode": "Device Mode Code",
    "temp_current_input": "Water Inlet Temperature",
    "temp_current_output": "Water Outlet Temperature",
    "water_flow_current": "Water Flow Current",
    "water_productivity": "Water Productivity",
}

# Shadow telemetry keys that are never surfaced as auto-generated sensors. The
# raw ``consumption_gas``/``consumption_water`` shadow fields are vestigial: the
# official app never reads them (they only appear in its mock data) and live
# devices report a constant ``1``. Real consumption comes from the getDataset /
# getGasHeaterUse dataset endpoints instead.
_SHADOW_SENSOR_EXCLUDED: set[str] = {
    "switch",
    "temp_set",
    "zero_cold_water_mode",
    "zero_cold_water_mode_status",
    "connected",
    "consumption_gas",
    "consumption_water",
    "state_antifreeze",
    "state_flame",
    "state_motor",
    "state_water",
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
        for key, value in reported.items():
            if key in _SHADOW_SENSOR_EXCLUDED or isinstance(value, bool):
                continue
            entities.append(KomecoShadowValueSensor(coordinator, key))

    entities.extend(
        [
            KomecoUsageConsumptionSensor(
                coordinator,
                key="gas_consumption_m3_s",
                name="Last Use Gas Consumption",
                icon="mdi:fire",
                unit=UnitOfVolume.CUBIC_METERS,
            ),
            KomecoUsageConsumptionSensor(
                coordinator,
                key="water_L_s",
                name="Last Use Water Consumption",
                icon="mdi:water",
                unit=UnitOfVolume.LITERS,
            ),
        ]
    )

    for period, period_label in (("today", "Today"), ("month", "This Month")):
        entities.extend(
            [
                KomecoPeriodUsageSensor(
                    coordinator,
                    period=period,
                    metric="gas_consumption_m3",
                    name=f"{period_label} Gas Consumption",
                    icon="mdi:fire",
                    unit=UnitOfVolume.CUBIC_METERS,
                    device_class=SensorDeviceClass.GAS,
                ),
                KomecoPeriodUsageSensor(
                    coordinator,
                    period=period,
                    metric="water_l",
                    name=f"{period_label} Water Consumption",
                    icon="mdi:water",
                    unit=UnitOfVolume.LITERS,
                    device_class=SensorDeviceClass.WATER,
                ),
            ]
        )

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
        if key == "mode":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

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


class KomecoUsageConsumptionSensor(KomecoEntity, SensorEntity):
    """Expose consumption from the newest getGasHeaterUse session.

    The dataset field names are misleading: ``gas_consumption_m3_s`` is a total
    volume in m³ for the session bucket (not m³/s) and ``water_L_s`` is total
    liters, so the volume unit/device class below is correct as-is.
    """

    _attr_device_class = SensorDeviceClass.VOLUME

    def __init__(
        self,
        coordinator,
        *,
        key: str,
        name: str,
        icon: str,
        unit: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.api.device_id}_usage_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> Any:
        """Return consumption from the newest usage session."""
        usage = self.coordinator.data.get("latest_usage", {})
        if not isinstance(usage, dict):
            return None
        value = usage.get(self._key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    @property
    def available(self) -> bool:
        """Return availability state."""
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose details for the newest session bucket."""
        attrs: dict[str, Any] = {
            "source": "getGasHeaterUse",
            "dataset_key": self._key,
        }
        usage = self.coordinator.data.get("latest_usage", {})
        if isinstance(usage, dict):
            timestamp = usage.get("timestamp")
            if isinstance(timestamp, (int, float)):
                epoch = float(timestamp)
                if epoch > 10_000_000_000:
                    epoch /= 1000
                attrs["usage_timestamp"] = dt_util.utc_from_timestamp(epoch)
            for src_key, attr_key in (
                ("usage_time", "usage_time_seconds"),
                ("usage_time_min", "usage_time_minutes"),
                ("usage_time_hour", "usage_time_hours"),
                ("turned_on_times", "ignitions"),
            ):
                value = usage.get(src_key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    attrs[attr_key] = value
        return attrs


class KomecoPeriodUsageSensor(KomecoEntity, SensorEntity):
    """Expose period totals aggregated from getDataset daily buckets."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator,
        *,
        period: str,
        metric: str,
        name: str,
        icon: str,
        unit: str,
        device_class: SensorDeviceClass,
    ) -> None:
        super().__init__(coordinator)
        self._period = period
        self._metric = metric
        self._data_key = f"{period}_usage"
        self._attr_unique_id = f"{coordinator.api.device_id}_{period}_{metric}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class

    def _totals(self) -> dict[str, Any]:
        totals = self.coordinator.data.get(self._data_key, {})
        return totals if isinstance(totals, dict) else {}

    @property
    def native_value(self) -> Any:
        """Return the aggregated period total."""
        value = self._totals().get(self._metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    @property
    def available(self) -> bool:
        """Return availability state."""
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose supporting totals for the period."""
        attrs: dict[str, Any] = {
            "source": "getDataset",
            "period": self._period,
            "dataset_name": "daily",
        }
        totals = self._totals()
        for src_key, attr_key in (
            ("usage_time_min", "usage_time_minutes"),
            ("turned_on_times", "ignitions"),
            ("sessions", "sessions"),
        ):
            value = totals.get(src_key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                attrs[attr_key] = value
        return attrs
