import logging
import os
import tempfile
import threading
import types
import unittest
from datetime import datetime

from tests.support import isolated_module_import


class ConfigUtilsTests(unittest.TestCase):
    def _build_config_stub(self, config_dir: str):
        return types.SimpleNamespace(
            config_lock=threading.Lock(),
            CONFIG_DIR=config_dir,
            CHANNELS_BACKUP_RETENTION_DAYS=10,
            STATS_BACKUP_RETENTION_DAYS=10,
            CLEAN_TIMES=["03:00"],
            DEFAULT_RETENTION=7,
            LOG_MAX_FILES=7,
            REPORT_FREQUENCY="monthly",
            REPORT_GROUP_MONTHLY=True,
            REPORT_GROUP_WEEKLY=True,
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            WARN_UNCONFIGURED=False,
            CATCHUP_MISSED_RUNS=True,
            LOG_LEVEL="INFO",
            TOKEN=None,
            LOG_CHANNEL_ID=None,
            REPORT_CHANNEL_ID=None,
            GITHUB_TOKEN=None,
            log=logging.getLogger("test-config-utils"),
            raw_channels=[],
        )

    def test_preview_channels_content_reports_added_removed_and_updated_entries(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_stub = self._build_config_stub(tempdir)
            config_stub.raw_channels = [
                {"id": 1, "name": "old-channel"},
                {"id": 2, "name": "keep-channel", "days": 7},
            ]
            content = (
                "channels:\n"
                "  - id: 2\n"
                "    name: keep-renamed\n"
                "    days: 14\n"
                "  - id: 3\n"
                "    name: new-channel\n"
                "    deep_clean: true\n"
            )

            with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                success, message, preview = config_utils.preview_channels_content(content)

            self.assertTrue(success)
            self.assertIn("preview ready", message)
            self.assertEqual(preview["summary"]["counts"]["added"], 1)
            self.assertEqual(preview["summary"]["counts"]["removed"], 1)
            self.assertEqual(preview["summary"]["counts"]["updated"], 1)
            self.assertEqual(preview["changes"]["added"][0]["name"], "new-channel")
            self.assertEqual(preview["changes"]["removed"][0]["name"], "old-channel")
            self.assertEqual(preview["changes"]["updated"][0]["label"], "#keep-renamed")
            changed_fields = {item["field"] for item in preview["changes"]["updated"][0]["changes"]}
            self.assertEqual(changed_fields, {"name", "days"})

    def test_preview_channel_restore_reports_diff_from_backup(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backups_dir = os.path.join(tempdir, "backups", "channels")
            os.makedirs(backups_dir, exist_ok=True)

            current_path = os.path.join(tempdir, "channels.yml")
            with open(current_path, "w") as f:
                f.write("channels:\n  - id: 1\n    name: old-channel\n")

            backup_path = os.path.join(backups_dir, "channels-20260415-054500.yml.bak")
            with open(backup_path, "w") as f:
                f.write(
                    "channels:\n"
                    "  - id: 1\n"
                    "    name: renamed-channel\n"
                    "  - id: 2\n"
                    "    name: new-channel\n"
                )

            config_stub = self._build_config_stub(tempdir)
            config_stub.raw_channels = [{"id": 1, "name": "old-channel"}]

            with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                success, message, preview = config_utils.preview_channel_restore("channels-20260415-054500.yml.bak")

            self.assertTrue(success)
            self.assertIn("Restore preview ready", message)
            self.assertEqual(preview["backup"]["filename"], "channels-20260415-054500.yml.bak")
            self.assertEqual(preview["summary"]["counts"]["added"], 1)
            self.assertEqual(preview["summary"]["counts"]["updated"], 1)
            self.assertEqual(preview["changes"]["added"][0]["name"], "new-channel")

    def test_update_report_grouping_updates_env_and_in_memory_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = os.path.join(tempdir, ".env.discord_cleanup")
            with open(env_path, "w") as f:
                f.write("REPORT_GROUP_MONTHLY=true\nREPORT_GROUP_WEEKLY=true\n")

            config_stub = self._build_config_stub(tempdir)
            config_stub.REPORT_GROUP_MONTHLY = True
            config_stub.REPORT_GROUP_WEEKLY = True

            with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                success, message = config_utils.update_report_grouping("monthly", False)

            self.assertTrue(success)
            self.assertEqual(message, "false")
            self.assertFalse(config_stub.REPORT_GROUP_MONTHLY)
            with open(env_path, "r") as f:
                self.assertIn("REPORT_GROUP_MONTHLY=false", f.read())

    def test_update_schedule_skip_dates_and_weekdays_updates_env_and_in_memory_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = os.path.join(tempdir, ".env.discord_cleanup")
            with open(env_path, "w") as f:
                f.write("SCHEDULE_SKIP_DATES=\nSCHEDULE_SKIP_WEEKDAYS=\n")

            config_stub = self._build_config_stub(tempdir)
            with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                success_dates, message_dates = config_utils.update_schedule_skip_dates(["2026-04-20", "2026-04-22"])
                success_weekdays, message_weekdays = config_utils.update_schedule_skip_weekdays(["Mon", "Fri"])

            self.assertTrue(success_dates)
            self.assertEqual(message_dates, "2026-04-20,2026-04-22")
            self.assertTrue(success_weekdays)
            self.assertEqual(message_weekdays, "mon,fri")
            self.assertEqual(config_stub.SCHEDULE_SKIP_DATES, ["2026-04-20", "2026-04-22"])
            self.assertEqual(config_stub.SCHEDULE_SKIP_WEEKDAYS, ["mon", "fri"])
            with open(env_path, "r") as f:
                env_text = f.read()
            self.assertIn("SCHEDULE_SKIP_DATES=2026-04-20,2026-04-22", env_text)
            self.assertIn("SCHEDULE_SKIP_WEEKDAYS=mon,fri", env_text)

    def test_reload_channels_returns_validation_error(self):
        with tempfile.TemporaryDirectory() as tempdir:
            with open(os.path.join(tempdir, "channels.yml"), "w") as f:
                f.write("channels:\n  - name: missing-id\n")

            config_stub = self._build_config_stub(tempdir)
            with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                success, message = config_utils.reload_channels()

            self.assertFalse(success)
            self.assertIn("channels.yml validation failed", message)
            self.assertIn("missing required key 'id'", message)

    def test_save_channels_content_creates_backup_and_updates_raw_channels(self):
        with tempfile.TemporaryDirectory() as tempdir:
            channels_path = os.path.join(tempdir, "channels.yml")
            with open(channels_path, "w") as f:
                f.write("channels:\n  - id: 123\n    name: old-name\n")

            config_stub = self._build_config_stub(tempdir)
            new_content = "channels:\n  - id: 456\n    name: new-name\n"

            with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                success, message, backup_path = config_utils.save_channels_content(new_content)

            self.assertTrue(success)
            self.assertIn("Saved and reloaded channels.yml", message)
            self.assertIsNotNone(backup_path)
            self.assertTrue(os.path.exists(backup_path))
            self.assertEqual(config_stub.raw_channels, [{"id": 456, "name": "new-name"}])
            with open(channels_path, "r") as f:
                self.assertEqual(f.read(), new_content)
            with open(backup_path, "r") as f:
                self.assertIn("old-name", f.read())

    def test_save_channels_content_prunes_old_backups(self):
        with tempfile.TemporaryDirectory() as tempdir:
            channels_path = os.path.join(tempdir, "channels.yml")
            backups_dir = os.path.join(tempdir, "backups", "channels")
            os.makedirs(backups_dir, exist_ok=True)

            with open(channels_path, "w") as f:
                f.write("channels:\n  - id: 123\n    name: old-name\n")

            old_backup = os.path.join(backups_dir, "channels-20260101-000000.yml.bak")
            recent_backup = os.path.join(backups_dir, "channels-20260407-000000.yml.bak")
            with open(old_backup, "w") as f:
                f.write("old backup")
            with open(recent_backup, "w") as f:
                f.write("recent backup")

            now = datetime.now().timestamp()
            old_time = 60 * 60 * 24 * 11
            recent_time = 60 * 60 * 24 * 2
            os.utime(old_backup, (now - old_time, now - old_time))
            os.utime(recent_backup, (now - recent_time, now - recent_time))

            config_stub = self._build_config_stub(tempdir)
            new_content = "channels:\n  - id: 456\n    name: new-name\n"

            with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                success, message, backup_path = config_utils.save_channels_content(new_content)

            self.assertTrue(success)
            self.assertIn("Saved and reloaded channels.yml", message)
            self.assertFalse(os.path.exists(old_backup))
            self.assertTrue(os.path.exists(recent_backup))
            self.assertTrue(os.path.exists(backup_path))

    def test_restore_channels_backup_restores_current_file_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as tempdir:
            channels_path = os.path.join(tempdir, "channels.yml")
            backups_dir = os.path.join(tempdir, "backups", "channels")
            os.makedirs(backups_dir, exist_ok=True)

            current_content = "channels:\n  - id: 123\n    name: current-channel\n"
            backup_content = "channels:\n  - id: 456\n    name: restored-channel\n"
            with open(channels_path, "w") as f:
                f.write(current_content)

            backup_file = os.path.join(backups_dir, "channels-20260415-054500.yml.bak")
            with open(backup_file, "w") as f:
                f.write(backup_content)

            config_stub = self._build_config_stub(tempdir)
            config_stub.raw_channels = [{"id": 123, "name": "current-channel"}]

            with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                success, message, backup_path = config_utils.restore_channels_backup("channels-20260415-054500.yml.bak")

            self.assertTrue(success)
            self.assertIn("Restored channels.yml from channels-20260415-054500.yml.bak", message)
            self.assertIsNotNone(backup_path)
            self.assertTrue(os.path.exists(backup_path))
            with open(channels_path, "r") as f:
                self.assertEqual(f.read(), backup_content)
            with open(backup_path, "r") as f:
                self.assertEqual(f.read(), current_content)
            self.assertEqual(config_stub.raw_channels, [{"id": 456, "name": "restored-channel"}])

    def test_preview_env_restore_reports_diff_and_restart_requirement(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backups_dir = os.path.join(tempdir, "backups", "env")
            os.makedirs(backups_dir, exist_ok=True)

            env_path = os.path.join(tempdir, ".env.discord_cleanup")
            with open(env_path, "w") as f:
                f.write(
                    "DISCORD_TOKEN=oldtoken123\n"
                    "REPORT_GROUP_WEEKLY=true\n"
                    "WEB_HOST=0.0.0.0\n"
                    "WARN_UNCONFIGURED=false\n"
                )

            backup_path = os.path.join(backups_dir, "env-20260415-054500.env.bak")
            with open(backup_path, "w") as f:
                f.write(
                    "DISCORD_TOKEN=newtoken456\n"
                    "REPORT_GROUP_WEEKLY=false\n"
                    "WEB_HOST=127.0.0.1\n"
                    "GITHUB_TOKEN=ghp_secret123\n"
                )

            config_stub = self._build_config_stub(tempdir)
            original_env = {key: os.environ.get(key) for key in ["DISCORD_TOKEN", "REPORT_GROUP_WEEKLY", "WEB_HOST", "GITHUB_TOKEN", "WARN_UNCONFIGURED"]}

            try:
                with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                    success, message, preview = config_utils.preview_env_restore("env-20260415-054500.env.bak")

                self.assertTrue(success)
                self.assertIn("Restore preview ready", message)
                self.assertEqual(preview["backup"]["filename"], "env-20260415-054500.env.bak")
                self.assertEqual(preview["counts"]["added"], 1)
                self.assertEqual(preview["counts"]["removed"], 1)
                self.assertEqual(preview["counts"]["updated"], 3)
                self.assertTrue(preview["restores"]["restart_required"])
                self.assertIn("WEB_HOST", preview["restores"]["startup_only_changed"])
            finally:
                for key, value in original_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_restore_env_backup_restores_current_file_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = os.path.join(tempdir, ".env.discord_cleanup")
            backups_dir = os.path.join(tempdir, "backups", "env")
            os.makedirs(backups_dir, exist_ok=True)

            current_content = (
                "DISCORD_TOKEN=oldtoken123\n"
                "REPORT_GROUP_WEEKLY=true\n"
                "WEB_HOST=0.0.0.0\n"
                "WARN_UNCONFIGURED=false\n"
            )
            backup_content = (
                "DISCORD_TOKEN=newtoken456\n"
                "REPORT_GROUP_WEEKLY=false\n"
                "WEB_HOST=127.0.0.1\n"
                "GITHUB_TOKEN=ghp_secret123\n"
            )
            with open(env_path, "w") as f:
                f.write(current_content)

            backup_file = os.path.join(backups_dir, "env-20260415-054500.env.bak")
            with open(backup_file, "w") as f:
                f.write(backup_content)

            config_stub = self._build_config_stub(tempdir)
            original_env = {key: os.environ.get(key) for key in ["DISCORD_TOKEN", "REPORT_GROUP_WEEKLY", "WEB_HOST", "GITHUB_TOKEN", "WARN_UNCONFIGURED"]}

            try:
                with isolated_module_import("config_utils", {"config": config_stub}) as config_utils:
                    success, message, backup_path = config_utils.restore_env_backup("env-20260415-054500.env.bak")

                self.assertTrue(success)
                self.assertIn("Restored .env.discord_cleanup from env-20260415-054500.env.bak", message)
                self.assertIsNotNone(backup_path)
                self.assertTrue(os.path.exists(backup_path))
                with open(env_path, "r") as f:
                    self.assertEqual(f.read(), backup_content)
                with open(backup_path, "r") as f:
                    self.assertEqual(f.read(), current_content)
                self.assertEqual(os.getenv("REPORT_GROUP_WEEKLY"), "false")
                self.assertEqual(os.getenv("WEB_HOST"), "127.0.0.1")
            finally:
                for key, value in original_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
