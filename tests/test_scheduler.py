import logging
import os
import tempfile
import threading
import types
import unittest

from tests.support import isolated_module_import


class SchedulerTests(unittest.TestCase):
    def _build_config_stub(self, config_dir: str):
        return types.SimpleNamespace(
            config_lock=threading.Lock(),
            CONFIG_DIR=config_dir,
            CLEAN_TIMES=["03:00"],
            log=logging.getLogger("test-scheduler"),
        )

    def test_update_schedule_rewrites_env_and_in_memory_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = os.path.join(tempdir, ".env.discord_cleanup")
            with open(env_path, "w") as f:
                f.write("CLEAN_TIME=03:00\nLOG_LEVEL=INFO\n")

            config_stub = self._build_config_stub(tempdir)
            with isolated_module_import("scheduler", {"config": config_stub}) as scheduler:
                success, message, reschedule_error = scheduler.update_schedule(["05:00", "23:15"])

            self.assertTrue(success)
            self.assertEqual(message, "05:00,23:15")
            self.assertIsNone(reschedule_error)
            self.assertEqual(config_stub.CLEAN_TIMES, ["05:00", "23:15"])
            with open(env_path, "r") as f:
                self.assertIn("CLEAN_TIME=05:00,23:15", f.read())

    def test_update_schedule_rejects_invalid_times_without_writing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = os.path.join(tempdir, ".env.discord_cleanup")
            original = "CLEAN_TIME=03:00\n"
            with open(env_path, "w") as f:
                f.write(original)

            config_stub = self._build_config_stub(tempdir)
            with isolated_module_import("scheduler", {"config": config_stub}) as scheduler:
                success, message, reschedule_error = scheduler.update_schedule(["25:00"])

            self.assertFalse(success)
            self.assertIn("is not a valid time", message)
            self.assertIsNone(reschedule_error)
            self.assertEqual(config_stub.CLEAN_TIMES, ["03:00"])
            with open(env_path, "r") as f:
                self.assertEqual(f.read(), original)


if __name__ == "__main__":
    unittest.main()
