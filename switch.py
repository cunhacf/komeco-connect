"""Switch entities for Komeco."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KomecoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Komeco switch entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            KomecoCommandSwitch(
                coordinator=coordinator,
                command_key="switch",
                name="Power",
                icon="mdi:power",
            ),
        ]
    )


class KomecoCommandSwitch(KomecoEntity, SwitchEntity):
    """Command-backed Komeco switch."""

    def __init__(self, *, coordinator, command_key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._command_key = command_key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.api.device_id}_{command_key}"

    @property
    def is_on(self) -> bool | None:
        """Return switch state."""
        value = self.coordinator.data.get("command_values", {}).get(self._command_key)
        if value is None:
            return None
        return bool(value)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn switch on."""
        await self.coordinator.async_send_command({self._command_key: True})

    async def async_turn_off(self, **kwargs) -> None:
        """Turn switch off."""
        await self.coordinator.async_send_command({self._command_key: False})
