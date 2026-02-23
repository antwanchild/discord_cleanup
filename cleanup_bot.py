import discord
from discord import app_commands
from discord.ext import commands
import schedule
import signal
import sys
import time
import asyncio
import threading
import os
import logging
import yaml
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(".env.discord_cleanup")

# Read version from VERSION file
with open("VERSION", "r") as f:
    BOT_VERSION = f.read().strip()

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
CLEAN_TIMES = [t.strip() for t in os.getenv("CLEAN_TIME", "03:00").split(",") if t.strip()]
LOG_MAX_FILES = int(os.getenv("LOG_MAX_FILES", 7))
DEFAULT_RETENTION = int(os.getenv("DEFAULT_RETENTION", 7))
LOG_DIR = "/app/logs"
LAST_VERSION_FILE = "/app/logs/last_version"

# Load channels from channels.yml
with open("channels.yml", "r") as f:
    config = yaml.safe_load(f)
    raw_channels = config.get("channels", [])

RETRY_DELAY = 300

# --- Logging Setup ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)

logger = logging.getLogger()
logger.setLevel(numeric_level)

console_handler = logging.StreamHandler()
console_handler.setLevel(numeric_level)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Set discord.py loggers to WARNING unless DEBUG is explicitly set
discord_log_level = numeric_level if LOG_LEVEL == "DEBUG" else logging.WARNING
logging.getLogger("discord").setLevel(discord_log_level)
logging.getLogger("discord.http").setLevel(discord_log_level)
logging.getLogger("discord.gateway").setLevel(discord_log_level)

# Prevent discord.py from adding its own handler
logging.getLogger("discord").propagate = True
discord.utils.setup_logging = lambda *args, **kwargs: None

log = logging.getLogger("discord-cleanup")


def setup_run_log():
    """Creates a new date stamped log file for this run and cleans up old ones."""
    os.makedirs(LOG_DIR, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"cleanup-{today}.log")

    for h in logger.handlers[:]:
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            h.close()

    file_handler = logging.FileHandler(log_path, mode="a")
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    log.info(f"=== Discord Cleanup Bot v{BOT_VERSION} ===")
    log.info(f"Log file started: {log_path}")
    log.info(
        f"Config snapshot | "
        f"CLEAN_TIMES={CLEAN_TIMES} | "
        f"TZ={os.getenv('TZ', 'UTC')} | "
        f"LOG_LEVEL={LOG_LEVEL} | "
        f"LOG_MAX_FILES={LOG_MAX_FILES} | "
        f"DEFAULT_RETENTION={DEFAULT_RETENTION}"
    )

    cutoff = datetime.now() - timedelta(days=LOG_MAX_FILES)
    for filename in os.listdir(LOG_DIR):
        if filename.startswith("cleanup-") and filename.endswith(".log"):
            try:
                file_date_str = filename.replace("cleanup-", "").replace(".log", "")
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    os.remove(os.path.join(LOG_DIR, filename))
                    log.info(f"Deleted old log file: {filename}")
            except ValueError:
                pass


def build_channel_map(guild):
    """
    Builds a map of channel_id -> {days, category_name, category_default, is_override}
    Handles categories, individual overrides, exclusions, default retention,
    and auto-detects Discord category name for individually listed channels.
    """
    override_map = {}
    exclude_set = set()
    category_map = {}

    for ch in raw_channels:
        ch_id = ch["id"]
        ch_type = ch.get("type", "channel")

        if ch_type == "category":
            category_map[ch_id] = {
                "name": ch.get("name", str(ch_id)),
                "days": ch.get("days", DEFAULT_RETENTION)
            }
        else:
            if ch.get("exclude", False):
                exclude_set.add(ch_id)
            else:
                override_map[ch_id] = ch.get("days", DEFAULT_RETENTION)

    channel_map = {}

    # Process configured categories
    for ch_config in raw_channels:
        ch_type = ch_config.get("type", "channel")
        if ch_type != "category":
            continue

        cat_id = ch_config["id"]
        cat_days = ch_config.get("days", DEFAULT_RETENTION)
        cat_name = ch_config.get("name", str(cat_id))

        category = guild.get_channel(cat_id)
        if not category:
            log.warning(f"Category ID {cat_id} not found in guild")
            continue

        for sub_channel in category.text_channels:
            if sub_channel.id in exclude_set:
                log.info(f"#{sub_channel.name} — excluded from cleanup (configured in channels.yml)")
                continue
            if sub_channel.id in override_map:
                channel_map[sub_channel.id] = {
                    "days": override_map[sub_channel.id],
                    "category_name": cat_name,
                    "category_default": cat_days,
                    "is_override": True
                }
            else:
                channel_map[sub_channel.id] = {
                    "days": cat_days,
                    "category_name": cat_name,
                    "category_default": cat_days,
                    "is_override": False
                }

    # Temporary debug
    for ch_id, ch_data in channel_map.items():
        log.info(f"Channel map entry: {ch_id} — category: {ch_data['category_name']} — cat_default: {ch_data['category_default']}")


    # Process individually listed channels
    for ch_config in raw_channels:
        ch_type = ch_config.get("type", "channel")
        if ch_type == "category":
            continue

        ch_id = ch_config["id"]
        if ch_id in channel_map:
            continue

        if ch_id in exclude_set:
            discord_channel = guild.get_channel(ch_id)
            ch_name = discord_channel.name if discord_channel else str(ch_id)
            log.info(f"#{ch_name} — excluded from cleanup (configured in channels.yml)")
            continue

        days = ch_config.get("days", DEFAULT_RETENTION)
        is_override = days != DEFAULT_RETENTION

        discord_channel = guild.get_channel(ch_id)
        if discord_channel and discord_channel.category:
            cat_name = discord_channel.category.name
            cat_default = None
            for cat_id, cat_data in category_map.items():
                if discord_channel.category.id == cat_id:
                    cat_default = cat_data["days"]
                    break
        else:
            cat_name = None
            cat_default = None

        channel_map[ch_id] = {
            "days": days,
            "category_name": cat_name,
            "category_default": cat_default,
            "is_override": is_override
        }

    return channel_map


