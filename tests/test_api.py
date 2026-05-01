import logging
import types
import unittest

from flask import Flask

from tests.support import isolated_module_import


class ApiTests(unittest.TestCase):
    def _utils_stub(self):
        return types.SimpleNamespace(
            get_uptime_str=lambda: "1m",
            get_next_run_str=lambda: "tomorrow",
            get_bot=lambda: None,
            get_run_owner=lambda: None,
            get_startup_path_status=lambda: {"/config/data": (True, "OK")},
            is_run_in_progress=lambda: False,
            list_cleanup_logs_with_sizes=lambda: (_ for _ in ()).throw(OSError("sensitive path info")),
            read_cleanup_log=lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")),
            read_latest_cleanup_log=lambda *_a, **_k: (_ for _ in ()).throw(OSError("sensitive path info")),
        )

    def _stats_stub(self):
        class StatsLoadError(RuntimeError):
            pass

        return types.SimpleNamespace(
            StatsLoadError=StatsLoadError,
            load_stats=lambda strict=False: (_ for _ in ()).throw(StatsLoadError("bad stats")),
            load_last_run=lambda: None,
            list_stats_backups=lambda: [{"type": "stats", "filename": "stats-1.json.bak", "path": "/config/data/backups/stats-1.json.bak", "modified": "2026-04-15 05:45:00", "size_bytes": 123}],
        )

    def test_api_returns_generic_internal_error_message(self):
        config_stub = types.SimpleNamespace(BOT_VERSION="1.0.0", STATS_BACKUP_RETENTION_DAYS=10, REPORT_GROUP_MONTHLY=True, REPORT_GROUP_WEEKLY=True, log=logging.getLogger("test-api"))

        with isolated_module_import(
            "api",
            {
                "config": config_stub,
                "config_utils": types.SimpleNamespace(list_channel_backups=lambda: []),
                "notifications": types.SimpleNamespace(get_recent_notification_fallbacks=lambda: []),
                "utils": self._utils_stub(),
                "stats": self._stats_stub(),
            },
        ) as api_module:
            app = Flask(__name__)
            app.register_blueprint(api_module.api)
            client = app.test_client()

            stats_response = client.get("/api/stats")
            logs_list_response = client.get("/api/logs")
            logs_response = client.get("/api/logs/latest")

        self.assertEqual(stats_response.status_code, 500)
        self.assertEqual(stats_response.get_json()["error"], "Internal server error")
        self.assertEqual(logs_list_response.status_code, 500)
        self.assertEqual(logs_list_response.get_json()["error"], "Internal server error")
        self.assertEqual(logs_response.status_code, 500)
        self.assertEqual(logs_response.get_json()["error"], "Internal server error")

    def test_api_exposes_stats_backups_and_status_config(self):
        config_stub = types.SimpleNamespace(BOT_VERSION="1.0.0", STATS_BACKUP_RETENTION_DAYS=10, CHANNELS_BACKUP_RETENTION_DAYS=10, CLEAN_TIMES=["03:00"], DEFAULT_RETENTION=7, LOG_LEVEL="INFO", WARN_UNCONFIGURED=False, REPORT_FREQUENCY="monthly", REPORT_GROUP_MONTHLY=True, REPORT_GROUP_WEEKLY=True, LOG_MAX_FILES=7, log=logging.getLogger("test-api"))
        utils_stub = types.SimpleNamespace(
            get_uptime_str=lambda: "1m",
            get_next_run_str=lambda: "tomorrow",
            get_bot=lambda: None,
            get_run_owner=lambda: None,
            get_startup_path_status=lambda: {"/config/data": (True, "OK")},
            is_run_in_progress=lambda: False,
            list_cleanup_logs_with_sizes=lambda: [],
            read_cleanup_log=lambda *_a, **_k: {},
            read_latest_cleanup_log=lambda *_a, **_k: {},
        )
        stats_stub = types.SimpleNamespace(
            StatsLoadError=RuntimeError,
            load_stats=lambda strict=False: {"all_time": {}},
            load_last_run=lambda: None,
            list_stats_backups=lambda: [{"type": "stats", "filename": "stats-1.json.bak", "path": "/config/data/backups/stats-1.json.bak", "modified": "2026-04-15 05:45:00", "size_bytes": 123}],
        )
        config_utils_stub = types.SimpleNamespace(list_channel_backups=lambda: [{"type": "channels", "filename": "channels-1.yml.bak", "path": "/config/backups/channels-1.yml.bak", "modified": "2026-04-15 05:45:00", "size_bytes": 99}])
        notifications_stub = types.SimpleNamespace(get_recent_notification_fallbacks=lambda: [{"context": "daily cleanup report", "timestamp": "2026-04-15 05:45:00"}])

        with isolated_module_import(
            "api",
            {
                "config": config_stub,
                "config_utils": config_utils_stub,
                "notifications": notifications_stub,
                "utils": utils_stub,
                "stats": stats_stub,
            },
        ) as api_module:
            app = Flask(__name__)
            app.register_blueprint(api_module.api)
            client = app.test_client()

            backups_response = client.get("/api/backups/stats")
            channel_backups_response = client.get("/api/backups/channels")
            fallbacks_response = client.get("/api/notifications/fallbacks")
            status_response = client.get("/api/status")

        self.assertEqual(backups_response.status_code, 200)
        self.assertEqual(backups_response.get_json()["retention_days"], 10)
        self.assertEqual(backups_response.get_json()["total"], 1)
        self.assertEqual(channel_backups_response.status_code, 200)
        self.assertEqual(channel_backups_response.get_json()["total"], 1)
        self.assertEqual(fallbacks_response.status_code, 200)
        self.assertEqual(fallbacks_response.get_json()["total"], 1)
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.get_json()["stats_backup_retention_days"], 10)
        self.assertEqual(status_response.get_json()["notification_fallbacks_recent"], 1)
        self.assertTrue(status_response.get_json()["startup_path_check"]["/config/data"]["ok"])

    def test_admin_restore_routes_expose_backup_preview_and_restore(self):
        config_stub = types.SimpleNamespace(log=logging.getLogger("test-admin"))
        utils_stub = types.SimpleNamespace(
            get_bot=lambda: None,
            get_bot_loop=lambda: None,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        config_utils_stub = types.SimpleNamespace(
            preview_channel_restore=lambda filename: (
                True,
                "Restore preview ready — channels-1.yml.bak",
                {
                    "summary": {
                        "current": {"entries": 1, "with_notification_groups": 0, "excluded": 0, "deep_clean": 0},
                        "proposed": {"entries": 2, "with_notification_groups": 1, "excluded": 0, "deep_clean": 1},
                        "delta": {"entries": 1, "categories": 0, "excluded": 0, "deep_clean": 1, "with_notification_groups": 1},
                        "counts": {"added": 1, "removed": 0, "updated": 0, "field_changes": 0},
                    },
                    "changes": {"added": [{"id": 2, "name": "new-channel"}], "removed": [], "updated": []},
                    "backup": {"filename": filename, "modified": "2026-04-15 05:45:00", "size_bytes": 99, "path": "/config/backups/channels-1.yml.bak", "type": "channels"},
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
                        "counts": {"added": 1, "removed": 1, "updated": 1, "field_changes": 0, "sensitive_updates": 1},
                    },
                    "added": [{"key": "GITHUB_TOKEN", "value": "gh***23"}],
                    "removed": [{"key": "WARN_UNCONFIGURED", "value": "false"}],
                    "updated": [{"key": "WEB_HOST", "before": "0.0.0.0", "after": "127.0.0.1"}],
                    "backup": {"filename": filename, "modified": "2026-04-15 05:45:00", "size_bytes": 88, "path": "/config/backups/env-1.env.bak", "type": "env"},
                    "restores": {"startup_only_changed": ["WEB_HOST"], "restart_required": True},
                },
            ),
            update_schedule_skip_dates=lambda dates: (True, ",".join(dates)),
            update_schedule_skip_weekdays=lambda weekdays: (True, ",".join(weekdays)),
            restore_channels_backup=lambda filename: (True, f"Restored channels.yml from {filename}", "/config/backups/channels-restore.yml.bak"),
            restore_env_backup=lambda filename: (True, f"Restored .env.discord_cleanup from {filename}", "/config/backups/env-restore.env.bak"),
            preview_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unused")),
            save_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unused")),
            update_report_grouping=lambda *_a, **_k: (True, "true"),
            validate_channels_content=lambda *_a, **_k: (True, "ok", []),
        )
        stats_stub = types.SimpleNamespace(reset_stats=lambda *_a, **_k: True)

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

            preview_response = client.post("/admin/config/channels/restore/preview", data={"backup_filename": "channels-1.yml.bak"})
            env_preview_response = client.post("/admin/config/env/restore/preview", data={"backup_filename": "env-1.env.bak"})
            restore_response = client.post("/admin/config/channels/restore", data={"backup_filename": "channels-1.yml.bak"})
            env_restore_response = client.post("/admin/config/env/restore", data={"backup_filename": "env-1.env.bak"})

        self.assertEqual(preview_response.status_code, 200)
        self.assertTrue(preview_response.get_json()["success"])
        self.assertEqual(preview_response.get_json()["preview"]["backup"]["filename"], "channels-1.yml.bak")
        self.assertEqual(env_preview_response.status_code, 200)
        self.assertTrue(env_preview_response.get_json()["success"])
        self.assertEqual(env_preview_response.get_json()["preview"]["backup"]["filename"], "env-1.env.bak")
        self.assertTrue(env_preview_response.get_json()["preview"]["restores"]["restart_required"])
        self.assertEqual(restore_response.status_code, 200)
        self.assertTrue(restore_response.get_json()["success"])
        self.assertEqual(restore_response.get_json()["backup_path"], "/config/backups/channels-restore.yml.bak")
        self.assertEqual(env_restore_response.status_code, 200)
        self.assertTrue(env_restore_response.get_json()["success"])
        self.assertEqual(env_restore_response.get_json()["backup_path"], "/config/backups/env-restore.env.bak")

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
            preview_channel_restore=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unused")),
            preview_env_restore=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unused")),
            restore_channels_backup=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unused")),
            restore_env_backup=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unused")),
            preview_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unused")),
            save_channels_content=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unused")),
            update_schedule_skip_dates=lambda dates: (True, ",".join(dates)),
            update_schedule_skip_weekdays=lambda weekdays: (True, ",".join(weekdays)),
            update_report_grouping=lambda *_a, **_k: (True, "true"),
            validate_channels_content=lambda *_a, **_k: (True, "ok", []),
        )
        stats_stub = types.SimpleNamespace(reset_stats=lambda *_a, **_k: True)

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

            add_date_response = client.post("/admin/schedule/skip/date", data={"action": "add", "date": "2026-04-20"})
            add_weekday_response = client.post("/admin/schedule/skip/weekday", data={"weekday": "fri", "enabled": "true"})

        self.assertEqual(add_date_response.status_code, 200)
        self.assertTrue(add_date_response.get_json()["success"])
        self.assertIn("2026-04-20", add_date_response.get_json()["dates"])
        self.assertEqual(add_weekday_response.status_code, 200)
        self.assertTrue(add_weekday_response.get_json()["success"])
        self.assertIn("fri", add_weekday_response.get_json()["weekdays"])


if __name__ == "__main__":
    unittest.main()
