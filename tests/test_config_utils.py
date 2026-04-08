import logging
import os
import tempfile
import threading
import types
import unittest

from tests.support import isolated_module_import


class ConfigUtilsTests(unittest.TestCase):
    def _build_config_stub(self, config_dir: str):
        return types.SimpleNamespace(
            config_lock=threading.Lock(),
            CONFIG_DIR=config_dir,
            log=logging.getLogger("test-config-utils"),
            raw_channels=[],
        )

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


if __name__ == "__main__":
    unittest.main()
