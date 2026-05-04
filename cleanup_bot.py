"""
cleanup_bot.py — Discord bot entry point. Initialises the bot, schedules tasks,
and wires together cleanup, reporting, health, and web UI threads.
"""
import asyncio
import signal
import warnings
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
import logging
import tempfile

warnings.filterwarnings("ignore", message=".*PyNaCl.*")
warnings.filterwarnings("ignore", message=".*davey.*")

from config import (
    BOT_VERSION, CATCHUP_MISSED_RUNS, CLEAN_TIMES, DATA_DIR, HEALTH_FILE,
    MISSED_RUN_THRESHOLD_MINUTES, STATUS_REPORT_TIME, TOKEN, LOG_DIR, LOG_LEVEL,
    log
)
import config as cfg
from cleanup import run_cleanup, validate_channels
from commands import cleanup_group
import commands_stats
from file_utils import atomic_write_text
from notifications import (
    post_deploy_notification, post_startup_notification,
    post_missed_monthly_report_notification, post_missed_run_alert,
    post_missed_weekly_report_notification,
    post_status_report, post_catchup_notification,
)
from stats import migrate_stats_categories, load_last_run, load_report_state, record_catchup_run
from scheduler import _matches_schedule_exception
from utils import (
    update_health,
    register_task,
    log_restart_separator,
    set_bot_loop,
    set_startup_path_status,
    is_run_in_progress,
    release_run,
    try_acquire_run,
)
from web import start_web_thread

# --- Discord logging suppression ---
discord_log_level = logging.DEBUG if LOG_LEVEL == "DEBUG" else logging.WARNING
logging.getLogger("discord").setLevel(discord_log_level)
logging.getLogger("discord.http").setLevel(discord_log_level)
logging.getLogger("discord.gateway").setLevel(discord_log_level)
logging.getLogger("discord").propagate = True
discord.utils.setup_logging = lambda *args, **kwargs: None


# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix=None, intents=intents)


# --- Scheduler Setup ---

def build_task_times():
    """Builds timezone-aware datetime.time objects for discord.ext.tasks."""
    tz_name = os.getenv("TZ")
    if not tz_name:
        log.warning("TZ not set in environment — defaulting to UTC. Set TZ in your compose file to use local time.")
        tz_name = "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.warning(f"Unknown timezone '{tz_name}' — falling back to UTC")
        tz = ZoneInfo("UTC")

    times = []
    for t in CLEAN_TIMES:
        hour, minute = map(int, t.split(":"))
        times.append(dtime(hour=hour, minute=minute, tzinfo=tz))

    return times, tz


def build_report_time(tz):
    """Builds timezone-aware datetime.time for the monthly report check."""
    hour, minute = map(int, STATUS_REPORT_TIME.split(":"))
    return dtime(hour=hour, minute=minute, tzinfo=tz)


task_times, TASK_TZ = build_task_times()
report_time = build_report_time(TASK_TZ)


def _report_state_month_key(moment: datetime) -> str:
    """Returns the YYYY-MM key used to track monthly report delivery."""
    return moment.strftime("%Y-%m")


def _report_state_week_key(moment: datetime) -> str:
    """Returns the ISO week key used to track weekly report delivery."""
    iso_year, iso_week, _weekday = moment.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _report_sent_for_current_month(moment: datetime) -> bool:
    """Checks whether the current month's monthly report has already been posted."""
    report_state = load_report_state()
    monthly = report_state.get("monthly", {}) if isinstance(report_state, dict) else {}
    return monthly.get("last_sent") == _report_state_month_key(moment)


def _report_sent_for_current_week(moment: datetime) -> bool:
    """Checks whether the current week's weekly report has already been posted."""
    report_state = load_report_state()
    weekly = report_state.get("weekly", {}) if isinstance(report_state, dict) else {}
    return weekly.get("last_sent") == _report_state_week_key(moment)


