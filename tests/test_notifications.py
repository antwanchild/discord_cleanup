import asyncio
import os
import tempfile
import types
import unittest

from tests.support import isolated_module_import


class NotificationGroupingTests(unittest.TestCase):
    def _discord_stub(self):
        class HTTPException(Exception):
            pass

        class Forbidden(Exception):
            pass

        class DummyEmbed:
            def __init__(self, title=None, description=None, color=None, timestamp=None):
                self.title = title
                self.description = description
                self.color = color
                self.timestamp = timestamp
                self._fields = []
                self._footer = {}

            def add_field(self, *, name, value, inline=False):
                self._fields.append({"name": name, "value": value, "inline": inline})

            def clear_fields(self):
                self._fields = []

            def set_footer(self, *, text):
                self._footer = {"text": text}

        return types.SimpleNamespace(Embed=DummyEmbed, HTTPException=HTTPException, Forbidden=Forbidden)

    def _module_stubs(self):
        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            DATA_DIR="/tmp",
            GITHUB_TOKEN=None,
            LAST_VERSION_FILE="/tmp/last_version",
            LOG_CHANNEL_ID=1,
            MISSED_RUN_THRESHOLD_MINUTES=15,
            REPORT_CHANNEL_ID=2,
            WARN_UNCONFIGURED=False,
            log=types.SimpleNamespace(
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
                exception=lambda *a, **k: None,
            ),
        )
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *a, **k: None)
        stats_stub = types.SimpleNamespace(load_stats=lambda: {})
        utils_stub = types.SimpleNamespace(get_next_run_str=lambda: "tomorrow")
        return config_stub, file_utils_stub, stats_stub, utils_stub, self._discord_stub()

    def test_build_notification_leaderboard_groups_channels_for_report_only(self):
        config_stub, file_utils_stub, stats_stub, utils_stub, discord_stub = self._module_stubs()

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

    def test_sanitize_embed_trims_fields_to_discord_limits(self):
        config_stub, file_utils_stub, stats_stub, utils_stub, discord_stub = self._module_stubs()

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
            embed = discord_stub.Embed(title="t" * 300, description="d" * 3500)
            embed.add_field(name="n" * 300, value="v" * 1500, inline=False)
            embed.set_footer(text="f" * 1800)

            notifications.sanitize_embed(embed)

        self.assertEqual(len(embed.title), notifications.EMBED_TITLE_LIMIT)
        self.assertLessEqual(len(embed.description), notifications.EMBED_DESCRIPTION_LIMIT)
        self.assertEqual(len(embed._fields[0]["name"]), notifications.EMBED_FIELD_NAME_LIMIT)
        self.assertLessEqual(len(embed._fields[0]["value"]), notifications.EMBED_FIELD_VALUE_LIMIT)
        self.assertLessEqual(len(embed._footer["text"]), notifications.EMBED_FOOTER_LIMIT)
        self.assertLessEqual(notifications._embed_text_length(embed), notifications.EMBED_TOTAL_LIMIT)

    def test_safe_send_embed_uses_plain_text_fallback_on_http_error(self):
        config_stub, file_utils_stub, stats_stub, utils_stub, discord_stub = self._module_stubs()

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
            class Channel:
                def __init__(self):
                    self.calls = []

                async def send(self, **kwargs):
                    self.calls.append(kwargs)
                    if "embed" in kwargs:
                        raise discord_stub.HTTPException("too big")
                    return None

            channel = Channel()
            embed = discord_stub.Embed(title="Report", description="payload")

            result = asyncio.run(
                notifications.safe_send_embed(
                    channel,
                    embed,
                    fallback_text="short fallback",
                    context="test notification",
                )
            )

        self.assertFalse(result)
        self.assertEqual(len(channel.calls), 2)
        self.assertIn("embed", channel.calls[0])
        self.assertEqual(channel.calls[1]["content"], "short fallback")

    def test_safe_send_embed_returns_false_for_forbidden_without_fallback_send(self):
        config_stub, file_utils_stub, stats_stub, utils_stub, discord_stub = self._module_stubs()

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
            class Channel:
                def __init__(self):
                    self.calls = []

                async def send(self, **kwargs):
                    self.calls.append(kwargs)
                    raise discord_stub.Forbidden("nope")

            channel = Channel()
            embed = discord_stub.Embed(title="Report", description="payload")

            result = asyncio.run(
                notifications.safe_send_embed(
                    channel,
                    embed,
                    fallback_text="short fallback",
                    context="test forbidden notification",
                )
            )

        self.assertFalse(result)
        self.assertEqual(len(channel.calls), 1)
        self.assertIn("embed", channel.calls[0])

    def test_load_recent_changelog_entries_reads_markdown_changelog(self):
        config_stub, file_utils_stub, stats_stub, utils_stub, discord_stub = self._module_stubs()

        with tempfile.TemporaryDirectory() as tempdir:
            changelog_path = os.path.join(tempdir, "CHANGELOG.md")
            with open(changelog_path, "w") as f:
                f.write(
                    "# Changelog\n\n"
                    "## Unreleased\n\n"
                    "### Changes\n"
                    "- Pending UI polish\n\n"
                    "## 5.6.0 - 2026-04-15\n\n"
                    "### Changes\n"
                    "- Add backup visibility\n\n"
                    "## 5.5.14 - 2026-04-15\n\n"
                    "### Changes\n"
                    "- Fix dashboard rendering\n"
                )

            cwd = os.getcwd()
            try:
                os.chdir(tempdir)
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
                    entries = notifications._load_recent_changelog_entries(last_version="5.5.14")
            finally:
                os.chdir(cwd)

        self.assertEqual(
            entries,
            [
                "- Pending UI polish",
                "- Add backup visibility `(5.6.0)`",
            ],
        )


if __name__ == "__main__":
    unittest.main()
