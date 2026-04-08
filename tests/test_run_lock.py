import logging
import threading
import types
import unittest
from datetime import datetime

from tests.support import isolated_module_import


class RunLockTests(unittest.TestCase):
    def _build_config_stub(self):
        logger = logging.getLogger("test-run-lock")
        return types.SimpleNamespace(
            config_lock=threading.Lock(),
            BOT_START_TIME=datetime.now(),
            BOT_VERSION="0.0.0-test",
            CLEAN_TIMES=["03:00"],
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/discord-cleanup-health-test",
            LOG_DIR="/tmp",
            LOG_MAX_FILES=7,
            LOG_LEVEL="INFO",
            numeric_level=logging.INFO,
            formatter=logging.Formatter("%(message)s"),
            logger=logger,
            log=logger,
        )

    def _build_config_utils_stub(self):
        def _ok(*args, **kwargs):
            return True, "ok"

        return types.SimpleNamespace(
            reload_channels=lambda: (True, "ok"),
            update_env_value=_ok,
            update_retention=_ok,
            update_log_level=_ok,
            update_warn_unconfigured=_ok,
            update_report_frequency=_ok,
            update_log_max_files=_ok,
        )

    def _build_scheduler_stub(self):
        return types.SimpleNamespace(
            get_next_run_str=lambda *args, **kwargs: "2026-01-01 03:00 AM",
            update_schedule=lambda times: (True, ",".join(times), None),
        )

    def test_only_one_run_can_be_acquired_at_a_time(self):
        stubs = {
            "config": self._build_config_stub(),
            "config_utils": self._build_config_utils_stub(),
            "scheduler": self._build_scheduler_stub(),
        }

        with isolated_module_import("utils", stubs) as utils:
            self.assertTrue(utils.try_acquire_run("scheduler"))
            self.assertTrue(utils.is_run_in_progress())
            self.assertEqual(utils.get_run_owner(), "scheduler")

            self.assertFalse(utils.try_acquire_run("web ui"))
            self.assertEqual(utils.get_run_owner(), "scheduler")

            utils.release_run()
            self.assertFalse(utils.is_run_in_progress())
            self.assertIsNone(utils.get_run_owner())
            self.assertTrue(utils.try_acquire_run("web ui"))
            self.assertEqual(utils.get_run_owner(), "web ui")
            utils.release_run()


if __name__ == "__main__":
    unittest.main()
