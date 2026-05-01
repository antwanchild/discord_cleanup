import os
import runpy
import unittest
from datetime import datetime, timedelta


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.health_path = "/tmp/health"
        self.original = None
        if os.path.exists(self.health_path):
            with open(self.health_path, "r") as f:
                self.original = f.read()

    def tearDown(self):
        if self.original is None:
            try:
                os.remove(self.health_path)
            except FileNotFoundError:
                pass
        else:
            with open(self.health_path, "w") as f:
                f.write(self.original)

    def test_healthcheck_exits_zero_when_timestamp_is_recent(self):
        with open(self.health_path, "w") as f:
            f.write(datetime.now().isoformat())

        with self.assertRaises(SystemExit) as ctx:
            runpy.run_path("healthcheck.py", run_name="__main__")

        self.assertEqual(ctx.exception.code, 0)

    def test_healthcheck_exits_one_when_timestamp_is_stale(self):
        with open(self.health_path, "w") as f:
            f.write((datetime.now() - timedelta(minutes=10)).isoformat())

        with self.assertRaises(SystemExit) as ctx:
            runpy.run_path("healthcheck.py", run_name="__main__")

        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
