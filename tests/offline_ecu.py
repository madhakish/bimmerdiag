"""Synthetic read fixtures. No transport imports, fallback or job execution."""
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch


class EdiabasError(Exception):
    pass


class NoHardware:
    def __init__(self, *args, **kwargs):
        raise AssertionError("Hardware construction is forbidden in offline tests")


class FixtureECU:
    def __init__(self, values):
        self.values = dict(values)
        self.calls = []

    def read_value(self, sgbd, job, result_name):
        key = (sgbd, job, result_name)
        self.calls.append(key)
        if key not in self.values:
            raise AssertionError(f"Unconfigured synthetic sensor: {key!r}")
        value = self.values[key]
        if isinstance(value, Exception):
            raise value
        return value

    def run_job(self, *args, **kwargs):
        raise AssertionError("ECU jobs are forbidden in offline read fixtures")


def load_diag():
    """Load actual CLI definitions with a temporary stub; never execute main()."""
    stub = types.ModuleType("ediabas")
    stub.Ediabas = NoHardware
    stub.EdiabasError = EdiabasError
    path = Path(__file__).resolve().parents[1] / "bin" / "diag.py"
    spec = importlib.util.spec_from_file_location("_offline_diag", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"ediabas": stub}):
        spec.loader.exec_module(module)
    return module
