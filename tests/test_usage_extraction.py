"""Tests for gas-heater usage dataset extraction."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
import unittest


PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "komeco_connect"


def _load_api_module():
    package = types.ModuleType("komeco_connect")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules.setdefault("komeco_connect", package)

    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientSession = object
    sys.modules.setdefault("aiohttp", aiohttp)

    crypto = types.ModuleType("Crypto")
    crypto_cipher = types.ModuleType("Crypto.Cipher")
    crypto_cipher.AES = object
    crypto_util = types.ModuleType("Crypto.Util")
    crypto_padding = types.ModuleType("Crypto.Util.Padding")
    crypto_padding.pad = lambda value, _: value
    sys.modules.setdefault("Crypto", crypto)
    sys.modules.setdefault("Crypto.Cipher", crypto_cipher)
    sys.modules.setdefault("Crypto.Util", crypto_util)
    sys.modules.setdefault("Crypto.Util.Padding", crypto_padding)

    spec = spec_from_file_location("komeco_connect.api", PACKAGE_DIR / "api.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules["komeco_connect.api"] = module
    spec.loader.exec_module(module)
    return module


api = _load_api_module()


class UsageExtractionTests(unittest.TestCase):
    """Validate usage-session extraction."""

    def test_selects_newest_session(self) -> None:
        usage = [
            {
                "timestamp": 1714532400,
                "usage_time": 120,
                "water_L_s": 30.5,
                "gas_consumption_m3_s": 0.04,
            },
            {
                "timestamp": 1714536000,
                "usage_time": 180,
                "water_L_s": 45.25,
                "gas_consumption_m3_s": 0.07,
            },
        ]

        latest = api.KomecoApiClient._extract_latest_usage(usage)

        self.assertEqual(latest, usage[1])

    def test_supports_wrapped_value_list(self) -> None:
        latest = api.KomecoApiClient._extract_latest_usage(
            {
                "value": [
                    {
                        "timestamp": 1714536000000,
                        "usage_time": 90,
                        "water_L_s": 20,
                        "gas_consumption_m3_s": 0.03,
                    }
                ]
            }
        )

        self.assertEqual(
            latest,
            {
                "timestamp": 1714536000000,
                "usage_time": 90,
                "water_L_s": 20,
                "gas_consumption_m3_s": 0.03,
            },
        )

    def test_ignores_non_usage_records(self) -> None:
        latest = api.KomecoApiClient._extract_latest_usage(
            [{"timestamp": 1714536000}, {"message": "not usage data"}]
        )

        self.assertEqual(latest, {})


class PeriodAggregationTests(unittest.TestCase):
    """Validate getDataset bucket aggregation."""

    def test_sums_bucket_records(self) -> None:
        # Real bucket shape taken from the decompiled app's DayReportSimulator.
        buckets = [
            {
                "gas_consumption_m3_s": 0.42629790476190443,
                "water_L_s": 402.316666666666,
                "usage_time": 1642,
                "usage_time_min": 27.366666666666667,
                "turned_on_times": 2,
                "timestamp": 1714532400,
            },
            {
                "gas_consumption_m3_s": 0.3565302857142856,
                "water_L_s": 347.0666666666664,
                "usage_time": 1407,
                "usage_time_min": 23.45,
                "turned_on_times": 3,
                "timestamp": 1714536000,
            },
        ]

        totals = api.KomecoApiClient._aggregate_usage_records(buckets)

        self.assertEqual(totals["gas_consumption_m3"], 0.7828)
        self.assertEqual(totals["water_l"], 749.38)
        self.assertEqual(totals["usage_time_min"], 50.82)
        self.assertEqual(totals["turned_on_times"], 5)
        self.assertEqual(totals["sessions"], 2)

    def test_supports_wrapped_value_list(self) -> None:
        totals = api.KomecoApiClient._aggregate_usage_records(
            {"value": [{"gas_consumption_m3_s": 1.0, "water_L_s": 10.0}]}
        )

        self.assertEqual(totals["gas_consumption_m3"], 1.0)
        self.assertEqual(totals["water_l"], 10.0)
        self.assertEqual(totals["sessions"], 1)

    def test_empty_dataset_returns_zero_totals(self) -> None:
        expected = {
            "gas_consumption_m3": 0.0,
            "water_l": 0.0,
            "usage_time_min": 0.0,
            "turned_on_times": 0,
            "sessions": 0,
        }

        self.assertEqual(api.KomecoApiClient._aggregate_usage_records([]), expected)
        self.assertEqual(
            api.KomecoApiClient._aggregate_usage_records({"value": []}), expected
        )

    def test_empty_for_invalid_records(self) -> None:
        self.assertEqual(
            api.KomecoApiClient._aggregate_usage_records([{"message": "nope"}]), {}
        )

    def test_period_bounds_are_ordered_and_aligned(self) -> None:
        for period in ("today", "month"):
            start, end = api.KomecoApiClient._period_bounds(period)
            self.assertIsInstance(start, int)
            self.assertIsInstance(end, int)
            self.assertLess(start, end)

        with self.assertRaises(ValueError):
            api.KomecoApiClient._period_bounds("decade")


if __name__ == "__main__":
    unittest.main()
