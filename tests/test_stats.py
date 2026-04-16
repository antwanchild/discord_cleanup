import json
import logging
import os
import tempfile
import types
import unittest
from datetime import datetime

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

    def test_load_stats_normalizes_partial_and_legacy_shapes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": {
                            "runs": "4",
                            "deleted": "9",
                            "channels": {
                                "123": 5,
                                456: {"name": "build-bot", "count": "7"},
                            },
                        },
                        "rolling_30": {"reset": "not-a-date"},
                        "monthly": {"catchup_runs": "3", "channels": []},
                        "last_month": {"deleted": "12"},
                    },
                    f,
                )

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                payload = stats.load_stats(strict=True)

            self.assertEqual(payload["all_time"]["runs"], 4)
            self.assertEqual(payload["all_time"]["deleted"], 9)
            self.assertEqual(payload["all_time"]["catchup_runs"], 0)
            self.assertEqual(payload["all_time"]["channels"]["123"]["count"], 5)
            self.assertEqual(payload["all_time"]["channels"]["123"]["category"], "Standalone")
            self.assertEqual(payload["all_time"]["channels"]["456"]["name"], "build-bot")
            self.assertEqual(payload["all_time"]["channels"]["456"]["count"], 7)
            self.assertIn("reset", payload["rolling_30"])
            self.assertEqual(payload["monthly"]["catchup_runs"], 3)
            self.assertEqual(payload["monthly"]["channels"], {})
            self.assertEqual(payload["last_month"]["deleted"], 12)
            self.assertIn("reset", payload["last_month"])

    def test_load_last_run_normalizes_missing_fields(self):
        with tempfile.TemporaryDirectory() as tempdir:
            last_run_path = os.path.join(tempdir, "last_run.json")
            with open(last_run_path, "w") as f:
                json.dump(
                    {
                        "timestamp": "2026-04-15 05:45:00",
                        "total_deleted": "22",
                        "categories": [{"name": "Github", "count": "18"}, "bad-item"],
                    },
                    f,
                )

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                payload = stats.load_last_run()

            self.assertEqual(payload["timestamp"], "2026-04-15 05:45:00")
            self.assertEqual(payload["total_deleted"], 22)
            self.assertEqual(payload["triggered_by"], "unknown")
            self.assertEqual(payload["channels_checked"], 0)
            self.assertEqual(payload["categories"], [{"name": "Github", "count": 18}])

    def test_load_last_run_preserves_category_object_shape(self):
        with tempfile.TemporaryDirectory() as tempdir:
            last_run_path = os.path.join(tempdir, "last_run.json")
            with open(last_run_path, "w") as f:
                json.dump(
                    {
                        "categories": [
                            {"name": "general", "count": 12},
                            {"name": "#build-bot", "count": 7},
                        ]
                    },
                    f,
                )

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                payload = stats.load_last_run()

            self.assertEqual(
                payload["categories"],
                [
                    {"name": "general", "count": 12},
                    {"name": "#build-bot", "count": 7},
                ],
            )

    def test_save_stats_creates_backup_when_replacing_existing_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                json.dump({"all_time": {"runs": 1}}, f)

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                stats.save_stats(stats._empty_stats())

            backups_dir = os.path.join(tempdir, "backups", "stats")
            backups = os.listdir(backups_dir)
            self.assertEqual(len(backups), 1)
            backup_path = os.path.join(backups_dir, backups[0])
            with open(backup_path, "r") as f:
                self.assertIn('"runs": 1', f.read())

    def test_save_stats_prunes_old_backups(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            backups_dir = os.path.join(tempdir, "backups", "stats")
            os.makedirs(backups_dir, exist_ok=True)

            with open(stats_path, "w") as f:
                json.dump({"all_time": {"runs": 1}}, f)

            old_backup = os.path.join(backups_dir, "stats-20260101-000000.json.bak")
            recent_backup = os.path.join(backups_dir, "stats-20260407-000000.json.bak")
            with open(old_backup, "w") as f:
                f.write("{}")
            with open(recent_backup, "w") as f:
                f.write("{}")

            now = datetime.now().timestamp()
            old_time = 60 * 60 * 24 * 11
            recent_time = 60 * 60 * 24 * 2
            os.utime(old_backup, (now - old_time, now - old_time))
            os.utime(recent_backup, (now - recent_time, now - recent_time))

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                stats.save_stats(stats._empty_stats())

            self.assertFalse(os.path.exists(old_backup))
            self.assertTrue(os.path.exists(recent_backup))

    def test_load_stats_strict_error_mentions_latest_backup(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            backups_dir = os.path.join(tempdir, "backups", "stats")
            os.makedirs(backups_dir, exist_ok=True)
            backup_path = os.path.join(backups_dir, "stats-20260415-054500.json.bak")

            with open(stats_path, "w") as f:
                f.write("{bad json")
            with open(backup_path, "w") as f:
                f.write("{}")

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                with self.assertRaises(stats.StatsLoadError) as ctx:
                    stats.load_stats(strict=True)

            self.assertIn("Latest backup", str(ctx.exception))
            self.assertIn(backup_path, str(ctx.exception))

    def test_save_last_run_creates_backup_when_replacing_existing_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            last_run_path = os.path.join(tempdir, "last_run.json")
            with open(last_run_path, "w") as f:
                json.dump({"timestamp": "old", "total_deleted": 4}, f)

            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                stats.save_last_run({"timestamp": "new", "total_deleted": 8})

            backups_dir = os.path.join(tempdir, "backups", "last-run")
            backups = [name for name in os.listdir(backups_dir) if name.startswith("last-run-")]
            self.assertEqual(len(backups), 1)
            with open(os.path.join(backups_dir, backups[0]), "r") as f:
                self.assertIn('"total_deleted": 4', f.read())

    def test_record_channel_history_persists_channel_runs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            with isolated_module_import("stats", {"config": self._config_stub(tempdir)}) as stats:
                stats.record_channel_history(
                    {
                        "123": {
                            "name": "build-bot",
                            "count": 5,
                            "category": "Github",
                            "rate_limits": 2,
                            "oldest": "2026-04-15 05:45:00",
                            "status": "deleted",
                        }
                    },
                    run_context={
                        "timestamp": "2026-04-15 05:45:00",
                        "triggered_by": "scheduler",
                        "dry_run": False,
                    },
                )
                payload = stats.load_stats(strict=True)

            history = payload["channel_history"]["123"][0]
            self.assertEqual(history["timestamp"], "2026-04-15 05:45:00")
            self.assertEqual(history["count"], 5)
            self.assertEqual(history["category"], "Github")
            self.assertEqual(history["triggered_by"], "scheduler")
            self.assertEqual(history["rate_limits"], 2)


if __name__ == "__main__":
    unittest.main()
