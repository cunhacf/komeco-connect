"""Water heater platform for Komeco."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    ATTR_TEMPERATURE,
    STATE_GAS,
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KomecoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Komeco water heater entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([KomecoWaterHeater(coordinator)])


class KomecoWaterHeater(KomecoEntity, WaterHeaterEntity):
    """Komeco water heater entity."""

    _attr_name = "Heater"
    _attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.ON_OFF
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 30
    _attr_max_temp = 60
    _attr_target_temperature_step = 1

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.device_id}_water_heater"

    @property
    def current_operation(self) -> str | None:
        """Return current operation mode."""
        switch_value = self.coordinator.data.get("command_values", {}).get("switch")
        if switch_value is None:
            return None
        return STATE_GAS if switch_value else STATE_OFF

    @property
    def operation_list(self) -> list[str]:
        """Return operation modes."""
        return [STATE_OFF, STATE_GAS]

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        temp_set = self.coordinator.data.get("command_values", {}).get("temp_set")
        return float(temp_set) if temp_set is not None else None

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        current = self.coordinator.data.get("current_temperature")
        return float(current) if current is not None else None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        payload: dict[str, Any] = {"temp_set": int(round(float(temperature)))}
        switch = self.coordinator.data.get("command_values", {}).get("switch")
        payload["switch"] = True if switch is None else bool(switch)
        await self.coordinator.async_send_command(payload)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn heater on."""
        await self.coordinator.async_send_command({"switch": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn heater off."""
        await self.coordinator.async_send_command({"switch": False})

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set operation mode."""
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()
