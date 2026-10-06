import asyncio
import logging
import types
import unittest
from unittest.mock import patch

from flask import Flask

from tests.support import isolated_module_import


class AdminTests(unittest.TestCase):
    def test_admin_restore_routes_expose_backup_preview_and_restore(self):
        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CLEAN_TIMES=["03:00"],
            DEFAULT_RETENTION=7,
            DATA_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_CHANNEL_ID=1,
            LOG_DIR="/tmp",
            LOG_MAX_FILES=7,
            LOG_LEVEL="INFO",
            RETRY_DELAY=300,
            STATS_FILE="/tmp/stats.json",
            WARN_UNCONFIGURED=False,
            config_lock=None,
            log=logging.getLogger("test-admin"),
            raw_channels=[],
        )
        utils_stub = types.SimpleNamespace(
            get_bot=lambda: None,
            get_bot_loop=lambda: None,
            get_next_run_str=lambda: "tomorrow",
            setup_run_log=lambda *_a, **_k: None,
            update_health=lambda *_a, **_k: None,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        config_utils_stub = types.SimpleNamespace(
            preview_channel_restore=lambda filename: (
                True,
                "Restore preview ready — channels-1.yml.bak",
                {
                    "summary": {
                        "current": {
                            "entries": 1,
                            "with_notification_groups": 0,
                            "excluded": 0,
                            "deep_clean": 0,
                        },
                        "proposed": {
                            "entries": 2,
                            "with_notification_groups": 1,
                            "excluded": 0,
                            "deep_clean": 1,
                        },
                        "delta": {
                            "entries": 1,
                            "categories": 0,
                            "excluded": 0,
                            "deep_clean": 1,
                            "with_notification_groups": 1,
                        },
                        "counts": {
                            "added": 1,
                            "removed": 0,
                            "updated": 0,
                            "field_changes": 0,
                        },
                    },
                    "changes": {
                        "added": [{"id": 2, "name": "new-channel"}],
                        "removed": [],
                        "updated": [],
                    },
                    "backup": {
                        "filename": filename,
                        "modified": "2026-04-15 05:45:00",
                        "size_bytes": 99,
                        "path": "/config/backups/channels-1.yml.bak",
                        "type": "channels",
                    },
                    "parsed_channels": [{"id": 1, "name": "old-channel"}],
                },
            ),
            preview_env_restore=lambda filename: (
                True,
                "Restore preview ready — env-1.env.bak",
                {
                    "summary": {
                        "current": {"keys": 4, "sensitive": 1},
                        "proposed": {"keys": 5, "sensitive": 2},
                        "delta": {"keys": 1, "sensitive": 1},
                        "counts": {
                            "added": 1,
                            "removed": 1,
                            "updated": 1,
                            "field_changes": 0,
                            "sensitive_updates": 1,
                        },
                    },
                    "added": [{"key": "GITHUB_TOKEN", "value": "gh***23"}],
                    "removed": [{"key": "WARN_UNCONFIGURED", "value": "false"}],
                    "updated": [
                        {"key": "WEB_HOST", "before": "0.0.0.0", "after": "127.0.0.1"}
                    ],
                    "backup": {
                        "filename": filename,
                        "modified": "2026-04-15 05:45:00",
                        "size_bytes": 88,
                        "path": "/config/backups/env-1.env.bak",
                        "type": "env",
                    },
                    "restores": {
                        "startup_only_changed": ["WEB_HOST"],
                        "restart_required": True,
                    },
                },
            ),
            update_schedule_skip_dates=lambda dates: (True, ",".join(dates)),
            update_schedule_skip_weekdays=lambda weekdays: (True, ",".join(weekdays)),
            restore_channels_backup=lambda filename: (
                True,
                f"Restored channels.yml from {filename}",
                "/config/backups/channels-restore.yml.bak",
            ),
            restore_env_backup=lambda filename: (
                True,
                f"Restored .env.discord_cleanup from {filename}",
                "/config/backups/env-restore.env.bak",
            ),
            preview_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            save_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            update_report_grouping=lambda *_a, **_k: (True, "true"),
            validate_channels_content=lambda *_a, **_k: (True, "ok", []),
        )
        stats_stub = types.SimpleNamespace(
            reset_stats=lambda *_a, **_k: True,
            repair_stats_snapshots=lambda: (
                True,
                "Monthly stats snapshots repaired from backup",
            ),
            record_channel_history=lambda *_a, **_k: None,
            update_stats=lambda *_a, **_k: None,
            load_stats=lambda *_a, **_k: {},
            save_last_run=lambda *_a, **_k: None,
        )

        with isolated_module_import(
            "admin",
            {
                "config": config_stub,
                "config_utils": config_utils_stub,
                "utils": utils_stub,
                "stats": stats_stub,
            },
        ) as admin_module:
            app = Flask(__name__)
            app.register_blueprint(admin_module.admin)
            client = app.test_client()

            preview_response = client.post(
                "/admin/config/channels/restore/preview",
                data={"backup_filename": "channels-1.yml.bak"},
            )
            env_preview_response = client.post(
                "/admin/config/env/restore/preview",
                data={"backup_filename": "env-1.env.bak"},
            )
            restore_response = client.post(
                "/admin/config/channels/restore",
                data={"backup_filename": "channels-1.yml.bak"},
            )
            env_restore_response = client.post(
                "/admin/config/env/restore", data={"backup_filename": "env-1.env.bak"}
            )

        self.assertEqual(preview_response.status_code, 200)
        self.assertTrue(preview_response.get_json()["success"])
        self.assertEqual(
            preview_response.get_json()["preview"]["backup"]["filename"],
            "channels-1.yml.bak",
        )
        self.assertEqual(env_preview_response.status_code, 200)
        self.assertTrue(env_preview_response.get_json()["success"])
        self.assertEqual(
            env_preview_response.get_json()["preview"]["backup"]["filename"],
            "env-1.env.bak",
        )
        self.assertTrue(
            env_preview_response.get_json()["preview"]["restores"]["restart_required"]
        )
        self.assertEqual(restore_response.status_code, 200)
        self.assertTrue(restore_response.get_json()["success"])
        self.assertEqual(
            restore_response.get_json()["backup_path"],
            "/config/backups/channels-restore.yml.bak",
        )
        self.assertEqual(env_restore_response.status_code, 200)
        self.assertTrue(env_restore_response.get_json()["success"])
        self.assertEqual(
            env_restore_response.get_json()["backup_path"],
            "/config/backups/env-restore.env.bak",
        )

    def test_admin_stats_repair_route_exposes_repair_action(self):
        config_stub = types.SimpleNamespace(
            log=logging.getLogger("test-admin"),
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            CLEAN_TIMES=["03:00"],
        )
        utils_stub = types.SimpleNamespace(
            get_bot=lambda: None,
            get_bot_loop=lambda: None,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        config_utils_stub = types.SimpleNamespace(
            preview_channel_restore=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            preview_env_restore=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            restore_channels_backup=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            restore_env_backup=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            preview_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            save_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            update_schedule_skip_dates=lambda dates: (True, ",".join(dates)),
            update_schedule_skip_weekdays=lambda weekdays: (True, ",".join(weekdays)),
            update_report_grouping=lambda *_a, **_k: (True, "true"),
            validate_channels_content=lambda *_a, **_k: (True, "ok", []),
        )
        stats_stub = types.SimpleNamespace(
            reset_stats=lambda *_a, **_k: True,
            repair_stats_snapshots=lambda: (
                True,
                "Monthly stats snapshots repaired from backup",
            ),
            clear_monthly_report_source=lambda: None,
        )

        with isolated_module_import(
            "admin",
            {
                "config": config_stub,
                "config_utils": config_utils_stub,
                "utils": utils_stub,
                "stats": stats_stub,
            },
        ) as admin_module:
            app = Flask(__name__)
            app.register_blueprint(admin_module.admin)
            client = app.test_client()

            response = client.post("/admin/api/stats/repair")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertTrue(response.get_json()["repaired"])
        self.assertIn("repaired", response.get_json()["message"].lower())

    def test_admin_stats_repair_and_repost_route_schedules_monthly_report(self):
        config_stub = types.SimpleNamespace(
            log=logging.getLogger("test-admin"),
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            CLEAN_TIMES=["03:00"],
        )
        utils_stub = types.SimpleNamespace(
            get_bot=lambda: types.SimpleNamespace(
                guilds=[types.SimpleNamespace(name="alpha")]
            ),
            get_bot_loop=lambda: object(),
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        config_utils_stub = types.SimpleNamespace(
            preview_channel_restore=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            preview_env_restore=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            restore_channels_backup=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            restore_env_backup=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            preview_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            save_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            update_schedule_skip_dates=lambda dates: (True, ",".join(dates)),
            update_schedule_skip_weekdays=lambda weekdays: (True, ",".join(weekdays)),
            update_report_grouping=lambda *_a, **_k: (True, "true"),
            validate_channels_content=lambda *_a, **_k: (True, "ok", []),
        )
        stats_stub = types.SimpleNamespace(
            reset_stats=lambda *_a, **_k: True,
            repair_stats_snapshots=lambda: (
                True,
                "Monthly stats snapshots repaired from backup",
            ),
        )
        notifications_stub = types.SimpleNamespace(
            post_status_report=lambda *a, **k: asyncio.sleep(0, result=True),
        )

        class FakeFuture:
            def __init__(self, result):
                self._result = result

            def add_done_callback(self, callback):
                callback(self)

            def result(self):
                return self._result

        def fake_run_coroutine_threadsafe(coro, loop):
            return FakeFuture(asyncio.run(coro))

        with isolated_module_import(
            "admin",
            {
                "config": config_stub,
                "config_utils": config_utils_stub,
                "notifications": notifications_stub,
                "utils": utils_stub,
                "stats": stats_stub,
            },
        ) as admin_module:
            with patch.object(
                admin_module.asyncio,
                "run_coroutine_threadsafe",
                fake_run_coroutine_threadsafe,
            ):
                app = Flask(__name__)
                app.register_blueprint(admin_module.admin)
                client = app.test_client()

                response = client.post("/admin/api/stats/repair-and-repost")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["repaired"])
        self.assertTrue(payload["reported"])
        self.assertIn("report repost queued", payload["message"].lower())

    def test_admin_schedule_exception_routes_update_env(self):
        config_stub = types.SimpleNamespace(
            log=logging.getLogger("test-admin"),
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            CLEAN_TIMES=["03:00"],
        )
        utils_stub = types.SimpleNamespace(
            get_bot=lambda: None,
            get_bot_loop=lambda: None,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        config_utils_stub = types.SimpleNamespace(
            preview_channel_restore=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            preview_env_restore=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            restore_channels_backup=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            restore_env_backup=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            preview_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            save_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unused")
            ),
            update_schedule_skip_dates=lambda dates: (True, ",".join(dates)),
            update_schedule_skip_weekdays=lambda weekdays: (True, ",".join(weekdays)),
            update_report_grouping=lambda *_a, **_k: (True, "true"),
            validate_channels_content=lambda *_a, **_k: (True, "ok", []),
        )
        stats_stub = types.SimpleNamespace(
            reset_stats=lambda *_a, **_k: True,
            repair_stats_snapshots=lambda: (False, "No stats repair was needed"),
        )

        with isolated_module_import(
            "admin",
            {
                "config": config_stub,
                "config_utils": config_utils_stub,
                "utils": utils_stub,
                "stats": stats_stub,
            },
        ) as admin_module:
            app = Flask(__name__)
            app.register_blueprint(admin_module.admin)
            client = app.test_client()

            add_date_response = client.post(
                "/admin/schedule/skip/date",
                data={"action": "add", "date": "2026-04-20"},
            )
            add_weekday_response = client.post(
                "/admin/schedule/skip/weekday",
                data={"weekday": "fri", "enabled": "true"},
            )

        self.assertEqual(add_date_response.status_code, 200)
        self.assertTrue(add_date_response.get_json()["success"])
        self.assertIn("2026-04-20", add_date_response.get_json()["dates"])
        self.assertEqual(add_weekday_response.status_code, 200)
        self.assertTrue(add_weekday_response.get_json()["success"])
        self.assertIn("fri", add_weekday_response.get_json()["weekdays"])


if __name__ == "__main__":
    unittest.main()
