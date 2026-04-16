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


if __name__ == "__main__":
    unittest.main()