def _month_report_due_time(moment: datetime) -> datetime:
    """Returns the scheduled time for the monthly report in the current month."""
    return datetime(
        moment.year,
        moment.month,
        1,
        report_time.hour,
        report_time.minute,
        tzinfo=TASK_TZ,
    )


def _week_report_due_time(moment: datetime) -> datetime:
    """Returns the scheduled time for the weekly report in the current ISO week."""
    monday = moment - timedelta(days=moment.weekday())
    return datetime(
        monday.year,
        monday.month,
        monday.day,
        report_time.hour,
        report_time.minute,
        tzinfo=TASK_TZ,
    )


def _report_labels_due(moment: datetime) -> list[str]:
    """Returns the report labels that should have already posted by now."""
    import config

    freq = config.REPORT_FREQUENCY
    labels = []

    if freq in {"monthly", "both"} and not _report_sent_for_current_month(moment) and moment >= _month_report_due_time(moment):
        labels.append("monthly")
    if freq in {"weekly", "both"} and not _report_sent_for_current_week(moment) and moment >= _week_report_due_time(moment):
        labels.append("weekly")
    return labels


def _missed_report_period_text(label: str, moment: datetime) -> str:
    """Returns a human-readable period description for missed report notifications."""
    if label == "monthly":
        return moment.strftime("%B %Y")
    if label == "weekly":
        return f"week of {_week_report_due_time(moment).strftime('%Y-%m-%d')}"
    return moment.strftime("%Y-%m-%d")


def _monthly_report_is_due(moment: datetime) -> bool:
    """Returns True when the monthly report should have already been posted."""
    return "monthly" in _report_labels_due(moment)


def _weekly_report_is_due(moment: datetime) -> bool:
    """Returns True when the weekly report should have already been posted."""
    return "weekly" in _report_labels_due(moment)


async def _run_per_guild(guilds, action, action_name: str):
    """Runs an async action for each guild without letting one failure stop the rest."""
    for guild in guilds:
        try:
            await action(guild)
        except Exception:
            log.exception("%s failed for guild=%s", action_name, getattr(guild, "name", guild))


def _probe_writable_directory(path: str) -> tuple[bool, str]:
    """Best-effort writable check for a directory path."""
    try:
        os.makedirs(path, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=path)
        os.close(fd)
        os.remove(temp_path)
        return True, "OK"
    except Exception as e:
        return False, str(e)


def _probe_writable_file(path: str) -> tuple[bool, str]:
    """Best-effort writable check for a file path."""
    try:
        atomic_write_text(path, datetime.now().isoformat())
        return True, "OK"
    except Exception as e:
        return False, str(e)


def log_startup_path_check() -> dict[str, tuple[bool, str]]:
    """Logs startup writable-path checks for key bot storage paths."""
    checks = {
        DATA_DIR: _probe_writable_directory(DATA_DIR),
        LOG_DIR: _probe_writable_directory(LOG_DIR),
        HEALTH_FILE: _probe_writable_file(HEALTH_FILE),
    }
    summary = " | ".join(
        f"{path}: {'OK' if status else 'FAIL'}"
        for path, (status, _detail) in checks.items()
    )
    log.info("Startup path check | %s", summary)
    for path, (status, detail) in checks.items():
        if not status:
            log.warning("Startup path check failed | path=%s | error=%s", path, detail)
    set_startup_path_status(checks)
    return checks