def validate_channels(guild):
    """Validates all configured channels exist in the guild on startup."""
    log.info("Validating configured channels...")
    issues = 0

    for ch in raw_channels:
        ch_id = ch["id"]
        ch_type = ch.get("type", "channel")
        ch_name = ch.get("name", str(ch_id))

        if ch.get("exclude", False):
            continue

        channel = guild.get_channel(ch_id)
        if not channel:
            log.warning(f"Validation failed — #{ch_name} (ID: {ch_id}) not found in server")
            issues += 1
        else:
            if ch_type == "category":
                sub_count = len(channel.text_channels)
                log.info(f"Validated category #{ch_name} — {sub_count} text channel(s) found")
            else:
                log.info(f"Validated channel #{channel.name}")

    if issues == 0:
        log.info("All configured channels validated successfully")
    else:
        log.warning(f"Validation complete — {issues} issue(s) found, check channels.yml")


async def post_deploy_notification(guild):
    """Posts a deploy notification to the log channel if this is a new version."""
    os.makedirs(LOG_DIR, exist_ok=True)

    last_version = None
    if os.path.exists(LAST_VERSION_FILE):
        with open(LAST_VERSION_FILE, "r") as f:
            last_version = f.read().strip()

    # Write current version
    with open(LAST_VERSION_FILE, "w") as f:
        f.write(BOT_VERSION)

    # Only notify if version has changed
    if last_version == BOT_VERSION:
        log.info("Version unchanged — skipping deploy notification")
        return

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post deploy notification — log channel not found")
        return

    if last_version:
        log.info(f"New version detected — {last_version} → {BOT_VERSION}, posting deploy notification")
        description = f"Updated from **v{last_version}** to **v{BOT_VERSION}**"
    else:
        log.info(f"First run detected — posting deploy notification for v{BOT_VERSION}")
        description = f"First deployment of **v{BOT_VERSION}**"

    embed = discord.Embed(
        title=f"🚀 New Version Deployed — v{BOT_VERSION}",
        description=description,
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.add_field(
        name="🐳 Image",
        value=f"`ghcr.io/antwanchild/discord_cleanup:{BOT_VERSION}`",
        inline=False
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")

    await log_channel.send(embed=embed)


# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix=None, intents=intents)

# --- Graceful Shutdown ---
shutdown_event = asyncio.Event()


def handle_shutdown(signum, frame):
    log.info("Shutdown signal received — finishing current operation before stopping...")
    asyncio.get_event_loop().call_soon_threadsafe(shutdown_event.set)


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


async def purge_channel(channel, days_old: int, bulk_cutoff: datetime, run_time: datetime) -> dict:
    """Purges old messages from a single channel. Returns stats dict."""
    cutoff = run_time - timedelta(days=days_old)
    guild = channel.guild
    total_deleted = 0
    rate_limit_count = 0
    oldest_message_date = None

    if not channel.permissions_for(guild.me).manage_messages:
        log.warning(f"Skipping #{channel.name} — missing Manage Messages permission")
        return {"count": -1, "rate_limits": 0, "oldest": None, "days": days_old}

    log.info(f"Starting purge on #{channel.name} | Days: {days_old}")

    while True:
        # Check for shutdown signal between batches
        if shutdown_event.is_set():
            log.info(f"#{channel.name} — shutdown requested, stopping after current batch")
            break

        try:
            messages_to_delete = []

            async for msg in channel.history(limit=100, before=cutoff):
                if msg.created_at > bulk_cutoff:
                    messages_to_delete.append(msg)
                    if oldest_message_date is None or msg.created_at < oldest_message_date:
                        oldest_message_date = msg.created_at

            if not messages_to_delete:
                log.info(f"#{channel.name} — no more messages to delete")
                break

            if len(messages_to_delete) == 1:
                await messages_to_delete[0].delete()
            else:
                await channel.delete_messages(messages_to_delete)

            total_deleted += len(messages_to_delete)
            log.info(f"#{channel.name} — deleted batch of {len(messages_to_delete)} | Running total: {total_deleted}")
            await asyncio.sleep(1.5)

        except discord.errors.HTTPException as e:
            if e.status == 429:
                rate_limit_count += 1
                retry_after = getattr(e, 'retry_after', RETRY_DELAY)
                log.warning(f"#{channel.name} — rate limited (hit #{rate_limit_count}). Retrying in {retry_after:.1f}s...")
                await asyncio.sleep(retry_after)
            else:
                log.error(f"#{channel.name} — HTTP error: {e}")
                break
        except discord.Forbidden:
            log.error(f"#{channel.name} — Forbidden. Check bot permissions.")
            return {"count": -1, "rate_limits": 0, "oldest": None, "days": days_old}

    log.info(f"#{channel.name} — complete | Total: {total_deleted} | Rate limits: {rate_limit_count}")
    return {"count": total_deleted, "rate_limits": rate_limit_count, "oldest": oldest_message_date, "days": days_old}


async def run_cleanup(guild, single_channel_id=None):
    """Core cleanup logic used by both scheduler and slash commands."""
    setup_run_log()

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.error("Log channel not found. Check LOG_CHANNEL_ID in .env.discord_cleanup")
        return

    # Calculate cutoffs once for the entire run
    run_time = datetime.now(timezone.utc)
    bulk_cutoff = run_time - timedelta(days=13)

    # Display in local time for readability
    local_run_time = datetime.now()
    local_bulk_cutoff = local_run_time - timedelta(days=13)
    log.info(f"Run cutoff: {local_run_time.strftime('%Y-%m-%d %H:%M:%S')} | Bulk cutoff: {local_bulk_cutoff.strftime('%Y-%m-%d %H:%M:%S')} | TZ: {os.getenv('TZ', 'UTC')}")

    channel_map = build_channel_map(guild)

    # If single channel specified filter down to just that one
    if single_channel_id:
        if single_channel_id in channel_map:
            channel_map = {single_channel_id: channel_map[single_channel_id]}
        else:
            log.warning(f"Channel ID {single_channel_id} not in configured channels")
            return

    log.info(f"Starting cleanup run on server: {guild.name} across {len(channel_map)} channel(s)...")

    category_results = {}
    standalone_results = {}

    grand_total = 0
    grand_rate_limits = 0
    has_warnings = False
    oldest_overall = None
    run_start = datetime.now()

    for channel_id, ch_config in channel_map.items():
        if shutdown_event.is_set():
            log.info("Shutdown requested — stopping cleanup run early")
            break

        channel = guild.get_channel(channel_id)
        if not channel:
            log.warning(f"Channel ID {channel_id} not found — skipping")
            has_warnings = True
            continue

        stats = await purge_channel(channel, ch_config["days"], bulk_cutoff, run_time)
        stats["is_override"] = ch_config["is_override"]

        if stats["count"] > 0:
            grand_total += stats["count"]
        if stats["count"] == -1:
            has_warnings = True

        grand_rate_limits += stats["rate_limits"]

        if stats["oldest"] and (oldest_overall is None or stats["oldest"] < oldest_overall):
            oldest_overall = stats["oldest"]

        cat_name = ch_config["category_name"]
        if cat_name:
            if cat_name not in category_results:
                category_results[cat_name] = {
                    "default_days": ch_config["category_default"],
                    "channels": {}
                }
            category_results[cat_name]["channels"][channel.name] = stats
        else:
            standalone_results[channel.name] = stats

        await asyncio.sleep(2)

    run_end = datetime.now()
    elapsed = run_end - run_start
    minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    timestamp = run_end.strftime('%Y-%m-%d %I:%M %p')

    # Next scheduled run
    now = datetime.now()
    next_run = None
    for t in sorted(CLEAN_TIMES):
        hour, minute = map(int, t.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            next_run = candidate
            break
    if not next_run:
        hour, minute = map(int, sorted(CLEAN_TIMES)[0].split(":"))
        next_run = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    next_run_str = next_run.strftime('%Y-%m-%d %I:%M %p')

    log.info(f"Cleanup run complete | Server: {guild.name} | Total deleted: {grand_total} | Rate limits: {grand_rate_limits} | Duration: {duration_str} | Next run: {next_run_str}")

    # --- Color Logic ---
    if has_warnings and grand_total == 0:
        color = 0xFF0000
        status = "⛔ Completed with Errors"
        log.error("Run completed with errors and nothing deleted — check warnings above")
    elif has_warnings:
        color = 0xFFA500
        status = "⚠️ Completed with Warnings"
        log.warning("Run completed with warnings — check channel permissions or IDs")
    elif grand_total > 0:
        color = 0x00C853
        status = "✅ Cleanup Successful"
        log.info("Run completed successfully")
    else:
        color = 0x3498DB
        status = "ℹ️ Nothing to Clean"
        log.info("Run completed — nothing to delete")

    # --- Build Breakdown ---
    breakdown_lines = []

    for cat_name, cat_data in category_results.items():
        active_lines = []
        for ch_name, stats in cat_data["channels"].items():
            if stats["count"] == -1:
                active_lines.append(f"\u3000🚫 `#{ch_name}` — skipped (missing permissions)")
            elif stats["count"] > 0:
                if stats["is_override"]:
                    active_lines.append(f"\u3000🗑️ `#{ch_name}` — **{stats['count']}** deleted ({stats['days']}d ⚡override)")
                else:
                    active_lines.append(f"\u3000🗑️ `#{ch_name}` — **{stats['count']}** deleted")

        if active_lines:
            if cat_data["default_days"]:
                breakdown_lines.append(f"📁 **{cat_name}** ({cat_data['default_days']}d default)")
            else:
                breakdown_lines.append(f"📁 **{cat_name}**")
            breakdown_lines.extend(active_lines)

    for ch_name, stats in standalone_results.items():
        if stats["count"] == -1:
            breakdown_lines.append(f"🚫 `#{ch_name}` — skipped (missing permissions)")
        elif stats["count"] > 0:
            if stats["is_override"]:
                breakdown_lines.append(f"🗑️ `#{ch_name}` — **{stats['count']}** deleted ({stats['days']}d ⚡override)")
            else:
                breakdown_lines.append(f"🗑️ `#{ch_name}` — **{stats['count']}** deleted")

    if not breakdown_lines:
        breakdown_lines.append("✅ No messages deleted this run")

    oldest_str = oldest_overall.strftime('%Y-%m-%d %I:%M %p') if oldest_overall else "N/A"

    summary = (
        f"🏠 Server: **{guild.name}**\n"
        f"📅 Default retention: **{DEFAULT_RETENTION} days**\n"
        f"🔍 Channels checked: **{len(channel_map)}**\n"
        f"🗑️ Total deleted: **{grand_total}**\n"
        + (f"📆 Oldest message deleted: **{oldest_str}**\n" if grand_total > 0 else "")
        + f"⚡ Rate limits hit: **{grand_rate_limits}**\n"
        f"⏱️ Duration: **{duration_str}**\n"
        f"⏭️ Next run: **{next_run_str}**"
    )

    embed = discord.Embed(
        title=f"🧹 Daily Cleanup Report — {status}",
        description=summary,
        color=color,
        timestamp=run_end
    )
    embed.add_field(name="📋 Per-Channel Breakdown", value="\n".join(breakdown_lines), inline=False)
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION} | Run time: {timestamp}")

    await log_channel.send(embed=embed)


# --- Slash Commands ---
cleanup_group = app_commands.Group(name="cleanup", description="Discord Cleanup Bot commands")


@cleanup_group.command(name="run", description="Trigger a full cleanup run on all configured channels")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_run(interaction: discord.Interaction):
    await interaction.response.send_message("🧹 Full cleanup started — report will be posted to the log channel when complete.", ephemeral=True)
    log.info(f"Manual full cleanup triggered by {interaction.user} in #{interaction.channel.name}")
    await run_cleanup(interaction.guild)


@cleanup_group.command(name="channel", description="Trigger cleanup on a specific configured channel")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel to clean up")
async def cleanup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    channel_map = build_channel_map(interaction.guild)
    if channel.id not in channel_map:
        await interaction.response.send_message(f"⚠️ `#{channel.name}` is not in your configured channels. Check `channels.yml`.", ephemeral=True)
        return
    await interaction.response.send_message(f"🧹 Cleanup started for `#{channel.name}` — report will be posted to the log channel when complete.", ephemeral=True)
    log.info(f"Manual channel cleanup triggered by {interaction.user} for #{channel.name}")
    await run_cleanup(interaction.guild, single_channel_id=channel.id)


@cleanup_group.command(name="status", description="Show current bot configuration and next scheduled run")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    # Next scheduled run
    now = datetime.now()
    next_run = None
    for t in sorted(CLEAN_TIMES):
        hour, minute = map(int, t.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            next_run = candidate
            break
    if not next_run:
        hour, minute = map(int, sorted(CLEAN_TIMES)[0].split(":"))
        next_run = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    next_run_str = next_run.strftime('%Y-%m-%d %I:%M %p')

    # Build display directly from raw_channels — no Discord API calls needed
    channel_lines = []
    excluded = []
    configured_count = 0

    for ch in raw_channels:
        ch_type = ch.get("type", "channel")
        ch_name = ch.get("name", str(ch["id"]))

        if ch.get("exclude", False):
            excluded.append(ch)
            continue

        if ch_type == "category":
            days = ch.get("days", DEFAULT_RETENTION)
            channel_lines.append(f"📁 **{ch_name}** ({days}d default)")
        else:
            days = ch.get("days", DEFAULT_RETENTION)
            is_override = days != DEFAULT_RETENTION
            retention = f"{days}d ⚡override" if is_override else f"{days}d"
            channel_lines.append(f"\u3000`#{ch_name}` — {retention}")
            configured_count += 1

    summary = (
        f"🏠 Server: **{interaction.guild.name}**\n"
        f"📅 Default retention: **{DEFAULT_RETENTION} days**\n"
        f"🔍 Channels configured: **{configured_count}**\n"
        f"⛔ Channels excluded: **{len(excluded)}**\n"
        f"🕐 Scheduled runs: **{', '.join(CLEAN_TIMES)}**\n"
        f"⏭️ Next run: **{next_run_str}**\n"
        f"📋 Log level: **{LOG_LEVEL}**\n"
        f"🗂️ Log retention: **{LOG_MAX_FILES} days**"
    )

    embed = discord.Embed(
        title="⚙️ Discord Cleanup Bot — Status",
        description=summary,
        color=0x5865F2,
        timestamp=datetime.now()
    )

    if channel_lines:
        embed.add_field(
            name="📋 Configured Channels",
            value="\n".join(channel_lines),
            inline=False
        )

    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@cleanup_group.error
async def cleanup_group_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("⛔ You need Administrator permissions to use this command.", ephemeral=True)


def schedule_runner():
    def job():
        for guild in bot.guilds:
            asyncio.run_coroutine_threadsafe(run_cleanup(guild), bot.loop)

    for t in CLEAN_TIMES:
        schedule.every().day.at(t).do(job)
        log.info(f"Scheduled daily run at {t}")

    log.info(f"Scheduler started — {len(CLEAN_TIMES)} run(s) per day: {', '.join(CLEAN_TIMES)}")

    while not shutdown_event.is_set():
        schedule.run_pending()
        time.sleep(30)

    log.info("Scheduler stopped")


def start_scheduler():
    if any(t.name == "scheduler" for t in threading.enumerate()):
        log.info("Scheduler already running — skipping")
        return

    log.info("Starting scheduler thread...")
    thread = threading.Thread(target=schedule_runner, daemon=True, name="scheduler")
    thread.start()


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} | v{BOT_VERSION}")
    log.info(f"Default retention: {DEFAULT_RETENTION} days")
    log.info(f"Cleanup scheduled {len(CLEAN_TIMES)} time(s) per day: {', '.join(CLEAN_TIMES)}")

    # Validate channels and post deploy notification for each guild
    for guild in bot.guilds:
        validate_channels(guild)
        await post_deploy_notification(guild)

    # Clear and re-sync slash commands
    bot.tree.clear_commands(guild=None)
    bot.tree.add_command(cleanup_group)
    await bot.tree.sync()
    log.info("Slash commands registered and synced")

    start_scheduler()


@bot.event
async def on_resumed():
    log.info("Bot resumed connection — scheduler already running")


async def shutdown():
    log.info("Shutting down bot...")
    await bot.close()
    log.info("Bot shutdown complete")


def main():
    loop = asyncio.get_event_loop()

    try:
        loop.run_until_complete(bot.start(TOKEN))
    except KeyboardInterrupt:
        pass
    finally:
        if not loop.is_closed():
            loop.run_until_complete(shutdown())
            loop.close()
        log.info("Goodbye!")
        sys.exit(0)


main()