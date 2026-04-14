import types
import unittest

from tests.support import isolated_module_import


class NotificationGroupingTests(unittest.TestCase):
    def test_build_notification_leaderboard_groups_channels_for_report_only(self):
        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            DATA_DIR="/tmp",
            GITHUB_TOKEN=None,
            LAST_VERSION_FILE="/tmp/last_version",
            LOG_CHANNEL_ID=1,
            MISSED_RUN_THRESHOLD_MINUTES=15,
            REPORT_CHANNEL_ID=2,
            WARN_UNCONFIGURED=False,
            log=types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        )
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *a, **k: None)
        stats_stub = types.SimpleNamespace(load_stats=lambda: {})
        utils_stub = types.SimpleNamespace(get_next_run_str=lambda: "tomorrow")
        discord_stub = types.SimpleNamespace(Embed=object)

        with isolated_module_import(
            "notifications",
            {
                "config": config_stub,
                "file_utils": file_utils_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "discord": discord_stub,
            },
        ) as notifications:
            leaderboard = notifications._build_notification_leaderboard(
                {
                    "101": {"name": "repo-a-builds", "count": 20},
                    "102": {"name": "repo-b-builds", "count": 15},
                    "103": {"name": "plex", "count": 8},
                },
                {
                    101: {"notification_group": "Build Channels"},
                    102: {"notification_group": "Build Channels"},
                    103: {},
                },
            )

        self.assertEqual(leaderboard[0]["label"], "Build Channels")
        self.assertEqual(leaderboard[0]["count"], 35)
        self.assertTrue(leaderboard[0]["grouped"])
        self.assertEqual(len(leaderboard[0]["channels"]), 2)
        self.assertEqual(leaderboard[1]["label"], "#plex")
        self.assertEqual(leaderboard[1]["count"], 8)
        self.assertFalse(leaderboard[1]["grouped"])
