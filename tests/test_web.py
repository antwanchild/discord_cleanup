import logging
import os
import types
import unittest

from flask import Blueprint

from tests.support import isolated_module_import


class WebConfigTests(unittest.TestCase):
    def _config_stub(self):
        return types.SimpleNamespace(
            CONFIG_DIR="/tmp",
            log=logging.getLogger("test-web"),
        )

    def _api_stub(self):
        api_blueprint = Blueprint("api", __name__)

        def _get_status_context():
            return {}

        return types.SimpleNamespace(
            api=api_blueprint, _get_status_context=_get_status_context
        )

    def _admin_stub(self):
        return types.SimpleNamespace(admin=Blueprint("admin", __name__))

    def test_invalid_web_port_fails_fast_on_import(self):
        original = os.environ.get("WEB_PORT")
        os.environ["WEB_PORT"] = "not-a-number"

        try:
            with self.assertRaisesRegex(ValueError, "WEB_PORT must be an integer"):
                with isolated_module_import(
                    "web",
                    {
                        "config": self._config_stub(),
                        "config_utils": types.SimpleNamespace(
                            list_channel_backups=lambda: [],
                            list_env_backups=lambda: [],
                        ),
                        "utils": types.SimpleNamespace(
                            get_bot=lambda: None,
                            get_run_owner=lambda: None,
                            is_run_in_progress=lambda: False,
                            read_cleanup_log=lambda *_a, **_k: {},
                            read_latest_cleanup_log=lambda *_a, **_k: {},
                        ),
                        "stats": types.SimpleNamespace(
                            list_stats_backups=lambda: [],
                            load_stats=lambda: {},
                            load_last_run=lambda: None,
                            load_monthly_report_source=lambda: None,
                        ),
                        "api": self._api_stub(),
                        "admin": self._admin_stub(),
                    },
                ):
                    pass
        finally:
            if original is None:
                os.environ.pop("WEB_PORT", None)
            else:
                os.environ["WEB_PORT"] = original

    def test_stats_page_includes_stats_repair_action(self):
        config_stub = self._config_stub()
        config_stub.BOT_VERSION = "1.0.0"
        config_stub.CLEAN_TIMES = ["03:00"]
        config_stub.DEFAULT_RETENTION = 7
        config_stub.LOG_LEVEL = "INFO"
        config_stub.WARN_UNCONFIGURED = False
        config_stub.REPORT_FREQUENCY = "monthly"
        config_stub.LOG_MAX_FILES = 7
        config_stub.STATS_BACKUP_RETENTION_DAYS = 10
        config_stub.REPORT_GROUP_MONTHLY = True
        config_stub.REPORT_GROUP_WEEKLY = True
        config_stub.SCHEDULE_SKIP_DATES = []
        config_stub.SCHEDULE_SKIP_WEEKDAYS = []

        stats_payload = {
            "all_time": {"runs": 1, "deleted": 5, "catchup_runs": 0, "channels": {}},
            "rolling_30": {
                "runs": 1,
                "deleted": 5,
                "catchup_runs": 0,
                "channels": {},
                "reset": "2026-06-01",
            },
            "monthly": {
                "runs": 1,
                "deleted": 5,
                "catchup_runs": 0,
                "channels": {},
                "reset": "2026-06-01",
            },
            "last_month": None,
            "previous_month": None,
            "channel_history": {},
        }

        with isolated_module_import(
            "web",
            {
                "config": config_stub,
                "config_utils": types.SimpleNamespace(
                    list_channel_backups=lambda: [],
                    list_env_backups=lambda: [],
                ),
                "cleanup": types.SimpleNamespace(
                    build_channel_map=lambda *_a, **_k: {}
                ),
                "utils": types.SimpleNamespace(
                    get_bot=lambda: None,
                    get_run_owner=lambda: None,
                    is_run_in_progress=lambda: False,
                    read_cleanup_log=lambda *_a, **_k: {},
                    read_latest_cleanup_log=lambda *_a, **_k: {},
                    get_uptime_str=lambda: "1m",
                    get_next_run_str=lambda: "tomorrow",
                ),
                "stats": types.SimpleNamespace(
                    list_stats_backups=lambda: [],
                    load_stats=lambda: stats_payload,
                    load_last_run=lambda: None,
                    load_monthly_report_source=lambda: {
                        "display": {
                            "runs": 31,
                            "deleted": 5712,
                            "channels": {},
                            "reset": "2026-06-01",
                        },
                        "comparison": {
                            "runs": 33,
                            "deleted": 8640,
                            "channels": {},
                            "reset": "2026-05-01",
                        },
                        "captured_at": "2026-06-01 09:00:00",
                        "month_key": "2026-06",
                    },
                ),
                "api": types.SimpleNamespace(
                    api=Blueprint("api", __name__),
                    _get_status_context=lambda: {
                        "version": "1.0.0",
                        "uptime": "1m",
                        "next_run": "tomorrow",
                        "schedule": ["03:00"],
                        "default_retention": 7,
                        "log_level": "INFO",
                        "warn_unconfigured": False,
                        "report_frequency": "monthly",
                        "log_max_files": 7,
                        "startup_path_check": {},
                        "notification_fallbacks_recent": 0,
                        "last_notification_fallback": None,
                        "run_in_progress": False,
                        "run_owner": None,
                    },
                ),
                "admin": self._admin_stub(),
            },
        ) as web_module:
            client = web_module.app.test_client()
            response = client.get("/stats")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Monthly Report Snapshot", response.data)
        self.assertIn(b"Repair Monthly Snapshot", response.data)
        self.assertIn(b"Repair + Repost Monthly Report", response.data)

    def test_stats_page_shows_ten_of_each_backup_type(self):
        config_stub = self._config_stub()
        config_stub.BOT_VERSION = "1.0.0"
        config_stub.CLEAN_TIMES = ["03:00"]
        config_stub.DEFAULT_RETENTION = 7
        config_stub.LOG_LEVEL = "INFO"
        config_stub.WARN_UNCONFIGURED = False
        config_stub.REPORT_FREQUENCY = "monthly"
        config_stub.LOG_MAX_FILES = 7
        config_stub.STATS_BACKUP_RETENTION_DAYS = 10
        config_stub.REPORT_GROUP_MONTHLY = True
        config_stub.REPORT_GROUP_WEEKLY = True
        config_stub.SCHEDULE_SKIP_DATES = []
        config_stub.SCHEDULE_SKIP_WEEKDAYS = []

        stats_backups = [
            {
                "type": "stats",
                "filename": f"stats-20260622-000{i}.json.bak",
                "path": f"/tmp/stats-{i}.json.bak",
                "modified": f"2026-06-22 00:00:{i:02d}",
                "size_bytes": 100 + i,
            }
            for i in range(11)
        ]
        last_run_backups = [
            {
                "type": "last_run",
                "filename": f"last-run-20260622-000{i}.json.bak",
                "path": f"/tmp/last-run-{i}.json.bak",
                "modified": f"2026-06-22 00:01:{i:02d}",
                "size_bytes": 200 + i,
            }
            for i in range(11)
        ]

        with isolated_module_import(
            "web",
            {
                "config": config_stub,
                "config_utils": types.SimpleNamespace(
                    list_channel_backups=lambda: [],
                    list_env_backups=lambda: [],
                ),
                "cleanup": types.SimpleNamespace(
                    build_channel_map=lambda *_a, **_k: {}
                ),
                "utils": types.SimpleNamespace(
                    get_bot=lambda: None,
                    get_run_owner=lambda: None,
                    is_run_in_progress=lambda: False,
                    read_cleanup_log=lambda *_a, **_k: {},
                    read_latest_cleanup_log=lambda *_a, **_k: {},
                    get_uptime_str=lambda: "1m",
                    get_next_run_str=lambda: "tomorrow",
                ),
                "stats": types.SimpleNamespace(
                    list_stats_backups=lambda: stats_backups + last_run_backups,
                    load_stats=lambda: {
                        "all_time": {
                            "runs": 1,
                            "deleted": 5,
                            "catchup_runs": 0,
                            "channels": {},
                        },
                        "rolling_30": {
                            "runs": 1,
                            "deleted": 5,
                            "catchup_runs": 0,
                            "channels": {},
                            "reset": "2026-06-01",
                        },
                        "monthly": {
                            "runs": 1,
                            "deleted": 5,
                            "catchup_runs": 0,
                            "channels": {},
                            "reset": "2026-06-01",
                        },
                        "last_month": None,
                        "previous_month": None,
                        "channel_history": {},
                    },
                    load_last_run=lambda: None,
                    load_monthly_report_source=lambda: None,
                ),
                "api": self._api_stub(),
                "admin": self._admin_stub(),
            },
        ) as web_module:
            client = web_module.app.test_client()
            response = client.get("/stats")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Stats Backups", response.data)
        self.assertIn(b"Last-Run Backups", response.data)
        self.assertIn(
            b"Showing the 10 most recent stats backups and the 10 most recent last-run backups.",
            response.data,
        )
        self.assertIn(b"stats-20260622-0009.json.bak", response.data)
        self.assertNotIn(b"stats-20260622-0010.json.bak", response.data)
        self.assertIn(b"last-run-20260622-0009.json.bak", response.data)
        self.assertNotIn(b"last-run-20260622-0010.json.bak", response.data)

    def test_stats_page_falls_back_to_history_data_when_bot_is_unavailable(self):
        config_stub = self._config_stub()
        config_stub.BOT_VERSION = "1.0.0"
        config_stub.CLEAN_TIMES = ["03:00"]
        config_stub.DEFAULT_RETENTION = 7
        config_stub.LOG_LEVEL = "INFO"
        config_stub.WARN_UNCONFIGURED = False
        config_stub.REPORT_FREQUENCY = "monthly"
        config_stub.LOG_MAX_FILES = 7
        config_stub.STATS_BACKUP_RETENTION_DAYS = 10
        config_stub.REPORT_GROUP_MONTHLY = True
        config_stub.REPORT_GROUP_WEEKLY = True
        config_stub.SCHEDULE_SKIP_DATES = []
        config_stub.SCHEDULE_SKIP_WEEKDAYS = []

        stats_payload = {
            "all_time": {
                "runs": 1,
                "deleted": 5,
                "catchup_runs": 0,
                "channels": {
                    "123": {"name": "general", "count": 5, "category": "Standalone"},
                },
            },
            "rolling_30": {
                "runs": 1,
                "deleted": 5,
                "catchup_runs": 0,
                "channels": {},
                "reset": "2026-06-01",
            },
            "monthly": {
                "runs": 1,
                "deleted": 5,
                "catchup_runs": 0,
                "channels": {},
                "reset": "2026-06-01",
            },
            "last_month": None,
            "previous_month": None,
            "channel_history": {
                "123": [
                    {
                        "timestamp": "2026-06-22 08:00:00",
                        "triggered_by": "scheduler",
                        "count": 5,
                        "category": "Standalone",
                        "status": "deleted",
                        "rate_limits": 0,
                        "dry_run": False,
                        "oldest": None,
                        "error": None,
                    }
                ]
            },
        }

        with isolated_module_import(
            "web",
            {
                "config": config_stub,
                "config_utils": types.SimpleNamespace(
                    list_channel_backups=lambda: [],
                    list_env_backups=lambda: [],
                ),
                "cleanup": types.SimpleNamespace(
                    build_channel_map=lambda *_a, **_k: {}
                ),
                "utils": types.SimpleNamespace(
                    get_bot=lambda: None,
                    get_run_owner=lambda: None,
                    is_run_in_progress=lambda: False,
                    read_cleanup_log=lambda *_a, **_k: {},
                    read_latest_cleanup_log=lambda *_a, **_k: {},
                    get_uptime_str=lambda: "1m",
                    get_next_run_str=lambda: "tomorrow",
                ),
                "stats": types.SimpleNamespace(
                    list_stats_backups=lambda: [],
                    load_stats=lambda: stats_payload,
                    load_last_run=lambda: None,
                    load_monthly_report_source=lambda: None,
                ),
                "api": self._api_stub(),
                "admin": self._admin_stub(),
            },
        ) as web_module:
            client = web_module.app.test_client()
            response = client.get("/stats")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Channel Drilldown", response.data)
        self.assertIn(b"general", response.data)
        self.assertIn(b"Live channel config is not loaded", response.data)
        self.assertIn(b"All-Time Deleted", response.data)
        self.assertIn(b"Recorded Runs", response.data)

    def test_stats_page_honors_selected_drilldown_channel_query(self):
        config_stub = self._config_stub()
        config_stub.BOT_VERSION = "1.0.0"
        config_stub.CLEAN_TIMES = ["03:00"]
        config_stub.DEFAULT_RETENTION = 7
        config_stub.LOG_LEVEL = "INFO"
        config_stub.WARN_UNCONFIGURED = False
        config_stub.REPORT_FREQUENCY = "monthly"
        config_stub.LOG_MAX_FILES = 7
        config_stub.STATS_BACKUP_RETENTION_DAYS = 10
        config_stub.REPORT_GROUP_MONTHLY = True
        config_stub.REPORT_GROUP_WEEKLY = True
        config_stub.SCHEDULE_SKIP_DATES = []
        config_stub.SCHEDULE_SKIP_WEEKDAYS = []

        stats_payload = {
            "all_time": {
                "runs": 2,
                "deleted": 14,
                "catchup_runs": 0,
                "channels": {
                    "123": {"name": "general", "count": 5, "category": "Standalone"},
                    "456": {"name": "warning", "count": 9, "category": "Standalone"},
                },
            },
            "rolling_30": {
                "runs": 2,
                "deleted": 14,
                "catchup_runs": 0,
                "channels": {},
                "reset": "2026-06-01",
            },
            "monthly": {
                "runs": 2,
                "deleted": 14,
                "catchup_runs": 0,
                "channels": {},
                "reset": "2026-06-01",
            },
            "last_month": None,
            "previous_month": None,
            "channel_history": {
                "123": [
                    {
                        "timestamp": "2026-06-22 08:00:00",
                        "triggered_by": "scheduler",
                        "count": 5,
                        "category": "Standalone",
                        "status": "deleted",
                        "rate_limits": 0,
                        "dry_run": False,
                        "oldest": None,
                        "error": None,
                    }
                ],
                "456": [
                    {
                        "timestamp": "2026-06-22 08:15:00",
                        "triggered_by": "scheduler",
                        "count": 9,
                        "category": "Standalone",
                        "status": "deleted",
                        "rate_limits": 0,
                        "dry_run": False,
                        "oldest": None,
                        "error": None,
                    }
                ],
            },
        }

        with isolated_module_import(
            "web",
            {
                "config": config_stub,
                "config_utils": types.SimpleNamespace(
                    list_channel_backups=lambda: [],
                    list_env_backups=lambda: [],
                ),
                "cleanup": types.SimpleNamespace(
                    build_channel_map=lambda *_a, **_k: {}
                ),
                "utils": types.SimpleNamespace(
                    get_bot=lambda: None,
                    get_run_owner=lambda: None,
                    is_run_in_progress=lambda: False,
                    read_cleanup_log=lambda *_a, **_k: {},
                    read_latest_cleanup_log=lambda *_a, **_k: {},
                    get_uptime_str=lambda: "1m",
                    get_next_run_str=lambda: "tomorrow",
                ),
                "stats": types.SimpleNamespace(
                    list_stats_backups=lambda: [],
                    load_stats=lambda: stats_payload,
                    load_last_run=lambda: None,
                    load_monthly_report_source=lambda: None,
                ),
                "api": self._api_stub(),
                "admin": self._admin_stub(),
            },
        ) as web_module:
            client = web_module.app.test_client()
            response = client.get("/stats?drilldown_channel=history-456")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"warning", response.data)
        self.assertIn(b"9", response.data)
        self.assertIn(b"Showing warning", response.data)
        self.assertIn(b"history-456", response.data)


if __name__ == "__main__":
    unittest.main()
