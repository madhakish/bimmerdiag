"""Offline display tests: python -m unittest discover -s tests -v."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


# Isolate the transport dependency so these tests never load a DLL, Mono,
# or an ECU connection, even when the platform backend changes.
BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
transport = ModuleType("ediabas")
transport.Ediabas = Mock(side_effect=AssertionError("Formatting tests must stay offline"))
transport.EdiabasError = Exception
with patch.dict(sys.modules, {"ediabas": transport}):
    spec = importlib.util.spec_from_file_location("diag", BIN_DIR / "diag.py")
    diag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diag)


class FormatValueTests(unittest.TestCase):
    def test_hpa_displays_bar(self):
        cases = (
            (0, "0 hPa (0.00 bar)"),
            (1000, "1000 hPa (1.00 bar)"),
            (1013.25, "1013 hPa (1.01 bar)"),
            (2000, "2000 hPa (2.00 bar)"),
            (1234.56, "1235 hPa (1.23 bar)"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(diag.format_value(value, "hPa"), expected)

    def test_missing_values(self):
        for unit in ("hPa", "mV", "km", "rpm", "bar", "kg/h", "mg/hub",
                     "degC", "°C", "s", "%", "unknown"):
            with self.subTest(unit=unit):
                self.assertEqual(diag.format_value(None, unit),
                                 f"{diag.DIM}(no data){diag.RESET}")

    def test_other_numeric_formats_are_unchanged(self):
        cases = (
            (14940, "mV", "14.94 V"),
            (10000, "km", "10,000 km (6,214 mi)"),
            (697.25, "rpm", "697.2 rpm"),
            (311.25, "bar", "311.2 bar"),
            (21.95, "kg/h", "21.9 kg/h"),
            (390.25, "mg/hub", "390.2 mg/stroke"),
            (-10.25, "degC", "-10.2 °C"),
            (80.25, "°C", "80.2 °C"),
            (5400, "s", "1.5 hours (5400 s)"),
            (85.25, "%", "85%"),
            (1.234, "unknown", "1.23 unknown"),
        )
        for value, unit, expected in cases:
            with self.subTest(value=value, unit=unit):
                self.assertEqual(diag.format_value(value, unit), expected)


if __name__ == "__main__":
    unittest.main()
