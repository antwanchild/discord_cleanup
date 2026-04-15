import json
import logging
import os
import tempfile
import types
import unittest

from tests.support import isolated_module_import


class StatsTests(unittest.TestCase):
    def _config_stub(self, tempdir: str):
        return types.SimpleNamespace(
            DATA_DIR=tempdir,
            STATS_FILE=os.path.join(tempdir, "stats.json"),
            log=logging.getLogger("test-stats"),
        )

    def test_load_stats_strict_raises_for_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                f.write("{bad json")

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                with self.assertRaises(stats.StatsLoadError):
                    stats.load_stats(strict=True)

    def test_update_stats_does_not_overwrite_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            original = "{bad json"
            with open(stats_path, "w") as f:
                f.write(original)

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                stats.update_stats({"123": {"name": "build-bot", "count": 5, "category": "Github"}})

            with open(stats_path, "r") as f:
                self.assertEqual(f.read(), original)

    def test_reset_stats_returns_false_when_file_is_unreadable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                f.write("{bad json")

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                self.assertFalse(stats.reset_stats("all"))

            with open(stats_path, "r") as f:
                self.assertEqual(f.read(), "{bad json")


if __name__ == "__main__":
    unittest.main()
