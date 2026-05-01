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

        return types.SimpleNamespace(api=api_blueprint, _get_status_context=_get_status_context)

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


if __name__ == "__main__":
    unittest.main()