@tasks.loop(time=task_times)
async def cleanup_task():
    """Runs scheduled cleanup for all guilds."""
    now = datetime.now(TASK_TZ)
    if is_run_in_progress():
        log.warning("Scheduled cleanup skipped because another cleanup operation is already running")
        return
    if not try_acquire_run("scheduler"):
        log.warning("Scheduled cleanup skipped because another cleanup operation is already running")
        return

    try:
        skipped, reason = _matches_schedule_exception(now)
        if skipped:
            log.info("Scheduled cleanup skipped due to configured exception (%s)", reason)
            return

        # Missed run detection
        for t in CLEAN_TIMES:
            hour, minute = map(int, t.split(":"))
            expected = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            delay = (now - expected).total_seconds() / 60
            if 0 < delay < 60:
                if delay > MISSED_RUN_THRESHOLD_MINUTES:
                    log.warning(f"Cleanup run for {t} is {delay:.1f} minutes late — posting alert")
                    await _run_per_guild(
                        bot.guilds,
                        lambda guild: post_missed_run_alert(bot, guild, t),
                        "Missed-run alert",
                    )
                break

        log.info(f"Scheduled cleanup run starting | Time: {now.strftime('%H:%M')} {TASK_TZ}")
        await _run_per_guild(
            bot.guilds,
            lambda guild: run_cleanup(bot, guild),
            "Scheduled cleanup",
        )
        update_health()
    except Exception:
        log.exception("Scheduled cleanup task failed unexpectedly")
    finally:
        release_run()


@tasks.loop(time=report_time)
async def monthly_report_task():
    """Posts scheduled reports based on REPORT_FREQUENCY setting."""
    try:
        now = datetime.now(TASK_TZ)
        labels = _report_labels_due(now)
        for label in labels:
            log.info(f"{label.capitalize()} report triggered — posting now")
            await _run_per_guild(
                bot.guilds,
                lambda guild, report_label=label: post_status_report(bot, guild, report_label),
                f"{label.capitalize()} report",
            )
    except Exception:
        log.exception("Monthly report task failed unexpectedly")


@tasks.loop(minutes=1)
async def health_task():
    """Updates the health file every minute."""
    update_health()


@cleanup_task.before_loop
async def before_cleanup():
    await bot.wait_until_ready()


@monthly_report_task.before_loop
async def before_report():
    await bot.wait_until_ready()


@health_task.before_loop
async def before_health():
    await bot.wait_until_ready()


# Register task references with utils so get_next_run_str works without circular imports
register_task(cleanup_task, TASK_TZ, bot)


# --- Missed run catchup ---

def _find_missed_run_time(last_run_time: datetime, now: datetime, clean_times: list) -> datetime | None:
    """Scans all scheduled times between last_run_time and now.
    Returns the most recent missed scheduled time, or None if no runs were missed."""
    missed = None
    current_day = last_run_time.date()
    while current_day <= now.date():
        for t in clean_times:
            hour, minute = map(int, t.split(":"))
            candidate = datetime(current_day.year, current_day.month, current_day.day, hour, minute)
            if last_run_time < candidate < now:
                skipped, _reason = _matches_schedule_exception(candidate)
                if skipped:
                    continue
                if missed is None or candidate > missed:
                    missed = candidate
        current_day += timedelta(days=1)
    return missed


