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
import json
import logging
import yaml
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

CONFIG_DIR = "/config"
BOT_START_TIME = datetime.now()
HEALTH_FILE = "/tmp/health"
MISSED_RUN_THRESHOLD_MINUTES = 15


def create_default_files():
    """Creates default config files if they don't exist. Exits if any were created."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except PermissionError:
        print(f"ERROR: Could not create {CONFIG_DIR} — check directory permissions.")
        sys.exit(1)
    created = False

    if not os.path.exists(f"{CONFIG_DIR}/.env.discord_cleanup"):
        try:
            with open(f"{CONFIG_DIR}/.env.discord_cleanup", "w") as f:
                f.write("# Discord bot token from Discord Developer Portal\n"
                        "DISCORD_TOKEN=your_bot_token_here\n\n"
                        "# Channel ID where cleanup reports are posted\n"
                        "LOG_CHANNEL_ID=your_log_channel_id_here\n\n"
                        "# Channel ID where monthly reports are posted\n"
                        "REPORT_CHANNEL_ID=your_report_channel_id_here\n\n"
                        "# Comma-separated run times in 24hr format e.g. 03:00 or 03:00,12:00\n"
                        "CLEAN_TIME=03:00\n\n"
                        "# Default message retention in days\n"
                        "DEFAULT_RETENTION=7\n\n"
                        "# Number of daily log files to retain\n"
                        "LOG_MAX_FILES=7\n\n"
                        "# Log level: DEBUG, INFO, WARNING, ERROR\n"
                        "LOG_LEVEL=INFO\n\n"
                        "# Time to post monthly report on the 1st (24hr format)\n"
                        "STATUS_REPORT_TIME=09:00\n\n"
                        "# User and group ID for file ownership (match your host user)\n"
                        "PUID=1000\n"
                        "PGID=1000\n")
            print(f"{CONFIG_DIR}/.env.discord_cleanup not found — created with default values. Please fill in your bot token and channel IDs then restart.")
            created = True
        except PermissionError:
            print(f"ERROR: Could not create {CONFIG_DIR}/.env.discord_cleanup — check directory permissions.")
            sys.exit(1)

    if not os.path.exists(f"{CONFIG_DIR}/channels.yml"):
        try:
            with open(f"{CONFIG_DIR}/channels.yml", "w") as f:
                f.write("channels:\n"
                        "  # --- CATEGORIES ---\n"
                        "  # Cleans all text channels under this Discord category\n"
                        "  # Uses DEFAULT_RETENTION from .env unless days is specified\n"
                        "  # Add deep_clean: true to also delete messages older than 14 days\n"
                        "  - id: 123456789012345678\n"
                        "    name: My Category\n"
                        "    type: category\n\n"
                        "  # Category with retention override and deep clean enabled\n"
                        "  - id: 234567890123456789\n"
                        "    name: My Category With Override\n"
                        "    type: category\n"
                        "    days: 4\n"
                        "    deep_clean: true\n\n"
                        "  # --- CHANNEL OVERRIDES ---\n"
                        "  - id: 345678901234567890\n"
                        "    name: my-channel-override\n"
                        "    days: 3\n\n"
                        "  # --- EXCLUSIONS ---\n"
                        "  - id: 456789012345678901\n"
                        "    name: my-excluded-channel\n"
                        "    exclude: true\n\n"
                        "  # --- STANDALONE CHANNELS ---\n"
                        "  - id: 567890123456789012\n"
                        "    name: my-standalone-channel\n\n"
                        "  # Standalone channel with deep clean enabled\n"
                        "  - id: 678901234567890123\n"
                        "    name: my-standalone-deep\n"
                        "    days: 14\n"
                        "    deep_clean: true\n")
            print(f"{CONFIG_DIR}/channels.yml not found — created with sample config. Please update with your real channel IDs then restart.")
            created = True
        except PermissionError:
            print(f"ERROR: Could not create {CONFIG_DIR}/channels.yml — check directory permissions.")
            sys.exit(1)

    if created:
        sys.exit(0)


create_default_files()

load_dotenv(f"{CONFIG_DIR}/.env.discord_cleanup")

try:
    with open("VERSION", "r") as f:
        BOT_VERSION = f.read().strip()
except FileNotFoundError:
    print("ERROR: VERSION file not found — cannot start bot.")
    sys.exit(1)
except PermissionError:
    print("ERROR: Could not read VERSION file — check permissions.")
    sys.exit(1)

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID"))
CLEAN_TIMES = [t.strip() for t in os.getenv("CLEAN_TIME", "03:00").split(",") if t.strip()]
LOG_MAX_FILES = int(os.getenv("LOG_MAX_FILES", 7))
DEFAULT_RETENTION = int(os.getenv("DEFAULT_RETENTION", 7))
STATUS_REPORT_TIME = os.getenv("STATUS_REPORT_TIME", "09:00")
LOG_DIR = f"{CONFIG_DIR}/logs"
DATA_DIR = f"{CONFIG_DIR}/data"
LAST_VERSION_FILE = f"{DATA_DIR}/last_version"
STATS_FILE = f"{DATA_DIR}/stats.json"
RETRY_DELAY = 300

try:
    with open(f"{CONFIG_DIR}/channels.yml", "r") as f:
        config = yaml.safe_load(f)
        raw_channels = config.get("channels", [])
except FileNotFoundError:
    print(f"ERROR: {CONFIG_DIR}/channels.yml not found — cannot start bot.")
    sys.exit(1)
except PermissionError:
    print(f"ERROR: Could not read {CONFIG_DIR}/channels.yml — check directory permissions.")
    sys.exit(1)
except yaml.YAMLError as e:
    print(f"ERROR: channels.yml is malformed — {e}")
    sys.exit(1)

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

discord_log_level = numeric_level if LOG_LEVEL == "DEBUG" else logging.WARNING
logging.getLogger("discord").setLevel(discord_log_level)
logging.getLogger("discord.http").setLevel(discord_log_level)
logging.getLogger("discord.gateway").setLevel(discord_log_level)
logging.getLogger("discord").propagate = True
discord.utils.setup_logging = lambda *args, **kwargs: None

log = logging.getLogger("discord-cleanup")


# --- Health Check ---

def update_health():
    """Updates the health file timestamp. Used by Docker HEALTHCHECK."""
    try:
        with open(HEALTH_FILE, "w") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        log.warning(f"Could not update health file — {e}")


# --- Stats Helpers ---

def _empty_stats():
    now = datetime.now().strftime("%Y-%m-%d")
    return {
        "all_time": {"runs": 0, "deleted": 0, "channels": {}},
        "rolling_30": {"runs": 0, "deleted": 0, "channels": {}, "reset": now},
        "monthly": {"runs": 0, "deleted": 0, "channels": {}, "reset": now}
    }


def load_stats() -> dict:
    """Loads stats from the stats file, returns empty structure if not found."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {DATA_DIR} — check directory permissions.")
        return _empty_stats()
    if not os.path.exists(STATS_FILE):
        return _empty_stats()
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Could not load stats file — {e}")
        return _empty_stats()


