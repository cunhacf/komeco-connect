"""Select entities for Komeco."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KomecoEntity
from .presets import (
    MANUAL_OPTION,
    PRESET_OPTIONS,
    PRESETS_BY_OPTION,
    KomecoPresetStore,
    active_preset_option,
    preset_command_payload,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Komeco select entities."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [KomecoPresetSelect(runtime["coordinator"], runtime["presets"])]
    )


class KomecoPresetSelect(KomecoEntity, SelectEntity):
    """Select an app-style temperature preset."""

    _attr_name = "Temperature Preset"
    _attr_icon = "mdi:tune-variant"
    _attr_options = PRESET_OPTIONS

    def __init__(self, coordinator, store: KomecoPresetStore) -> None:
        super().__init__(coordinator)
        self._store = store
        self._attr_unique_id = f"{coordinator.api.device_id}_mode"

    @property
    def current_option(self) -> str:
        """Return the active preset, or Manual after an external change."""
        target = self.coordinator.data.get("command_values", {}).get("temp_set")
        return active_preset_option(
            self._store.selected_preset,
            target,
            self._store.temperatures,
        )

    async def async_select_option(self, option: str) -> None:
        """Apply a preset temperature using the official app command shape."""
        if option == MANUAL_OPTION:
            self._store.set_selected_preset(0)
            self.coordinator.async_update_listeners()
            return

        preset = PRESETS_BY_OPTION.get(option)
        if preset is None:
            raise ValueError(f"Unknown preset option: {option}")
        temperature = self._store.temperatures[preset.preset_id]
        await self.coordinator.async_send_command(
            preset_command_payload(temperature)
        )
        self._store.set_selected_preset(preset.preset_id)
        self.coordinator.async_update_listeners()
