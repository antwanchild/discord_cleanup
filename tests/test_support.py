import logging
import sys
import threading
import types
import unittest

from tests.support import isolated_module_import


class ImportIsolationTests(unittest.TestCase):
    def test_config_dependencies_use_current_lock_and_restore_cached_modules(self):
        names = (
            "config",
            "config_utils",
            "config_backups",
            "config_channels",
            "config_settings",
        )
        originals = {name: sys.modules.get(name) for name in names}
        first = types.SimpleNamespace(
            config_lock=threading.Lock(), log=logging.getLogger("first")
        )
        second = types.SimpleNamespace(
            config_lock=threading.Lock(), log=logging.getLogger("second")
        )
        with isolated_module_import("config_utils", {"config": first}) as outer:
            outer_backups = sys.modules["config_backups"]
            self.assertIs(sys.modules["config_channels"].config_lock, first.config_lock)
            with isolated_module_import("config_utils", {"config": second}):
                self.assertIs(
                    sys.modules["config_channels"].config_lock, second.config_lock
                )
                self.assertIs(
                    sys.modules["config_backups"].config_lock, second.config_lock
                )
            self.assertIs(sys.modules["config_utils"], outer)
            self.assertIs(sys.modules["config_backups"], outer_backups)
        for name in names:
            self.assertIs(sys.modules.get(name), originals[name])

    def test_failed_import_restores_modules_and_removes_partial_dependencies(self):
        originals = {
            name: sys.modules.get(name)
            for name in ("config", "config_channels", "config_backups")
        }
        with self.assertRaises(ImportError):
            with isolated_module_import(
                "config_channels", {"config": types.SimpleNamespace()}
            ):
                self.fail("Import should fail without config_lock")
        for name, original in originals.items():
            self.assertIs(sys.modules.get(name), original)