def save_stats(stats: dict):
    """Saves stats to the stats file."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {DATA_DIR} — check directory permissions.")
        return
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        log.warning(f"Could not save stats file — {e}")


def update_stats(channel_results: dict):
    """Updates stats after a cleanup run."""
    stats = load_stats()
    now = datetime.now()

    rolling_reset = datetime.strptime(stats["rolling_30"]["reset"], "%Y-%m-%d")
    if (now - rolling_reset).days >= 30:
        log.info("Resetting rolling 30-day stats")
        stats["rolling_30"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now.strftime("%Y-%m-%d")}

    monthly_reset = datetime.strptime(stats["monthly"]["reset"], "%Y-%m-%d")
    if now.month != monthly_reset.month or now.year != monthly_reset.year:
        log.info("Resetting monthly stats")
        stats["monthly"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now.strftime("%Y-%m-%d")}

    total_deleted = sum(v for v in channel_results.values() if v > 0)

    for bucket in ["all_time", "rolling_30", "monthly"]:
        stats[bucket]["runs"] += 1
        stats[bucket]["deleted"] += total_deleted
        for ch_name, count in channel_results.items():
            if count > 0:
                stats[bucket]["channels"][ch_name] = stats[bucket]["channels"].get(ch_name, 0) + count

    save_stats(stats)
    log.info(f"Stats updated | Run total: {total_deleted} | All-time: {stats['all_time']['deleted']}")


def reset_stats(scope: str) -> bool:
    """Resets stats for the given scope: 'rolling', 'monthly', or 'all'."""
    stats = load_stats()
    now = datetime.now().strftime("%Y-%m-%d")

    if scope == "rolling":
        stats["rolling_30"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now}
        log.info("Rolling 30-day stats reset by user")
    elif scope == "monthly":
        stats["monthly"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now}
        log.info("Monthly stats reset by user")
    elif scope == "all":
        stats = _empty_stats()
        log.info("All stats reset by user")
    else:
        return False

    save_stats(stats)
    return True


def setup_run_log():
    """Creates a date-stamped log file for this run and cleans up old ones."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {LOG_DIR} — check directory permissions.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"cleanup-{today}.log")

    for h in logger.handlers[:]:
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            h.close()

    try:
        file_handler = logging.FileHandler(log_path, mode="a")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except PermissionError:
        log.error(f"Could not create log file {log_path} — check directory permissions.")
        return

    log.info("=" * 60)
    log.info(f"=== Discord Cleanup Bot v{BOT_VERSION} ===")
    log.info(f"Log file started: {log_path}")
    log.info(
        f"Config snapshot | CLEAN_TIMES={CLEAN_TIMES} | TZ={os.getenv('TZ', 'UTC')} | "
        f"LOG_LEVEL={LOG_LEVEL} | LOG_MAX_FILES={LOG_MAX_FILES} | DEFAULT_RETENTION={DEFAULT_RETENTION}"
    )

    cutoff = datetime.now() - timedelta(days=LOG_MAX_FILES)
    for filename in os.listdir(LOG_DIR):
        if filename.startswith("cleanup-") and filename.endswith(".log"):
            try:
                file_date = datetime.strptime(filename.replace("cleanup-", "").replace(".log", ""), "%Y-%m-%d")
                if file_date < cutoff:
                    os.remove(os.path.join(LOG_DIR, filename))
                    log.info(f"Deleted old log file: {filename}")
            except ValueError:
                pass
            except PermissionError:
                log.warning(f"Could not delete old log file {filename} — check directory permissions.")


