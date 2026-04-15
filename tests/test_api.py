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
            is_run_in_progress=lambda: False,
            list_cleanup_logs_with_sizes=lambda: (_ for _ in ()).throw(RuntimeError("sensitive path info")),
            read_cleanup_log=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
            read_latest_cleanup_log=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("sensitive path info")),
        )

    def _stats_stub(self):
        class StatsLoadError(RuntimeError):
            pass

        return types.SimpleNamespace(
            StatsLoadError=StatsLoadError,
            load_stats=lambda strict=False: (_ for _ in ()).throw(StatsLoadError("bad stats")),
            load_last_run=lambda: None,
        )

    def test_api_returns_generic_internal_error_message(self):
        config_stub = types.SimpleNamespace(BOT_VERSION="1.0.0", log=logging.getLogger("test-api"))

        with isolated_module_import(
            "api",
            {
                "config": config_stub,
                "utils": self._utils_stub(),
                "stats": self._stats_stub(),
            },
        ) as api_module:
            app = Flask(__name__)
            app.register_blueprint(api_module.api)
            client = app.test_client()

            stats_response = client.get("/api/stats")
            logs_response = client.get("/api/logs/latest")

        self.assertEqual(stats_response.status_code, 500)
        self.assertEqual(stats_response.get_json()["error"], "Internal server error")
        self.assertEqual(logs_response.status_code, 500)
        self.assertEqual(logs_response.get_json()["error"], "Internal server error")


if __name__ == "__main__":
    unittest.main()
