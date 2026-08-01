"""Tests for gas-heater preset state."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "komeco_connect"


def _load_presets_module():
    spec = spec_from_file_location("komeco_connect.presets", PACKAGE_DIR / "presets.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


presets = _load_presets_module()


class PresetStateTests(unittest.TestCase):
    """Validate locally persisted preset behavior from the official app."""

    def test_uses_official_app_defaults(self) -> None:
        temperatures = presets.load_preset_temperatures({})

        self.assertEqual(
            temperatures,
            {
                1: 38,
                2: 35,
                3: 39,
                4: 50,
                5: 38,
                6: 43,
                7: 40,
                8: 43,
            },
        )
        self.assertEqual(
            presets.PRESET_OPTIONS,
            [
                "Manual",
                "Bebê",
                "Pet",
                "Idoso",
                "Louça",
                "Verão",
                "Inverno",
                "Homem",
                "Mulher",
            ],
        )

    def test_loads_valid_overrides_and_ignores_invalid_values(self) -> None:
        temperatures = presets.load_preset_temperatures(
            {
                presets.CONF_PRESET_TEMPERATURES: {
                    "4": 48,
                    "5": 20,
                    "7": "42",
                    "unknown": 45,
                }
            }
        )

        self.assertEqual(temperatures[4], 48)
        self.assertEqual(temperatures[5], 38)
        self.assertEqual(temperatures[7], 42)
        self.assertNotIn("unknown", temperatures)

    def test_selected_mode_requires_matching_target_temperature(self) -> None:
        temperatures = presets.load_preset_temperatures({})

        self.assertEqual(
            presets.active_preset_option(7, 40, temperatures), "Homem"
        )
        self.assertEqual(
            presets.active_preset_option(7, 41, temperatures), presets.MANUAL_OPTION
        )
        self.assertEqual(
            presets.active_preset_option(0, 40, temperatures), presets.MANUAL_OPTION
        )

    def test_preset_command_matches_official_app(self) -> None:
        self.assertEqual(
            presets.preset_command_payload(40),
            {"temp_set": 40, "switch": True},
        )

    def test_persists_edits_and_selected_mode(self) -> None:
        class ConfigEntries:
            def async_update_entry(self, entry, *, options) -> None:
                entry.options = options

        class Hass:
            config_entries = ConfigEntries()

        class Entry:
            options = {}

        entry = Entry()
        store = presets.KomecoPresetStore(Hass(), entry)

        store.set_temperature(7, 42)
        store.set_selected_preset(7)

        self.assertEqual(entry.options[presets.CONF_PRESET_TEMPERATURES]["7"], 42)
        self.assertEqual(entry.options[presets.CONF_SELECTED_PRESET], 7)

        store.set_temperature(7, 41)

        self.assertEqual(store.selected_preset, 0)
        self.assertEqual(entry.options[presets.CONF_SELECTED_PRESET], 0)


if __name__ == "__main__":
    unittest.main()
