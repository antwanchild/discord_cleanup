import asyncio
import logging
import os
import tempfile
import threading
import types
import unittest
from datetime import datetime

from tests.support import isolated_module_import


class SchedulerTests(unittest.TestCase):
    def _build_config_stub(self, config_dir: str):
        return types.SimpleNamespace(
            config_lock=threading.Lock(),
            CONFIG_DIR=config_dir,
            CLEAN_TIMES=["03:00"],
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            log=logging.getLogger("test-scheduler"),
        )

    def test_update_schedule_rewrites_env_and_in_memory_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = os.path.join(tempdir, ".env.discord_cleanup")
            with open(env_path, "w") as f:
                f.write("CLEAN_TIME=03:00\nLOG_LEVEL=INFO\n")

            config_stub = self._build_config_stub(tempdir)
            with isolated_module_import("scheduler", {"config": config_stub}) as scheduler:
                success, message, reschedule_error = scheduler.update_schedule(["05:00", "23:15"])

            self.assertTrue(success)
            self.assertEqual(message, "05:00,23:15")
            self.assertIsNone(reschedule_error)
            self.assertEqual(config_stub.CLEAN_TIMES, ["05:00", "23:15"])
            with open(env_path, "r") as f:
                self.assertIn("CLEAN_TIME=05:00,23:15", f.read())

    def test_update_schedule_rejects_invalid_times_without_writing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_path = os.path.join(tempdir, ".env.discord_cleanup")
            original = "CLEAN_TIME=03:00\n"
            with open(env_path, "w") as f:
                f.write(original)

            config_stub = self._build_config_stub(tempdir)
            with isolated_module_import("scheduler", {"config": config_stub}) as scheduler:
                success, message, reschedule_error = scheduler.update_schedule(["25:00"])

            self.assertFalse(success)
            self.assertIn("is not a valid time", message)
            self.assertIsNone(reschedule_error)
            self.assertEqual(config_stub.CLEAN_TIMES, ["03:00"])
            with open(env_path, "r") as f:
                self.assertEqual(f.read(), original)

    def test_matches_schedule_exception_detects_dates_and_weekdays(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_stub = self._build_config_stub(tempdir)
            config_stub.SCHEDULE_SKIP_DATES = ["2026-04-20"]
            config_stub.SCHEDULE_SKIP_WEEKDAYS = ["thu"]

            with isolated_module_import("scheduler", {"config": config_stub}) as scheduler:
                self.assertTrue(scheduler._matches_schedule_exception(datetime(2026, 4, 20, 3, 0))[0])
                self.assertTrue(scheduler._matches_schedule_exception(datetime(2026, 4, 23, 3, 0))[0])
                self.assertFalse(scheduler._matches_schedule_exception(datetime(2026, 4, 21, 3, 0))[0])


class CleanupRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def _discord_stubs(self):
        class DummyIntents:
            @staticmethod
            def default():
                return types.SimpleNamespace(message_content=False, guilds=False, messages=False)

        class DummyTree:
            def clear_commands(self, guild=None):
                return None

            def add_command(self, command):
                return None

            async def sync(self):
                return None

            def error(self, func):
                return func

        class DummyBot:
            def __init__(self, *args, **kwargs):
                self.guilds = []
                self.tree = DummyTree()
                self.user = "bot"

            def get_channel(self, _channel_id):
                return None

            def event(self, func):
                return func

            async def wait_until_ready(self):
                return None

            async def start(self, _token):
                return None

        class DummyLoop:
            def __init__(self, func):
                self.func = func
                self.next_iteration = None

            def __call__(self, *args, **kwargs):
                return self.func(*args, **kwargs)

            def before_loop(self, func):
                return func

            def is_running(self):
                return False

            def start(self):
                return None

            def cancel(self):
                return None

            def change_interval(self, **kwargs):
                return None

        def loop(*_args, **_kwargs):
            def decorator(func):
                return DummyLoop(func)
            return decorator

        app_commands = types.SimpleNamespace(
            AppCommandError=Exception,
            errors=types.SimpleNamespace(MissingPermissions=type("MissingPermissions", (Exception,), {})),
        )
        commands = types.SimpleNamespace(Bot=DummyBot)
        tasks = types.SimpleNamespace(loop=loop)
        discord_module = types.SimpleNamespace(
            Intents=DummyIntents,
            utils=types.SimpleNamespace(setup_logging=lambda *a, **k: None),
            HTTPException=type("HTTPException", (Exception,), {}),
            Interaction=object,
            app_commands=app_commands,
        )
        return discord_module, commands, tasks

    async def test_run_per_guild_continues_after_failure(self):
        discord_module, commands, tasks = self._discord_stubs()
        logger = logging.getLogger("test-cleanup-bot")
        logger.setLevel(logging.INFO)

        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CATCHUP_MISSED_RUNS=True,
            CLEAN_TIMES=["03:00"],
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            DATA_DIR="/config/data",
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_DIR="/config/logs",
            MISSED_RUN_THRESHOLD_MINUTES=15,
            STATUS_REPORT_TIME="09:00",
            TOKEN="token",
            LOG_LEVEL="INFO",
            REPORT_FREQUENCY="monthly",
            DEFAULT_RETENTION=7,
            config_lock=threading.Lock(),
            log=logger,
        )
        cleanup_stub = types.SimpleNamespace(run_cleanup=lambda *a, **k: None, validate_channels=lambda *_a, **_k: None)
        commands_stub = types.SimpleNamespace(cleanup_group=object())
        notifications_stub = types.SimpleNamespace(
            post_deploy_notification=lambda *a, **k: None,
            post_startup_notification=lambda *a, **k: None,
            post_missed_monthly_report_notification=lambda *a, **k: None,
            post_missed_weekly_report_notification=lambda *a, **k: None,
            post_missed_run_alert=lambda *a, **k: None,
            post_status_report=lambda *a, **k: None,
            post_catchup_notification=lambda *a, **k: None,
        )
        stats_stub = types.SimpleNamespace(
            migrate_stats_categories=lambda *_a, **_k: None,
            load_last_run=lambda: None,
            load_report_state=lambda: {},
            record_catchup_run=lambda: None,
            record_report_sent=lambda *_a, **_k: None,
            record_monthly_report_sent=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            update_health=lambda: None,
            register_task=lambda *_a, **_k: None,
            log_restart_separator=lambda: None,
            set_bot_loop=lambda *_a, **_k: None,
            set_startup_path_status=lambda *_a, **_k: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        web_stub = types.SimpleNamespace(start_web_thread=lambda: None)
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *_a, **_k: None)
        commands_stats_stub = types.SimpleNamespace()

        with isolated_module_import(
            "cleanup_bot",
            {
                "config": config_stub,
                "cleanup": cleanup_stub,
                "commands": commands_stub,
                "commands_stats": commands_stats_stub,
                "file_utils": file_utils_stub,
                "notifications": notifications_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "web": web_stub,
                "discord": discord_module,
                "discord.ext": types.SimpleNamespace(commands=commands, tasks=tasks),
                "discord.ext.commands": commands,
                "discord.ext.tasks": tasks,
            },
        ) as cleanup_bot:
            seen = []

            async def action(guild):
                seen.append(guild.name)
                if guild.name == "first":
                    raise RuntimeError("boom")

            await cleanup_bot._run_per_guild(
                [types.SimpleNamespace(name="first"), types.SimpleNamespace(name="second")],
                action,
                "Test action",
            )

        self.assertEqual(seen, ["first", "second"])

    async def test_find_missed_run_time_skips_blackout_dates(self):
        discord_module, commands, tasks = self._discord_stubs()
        logger = logging.getLogger("test-cleanup-bot")
        logger.setLevel(logging.INFO)

        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CATCHUP_MISSED_RUNS=True,
            CLEAN_TIMES=["03:00"],
            SCHEDULE_SKIP_DATES=["2026-04-20"],
            SCHEDULE_SKIP_WEEKDAYS=[],
            DATA_DIR="/config/data",
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_DIR="/config/logs",
            MISSED_RUN_THRESHOLD_MINUTES=15,
            STATUS_REPORT_TIME="09:00",
            TOKEN="token",
            LOG_LEVEL="INFO",
            REPORT_FREQUENCY="monthly",
            DEFAULT_RETENTION=7,
            config_lock=threading.Lock(),
            log=logger,
        )
        cleanup_stub = types.SimpleNamespace(run_cleanup=lambda *a, **k: None, validate_channels=lambda *_a, **_k: None)
        commands_stub = types.SimpleNamespace(cleanup_group=object())
        notifications_stub = types.SimpleNamespace(
            post_deploy_notification=lambda *a, **k: None,
            post_startup_notification=lambda *a, **k: None,
            post_missed_monthly_report_notification=lambda *a, **k: None,
            post_missed_weekly_report_notification=lambda *a, **k: None,
            post_missed_run_alert=lambda *a, **k: None,
            post_status_report=lambda *a, **k: None,
            post_catchup_notification=lambda *a, **k: None,
        )
        stats_stub = types.SimpleNamespace(
            migrate_stats_categories=lambda *_a, **_k: None,
            load_last_run=lambda: {"timestamp": "2026-04-19 02:00:00"},
            load_report_state=lambda: {},
            record_catchup_run=lambda: None,
            record_report_sent=lambda *_a, **_k: None,
            record_monthly_report_sent=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            update_health=lambda: None,
            register_task=lambda *_a, **_k: None,
            log_restart_separator=lambda: None,
            set_bot_loop=lambda *_a, **_k: None,
            set_startup_path_status=lambda *_a, **_k: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        web_stub = types.SimpleNamespace(start_web_thread=lambda: None)
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *_a, **_k: None)
        commands_stats_stub = types.SimpleNamespace()

        with isolated_module_import(
            "cleanup_bot",
            {
                "config": config_stub,
                "cleanup": cleanup_stub,
                "commands": commands_stub,
                "commands_stats": commands_stats_stub,
                "file_utils": file_utils_stub,
                "notifications": notifications_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "web": web_stub,
                "discord": discord_module,
                "discord.ext": types.SimpleNamespace(commands=commands, tasks=tasks),
                "discord.ext.commands": commands,
                "discord.ext.tasks": tasks,
            },
        ) as cleanup_bot:
            result = cleanup_bot._find_missed_run_time(
                datetime(2026, 4, 19, 2, 0, 0),
                datetime(2026, 4, 21, 4, 0, 0),
                ["03:00"],
            )

        self.assertEqual(result.strftime("%Y-%m-%d %H:%M:%S"), "2026-04-21 03:00:00")

    async def test_monthly_report_catchup_posts_when_report_was_missed(self):
        discord_module, commands, tasks = self._discord_stubs()
        logger = logging.getLogger("test-cleanup-bot-report")
        logger.setLevel(logging.INFO)

        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CATCHUP_MISSED_RUNS=True,
            CLEAN_TIMES=["03:00"],
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            DATA_DIR="/config/data",
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_DIR="/config/logs",
            MISSED_RUN_THRESHOLD_MINUTES=15,
            STATUS_REPORT_TIME="09:00",
            TOKEN="token",
            LOG_LEVEL="INFO",
            REPORT_FREQUENCY="monthly",
            DEFAULT_RETENTION=7,
            config_lock=threading.Lock(),
            log=logger,
        )
        cleanup_stub = types.SimpleNamespace(run_cleanup=lambda *a, **k: None, validate_channels=lambda *_a, **_k: None)
        commands_stub = types.SimpleNamespace(cleanup_group=object())
        posted = []
        missed_notices = []
        async def post_status_report(bot, guild, label="monthly"):
            posted.append((guild.name, label))

        async def post_missed_monthly_report_notification(bot, missed_month):
            missed_notices.append(missed_month)

        notifications_stub = types.SimpleNamespace(
            post_deploy_notification=lambda *a, **k: None,
            post_startup_notification=lambda *a, **k: None,
            post_missed_monthly_report_notification=post_missed_monthly_report_notification,
            post_missed_weekly_report_notification=lambda *a, **k: None,
            post_missed_run_alert=lambda *a, **k: None,
            post_status_report=post_status_report,
            post_catchup_notification=lambda *a, **k: None,
        )
        stats_stub = types.SimpleNamespace(
            migrate_stats_categories=lambda *_a, **_k: None,
            load_last_run=lambda: None,
            load_report_state=lambda: {},
            record_catchup_run=lambda: None,
            record_report_sent=lambda *_a, **_k: None,
            record_monthly_report_sent=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            update_health=lambda: None,
            register_task=lambda *_a, **_k: None,
            log_restart_separator=lambda: None,
            set_bot_loop=lambda *_a, **_k: None,
            set_startup_path_status=lambda *_a, **_k: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        web_stub = types.SimpleNamespace(start_web_thread=lambda: None)
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *_a, **_k: None)
        commands_stats_stub = types.SimpleNamespace()

        with isolated_module_import(
            "cleanup_bot",
            {
                "config": config_stub,
                "cleanup": cleanup_stub,
                "commands": commands_stub,
                "commands_stats": commands_stats_stub,
                "file_utils": file_utils_stub,
                "notifications": notifications_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "web": web_stub,
                "discord": discord_module,
                "discord.ext": types.SimpleNamespace(commands=commands, tasks=tasks),
                "discord.ext.commands": commands,
                "discord.ext.tasks": tasks,
            },
        ) as cleanup_bot:
            cleanup_bot._monthly_report_is_due = lambda _moment: True
            cleanup_bot.bot.guilds = [types.SimpleNamespace(name="alpha")]
            await cleanup_bot._check_and_catchup_monthly_report(cleanup_bot.bot)

        self.assertEqual(missed_notices, ["May 2026"])
        self.assertEqual(posted, [("alpha", "monthly")])

    async def test_monthly_report_catchup_handles_both_labels(self):
        discord_module, commands, tasks = self._discord_stubs()
        logger = logging.getLogger("test-cleanup-bot-report-both")
        logger.setLevel(logging.INFO)

        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CATCHUP_MISSED_RUNS=True,
            CLEAN_TIMES=["03:00"],
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            DATA_DIR="/config/data",
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_DIR="/config/logs",
            MISSED_RUN_THRESHOLD_MINUTES=15,
            STATUS_REPORT_TIME="09:00",
            TOKEN="token",
            LOG_LEVEL="INFO",
            REPORT_FREQUENCY="both",
            DEFAULT_RETENTION=7,
            config_lock=threading.Lock(),
            log=logger,
        )
        cleanup_stub = types.SimpleNamespace(run_cleanup=lambda *a, **k: None, validate_channels=lambda *_a, **_k: None)
        commands_stub = types.SimpleNamespace(cleanup_group=object())
        notices = []
        posts = []

        async def post_missed_monthly_report_notification(bot, missed_period):
            notices.append(("monthly", missed_period))

        async def post_missed_weekly_report_notification(bot, missed_period):
            notices.append(("weekly", missed_period))

        async def post_status_report(bot, guild, label="monthly"):
            posts.append((guild.name, label))

        notifications_stub = types.SimpleNamespace(
            post_deploy_notification=lambda *a, **k: None,
            post_startup_notification=lambda *a, **k: None,
            post_missed_monthly_report_notification=post_missed_monthly_report_notification,
            post_missed_weekly_report_notification=post_missed_weekly_report_notification,
            post_missed_run_alert=lambda *a, **k: None,
            post_status_report=post_status_report,
            post_catchup_notification=lambda *a, **k: None,
        )
        stats_stub = types.SimpleNamespace(
            migrate_stats_categories=lambda *_a, **_k: None,
            load_last_run=lambda: None,
            load_report_state=lambda: {},
            record_catchup_run=lambda: None,
            record_report_sent=lambda *_a, **_k: None,
            record_monthly_report_sent=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            update_health=lambda: None,
            register_task=lambda *_a, **_k: None,
            log_restart_separator=lambda: None,
            set_bot_loop=lambda *_a, **_k: None,
            set_startup_path_status=lambda *_a, **_k: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        web_stub = types.SimpleNamespace(start_web_thread=lambda: None)
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *_a, **_k: None)
        commands_stats_stub = types.SimpleNamespace()

        with isolated_module_import(
            "cleanup_bot",
            {
                "config": config_stub,
                "cleanup": cleanup_stub,
                "commands": commands_stub,
                "commands_stats": commands_stats_stub,
                "file_utils": file_utils_stub,
                "notifications": notifications_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "web": web_stub,
                "discord": discord_module,
                "discord.ext": types.SimpleNamespace(commands=commands, tasks=tasks),
                "discord.ext.commands": commands,
                "discord.ext.tasks": tasks,
            },
        ) as cleanup_bot:
            cleanup_bot._report_labels_due = lambda _moment: ["monthly", "weekly"]
            cleanup_bot._missed_report_period_text = lambda label, _moment: f"{label}-period"
            cleanup_bot.bot.guilds = [types.SimpleNamespace(name="alpha")]
            await cleanup_bot._check_and_catchup_monthly_report(cleanup_bot.bot)

        self.assertEqual(notices, [("monthly", "monthly-period"), ("weekly", "weekly-period")])
        self.assertEqual(posts, [("alpha", "monthly"), ("alpha", "weekly")])

    async def test_report_labels_due_respects_frequency_setting(self):
        discord_module, commands, tasks = self._discord_stubs()
        logger = logging.getLogger("test-cleanup-bot-report-due")
        logger.setLevel(logging.INFO)

        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CATCHUP_MISSED_RUNS=True,
            CLEAN_TIMES=["03:00"],
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            DATA_DIR="/config/data",
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_DIR="/config/logs",
            MISSED_RUN_THRESHOLD_MINUTES=15,
            STATUS_REPORT_TIME="09:00",
            TOKEN="token",
            LOG_LEVEL="INFO",
            REPORT_FREQUENCY="monthly",
            DEFAULT_RETENTION=7,
            config_lock=threading.Lock(),
            log=logger,
        )
        cleanup_stub = types.SimpleNamespace(run_cleanup=lambda *a, **k: None, validate_channels=lambda *_a, **_k: None)
        commands_stub = types.SimpleNamespace(cleanup_group=object())
        notifications_stub = types.SimpleNamespace(
            post_deploy_notification=lambda *a, **k: None,
            post_startup_notification=lambda *a, **k: None,
            post_missed_monthly_report_notification=lambda *a, **k: None,
            post_missed_weekly_report_notification=lambda *a, **k: None,
            post_missed_run_alert=lambda *a, **k: None,
            post_status_report=lambda *a, **k: None,
            post_catchup_notification=lambda *a, **k: None,
        )
        stats_stub = types.SimpleNamespace(
            migrate_stats_categories=lambda *_a, **_k: None,
            load_last_run=lambda: None,
            load_report_state=lambda: {},
            record_catchup_run=lambda: None,
            record_report_sent=lambda *_a, **_k: None,
            record_monthly_report_sent=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            update_health=lambda: None,
            register_task=lambda *_a, **_k: None,
            log_restart_separator=lambda: None,
            set_bot_loop=lambda *_a, **_k: None,
            set_startup_path_status=lambda *_a, **_k: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        web_stub = types.SimpleNamespace(start_web_thread=lambda: None)
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *_a, **_k: None)
        commands_stats_stub = types.SimpleNamespace()

        with isolated_module_import(
            "cleanup_bot",
            {
                "config": config_stub,
                "cleanup": cleanup_stub,
                "commands": commands_stub,
                "commands_stats": commands_stats_stub,
                "file_utils": file_utils_stub,
                "notifications": notifications_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "web": web_stub,
                "discord": discord_module,
                "discord.ext": types.SimpleNamespace(commands=commands, tasks=tasks),
                "discord.ext.commands": commands,
                "discord.ext.tasks": tasks,
            },
        ) as cleanup_bot:
            cleanup_bot.load_report_state = lambda: {}
            config_stub.REPORT_FREQUENCY = "monthly"
            monthly_labels = cleanup_bot._report_labels_due(cleanup_bot.datetime(2026, 6, 2, 10, 0, 0, tzinfo=cleanup_bot.TASK_TZ))
            config_stub.REPORT_FREQUENCY = "weekly"
            weekly_labels = cleanup_bot._report_labels_due(cleanup_bot.datetime(2026, 6, 2, 10, 0, 0, tzinfo=cleanup_bot.TASK_TZ))
            config_stub.REPORT_FREQUENCY = "both"
            both_labels = cleanup_bot._report_labels_due(cleanup_bot.datetime(2026, 6, 1, 10, 0, 0, tzinfo=cleanup_bot.TASK_TZ))

        self.assertEqual(monthly_labels, ["monthly"])
        self.assertEqual(weekly_labels, ["weekly"])
        self.assertEqual(both_labels, ["monthly", "weekly"])

    async def test_monthly_report_catchup_skips_when_current_month_already_sent(self):
        discord_module, commands, tasks = self._discord_stubs()
        logger = logging.getLogger("test-cleanup-bot-report-skip")
        logger.setLevel(logging.INFO)

        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CATCHUP_MISSED_RUNS=True,
            CLEAN_TIMES=["03:00"],
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            DATA_DIR="/config/data",
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_DIR="/config/logs",
            MISSED_RUN_THRESHOLD_MINUTES=15,
            STATUS_REPORT_TIME="09:00",
            TOKEN="token",
            LOG_LEVEL="INFO",
            REPORT_FREQUENCY="monthly",
            DEFAULT_RETENTION=7,
            config_lock=threading.Lock(),
            log=logger,
        )
        cleanup_stub = types.SimpleNamespace(run_cleanup=lambda *a, **k: None, validate_channels=lambda *_a, **_k: None)
        commands_stub = types.SimpleNamespace(cleanup_group=object())
        posted = []
        async def post_status_report(bot, guild, label="monthly"):
            posted.append((guild.name, label))

        notifications_stub = types.SimpleNamespace(
            post_deploy_notification=lambda *a, **k: None,
            post_startup_notification=lambda *a, **k: None,
            post_missed_monthly_report_notification=lambda *a, **k: None,
            post_missed_weekly_report_notification=lambda *a, **k: None,
            post_missed_run_alert=lambda *a, **k: None,
            post_status_report=post_status_report,
            post_catchup_notification=lambda *a, **k: None,
        )
        stats_stub = types.SimpleNamespace(
            migrate_stats_categories=lambda *_a, **_k: None,
            load_last_run=lambda: None,
            load_report_state=lambda: {"monthly": {"last_sent": "2026-05"}},
            record_catchup_run=lambda: None,
            record_report_sent=lambda *_a, **_k: None,
            record_monthly_report_sent=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            update_health=lambda: None,
            register_task=lambda *_a, **_k: None,
            log_restart_separator=lambda: None,
            set_bot_loop=lambda *_a, **_k: None,
            set_startup_path_status=lambda *_a, **_k: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        web_stub = types.SimpleNamespace(start_web_thread=lambda: None)
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *_a, **_k: None)
        commands_stats_stub = types.SimpleNamespace()

        with isolated_module_import(
            "cleanup_bot",
            {
                "config": config_stub,
                "cleanup": cleanup_stub,
                "commands": commands_stub,
                "commands_stats": commands_stats_stub,
                "file_utils": file_utils_stub,
                "notifications": notifications_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "web": web_stub,
                "discord": discord_module,
                "discord.ext": types.SimpleNamespace(commands=commands, tasks=tasks),
                "discord.ext.commands": commands,
                "discord.ext.tasks": tasks,
            },
        ) as cleanup_bot:
            cleanup_bot._monthly_report_is_due = lambda _moment: False
            cleanup_bot.bot.guilds = [types.SimpleNamespace(name="alpha")]
            await cleanup_bot._check_and_catchup_monthly_report(cleanup_bot.bot)

        self.assertEqual(posted, [])

    async def test_monthly_report_is_due_checks_time_and_state(self):
        discord_module, commands, tasks = self._discord_stubs()
        logger = logging.getLogger("test-cleanup-bot-report-due")
        logger.setLevel(logging.INFO)

        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CATCHUP_MISSED_RUNS=True,
            CLEAN_TIMES=["03:00"],
            SCHEDULE_SKIP_DATES=[],
            SCHEDULE_SKIP_WEEKDAYS=[],
            DATA_DIR="/config/data",
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_DIR="/config/logs",
            MISSED_RUN_THRESHOLD_MINUTES=15,
            STATUS_REPORT_TIME="09:00",
            TOKEN="token",
            LOG_LEVEL="INFO",
            REPORT_FREQUENCY="monthly",
            DEFAULT_RETENTION=7,
            config_lock=threading.Lock(),
            log=logger,
        )
        cleanup_stub = types.SimpleNamespace(run_cleanup=lambda *a, **k: None, validate_channels=lambda *_a, **_k: None)
        commands_stub = types.SimpleNamespace(cleanup_group=object())
        async def post_status_report(bot, guild, label="monthly"):
            return None

        notifications_stub = types.SimpleNamespace(
            post_deploy_notification=lambda *a, **k: None,
            post_startup_notification=lambda *a, **k: None,
            post_missed_monthly_report_notification=lambda *a, **k: None,
            post_missed_weekly_report_notification=lambda *a, **k: None,
            post_missed_run_alert=lambda *a, **k: None,
            post_status_report=post_status_report,
            post_catchup_notification=lambda *a, **k: None,
        )
        stats_stub = types.SimpleNamespace(
            migrate_stats_categories=lambda *_a, **_k: None,
            load_last_run=lambda: None,
            load_report_state=lambda: {},
            record_catchup_run=lambda: None,
            record_report_sent=lambda *_a, **_k: None,
            record_monthly_report_sent=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            update_health=lambda: None,
            register_task=lambda *_a, **_k: None,
            log_restart_separator=lambda: None,
            set_bot_loop=lambda *_a, **_k: None,
            set_startup_path_status=lambda *_a, **_k: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        web_stub = types.SimpleNamespace(start_web_thread=lambda: None)
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *_a, **_k: None)
        commands_stats_stub = types.SimpleNamespace()

        with isolated_module_import(
            "cleanup_bot",
            {
                "config": config_stub,
                "cleanup": cleanup_stub,
                "commands": commands_stub,
                "commands_stats": commands_stats_stub,
                "file_utils": file_utils_stub,
                "notifications": notifications_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "web": web_stub,
                "discord": discord_module,
                "discord.ext": types.SimpleNamespace(commands=commands, tasks=tasks),
                "discord.ext.commands": commands,
                "discord.ext.tasks": tasks,
            },
        ) as cleanup_bot:
            cleanup_bot.load_report_state = lambda: {"monthly": {"last_sent": "2026-04"}}
            self.assertTrue(cleanup_bot._monthly_report_is_due(cleanup_bot.datetime(2026, 5, 2, 10, 0, 0, tzinfo=cleanup_bot.TASK_TZ)))
            cleanup_bot.load_report_state = lambda: {"monthly": {"last_sent": "2026-05"}}
            self.assertFalse(cleanup_bot._monthly_report_is_due(cleanup_bot.datetime(2026, 5, 2, 10, 0, 0, tzinfo=cleanup_bot.TASK_TZ)))
            cleanup_bot.load_report_state = lambda: {}
            self.assertFalse(cleanup_bot._monthly_report_is_due(cleanup_bot.datetime(2026, 5, 1, 8, 59, 0, tzinfo=cleanup_bot.TASK_TZ)))

    async def test_log_startup_path_check_reports_expected_paths(self):
        discord_module, commands, tasks = self._discord_stubs()
        logger = logging.getLogger("test-cleanup-bot-paths")
        logger.setLevel(logging.INFO)

        config_stub = types.SimpleNamespace(
            BOT_VERSION="1.0.0",
            CATCHUP_MISSED_RUNS=True,
            CLEAN_TIMES=["03:00"],
            DATA_DIR="/config/data",
            CONFIG_DIR="/tmp",
            HEALTH_FILE="/tmp/health",
            LOG_DIR="/config/logs",
            MISSED_RUN_THRESHOLD_MINUTES=15,
            STATUS_REPORT_TIME="09:00",
            TOKEN="token",
            LOG_LEVEL="INFO",
            REPORT_FREQUENCY="monthly",
            DEFAULT_RETENTION=7,
            config_lock=threading.Lock(),
            log=logger,
        )
        cleanup_stub = types.SimpleNamespace(run_cleanup=lambda *a, **k: None, validate_channels=lambda *_a, **_k: None)
        commands_stub = types.SimpleNamespace(cleanup_group=object())
        notifications_stub = types.SimpleNamespace(
            post_deploy_notification=lambda *a, **k: None,
            post_startup_notification=lambda *a, **k: None,
            post_missed_monthly_report_notification=lambda *a, **k: None,
            post_missed_weekly_report_notification=lambda *a, **k: None,
            post_missed_run_alert=lambda *a, **k: None,
            post_status_report=lambda *a, **k: None,
            post_catchup_notification=lambda *a, **k: None,
        )
        stats_stub = types.SimpleNamespace(
            migrate_stats_categories=lambda *_a, **_k: None,
            load_last_run=lambda: None,
            load_report_state=lambda: {},
            record_catchup_run=lambda: None,
            record_report_sent=lambda *_a, **_k: None,
            record_monthly_report_sent=lambda *_a, **_k: None,
        )
        utils_stub = types.SimpleNamespace(
            update_health=lambda: None,
            register_task=lambda *_a, **_k: None,
            log_restart_separator=lambda: None,
            set_bot_loop=lambda *_a, **_k: None,
            set_startup_path_status=lambda *_a, **_k: None,
            is_run_in_progress=lambda: False,
            release_run=lambda: None,
            try_acquire_run=lambda *_a, **_k: True,
        )
        web_stub = types.SimpleNamespace(start_web_thread=lambda: None)
        file_utils_stub = types.SimpleNamespace(atomic_write_text=lambda *_a, **_k: None)
        commands_stats_stub = types.SimpleNamespace()

        with isolated_module_import(
            "cleanup_bot",
            {
                "config": config_stub,
                "cleanup": cleanup_stub,
                "commands": commands_stub,
                "commands_stats": commands_stats_stub,
                "file_utils": file_utils_stub,
                "notifications": notifications_stub,
                "stats": stats_stub,
                "utils": utils_stub,
                "web": web_stub,
                "discord": discord_module,
                "discord.ext": types.SimpleNamespace(commands=commands, tasks=tasks),
                "discord.ext.commands": commands,
                "discord.ext.tasks": tasks,
            },
        ) as cleanup_bot:
            cleanup_bot._probe_writable_directory = lambda path: (True, "OK")
            cleanup_bot._probe_writable_file = lambda path: (False, "denied")

            checks = cleanup_bot.log_startup_path_check()

        self.assertEqual(checks["/config/data"], (True, "OK"))
        self.assertEqual(checks["/config/logs"], (True, "OK"))
        self.assertEqual(checks["/tmp/health"], (False, "denied"))


if __name__ == "__main__":
    unittest.main()
