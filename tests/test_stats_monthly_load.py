import json
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

from tests.support import isolated_module_import

from tests.stats_support import StatsConfigMixin, stats_bucket


class MonthlyReportLoadTests(StatsConfigMixin, unittest.TestCase):
    def test_load_stats_repairs_missing_monthly_snapshots_from_latest_backup(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            backups_dir = os.path.join(tempdir, "backups", "stats")
            os.makedirs(backups_dir, exist_ok=True)

            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": stats_bucket(runs=1, deleted=9),
                        "rolling_30": stats_bucket(
                            runs=1, deleted=9, reset="2026-06-01"
                        ),
                        "monthly": stats_bucket(runs=0, deleted=0, reset="2026-06-01"),
                        "last_month": stats_bucket(
                            runs=33, deleted=8640, reset="2026-05-01"
                        ),
                    },
                    f,
                )

            with open(
                os.path.join(backups_dir, "stats-20260531-090000.json.bak"), "w"
            ) as f:
                json.dump(
                    {
                        "all_time": stats_bucket(runs=1, deleted=9),
                        "rolling_30": stats_bucket(
                            runs=1, deleted=9, reset="2026-06-01"
                        ),
                        "monthly": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                        "last_month": stats_bucket(
                            runs=4,
                            deleted=8186,
                            channels={
                                "102": {
                                    "name": "crowdsec",
                                    "count": 649,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-04-01",
                        ),
                    },
                    f,
                )

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                payload = stats.load_stats(strict=True)

            self.assertEqual(
                payload["last_month"]["channels"]["101"]["name"], "notifications-kometa"
            )
            self.assertEqual(payload["last_month"]["channels"]["101"]["count"], 1342)
            self.assertEqual(
                payload["previous_month"]["channels"]["102"]["name"], "crowdsec"
            )

            with open(stats_path, "r") as f:
                on_disk = json.load(f)
            self.assertIn("101", on_disk["last_month"]["channels"])
            self.assertIn("102", on_disk["previous_month"]["channels"])

    def test_load_monthly_report_source_rebuilds_from_latest_backup(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            backups_dir = os.path.join(tempdir, "backups", "stats")
            os.makedirs(backups_dir, exist_ok=True)

            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": {"runs": 1},
                        "monthly": stats_bucket(runs=0, deleted=0, reset="2026-06-01"),
                    },
                    f,
                )

            with open(
                os.path.join(backups_dir, "stats-20260601-090000.json.bak"), "w"
            ) as f:
                json.dump(
                    {
                        "all_time": stats_bucket(runs=1, deleted=9, catchup_runs=0),
                        "rolling_30": stats_bucket(
                            runs=1, deleted=9, catchup_runs=0, reset="2026-06-01"
                        ),
                        "monthly": stats_bucket(
                            runs=1,
                            deleted=156,
                            catchup_runs=0,
                            channels={
                                "999": {
                                    "name": "wrong",
                                    "count": 1,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "last_month": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                    },
                    f,
                )

            with open(
                os.path.join(backups_dir, "stats-20260531-054744.json.bak"), "w"
            ) as f:
                json.dump(
                    {
                        "all_time": stats_bucket(runs=1, deleted=9, catchup_runs=0),
                        "rolling_30": stats_bucket(
                            runs=1, deleted=9, catchup_runs=0, reset="2026-06-01"
                        ),
                        "monthly": stats_bucket(
                            runs=33,
                            deleted=8640,
                            catchup_runs=0,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                        "last_month": stats_bucket(
                            runs=4,
                            deleted=8186,
                            channels={
                                "202": {
                                    "name": "crowdsec",
                                    "count": 649,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-04-01",
                        ),
                    },
                    f,
                )

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                source = stats.load_monthly_report_source()

            self.assertIsNotNone(source)
            self.assertEqual(source["display"]["deleted"], 8640)
            self.assertEqual(source["display"]["channels"]["101"]["count"], 1342)
            self.assertEqual(source["comparison"]["deleted"], 8186)
            self.assertEqual(source["comparison"]["channels"]["202"]["count"], 649)
            self.assertTrue(
                os.path.exists(os.path.join(tempdir, "monthly_report_source.json"))
            )

    def test_load_monthly_report_source_prefers_last_completed_month(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": {"runs": 1},
                        "monthly": stats_bucket(
                            runs=1,
                            deleted=156,
                            channels={"999": {"name": "partial", "count": 156}},
                            reset="2026-07-01",
                        ),
                        "last_month": stats_bucket(
                            runs=31,
                            deleted=5712,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "previous_month": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "202": {
                                    "name": "crowdsec",
                                    "count": 649,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                    },
                    f,
                )

            class FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 7, 1, 9, 0, 0, tzinfo=tz)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                with patch.object(stats, "datetime", FixedDateTime):
                    source = stats.load_monthly_report_source()

            self.assertIsNotNone(source)
            self.assertEqual(source["display"]["deleted"], 5712)
            self.assertEqual(source["display"]["reset"], "2026-06-01")
            self.assertEqual(source["comparison"]["deleted"], 8640)
            self.assertEqual(source["comparison"]["reset"], "2026-05-01")
            self.assertEqual(source["month_key"], "2026-06")

    def test_load_monthly_report_source_keeps_current_cached_source(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": {"runs": 1},
                        "monthly": stats_bucket(
                            runs=31,
                            deleted=156,
                            channels={"999": {"name": "partial", "count": 156}},
                            reset="2026-07-01",
                        ),
                        "last_month": stats_bucket(
                            runs=31,
                            deleted=5712,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "previous_month": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "202": {
                                    "name": "crowdsec",
                                    "count": 649,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                    },
                    f,
                )

            with open(os.path.join(tempdir, "monthly_report_source.json"), "w") as f:
                json.dump(
                    {
                        "display": stats_bucket(
                            runs=31,
                            deleted=5712,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "comparison": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "202": {
                                    "name": "crowdsec",
                                    "count": 649,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                        "captured_at": "2026-07-01 11:54:00",
                        "month_key": "2026-06",
                    },
                    f,
                )

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                source = stats.load_monthly_report_source()

            self.assertIsNotNone(source)
            self.assertEqual(source["display"]["deleted"], 5712)
            self.assertEqual(source["comparison"]["deleted"], 8640)
            self.assertEqual(source["month_key"], "2026-06")

    def test_load_monthly_report_source_does_not_overwrite_with_backup_when_cached_source_exists(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            backups_dir = os.path.join(tempdir, "backups", "stats")
            os.makedirs(backups_dir, exist_ok=True)

            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": {"runs": 1},
                        "monthly": stats_bucket(
                            runs=31,
                            deleted=156,
                            channels={"999": {"name": "partial", "count": 156}},
                            reset="2026-07-01",
                        ),
                        "last_month": stats_bucket(
                            runs=31,
                            deleted=5712,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "previous_month": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "202": {
                                    "name": "crowdsec",
                                    "count": 649,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                    },
                    f,
                )

            with open(os.path.join(tempdir, "monthly_report_source.json"), "w") as f:
                json.dump(
                    {
                        "display": stats_bucket(
                            runs=31,
                            deleted=5712,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "comparison": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "202": {
                                    "name": "crowdsec",
                                    "count": 649,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                        "captured_at": "2026-07-01 11:54:00",
                        "month_key": "2026-06",
                    },
                    f,
                )

            with open(
                os.path.join(backups_dir, "stats-20260601-090000.json.bak"), "w"
            ) as f:
                json.dump(
                    {
                        "all_time": stats_bucket(runs=1, deleted=9),
                        "rolling_30": stats_bucket(
                            runs=1, deleted=9, reset="2026-06-01"
                        ),
                        "monthly": stats_bucket(
                            runs=1,
                            deleted=156,
                            channels={
                                "999": {
                                    "name": "wrong",
                                    "count": 1,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "last_month": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                    },
                    f,
                )

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                source = stats.load_monthly_report_source()

            self.assertIsNotNone(source)
            self.assertEqual(source["display"]["deleted"], 5712)
            self.assertEqual(source["comparison"]["deleted"], 8640)

    def test_load_monthly_report_source_repairs_stale_comparison(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                json.dump(
                    {
                        "all_time": {"runs": 1},
                        "monthly": stats_bucket(
                            runs=31,
                            deleted=156,
                            channels={
                                "999": {
                                    "name": "partial",
                                    "count": 156,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-07-01",
                        ),
                        "last_month": stats_bucket(
                            runs=31,
                            deleted=5712,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "previous_month": stats_bucket(
                            runs=33,
                            deleted=8640,
                            channels={
                                "202": {
                                    "name": "crowdsec",
                                    "count": 649,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-05-01",
                        ),
                    },
                    f,
                )

            with open(os.path.join(tempdir, "monthly_report_source.json"), "w") as f:
                json.dump(
                    {
                        "display": stats_bucket(
                            runs=31,
                            deleted=5712,
                            channels={
                                "101": {
                                    "name": "notifications-kometa",
                                    "count": 1342,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                        "comparison": stats_bucket(
                            runs=1,
                            deleted=156,
                            channels={
                                "999": {
                                    "name": "partial",
                                    "count": 156,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-07-01",
                        ),
                        "captured_at": "2026-07-01 11:54:00",
                        "month_key": "2026-07",
                    },
                    f,
                )

            class FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 7, 1, 11, 54, 0)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                with patch.object(stats, "datetime", FixedDateTime):
                    source = stats.load_monthly_report_source()

            self.assertIsNotNone(source)
            self.assertEqual(source["display"]["deleted"], 5712)
            self.assertEqual(source["comparison"]["deleted"], 8640)
            self.assertEqual(source["comparison"]["reset"], "2026-05-01")

            with open(os.path.join(tempdir, "monthly_report_source.json"), "r") as f:
                persisted = json.load(f)
            self.assertEqual(persisted["comparison"]["deleted"], 8640)
            self.assertEqual(persisted["comparison"]["reset"], "2026-05-01")
