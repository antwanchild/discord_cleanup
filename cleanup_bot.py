import asyncio
import signal
import warnings
import discord
from discord.ext import commands, tasks
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
import logging

warnings.filterwarnings("ignore", message=".*PyNaCl.*")

from config import (
    BOT_VERSION, CLEAN_TIMES, STATUS_REPORT_TIME, TOKEN,
    LOG_LEVEL, log
)
import config as cfg
from cleanup import run_cleanup, validate_channels
from commands import cleanup_group
from notifications import post_deploy_notification, post_startup_notification, post_missed_run_alert, post_status_report
from utils import update_health, register_task

MISSED_RUN_THRESHOLD_MINUTES = 15

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


@tasks.loop(time=task_times)
async def cleanup_task():
    """Runs scheduled cleanup for all guilds."""
    now = datetime.now(TASK_TZ)

    # Missed run detection
    for t in CLEAN_TIMES:
        hour, minute = map(int, t.split(":"))
        expected = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delay = (now - expected).total_seconds() / 60
        if 0 < delay < 60:
            if delay > MISSED_RUN_THRESHOLD_MINUTES:
                log.warning(f"Cleanup run for {t} is {delay:.1f} minutes late — posting alert")
                for guild in bot.guilds:
                    await post_missed_run_alert(bot, guild, t)
            break

    log.info(f"Scheduled cleanup run starting | Time: {now.strftime('%H:%M')} {TASK_TZ}")
    for guild in bot.guilds:
        await run_cleanup(bot, guild)
    update_health()


@tasks.loop(time=report_time)
async def monthly_report_task():
    """Checks if it's the 1st of the month and posts the monthly report."""
    if datetime.now(TASK_TZ).day == 1:
        log.info("1st of month — posting monthly report")
        for guild in bot.guilds:
            await post_status_report(bot, guild)


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


# --- Events ---

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} | v{BOT_VERSION}")
    log.info(f"Default retention: {cfg.DEFAULT_RETENTION} days")
    log.info(f"Cleanup scheduled {len(CLEAN_TIMES)} time(s) per day: {', '.join(CLEAN_TIMES)} ({TASK_TZ})")

    for guild in bot.guilds:
        validate_channels(guild)
        await post_deploy_notification(bot, guild)
        await post_startup_notification(bot, guild)

    bot.tree.clear_commands(guild=None)
    bot.tree.add_command(cleanup_group)
    await bot.tree.sync()
    log.info("Slash commands registered and synced")

    if not cleanup_task.is_running():
        cleanup_task.start()
        log.info("Cleanup task started")

    if not monthly_report_task.is_running():
        monthly_report_task.start()
        log.info("Monthly report task started")

    if not health_task.is_running():
        health_task.start()
        log.info("Health task started")

    update_health()


@bot.event
async def on_resumed():
    log.info("Bot resumed connection")
    update_health()


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


main()