def reload_channels():
    """Reloads channels.yml and updates raw_channels. Returns (success, message)."""
    global raw_channels
    try:
        with open(f"{CONFIG_DIR}/channels.yml", "r") as f:
            config = yaml.safe_load(f)
            raw_channels = config.get("channels", [])
        log.info("channels.yml reloaded successfully")
        return True, f"Loaded {len(raw_channels)} channel entries"
    except FileNotFoundError:
        log.error("channels.yml not found during reload")
        return False, "channels.yml not found"
    except PermissionError:
        log.error("Permission denied reading channels.yml during reload")
        return False, "Permission denied reading channels.yml"
    except yaml.YAMLError as e:
        log.error(f"channels.yml is malformed during reload — {e}")
        return False, f"channels.yml is malformed — {e}"


def build_channel_map(guild):
    """Builds a map of channel_id -> config dict."""
    override_map = {}
    exclude_set = set()
    category_map = {}

    for ch in raw_channels:
        ch_id = ch["id"]
        if ch.get("type") == "category":
            category_map[ch_id] = {
                "name": ch.get("name", str(ch_id)),
                "days": ch.get("days", DEFAULT_RETENTION),
                "deep_clean": ch.get("deep_clean", False)
            }
        elif ch.get("exclude", False):
            exclude_set.add(ch_id)
        else:
            override_map[ch_id] = {
                "days": ch.get("days", DEFAULT_RETENTION),
                "deep_clean": ch.get("deep_clean", False)
            }

    channel_map = {}

    for ch_config in raw_channels:
        if ch_config.get("type") != "category":
            continue
        cat_id = ch_config["id"]
        cat_days = ch_config.get("days", DEFAULT_RETENTION)
        cat_name = ch_config.get("name", str(cat_id))
        cat_deep_clean = ch_config.get("deep_clean", False)

        category = guild.get_channel(cat_id)
        if not category:
            log.warning(f"Category ID {cat_id} not found in guild")
            continue

        for sub in category.text_channels:
            if sub.id in exclude_set:
                log.info(f"#{sub.name} — excluded from cleanup (configured in channels.yml)")
                continue
            if sub.id in override_map:
                ov = override_map[sub.id]
                channel_map[sub.id] = {
                    "days": ov["days"],
                    "category_name": cat_name,
                    "category_default": cat_days,
                    "is_override": True,
                    "deep_clean": ov["deep_clean"] or cat_deep_clean
                }
            else:
                channel_map[sub.id] = {
                    "days": cat_days,
                    "category_name": cat_name,
                    "category_default": cat_days,
                    "is_override": False,
                    "deep_clean": cat_deep_clean
                }

    for ch_config in raw_channels:
        if ch_config.get("type") == "category":
            continue
        ch_id = ch_config["id"]
        if ch_id in channel_map or ch_id in exclude_set:
            continue

        days = ch_config.get("days", DEFAULT_RETENTION)
        deep_clean = ch_config.get("deep_clean", False)
        discord_channel = guild.get_channel(ch_id)
        cat_name = None
        cat_default = None

        if discord_channel and discord_channel.category:
            cat_name = discord_channel.category.name
            for cat_id, cat_data in category_map.items():
                if discord_channel.category.id == cat_id:
                    cat_default = cat_data["days"]
                    break

        channel_map[ch_id] = {
            "days": days,
            "category_name": cat_name,
            "category_default": cat_default,
            "is_override": days != DEFAULT_RETENTION,
            "deep_clean": deep_clean
        }

    return channel_map


def validate_channels(guild):
    """Validates all configured channels exist in the guild on startup."""
    log.info("Validating configured channels...")
    issues = 0

    for ch in raw_channels:
        if ch.get("exclude", False):
            continue
        ch_id = ch["id"]
        ch_name = ch.get("name", str(ch_id))
        channel = guild.get_channel(ch_id)
        if not channel:
            log.warning(f"Validation failed — #{ch_name} (ID: {ch_id}) not found in server")
            issues += 1
        elif ch.get("type") == "category":
            deep = " | deep_clean: enabled" if ch.get("deep_clean") else ""
            log.info(f"Validated category #{ch_name} — {len(channel.text_channels)} text channel(s){deep}")
        else:
            deep = " | deep_clean: enabled" if ch.get("deep_clean") else ""
            log.info(f"Validated channel #{channel.name}{deep}")

    if issues == 0:
        log.info("All configured channels validated successfully")
    else:
        log.warning(f"Validation complete — {issues} issue(s) found, check channels.yml")


def get_next_run_str():
    """Returns the next scheduled run time as a formatted string."""
    now = datetime.now()
    for t in sorted(CLEAN_TIMES):
        hour, minute = map(int, t.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate.strftime('%Y-%m-%d %I:%M %p')
    hour, minute = map(int, sorted(CLEAN_TIMES)[0].split(":"))
    return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0).strftime('%Y-%m-%d %I:%M %p')


