"""Number entities for Komeco."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KomecoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Komeco number entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            KomecoCommandNumber(
                coordinator=coordinator,
                command_key="temp_set",
                name="Target Temperature",
                icon="mdi:thermometer",
                minimum=30,
                maximum=60,
                step=1,
                unit=UnitOfTemperature.CELSIUS,
            ),
            KomecoCommandNumber(
                coordinator=coordinator,
                command_key="zero_cold_water_mode",
                name="Zero Cold Water Mode",
                icon="mdi:tune-variant",
                minimum=0,
                maximum=10,
                step=1,
                unit=None,
            ),
        ]
    )


class KomecoCommandNumber(KomecoEntity, NumberEntity):
    """Command-backed Komeco number."""

    def __init__(
        self,
        *,
        coordinator,
        command_key: str,
        name: str,
        icon: str,
        minimum: float,
        maximum: float,
        step: float,
        unit: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._command_key = command_key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.api.device_id}_{command_key}"
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float | None:
        """Return current value."""
        value = self.coordinator.data.get("command_values", {}).get(self._command_key)
        if value is None:
            return None
        return float(value)

    @property
    def available(self) -> bool:
        """Return availability state."""
        if not super().available:
            return False
        supported = self.coordinator.data.get("supported_command_keys", [])
        return self._command_key in supported

    async def async_set_native_value(self, value: float) -> None:
        """Set value."""
        supported = self.coordinator.data.get("supported_command_keys", [])
        if self._command_key not in supported:
            raise HomeAssistantError(
                f"Command '{self._command_key}' is not supported by this device model"
            )
        payload = {self._command_key: int(round(value))}
        if self._command_key == "temp_set":
            switch = self.coordinator.data.get("command_values", {}).get("switch")
            payload["switch"] = True if switch is None else bool(switch)
        await self.coordinator.async_send_command(payload)