async def _check_and_catchup_missed_run(bot, guild):
    """Checks on startup whether a scheduled cleanup run was missed while the bot was offline.
    If CATCHUP_MISSED_RUNS is enabled and a missed run is detected, posts a notification
    and triggers a catchup run immediately."""
    try:
        if not CATCHUP_MISSED_RUNS:
            log.debug("CATCHUP_MISSED_RUNS is disabled — skipping missed run check")
            return

        last_run_data = load_last_run()
        if not last_run_data:
            log.debug("No previous run data found — skipping missed run check")
            return

        try:
            last_run_time = datetime.strptime(last_run_data["timestamp"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError) as e:
            log.warning(f"Could not parse last run timestamp — skipping missed run check: {e}")
            return

        missed_time = _find_missed_run_time(last_run_time, datetime.now(), CLEAN_TIMES)
        if missed_time is None:
            log.debug("No missed scheduled runs detected")
            return

        if is_run_in_progress():
            log.warning("Missed-run catchup skipped because another cleanup operation is already running")
            return
        if not try_acquire_run("catchup"):
            log.warning("Missed-run catchup skipped because another cleanup operation is already running")
            return

        missed_str = missed_time.strftime('%Y-%m-%d %I:%M %p')
        log.info(f"Missed scheduled run detected for {missed_str} — triggering catchup run")
        try:
            await post_catchup_notification(bot, guild, missed_str)
            await run_cleanup(bot, guild, triggered_by=f"catchup (missed {missed_str})")
            record_catchup_run()
        finally:
            release_run()
    except Exception:
        log.exception("Missed-run catchup check failed unexpectedly")


async def _check_and_catchup_monthly_report(bot):
    """Checks on startup whether scheduled reports were missed and posts catchups."""
    try:
        if not CATCHUP_MISSED_RUNS:
            log.debug("CATCHUP_MISSED_RUNS is disabled — skipping report catchup")
            return

        now = datetime.now(TASK_TZ)
        labels = _report_labels_due(now)
        if not labels:
            log.debug("No missed reports detected")
            return

        if is_run_in_progress():
            log.warning("Report catchup skipped because another operation is already running")
            return
        if not try_acquire_run("monthly-report-catchup"):
            log.warning("Report catchup skipped because another operation is already running")
            return

        try:
            for label in labels:
                missed_period = _missed_report_period_text(label, now)
                log.info(
                    "Missed %s report detected for %s — triggering catchup report",
                    label,
                    missed_period,
                )
                if label == "monthly":
                    await post_missed_monthly_report_notification(bot, missed_period)
                elif label == "weekly":
                    await post_missed_weekly_report_notification(bot, missed_period)
                await _run_per_guild(
                    bot.guilds,
                    lambda guild, report_label=label: post_status_report(bot, guild, report_label),
                    f"{label.capitalize()} report",
                )
        finally:
            release_run()
    except Exception:
        log.exception("Report catchup check failed unexpectedly")


# --- Events ---

@bot.event
async def on_ready():
    log_restart_separator()
    log.debug(f"Logged in as {bot.user} | v{BOT_VERSION}")
    log.debug(f"Default retention: {cfg.DEFAULT_RETENTION} days")
    log.debug(f"Cleanup scheduled {len(CLEAN_TIMES)} time(s) per day: {', '.join(CLEAN_TIMES)} ({TASK_TZ})")
    log_startup_path_check()

    for guild in bot.guilds:
        validate_channels(guild)
        migrate_stats_categories(guild)
        await post_deploy_notification(bot, guild)
        await post_startup_notification(bot, guild)

    bot.tree.clear_commands(guild=None)
    bot.tree.add_command(cleanup_group)
    await bot.tree.sync()
    log.debug("Slash commands registered and synced")

    if not cleanup_task.is_running():
        cleanup_task.start()
        log.debug("Cleanup task started")

    if not monthly_report_task.is_running():
        monthly_report_task.start()
        log.debug("Monthly report task started")

    if not health_task.is_running():
        health_task.start()
        log.debug("Health task started")

    set_bot_loop(asyncio.get_event_loop())
    start_web_thread()
    update_health()

    # Fire catchup check as a background task — runs after on_ready without blocking it
    for guild in bot.guilds:
        asyncio.create_task(_check_and_catchup_missed_run(bot, guild))
    asyncio.create_task(_check_and_catchup_monthly_report(bot))


@bot.event
async def on_resumed():
    log.info("Bot resumed connection")
    update_health()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Logs slash-command failures and sends a friendly ephemeral response."""
    original = getattr(error, "original", error)

    if isinstance(error, app_commands.errors.MissingPermissions):
        message = "⛔ You need Administrator permissions to use this command."
    else:
        log.error(
            "Slash command failed",
            exc_info=(type(original), original, original.__traceback__),
        )
        message = "⛔ Command failed unexpectedly. Check the bot logs for details."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        log.exception("Failed to send slash-command error response")


# --- Graceful Shutdown ---

def handle_shutdown(signum, frame):
    log.info("Shutdown signal received — finishing current operation before stopping...")
    for task in [cleanup_task, monthly_report_task, health_task]:
        task.cancel()
    asyncio.get_event_loop().call_soon_threadsafe(asyncio.get_event_loop().stop)


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


# --- Entry Point ---

def main():
    asyncio.run(bot.start(TOKEN))


if __name__ == "__main__":
    main()
