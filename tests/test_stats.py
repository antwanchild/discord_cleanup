import json
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

from tests.support import isolated_module_import

from tests.stats_support import StatsConfigMixin, stats_bucket


class StatsTests(StatsConfigMixin, unittest.TestCase):
    def test_load_stats_strict_raises_for_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                f.write("{bad json")

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                with self.assertRaises(stats.StatsLoadError):
                    stats.load_stats(strict=True)

    def test_update_stats_does_not_overwrite_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            original = "{bad json"
            with open(stats_path, "w") as f:
                f.write(original)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                stats.update_stats(
                    {"123": {"name": "build-bot", "count": 5, "category": "Github"}}
                )

            with open(stats_path, "r") as f:
                self.assertEqual(f.read(), original)

    def test_reset_stats_returns_false_when_file_is_unreadable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                f.write("{bad json")

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                self.assertFalse(stats.reset_stats("all"))

            with open(stats_path, "r") as f:
                self.assertEqual(f.read(), "{bad json")

    def test_load_stats_normalizes_partial_and_legacy_shapes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": stats_bucket(
                            runs="4",
                            deleted="9",
                            channels={
                                "123": 5,
                                456: {"name": "build-bot", "count": "7"},
                            },
                        ),
                        "rolling_30": {"reset": "not-a-date"},
                        "monthly": {"catchup_runs": "3", "channels": []},
                        "last_month": {
                            "deleted": "12",
                            "channels": {"789": {"name": "plex", "count": "4"}},
                        },
                        "previous_month": {
                            "deleted": "7",
                            "channels": {"456": {"name": "sonarr", "count": "2"}},
                        },
                    },
                    f,
                )

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                payload = stats.load_stats(strict=True)

            self.assertEqual(payload["all_time"]["runs"], 4)
            self.assertEqual(payload["all_time"]["deleted"], 9)
            self.assertEqual(payload["all_time"]["catchup_runs"], 0)
            self.assertEqual(payload["all_time"]["channels"]["123"]["count"], 5)
            self.assertEqual(
                payload["all_time"]["channels"]["123"]["category"], "Standalone"
            )
            self.assertEqual(
                payload["all_time"]["channels"]["456"]["name"], "build-bot"
            )
            self.assertEqual(payload["all_time"]["channels"]["456"]["count"], 7)
            self.assertIn("reset", payload["rolling_30"])
            self.assertEqual(payload["monthly"]["catchup_runs"], 3)
            self.assertEqual(payload["monthly"]["channels"], {})
            self.assertEqual(payload["last_month"]["deleted"], 12)
            self.assertEqual(payload["last_month"]["channels"]["789"]["count"], 4)
            self.assertEqual(payload["previous_month"]["deleted"], 7)
            self.assertEqual(
                payload["previous_month"]["channels"]["456"]["name"], "sonarr"
            )
            self.assertIn("reset", payload["last_month"])

    def test_load_last_run_normalizes_missing_fields(self):
        with tempfile.TemporaryDirectory() as tempdir:
            last_run_path = os.path.join(tempdir, "last_run.json")
            with open(last_run_path, "w") as f:
                json.dump(
                    {
                        "timestamp": "2026-04-15 05:45:00",
                        "total_deleted": "22",
                        "categories": [{"name": "Github", "count": "18"}, "bad-item"],
                    },
                    f,
                )

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                payload = stats.load_last_run()

            self.assertEqual(payload["timestamp"], "2026-04-15 05:45:00")
            self.assertEqual(payload["total_deleted"], 22)
            self.assertEqual(payload["triggered_by"], "unknown")
            self.assertEqual(payload["channels_checked"], 0)
            self.assertEqual(payload["categories"], [{"name": "Github", "count": 18}])

    def test_load_last_run_preserves_category_object_shape(self):
        with tempfile.TemporaryDirectory() as tempdir:
            last_run_path = os.path.join(tempdir, "last_run.json")
            with open(last_run_path, "w") as f:
                json.dump(
                    {
                        "categories": [
                            {"name": "general", "count": 12},
                            {"name": "#build-bot", "count": 7},
                        ]
                    },
                    f,
                )

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                payload = stats.load_last_run()

            self.assertEqual(
                payload["categories"],
                [
                    {"name": "general", "count": 12},
                    {"name": "#build-bot", "count": 7},
                ],
            )

    def test_update_stats_rollover_preserves_last_month_snapshot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            source_path = os.path.join(tempdir, "monthly_report_source.json")
            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": stats_bucket(runs=10, deleted=50, catchup_runs=0),
                        "rolling_30": stats_bucket(
                            runs=4, deleted=19, catchup_runs=0, reset="2026-05-20"
                        ),
                        "monthly": stats_bucket(
                            runs=3,
                            deleted=11,
                            catchup_runs=0,
                            channels={
                                "101": {"name": "plex", "count": 7, "category": "Media"}
                            },
                            reset="2026-05-01",
                        ),
                        "last_month": stats_bucket(
                            runs=2,
                            deleted=8,
                            channels={
                                "202": {
                                    "name": "sonarr",
                                    "count": 5,
                                    "category": "Media",
                                }
                            },
                            reset="2026-04-01",
                        ),
                    },
                    f,
                )

            class FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 6, 1, 3, 0, 0)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                with patch.object(stats, "datetime", FixedDateTime):
                    stats.update_stats(
                        {"101": {"name": "plex", "count": 2, "category": "Media"}}
                    )
                    payload = stats.load_stats(strict=True)

            self.assertEqual(payload["last_month"]["runs"], 3)
            self.assertEqual(payload["last_month"]["deleted"], 11)
            self.assertEqual(payload["last_month"]["channels"]["101"]["count"], 7)
            self.assertEqual(payload["previous_month"]["runs"], 2)
            self.assertEqual(payload["previous_month"]["deleted"], 8)
            self.assertEqual(payload["previous_month"]["channels"]["202"]["count"], 5)
            self.assertEqual(payload["monthly"]["runs"], 1)
            self.assertEqual(payload["monthly"]["deleted"], 2)
            self.assertEqual(payload["monthly"]["channels"]["101"]["count"], 2)
            self.assertTrue(os.path.exists(source_path))
            with open(source_path, "r") as f:
                source = json.load(f)
            self.assertEqual(source["display"]["deleted"], 11)
            self.assertEqual(source["display"]["channels"]["101"]["count"], 7)
            self.assertEqual(source["comparison"]["deleted"], 8)
            self.assertEqual(source["comparison"]["channels"]["202"]["count"], 5)
            self.assertEqual(source["month_key"], "2026-05")
