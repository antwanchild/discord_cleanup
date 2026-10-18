import json
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

from tests.support import isolated_module_import

from tests.stats_support import StatsConfigMixin, stats_bucket


class MonthlyReportSaveTests(StatsConfigMixin, unittest.TestCase):
    def test_save_monthly_report_source_preserves_existing_comparison_when_incoming_is_stale(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = os.path.join(tempdir, "monthly_report_source-2026-06.json")
            with open(source_path, "w") as f:
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
                        "captured_at": "2026-06-01 09:00:00",
                        "month_key": "2026-06",
                    },
                    f,
                )

            class FixedJune(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 6, 1, 9, 0, 0)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                with patch.object(stats, "datetime", FixedJune):
                    stats.save_monthly_report_source(
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
                                reset="2026-06-01",
                            ),
                        }
                    )

            with open(source_path, "r") as f:
                persisted = json.load(f)

            self.assertEqual(persisted["comparison"]["deleted"], 8640)
            self.assertEqual(persisted["comparison"]["reset"], "2026-05-01")

    def test_save_monthly_report_source_does_not_preserve_current_month_partial_comparison(
        self,
    ):
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = os.path.join(tempdir, "monthly_report_source-2026-06.json")
            with open(source_path, "w") as f:
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
                        "captured_at": "2026-06-01 09:00:00",
                        "month_key": "2026-06",
                    },
                    f,
                )

            class FixedJuly(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 7, 1, 9, 0, 0)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                with patch.object(stats, "datetime", FixedJuly):
                    stats.save_monthly_report_source(
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
                        }
                    )

            with open(source_path, "r") as f:
                persisted = json.load(f)

            self.assertNotEqual(persisted.get("comparison", {}).get("deleted"), 156)
            self.assertNotEqual(
                persisted.get("comparison", {}).get("reset"), "2026-07-01"
            )

    def test_save_monthly_report_source_writes_month_scoped_snapshot_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            june_source = {
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
            }
            july_source = {
                "display": stats_bucket(
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
                "comparison": stats_bucket(
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
            }

            class FixedJune(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 6, 1, 9, 0, 0)

            class FixedJuly(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 7, 1, 9, 0, 0)

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                with patch.object(stats, "datetime", FixedJune):
                    stats.save_monthly_report_source(june_source)

                with patch.object(stats, "datetime", FixedJuly):
                    stats.save_monthly_report_source(july_source)

            june_path = os.path.join(tempdir, "monthly_report_source-2026-06.json")
            july_path = os.path.join(tempdir, "monthly_report_source-2026-07.json")
            legacy_path = os.path.join(tempdir, "monthly_report_source.json")

            with open(june_path, "r") as f:
                june_persisted = json.load(f)
            with open(july_path, "r") as f:
                july_persisted = json.load(f)
            with open(legacy_path, "r") as f:
                legacy_persisted = json.load(f)

            self.assertEqual(june_persisted["display"]["reset"], "2026-06-01")
            self.assertEqual(june_persisted["comparison"]["deleted"], 8640)
            self.assertEqual(july_persisted["display"]["reset"], "2026-07-01")
            self.assertEqual(july_persisted["comparison"]["deleted"], 5712)
            self.assertEqual(legacy_persisted["display"]["reset"], "2026-07-01")

    def test_save_stats_prunes_old_monthly_report_source_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            stats_path = os.path.join(tempdir, "stats.json")
            with open(stats_path, "w") as f:
                json.dump({"all_time": {"runs": 1}}, f)

            old_source_path = os.path.join(
                tempdir, "monthly_report_source-2026-01.json"
            )
            current_source_path = os.path.join(
                tempdir, "monthly_report_source-2026-07.json"
            )
            with open(old_source_path, "w") as f:
                json.dump(
                    {
                        "display": stats_bucket(
                            runs=1,
                            deleted=1,
                            channels={
                                "1": {
                                    "name": "old",
                                    "count": 1,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-01-01",
                        ),
                        "comparison": stats_bucket(
                            runs=1, deleted=0, reset="2025-12-01"
                        ),
                    },
                    f,
                )
            with open(current_source_path, "w") as f:
                json.dump(
                    {
                        "display": stats_bucket(
                            runs=1,
                            deleted=2,
                            channels={
                                "2": {
                                    "name": "new",
                                    "count": 2,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-07-01",
                        ),
                        "comparison": stats_bucket(
                            runs=1,
                            deleted=1,
                            channels={
                                "1": {
                                    "name": "old",
                                    "count": 1,
                                    "category": "Standalone",
                                }
                            },
                            reset="2026-06-01",
                        ),
                    },
                    f,
                )

            old_time = datetime(2026, 1, 1, 0, 0, 0).timestamp()
            os.utime(old_source_path, (old_time, old_time))

            with isolated_module_import(
                "stats", {"config": self._config_stub(tempdir)}
            ) as stats:
                stats.save_stats(stats._empty_stats())

            self.assertFalse(os.path.exists(old_source_path))
            self.assertTrue(os.path.exists(current_source_path))