def get_uptime_str():
    """Returns the bot uptime as a formatted string."""
    uptime = datetime.now() - BOT_START_TIME
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m {seconds}s"


# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix=None, intents=intents)

shutdown_event = asyncio.Event()


def handle_shutdown(signum, frame):
    log.info("Shutdown signal received — finishing current operation before stopping...")
    asyncio.get_event_loop().call_soon_threadsafe(shutdown_event.set)


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


# --- Notifications ---

async def post_startup_notification(guild):
    """Posts a startup notification to the log channel on every boot."""
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post startup notification — log channel not found")
        return
    embed = discord.Embed(
        title=f"🟢 Bot Online — v{BOT_VERSION}",
        description=f"🏠 Server: **{guild.name}**\n⏭️ Next run: **{get_next_run_str()}**",
        color=0x2ECC71,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)
    log.info("Startup notification posted")


async def post_deploy_notification(guild):
    """Posts a deploy notification if the version has changed."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {DATA_DIR} — check directory permissions.")
        return

    last_version = None
    if os.path.exists(LAST_VERSION_FILE):
        try:
            with open(LAST_VERSION_FILE, "r") as f:
                last_version = f.read().strip()
        except PermissionError:
            log.error(f"Could not read {LAST_VERSION_FILE} — check directory permissions.")
            return

    try:
        with open(LAST_VERSION_FILE, "w") as f:
            f.write(BOT_VERSION)
    except PermissionError:
        log.error(f"Could not write {LAST_VERSION_FILE} — check directory permissions.")
        return

    if last_version == BOT_VERSION:
        log.info("Version unchanged — skipping deploy notification")
        return

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post deploy notification — log channel not found")
        return

    if last_version:
        log.info(f"New version detected — {last_version} -> {BOT_VERSION}, posting deploy notification")
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
    embed.add_field(name="🐳 Image", value=f"`ghcr.io/antwanchild/discord_cleanup:{BOT_VERSION}`", inline=False)
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)


async def post_status_report(guild):
    """Posts a monthly stats report to the report channel."""
    report_channel = bot.get_channel(REPORT_CHANNEL_ID)
    if not report_channel:
        log.warning("Could not post status report — report channel not found")
        return

    stats = load_stats()
    monthly = stats.get("monthly", {})
    channels = monthly.get("channels", {})
    top_channels = sorted(channels.items(), key=lambda x: x[1], reverse=True)[:10]

    embed = discord.Embed(
        title="📊 Monthly Cleanup Report",
        description=(
            f"🏠 Server: **{guild.name}**\n"
            f"📅 Period: **Since {monthly.get('reset', 'N/A')}**\n"
            f"🔁 Runs completed: **{monthly.get('runs', 0)}**\n"
            f"🗑️ Total deleted: **{monthly.get('deleted', 0)}**\n"
            f"📋 Active channels: **{len(channels)}**"
        ),
        color=0xE67E22,
        timestamp=datetime.now()
    )
    if top_channels:
        embed.add_field(
            name="🏆 Top Channels",
            value="\n".join([f"`#{ch}` — **{count}** deleted" for ch, count in top_channels]),
            inline=False
        )
    else:
        embed.add_field(name="🏆 Top Channels", value="No messages deleted this period", inline=False)

    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await report_channel.send(embed=embed)
    log.info("Monthly status report posted")


async def post_missed_run_alert(guild, scheduled_time: str):
    """Posts an alert when a scheduled run is delayed beyond the threshold."""
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post missed run alert — log channel not found")
        return
    embed = discord.Embed(
        title="⚠️ Scheduled Run Delayed",
        description=(
            f"🏠 Server: **{guild.name}**\n"
            f"🕐 Scheduled time: **{scheduled_time}**\n"
            f"⏱️ Threshold: **{MISSED_RUN_THRESHOLD_MINUTES} minutes**\n\n"
            f"The cleanup run has not started within the expected window. "
            f"Check the container logs for issues."
        ),
        color=0xFFA500,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)
    log.warning(f"Missed run alert posted for scheduled time {scheduled_time}")


# --- Core Cleanup ---

async def purge_channel(channel, days_old: int, bulk_cutoff: datetime, run_time: datetime, dry_run: bool = False, deep_clean: bool = False) -> dict:
    """Purges old messages from a single channel. Returns stats dict."""
    cutoff = run_time - timedelta(days=days_old)
    guild = channel.guild
    total_deleted = 0
    rate_limit_count = 0
    oldest_message_date = None

    if not channel.permissions_for(guild.me).manage_messages:
        log.warning(f"Skipping #{channel.name} — missing Manage Messages permission")
        return {"count": -1, "rate_limits": 0, "oldest": None, "days": days_old, "deep_clean": deep_clean}

    log.info(f"{'[DRY RUN] ' if dry_run else ''}Starting purge on #{channel.name} | Days: {days_old} | Deep clean: {deep_clean}")

    # Bulk delete — messages between retention cutoff and 14 days old
    while True:
        if shutdown_event.is_set():
            log.info(f"#{channel.name} — shutdown requested, stopping")
            break
        try:
            messages_to_delete = []
            async for msg in channel.history(limit=100, before=cutoff):
                if msg.created_at > bulk_cutoff:
                    messages_to_delete.append(msg)
                    if oldest_message_date is None or msg.created_at < oldest_message_date:
                        oldest_message_date = msg.created_at

            if not messages_to_delete:
                log.info(f"#{channel.name} — bulk delete complete | Total: {total_deleted}")
                break

            if dry_run:
                total_deleted += len(messages_to_delete)
                log.info(f"[DRY RUN] #{channel.name} — would bulk delete {len(messages_to_delete)} | Running total: {total_deleted}")
                break
            else:
                if len(messages_to_delete) == 1:
                    await messages_to_delete[0].delete()
                else:
                    await channel.delete_messages(messages_to_delete)
                total_deleted += len(messages_to_delete)
                log.info(f"#{channel.name} — bulk deleted {len(messages_to_delete)} | Running total: {total_deleted}")
                await asyncio.sleep(1.5)

        except discord.errors.HTTPException as e:
            if e.status == 429:
                rate_limit_count += 1
                retry_after = getattr(e, 'retry_after', RETRY_DELAY)
                log.warning(f"#{channel.name} — rate limited (#{rate_limit_count}), retrying in {retry_after:.1f}s")
                await asyncio.sleep(retry_after)
            else:
                log.error(f"#{channel.name} — HTTP error during bulk delete: {e}")
                break
        except discord.Forbidden:
            log.error(f"#{channel.name} — Forbidden. Check bot permissions.")
            return {"count": -1, "rate_limits": 0, "oldest": None, "days": days_old, "deep_clean": deep_clean}

    # Deep clean — individual deletion for messages older than 14 days
    if deep_clean and not shutdown_event.is_set():
        log.info(f"{'[DRY RUN] ' if dry_run else ''}#{channel.name} — starting deep clean")
        deep_deleted = 0

        while True:
            if shutdown_event.is_set():
                log.info(f"#{channel.name} — shutdown requested, stopping deep clean")
                break
            try:
                old_messages = []
                async for msg in channel.history(limit=50, before=bulk_cutoff):
                    if msg.created_at < cutoff:
                        old_messages.append(msg)
                        if oldest_message_date is None or msg.created_at < oldest_message_date:
                            oldest_message_date = msg.created_at

                if not old_messages:
                    log.info(f"#{channel.name} — deep clean complete | Individual deleted: {deep_deleted}")
                    break

                if dry_run:
                    deep_deleted += len(old_messages)
                    log.info(f"[DRY RUN] #{channel.name} — would individually delete {len(old_messages)} | Deep total: {deep_deleted}")
                    break
                else:
                    for msg in old_messages:
                        if shutdown_event.is_set():
                            break
                        try:
                            await msg.delete()
                            deep_deleted += 1
                            await asyncio.sleep(1.0)
                        except discord.errors.HTTPException as e:
                            if e.status == 429:
                                rate_limit_count += 1
                                retry_after = getattr(e, 'retry_after', RETRY_DELAY)
                                log.warning(f"#{channel.name} — rate limited during deep clean, retrying in {retry_after:.1f}s")
                                await asyncio.sleep(retry_after)
                            else:
                                log.error(f"#{channel.name} — HTTP error during deep clean: {e}")
                        except discord.Forbidden:
                            log.error(f"#{channel.name} — Forbidden during deep clean.")
                            break
                    log.info(f"#{channel.name} — deep clean batch | Deleted: {deep_deleted}")
                    await asyncio.sleep(2)

            except discord.errors.HTTPException as e:
                if e.status == 429:
                    rate_limit_count += 1
                    retry_after = getattr(e, 'retry_after', RETRY_DELAY)
                    await asyncio.sleep(retry_after)
                else:
                    log.error(f"#{channel.name} — HTTP error fetching old messages: {e}")
                    break

        total_deleted += deep_deleted

    log.info(f"{'[DRY RUN] ' if dry_run else ''}#{channel.name} — complete | Total: {total_deleted} | Rate limits: {rate_limit_count}")
    return {"count": total_deleted, "rate_limits": rate_limit_count, "oldest": oldest_message_date, "days": days_old, "deep_clean": deep_clean}


async def run_cleanup(guild, single_channel_id=None, dry_run: bool = False):
    """Core cleanup logic used by both scheduler and slash commands."""
    setup_run_log()
    update_health()

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.error("Log channel not found. Check LOG_CHANNEL_ID in .env.discord_cleanup")
        return

    run_time = datetime.now(timezone.utc)
    bulk_cutoff = run_time - timedelta(days=14)
    local_run_time = datetime.now()

    log.info(
        f"{'[DRY RUN] ' if dry_run else ''}Run cutoff: {local_run_time.strftime('%Y-%m-%d %H:%M:%S')} | "
        f"Bulk cutoff: {(local_run_time - timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S')} | "
        f"TZ: {os.getenv('TZ', 'UTC')}"
    )

    channel_map = build_channel_map(guild)

    for ch in raw_channels:
        if ch.get("exclude", False):
            log.info(f"#{ch.get('name', ch['id'])} — excluded (skipping)")

    if single_channel_id:
        if single_channel_id in channel_map:
            channel_map = {single_channel_id: channel_map[single_channel_id]}
        else:
            log.warning(f"Channel ID {single_channel_id} not in configured channels")
            return

    log.info(f"Starting {'dry run' if dry_run else 'cleanup run'} on {guild.name} across {len(channel_map)} channel(s)...")

    category_results = {}
    standalone_results = {}
    channel_results = {}
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

        stats = await purge_channel(
            channel, ch_config["days"], bulk_cutoff, run_time,
            dry_run=dry_run, deep_clean=ch_config.get("deep_clean", False)
        )
        stats["is_override"] = ch_config["is_override"]

        if stats["count"] > 0:
            grand_total += stats["count"]
            channel_results[channel.name] = stats["count"]
        if stats["count"] == -1:
            has_warnings = True

        grand_rate_limits += stats["rate_limits"]

        if stats["oldest"] and (oldest_overall is None or stats["oldest"] < oldest_overall):
            oldest_overall = stats["oldest"]

        cat_name = ch_config["category_name"]
        if cat_name:
            if cat_name not in category_results:
                category_results[cat_name] = {"default_days": ch_config["category_default"], "channels": {}}
            category_results[cat_name]["channels"][channel.name] = stats
        else:
            standalone_results[channel.name] = stats

        await asyncio.sleep(2)

    run_end = datetime.now()
    elapsed = run_end - run_start
    minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"

    if not dry_run and not single_channel_id:
        update_stats(channel_results)

    log.info(
        f"{'[DRY RUN] ' if dry_run else ''}=== RUN SUMMARY | "
        f"Channels: {len(channel_map)} | Deleted: {grand_total} | "
        f"Rate limits: {grand_rate_limits} | Duration: {duration_str} ==="
    )

    # Color logic
    if dry_run:
        color, status = 0x95A5A6, "🔍 Dry Run Complete"
    elif has_warnings and grand_total == 0:
        color, status = 0xFF0000, "⛔ Completed with Errors"
        log.error("Run completed with errors and nothing deleted")
    elif has_warnings:
        color, status = 0xFFA500, "⚠️ Completed with Warnings"
        log.warning("Run completed with warnings")
    elif grand_total > 0:
        color, status = 0x00C853, "✅ Cleanup Successful"
        log.info("Run completed successfully")
    else:
        color, status = 0x3498DB, "ℹ️ Nothing to Clean"
        log.info("Run completed — nothing to delete")

    # Build breakdown
    breakdown_lines = []
    for cat_name, cat_data in category_results.items():
        active_lines = []
        for ch_name, stats in cat_data["channels"].items():
            if stats["count"] == -1:
                active_lines.append(f"\u3000🚫 `#{ch_name}` — skipped (missing permissions)")
            elif stats["count"] > 0:
                label = "would delete" if dry_run else "deleted"
                deep_tag = " 🧹deep" if stats.get("deep_clean") else ""
                override_tag = f" ({stats['days']}d ⚡override)" if stats["is_override"] else ""
                active_lines.append(f"\u3000🗑️ `#{ch_name}` — **{stats['count']}** {label}{override_tag}{deep_tag}")
        if active_lines:
            breakdown_lines.append(f"📁 **{cat_name}** ({cat_data['default_days']}d default)")
            breakdown_lines.extend(active_lines)

    for ch_name, stats in standalone_results.items():
        if stats["count"] == -1:
            breakdown_lines.append(f"🚫 `#{ch_name}` — skipped (missing permissions)")
        elif stats["count"] > 0:
            label = "would delete" if dry_run else "deleted"
            deep_tag = " 🧹deep" if stats.get("deep_clean") else ""
            override_tag = f" ({stats['days']}d ⚡override)" if stats["is_override"] else ""
            breakdown_lines.append(f"🗑️ `#{ch_name}` — **{stats['count']}** {label}{override_tag}{deep_tag}")

    if not breakdown_lines:
        breakdown_lines.append("✅ No messages to clean")

    oldest_str = oldest_overall.strftime('%Y-%m-%d %I:%M %p') if oldest_overall else "N/A"
    title_prefix = "🔍 Dry Run Report" if dry_run else "🧹 Daily Cleanup Report"

    summary = (
        f"🏠 Server: **{guild.name}**\n"
        f"📅 Default retention: **{DEFAULT_RETENTION} days**\n"
        f"🔍 Channels checked: **{len(channel_map)}**\n"
        f"🗑️ {'Would delete' if dry_run else 'Total deleted'}: **{grand_total}**\n"
        + (f"📆 Oldest message: **{oldest_str}**\n" if grand_total > 0 else "")
        + (f"⚡ Rate limits hit: **{grand_rate_limits}**\n" if not dry_run else "")
        + f"⏱️ Duration: **{duration_str}**\n"
        + (f"⏭️ Next run: **{get_next_run_str()}**" if not dry_run else "")
    )

    embed = discord.Embed(
        title=f"{title_prefix} — {status}",
        description=summary,
        color=color,
        timestamp=run_end
    )
    embed.add_field(name="📋 Per-Channel Breakdown", value="\n".join(breakdown_lines), inline=False)
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)
    update_health()


# --- Slash Commands ---
cleanup_group = app_commands.Group(name="cleanup", description="Discord Cleanup Bot commands")
stats_group = app_commands.Group(name="stats", description="Cleanup statistics commands", parent=cleanup_group)


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


@cleanup_group.command(name="dryrun", description="Preview what would be deleted without actually deleting anything")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_dryrun(interaction: discord.Interaction):
    await interaction.response.send_message("🔍 Dry run started — preview report will be posted to the log channel when complete.", ephemeral=True)
    log.info(f"Dry run triggered by {interaction.user} in #{interaction.channel.name}")
    await run_cleanup(interaction.guild, dry_run=True)


@cleanup_group.command(name="reload", description="Reload channels.yml without restarting the bot")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_reload(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    log.info(f"channels.yml reload triggered by {interaction.user}")
    success, message = reload_channels()
    embed = discord.Embed(
        title="🔄 channels.yml Reloaded" if success else "🔄 Reload Failed",
        description=f"{'✅' if success else '⛔'} {message}",
        color=0x2ECC71 if success else 0xFF0000,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@cleanup_group.command(name="version", description="Show bot version and uptime")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_version(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title=f"ℹ️ Discord Cleanup Bot v{BOT_VERSION}",
        description=(
            f"⏱️ Uptime: **{get_uptime_str()}**\n"
            f"🐳 Image: `ghcr.io/antwanchild/discord_cleanup:{BOT_VERSION}`\n"
            f"🕐 Scheduled runs: **{', '.join(CLEAN_TIMES)}**"
        ),
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@cleanup_group.command(name="status", description="Show current bot configuration and next scheduled run")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    configured_count = 0
    excluded = []
    exclude_ids = {ch["id"] for ch in raw_channels if ch.get("exclude", False)}

    for ch in raw_channels:
        if ch.get("exclude", False):
            excluded.append(ch)
            continue
        if ch.get("type") == "category":
            category = interaction.guild.get_channel(ch["id"])
            if category:
                for sub in category.text_channels:
                    if sub.id not in exclude_ids and sub.permissions_for(interaction.guild.me).manage_messages:
                        configured_count += 1
        else:
            discord_channel = interaction.guild.get_channel(ch["id"])
            if discord_channel and discord_channel.category:
                if any(c["id"] == discord_channel.category.id for c in raw_channels if c.get("type") == "category"):
                    continue
            configured_count += 1

    channel_lines = []
    last_category_days = DEFAULT_RETENTION
    for ch in raw_channels:
        if ch.get("exclude", False):
            continue
        ch_name = ch.get("name", str(ch["id"]))
        deep = " 🧹" if ch.get("deep_clean") else ""
        if ch.get("type") == "category":
            days = ch.get("days", DEFAULT_RETENTION)
            last_category_days = days
            channel_lines.append(f"📁 **{ch_name}** ({days}d default{deep})")
        else:
            days = ch.get("days", DEFAULT_RETENTION)
            retention = f"{days}d ⚡override" if days != last_category_days else f"{days}d"
            channel_lines.append(f"\u3000`#{ch_name}` — {retention}{deep}")

    embed = discord.Embed(
        title="⚙️ Discord Cleanup Bot — Status",
        description=(
            f"🏠 Server: **{interaction.guild.name}**\n"
            f"📅 Default retention: **{DEFAULT_RETENTION} days**\n"
            f"🔍 Channels configured: **{configured_count}**\n"
            f"⛔ Channels excluded: **{len(excluded)}**\n"
            f"🕐 Scheduled runs: **{', '.join(CLEAN_TIMES)}**\n"
            f"⏭️ Next run: **{get_next_run_str()}**\n"
            f"📋 Log level: **{LOG_LEVEL}**\n"
            f"🗂️ Log retention: **{LOG_MAX_FILES} days**\n"
            f"⏱️ Uptime: **{get_uptime_str()}**"
        ),
        color=0x5865F2,
        timestamp=datetime.now()
    )
    if channel_lines:
        embed.add_field(
            name="📋 Configured Channels (🧹 = deep clean enabled)",
            value="\n".join(channel_lines),
            inline=False
        )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


# --- Stats Commands ---

@stats_group.command(name="view", description="Show cleanup statistics")
@app_commands.checks.has_permissions(administrator=True)
async def stats_view(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    stats = load_stats()
    all_time = stats.get("all_time", {})
    rolling_30 = stats.get("rolling_30", {})
    monthly = stats.get("monthly", {})
    top_channels = sorted(all_time.get("channels", {}).items(), key=lambda x: x[1], reverse=True)[:5]

    embed = discord.Embed(
        title="📊 Cleanup Statistics",
        description=(
            f"🏠 Server: **{interaction.guild.name}**\n\n"
            f"**📅 Last 30 Days** (since {rolling_30.get('reset', 'N/A')})\n"
            f"\u3000🔁 Runs: **{rolling_30.get('runs', 0)}**\n"
            f"\u3000🗑️ Deleted: **{rolling_30.get('deleted', 0)}**\n\n"
            f"**🗓️ This Month** (since {monthly.get('reset', 'N/A')})\n"
            f"\u3000🔁 Runs: **{monthly.get('runs', 0)}**\n"
            f"\u3000🗑️ Deleted: **{monthly.get('deleted', 0)}**\n\n"
            f"**🏆 All Time**\n"
            f"\u3000🔁 Runs: **{all_time.get('runs', 0)}**\n"
            f"\u3000🗑️ Deleted: **{all_time.get('deleted', 0)}**"
        ),
        color=0x9B59B6,
        timestamp=datetime.now()
    )
    if top_channels:
        embed.add_field(
            name="🏆 Top 5 Channels (All Time)",
            value="\n".join([f"`#{ch}` — **{count}** deleted" for ch, count in top_channels]),
            inline=False
        )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


class StatsResetView(discord.ui.View):
    def __init__(self, scope: str, user: discord.User):
        super().__init__(timeout=30)
        self.scope = scope
        self.user = user

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("⛔ Only the person who triggered this reset can confirm it.", ephemeral=True)
            return
        success = reset_stats(self.scope)
        self.stop()
        embed = discord.Embed(
            title="🗑️ Stats Reset" if success else "🗑️ Stats Reset Failed",
            description=f"✅ **{self.scope.capitalize()}** stats have been reset." if success else "⛔ Invalid scope — no stats were changed.",
            color=0x2ECC71 if success else 0xFF0000,
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("⛔ Only the person who triggered this reset can cancel it.", ephemeral=True)
            return
        self.stop()
        log.info(f"Stats reset cancelled by {interaction.user} — scope: {self.scope}")
        embed = discord.Embed(
            title="🗑️ Stats Reset Cancelled",
            description="No stats were changed.",
            color=0x95A5A6,
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self):
        log.info(f"Stats reset timed out — scope: {self.scope}")


@stats_group.command(name="reset", description="Reset cleanup statistics")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(scope="Which stats to reset")
@app_commands.choices(scope=[
    app_commands.Choice(name="Rolling 30 Days", value="rolling"),
    app_commands.Choice(name="This Month", value="monthly"),
    app_commands.Choice(name="All Time", value="all"),
])
async def stats_reset(interaction: discord.Interaction, scope: app_commands.Choice[str]):
    log.info(f"Stats reset requested by {interaction.user} — scope: {scope.value}")
    embed = discord.Embed(
        title="⚠️ Confirm Stats Reset",
        description=f"Are you sure you want to reset **{scope.name}** stats?\n\nThis cannot be undone.",
        color=0xFFA500,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.response.send_message(embed=embed, view=StatsResetView(scope=scope.value, user=interaction.user), ephemeral=True)


@cleanup_group.error
async def cleanup_group_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("⛔ You need Administrator permissions to use this command.", ephemeral=True)


# --- Scheduler ---

def schedule_runner():
    def cleanup_job(scheduled_time: str):
        now = datetime.now()
        hour, minute = map(int, scheduled_time.split(":"))
        expected = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delay_minutes = (now - expected).total_seconds() / 60

        if delay_minutes > MISSED_RUN_THRESHOLD_MINUTES:
            log.warning(f"Cleanup run for {scheduled_time} is {delay_minutes:.1f} minutes late — posting alert")
            for guild in bot.guilds:
                asyncio.run_coroutine_threadsafe(post_missed_run_alert(guild, scheduled_time), bot.loop)

        log.info(f"Scheduled cleanup run starting for time slot {scheduled_time}")
        for guild in bot.guilds:
            asyncio.run_coroutine_threadsafe(run_cleanup(guild), bot.loop)

    def monthly_check():
        if datetime.now().day == 1:
            for guild in bot.guilds:
                asyncio.run_coroutine_threadsafe(post_status_report(guild), bot.loop)

    for t in CLEAN_TIMES:
        t_captured = t
        schedule.every().day.at(t).do(lambda st=t_captured: cleanup_job(st))
        log.info(f"Scheduled daily cleanup at {t}")

    schedule.every().day.at(STATUS_REPORT_TIME).do(monthly_check)
    log.info(f"Scheduled monthly report check at {STATUS_REPORT_TIME} (fires on 1st)")
    log.info(f"Scheduler started — {len(CLEAN_TIMES)} cleanup run(s) per day: {', '.join(CLEAN_TIMES)}")

    while not shutdown_event.is_set():
        schedule.run_pending()
        update_health()
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

    for guild in bot.guilds:
        validate_channels(guild)
        await post_deploy_notification(guild)
        await post_startup_notification(guild)

    bot.tree.clear_commands(guild=None)
    bot.tree.add_command(cleanup_group)
    await bot.tree.sync()
    log.info("Slash commands registered and synced")

    update_health()
    start_scheduler()


@bot.event
async def on_resumed():
    log.info("Bot resumed connection — scheduler already running")
    update_health()


def main():
    asyncio.run(bot.start(TOKEN))


main()
