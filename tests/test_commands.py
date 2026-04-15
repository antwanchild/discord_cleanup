import asyncio
import logging
import types
import unittest

from tests.support import isolated_module_import


class CommandSendHelperTests(unittest.TestCase):
    def _discord_stub(self):
        class HTTPException(Exception):
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
                return self

        class DummyChoice:
            def __class_getitem__(cls, item):
                return cls

            def __init__(self, name=None, value=None):
                self.name = name
                self.value = value

        class DummyGroup:
            def __init__(self, *args, **kwargs):
                pass

            def command(self, *args, **kwargs):
                def decorator(func):
                    return func
                return decorator

            def error(self, func):
                return func

        def passthrough_decorator(*args, **kwargs):
            def decorator(func):
                return func
            return decorator

        app_commands = types.SimpleNamespace(
            AppCommandError=Exception,
            MissingPermissions=type("MissingPermissions", (Exception,), {}),
            Group=DummyGroup,
            Choice=DummyChoice,
            checks=types.SimpleNamespace(has_permissions=passthrough_decorator),
            describe=passthrough_decorator,
            choices=passthrough_decorator,
        )

        class DummyView:
            def __init__(self, *args, **kwargs):
                pass

        def button(*args, **kwargs):
            def decorator(func):
                return func
            return decorator

        ui = types.SimpleNamespace(View=DummyView, button=button, Button=object)
        return types.SimpleNamespace(
            Embed=DummyEmbed,
            HTTPException=HTTPException,
            Interaction=object,
            TextChannel=object,
            User=object,
            ButtonStyle=types.SimpleNamespace(danger=1, secondary=2),
            ui=ui,
            app_commands=app_commands,
        )

    def _module_stubs(self):
        discord_stub = self._discord_stub()
        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            DEFAULT_RETENTION=7,
            LOG_CHANNEL_ID=1,
            LOG_DIR="/tmp",
            CLEAN_TIMES=["03:00"],
            LOG_MAX_FILES=7,
            LOG_LEVEL="INFO",
            WARN_UNCONFIGURED=False,
            log=logging.getLogger("test-commands"),
        )
        cleanup_stub = types.SimpleNamespace(build_channel_map=lambda *_a, **_k: {}, run_cleanup=lambda *_a, **_k: None, purge_all_channel=lambda *_a, **_k: None)
        notifications_stub = types.SimpleNamespace(
            post_status_report=lambda *_a, **_k: None,
            safe_send_embed=lambda *_a, **_k: True,
            sanitize_embed=lambda embed: embed,
        )
        utils_stub = types.SimpleNamespace(
            get_next_run_str=lambda: "tomorrow",
            get_uptime_str=lambda: "1m",
            reload_channels=lambda: (True, "ok"),
            get_bot=lambda: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        return discord_stub, config_stub, cleanup_stub, notifications_stub, utils_stub

    def test_safe_followup_send_falls_back_to_content(self):
        discord_stub, config_stub, cleanup_stub, notifications_stub, utils_stub = self._module_stubs()

        with isolated_module_import(
            "commands",
            {
                "discord": discord_stub,
                "config": config_stub,
                "cleanup": cleanup_stub,
                "notifications": notifications_stub,
                "utils": utils_stub,
            },
        ) as commands:
            class Followup:
                def __init__(self):
                    self.calls = []

                async def send(self, **kwargs):
                    self.calls.append(kwargs)
                    if kwargs.get("embed") is not None:
                        raise discord_stub.HTTPException("boom")
                    return None

            interaction = types.SimpleNamespace(
                followup=Followup(),
                command=types.SimpleNamespace(name="status"),
            )
            embed = discord_stub.Embed(title="hello")

            result = asyncio.run(
                commands.safe_followup_send(
                    interaction,
                    embed=embed,
                    fallback_text="fallback",
                    ephemeral=True,
                )
            )

        self.assertFalse(result)
        self.assertEqual(len(interaction.followup.calls), 2)
        self.assertEqual(interaction.followup.calls[1]["content"], "fallback")

    def test_safe_response_send_falls_back_to_followup(self):
        discord_stub, config_stub, cleanup_stub, notifications_stub, utils_stub = self._module_stubs()

        with isolated_module_import(
            "commands",
            {
                "discord": discord_stub,
                "config": config_stub,
                "cleanup": cleanup_stub,
                "notifications": notifications_stub,
                "utils": utils_stub,
            },
        ) as commands:
            class Response:
                async def send_message(self, **kwargs):
                    raise discord_stub.HTTPException("boom")

            class Followup:
                def __init__(self):
                    self.calls = []

                async def send(self, **kwargs):
                    self.calls.append(kwargs)
                    return None

            interaction = types.SimpleNamespace(
                response=Response(),
                followup=Followup(),
                command=types.SimpleNamespace(name="purge"),
            )
            embed = discord_stub.Embed(title="hello")

            result = asyncio.run(
                commands.safe_response_send(
                    interaction,
                    embed=embed,
                    fallback_text="fallback",
                    ephemeral=True,
                )
            )

        self.assertFalse(result)
        self.assertEqual(len(interaction.followup.calls), 1)
        self.assertEqual(interaction.followup.calls[0]["content"], "fallback")


if __name__ == "__main__":
    unittest.main()
