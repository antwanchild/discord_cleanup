import logging
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

from tests.support import isolated_module_import


class PurgeChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        class HTTPException(Exception):
            def __init__(self, status, retry_after=3):
                self.status = status
                self.retry_after = retry_after

        self.discord = types.SimpleNamespace(
            Forbidden=type("Forbidden", (Exception,), {}),
            errors=types.SimpleNamespace(HTTPException=HTTPException),
        )
        self.now = datetime(2026, 10, 17, tzinfo=timezone.utc)
        self.bulk_cutoff = self.now - timedelta(days=14)
        self.cleanup = self.enterContext(
            isolated_module_import(
                "cleanup",
                {
                    "discord": self.discord,
                    "config": types.SimpleNamespace(
                        BOT_VERSION="test",
                        CLEAN_TIMES=[],
                        DEFAULT_RETENTION=7,
                        LOG_CHANNEL_ID=1,
                        RETRY_DELAY=3,
                        WARN_UNCONFIGURED=False,
                        raw_channels=[],
                        log=logging.getLogger("test-purge"),
                    ),
                    "stats": types.SimpleNamespace(
                        **{
                            name: Mock()
                            for name in (
                                "record_channel_history",
                                "update_stats",
                                "load_stats",
                                "save_last_run",
                            )
                        }
                    ),
                    "utils": types.SimpleNamespace(
                        **{
                            name: Mock()
                            for name in (
                                "get_next_run_str",
                                "setup_run_log",
                                "update_health",
                            )
                        }
                    ),
                },
            )
        )
        self.sleep = self.enterContext(
            patch.object(self.cleanup.asyncio, "sleep", new_callable=AsyncMock)
        )

    def _message(self, age):
        return types.SimpleNamespace(
            created_at=self.now - timedelta(days=age), delete=AsyncMock()
        )

    def _channel(self, *messages, history_errors=()):
        remaining = list(messages)
        errors = iter(history_errors)

        async def history(*, limit, before):
            error = next(errors, None)
            if error is not None:
                raise error
            for message in [m for m in remaining if m.created_at < before][:limit]:
                yield message

        async def delete_messages(batch):
            for message in batch:
                remaining.remove(message)

        for message in messages:
            message.delete.side_effect = lambda m=message: remaining.remove(m)
        return types.SimpleNamespace(
            name="general",
            guild=types.SimpleNamespace(me=object()),
            permissions_for=Mock(
                return_value=types.SimpleNamespace(
                    read_message_history=True,
                    manage_messages=True,
                )
            ),
            history=Mock(side_effect=history),
            delete_messages=AsyncMock(side_effect=delete_messages),
        )

    async def _purge(self, channel, **options):
        return await self.cleanup.purge_channel(
            channel, 7, self.bulk_cutoff, self.now, **options
        )

    async def test_bulk_delete_respects_retention_and_fourteen_day_boundary(self):
        recent, boundary, first, second, old = [
            self._message(age) for age in (3, 7, 8, 10, 14)
        ]
        channel = self._channel(recent, boundary, first, second, old)
        result = await self._purge(channel)
        channel.delete_messages.assert_awaited_once_with([first, second])
        for message in (recent, boundary, old):
            message.delete.assert_not_awaited()
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["oldest"], second.created_at)
        self.assertIsNone(result["error"])

    async def test_single_message_uses_individual_delete(self):
        message = self._message(8)
        channel = self._channel(message)
        result = await self._purge(channel)
        message.delete.assert_awaited_once()
        channel.delete_messages.assert_not_awaited()
        self.assertEqual(result["count"], 1)

    async def test_deep_clean_deletes_old_messages_individually(self):
        recent, old = self._message(3), self._message(20)
        channel = self._channel(recent, old)
        result = await self._purge(channel, deep_clean=True)
        old.delete.assert_awaited_once()
        recent.delete.assert_not_awaited()
        channel.delete_messages.assert_not_awaited()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["oldest"], old.created_at)

    async def test_dry_run_counts_bulk_and_deep_messages_without_deletion_or_sleep(
        self,
    ):
        messages = [self._message(age) for age in (3, 8, 10, 20)]
        channel = self._channel(*messages)
        result = await self._purge(channel, dry_run=True, deep_clean=True)
        self.assertEqual(result["count"], 3)
        channel.delete_messages.assert_not_awaited()
        for message in messages:
            message.delete.assert_not_awaited()
        self.sleep.assert_not_awaited()

    async def test_rate_limit_retries_and_records_wait(self):
        message = self._message(8)
        channel = self._channel(
            message, history_errors=[self.discord.errors.HTTPException(429, 4)]
        )
        result = await self._purge(channel)
        self.assertEqual(result["rate_limits"], 1)
        self.assertEqual(result["count"], 1)
        self.sleep.assert_any_await(4)
        message.delete.assert_awaited_once()

    async def test_missing_permissions_skip_history_and_deletion(self):
        for permission in ("read_message_history", "manage_messages"):
            with self.subTest(permission=permission):
                channel = self._channel(self._message(8))
                setattr(channel.permissions_for.return_value, permission, False)
                result = await self._purge(channel)
                self.assertEqual(result["count"], -1)
                self.assertIn("Missing", result["error"])
                channel.history.assert_not_called()
                channel.delete_messages.assert_not_awaited()

    async def test_forbidden_returns_error(self):
        channel = self._channel(history_errors=[self.discord.Forbidden()])
        result = await self._purge(channel)
        self.assertEqual(result["count"], -1)
        self.assertIn("Forbidden", result["error"])
        self.sleep.assert_not_awaited()
