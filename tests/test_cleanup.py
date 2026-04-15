import types
import unittest

from tests.support import isolated_module_import


class BuildChannelMapTests(unittest.TestCase):
    def test_category_subchannel_with_same_days_is_not_marked_as_retention_override(self):
        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CLEAN_TIMES=["05:45"],
            DEFAULT_RETENTION=7,
            LOG_CHANNEL_ID=1,
            RETRY_DELAY=1,
            WARN_UNCONFIGURED=False,
            log=types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
            raw_channels=[
                {"id": 10, "name": "Github", "type": "category", "days": 7},
                {"id": 20, "name": "build-bot", "days": 7, "notification_group": "Build Channels"},
                {"id": 21, "name": "build-fast", "days": 3},
            ],
        )
        stats_stub = types.SimpleNamespace(
            load_stats=lambda: {},
            save_last_run=lambda *_a, **_k: None,
            update_stats=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            get_next_run_str=lambda: "tomorrow",
            setup_run_log=lambda *_a, **_k: None,
            update_health=lambda *_a, **_k: None,
        )
        discord_stub = types.SimpleNamespace(Embed=object)

        with isolated_module_import(
            "cleanup",
            {
                "config": config_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "discord": discord_stub,
            },
        ) as cleanup:
            build_bot = types.SimpleNamespace(id=20, name="build-bot")
            build_fast = types.SimpleNamespace(id=21, name="build-fast")
            category = types.SimpleNamespace(id=10, text_channels=[build_bot, build_fast])
            guild = types.SimpleNamespace(get_channel=lambda channel_id: {10: category}.get(channel_id))

            channel_map = cleanup.build_channel_map(guild)

        self.assertFalse(channel_map[20]["is_override"])
        self.assertEqual(channel_map[20]["days"], 7)
        self.assertEqual(channel_map[20]["notification_group"], "Build Channels")
        self.assertTrue(channel_map[21]["is_override"])
        self.assertEqual(channel_map[21]["days"], 3)

    def test_daily_breakdown_groups_channels_by_notification_group(self):
        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CLEAN_TIMES=["05:45"],
            DEFAULT_RETENTION=7,
            LOG_CHANNEL_ID=1,
            RETRY_DELAY=1,
            WARN_UNCONFIGURED=False,
            log=types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
            raw_channels=[],
        )
        stats_stub = types.SimpleNamespace(
            load_stats=lambda: {},
            save_last_run=lambda *_a, **_k: None,
            update_stats=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            get_next_run_str=lambda: "tomorrow",
            setup_run_log=lambda *_a, **_k: None,
            update_health=lambda *_a, **_k: None,
        )
        discord_stub = types.SimpleNamespace(Embed=object)

        with isolated_module_import(
            "cleanup",
            {
                "config": config_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "discord": discord_stub,
            },
        ) as cleanup:
            lines = cleanup._build_grouped_breakdown_lines(
                [
                    ("build-bot", {"count": 12, "notification_group": "Build Channels", "is_override": False, "deep_clean": False, "days": 7}),
                    ("build-docs", {"count": 8, "notification_group": "Build Channels", "is_override": False, "deep_clean": False, "days": 7}),
                    ("discord-cleanup-gh", {"count": 5, "notification_group": None, "is_override": False, "deep_clean": False, "days": 7}),
                ]
            )

        self.assertEqual(lines[0], "\u3000🗑️ `Build Channels` — **20** deleted across **2** channels")
        self.assertEqual(lines[1], "\u3000🗑️ `#discord-cleanup-gh` — **5** deleted")


if __name__ == "__main__":
    unittest.main()
