import sys
import unittest

from offline_ecu import EdiabasError, FixtureECU, load_diag


class SensorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.diag = load_diag()

    def test_raw_values_and_missing_data_are_preserved(self):
        key = ("SYNTHETIC", "STATUS_TEST", "VALUE")
        for value in (0, -5.25, 1234.5, None):
            with self.subTest(value=value):
                ecu = FixtureECU({key: value})
                self.assertEqual(self.diag.read_sensor(ecu, *key), value)
                self.assertEqual(ecu.calls, [key])

    def test_backend_error_becomes_missing_data(self):
        key = ("SYNTHETIC", "STATUS_TEST", "VALUE")
        ecu = FixtureECU({key: EdiabasError("synthetic unavailable")})
        self.assertIsNone(self.diag.read_sensor(ecu, *key))

    def test_unknown_read_and_unexpected_error_fail_loudly(self):
        key = ("SYNTHETIC", "STATUS_TEST", "VALUE")
        with self.assertRaises(AssertionError):
            self.diag.read_sensor(FixtureECU({}), *key)
        with self.assertRaises(ValueError):
            self.diag.read_sensor(FixtureECU({key: ValueError("bad fixture")}), *key)

    def test_hardware_and_arbitrary_jobs_are_blocked(self):
        with self.assertRaises(AssertionError):
            self.diag.Ediabas()
        with self.assertRaises(AssertionError):
            FixtureECU({}).run_job("SYNTHETIC", "CLEAR")

    def test_import_stub_does_not_leak(self):
        before = sys.modules.get("ediabas")
        load_diag()
        self.assertIs(sys.modules.get("ediabas"), before)

    def test_every_sensor_routes_its_exact_job_and_result(self):
        keys = [("SYNTHETIC", job, result) for job, result, _, _ in self.diag.ALL_SENSORS]
        self.assertTrue(keys)
        self.assertEqual(len(keys), len(set(keys)))
        ecu = FixtureECU({key: index for index, key in enumerate(keys)})
        for index, key in enumerate(keys):
            self.assertEqual(self.diag.read_sensor(ecu, *key), index)
        self.assertEqual(ecu.calls, keys)


if __name__ == "__main__":
    unittest.main()
