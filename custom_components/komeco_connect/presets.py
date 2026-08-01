"""Local gas-heater presets matching the official app."""

from __future__ import annotations

from typing import Any, NamedTuple


CONF_PRESET_TEMPERATURES = "preset_temperatures"
CONF_SELECTED_PRESET = "selected_preset"
MANUAL_OPTION = "Manual"
MIN_PRESET_TEMPERATURE = 35
MAX_PRESET_TEMPERATURE = 60


class KomecoPreset(NamedTuple):
    """Definition of an official-app gas-heater preset."""

    preset_id: int
    slug: str
    label: str
    icon: str
    default_temperature: int
    editable: bool


PRESETS: tuple[KomecoPreset, ...] = (
    KomecoPreset(1, "bebe", "Bebê", "mdi:baby-carriage", 38, False),
    KomecoPreset(2, "pet", "Pet", "mdi:paw", 35, False),
    KomecoPreset(3, "idoso", "Idoso", "mdi:human-cane", 39, False),
    KomecoPreset(4, "louca", "Louça", "mdi:faucet", 50, True),
    KomecoPreset(5, "verao", "Verão", "mdi:white-balance-sunny", 38, True),
    KomecoPreset(6, "inverno", "Inverno", "mdi:snowflake", 43, True),
    KomecoPreset(7, "homem", "Homem", "mdi:human-male", 40, True),
    KomecoPreset(8, "mulher", "Mulher", "mdi:human-female", 43, True),
)

PRESETS_BY_ID = {preset.preset_id: preset for preset in PRESETS}
PRESETS_BY_OPTION = {preset.label: preset for preset in PRESETS}
EDITABLE_PRESETS = tuple(preset for preset in PRESETS if preset.editable)
PRESET_OPTIONS = [MANUAL_OPTION, *(preset.label for preset in PRESETS)]


def load_preset_temperatures(options: dict[str, Any]) -> dict[int, int]:
    """Load valid editable overrides on top of official-app defaults."""
    temperatures = {
        preset.preset_id: preset.default_temperature for preset in PRESETS
    }
    raw_values = options.get(CONF_PRESET_TEMPERATURES, {})
    if not isinstance(raw_values, dict):
        return temperatures

    for preset in EDITABLE_PRESETS:
        raw_value = raw_values.get(str(preset.preset_id))
        if raw_value is None:
            raw_value = raw_values.get(preset.preset_id)
        if isinstance(raw_value, bool):
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if MIN_PRESET_TEMPERATURE <= value <= MAX_PRESET_TEMPERATURE:
            temperatures[preset.preset_id] = value

    return temperatures


def active_preset_option(
    selected_preset: int,
    target_temperature: Any,
    temperatures: dict[int, int],
) -> str:
    """Return the selected option while its temperature matches the heater."""
    preset = PRESETS_BY_ID.get(selected_preset)
    if preset is None or isinstance(target_temperature, bool):
        return MANUAL_OPTION
    try:
        target = int(target_temperature)
    except (TypeError, ValueError):
        return MANUAL_OPTION
    if temperatures.get(selected_preset) != target:
        return MANUAL_OPTION
    return preset.label


def preset_command_payload(temperature: int) -> dict[str, Any]:
    """Build the command sent by the official app when applying a preset."""
    return {"temp_set": temperature, "switch": True}


class KomecoPresetStore:
    """Persist app-style presets in the Home Assistant config entry."""

    def __init__(self, hass: Any, entry: Any) -> None:
        self._hass = hass
        self._entry = entry
        self.temperatures = load_preset_temperatures(dict(entry.options))
        selected = entry.options.get(CONF_SELECTED_PRESET, 0)
        try:
            selected_id = int(selected)
        except (TypeError, ValueError):
            selected_id = 0
        self.selected_preset = selected_id if selected_id in PRESETS_BY_ID else 0

    def set_temperature(self, preset_id: int, temperature: int) -> None:
        """Update an editable preset and persist it."""
        preset = PRESETS_BY_ID[preset_id]
        if not preset.editable:
            raise ValueError(f"Preset {preset_id} is not editable")
        if not MIN_PRESET_TEMPERATURE <= temperature <= MAX_PRESET_TEMPERATURE:
            raise ValueError(f"Invalid preset temperature: {temperature}")
        self.temperatures[preset_id] = temperature
        if self.selected_preset == preset_id:
            self.selected_preset = 0
        self._persist()

    def set_selected_preset(self, preset_id: int) -> None:
        """Update the locally selected preset and persist it."""
        if preset_id != 0 and preset_id not in PRESETS_BY_ID:
            raise ValueError(f"Unknown preset: {preset_id}")
        self.selected_preset = preset_id
        self._persist()

    def _persist(self) -> None:
        options = dict(self._entry.options)
        options[CONF_PRESET_TEMPERATURES] = {
            str(preset.preset_id): self.temperatures[preset.preset_id]
            for preset in EDITABLE_PRESETS
        }
        options[CONF_SELECTED_PRESET] = self.selected_preset
        self._hass.config_entries.async_update_entry(self._entry, options=options)
