import json
import os
import tempfile
import unittest
from datetime import datetime

from tests.support import isolated_module_import

from tests.stats_support import StatsConfigMixin


class StatsPersistenceTests(StatsConfigMixin, unittest.TestCase):
    def test_save_stats_creates_backup_when_replacing_existing_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                json.dump({"all_time": {"runs": 1}}, f)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
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

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
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

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                with self.assertRaises(stats.StatsLoadError) as ctx:
                    stats.load_stats(strict=True)

            self.assertIn("Latest backup", str(ctx.exception))
            self.assertIn(backup_path, str(ctx.exception))

    def test_save_last_run_creates_backup_when_replacing_existing_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            last_run_path = os.path.join(tempdir, "last_run.json")
            with open(last_run_path, "w") as f:
                json.dump({"timestamp": "old", "total_deleted": 4}, f)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                stats.save_last_run({"timestamp": "new", "total_deleted": 8})

            backups_dir = os.path.join(tempdir, "backups", "last-run")
            backups = [
                name for name in os.listdir(backups_dir) if name.startswith("last-run-")
            ]
            self.assertEqual(len(backups), 1)
            with open(os.path.join(backups_dir, backups[0]), "r") as f:
                self.assertIn('"total_deleted": 4', f.read())

    def test_record_channel_history_persists_channel_runs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
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
