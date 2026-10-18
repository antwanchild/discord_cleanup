import runpy
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import mock_open, patch


class HealthcheckTests(unittest.TestCase):
    def _check(self, *, content="", error=None):
        opened = mock_open(read_data=content)
        if error is not None:
            opened.side_effect = error
        with patch("builtins.open", opened):
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_path(
                    str(Path(__file__).resolve().parents[1] / "healthcheck.py"),
                    run_name="__main__",
                )
        opened.assert_called_once_with("/tmp/health")
        return ctx.exception.code

    def test_healthcheck_exits_zero_when_timestamp_is_recent(self):
        self.assertEqual(self._check(content=datetime.now().isoformat()), 0)

    def test_healthcheck_exits_one_when_timestamp_is_stale(self):
        timestamp = datetime.now() - timedelta(minutes=10)
        self.assertEqual(self._check(content=timestamp.isoformat()), 1)

    def test_healthcheck_exits_one_when_file_is_missing(self):
        self.assertEqual(self._check(error=FileNotFoundError()), 1)

    def test_healthcheck_exits_one_when_timestamp_is_invalid(self):
        self.assertEqual(self._check(content="invalid"), 1)
