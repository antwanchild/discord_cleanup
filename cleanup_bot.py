import discord
import schedule
import time
import asyncio
import threading
import os
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(".env.discord_cleanup")

BOT_VERSION = "1.5.0"

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
CLEAN_TIMES = [t.strip() for t in os.getenv("CLEAN_TIME", "03:00").split(",") if t.strip()]
LOG_MAX_FILES = int(os.getenv("LOG_MAX_FILES", 7))
LOG_DIR = "/app/logs"

# Parse TARGET_CHANNELS as channel_id:days pairs
TARGET_CHANNELS = {}
for entry in os.getenv("TARGET_CHANNELS", "").split(","):
    entry = entry.strip()
    if ":" in entry:
        channel_id, days = entry.split(":")
        TARGET_CHANNELS[int(channel_id.strip())] = int(days.strip())

RETRY_DELAY = 300

# --- Logging Setup ---
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

log = logging.getLogger("discord-cleanup")


def setup_run_log():
    """Creates a new date stamped log file for this run and cleans up old ones."""
    os.makedirs(LOG_DIR, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"cleanup-{today}.log")

    # Remove any previous file handlers
    for h in logger.handlers[:]:
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            h.close()

    # Add new file handler for today
    file_handler = logging.FileHandler(log_path, mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    log.info(f"=== Discord Cleanup Bot v{BOT_VERSION} ===")
    log.info(f"Log file started: {log_path}")
    log.info(
        f"Config snapshot | "
        f"CLEAN_TIMES={CLEAN_TIMES} | "
        f"TZ={os.getenv('TZ', 'UTC')} | "
        f"LOG_MAX_FILES={LOG_MAX_FILES} | "
        f"TARGET_CHANNELS={TARGET_CHANNELS}"
    )

    # Delete log files older than LOG_MAX_FILES days
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


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

client = discord.Client(intents=intents)


async def purge_channel(channel, days_old: int) -> dict:
    """Purges old messages from a single channel. Returns stats dict."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)
    guild = channel.guild
    total_deleted = 0
    rate_limit_count = 0
    retry_count = 0
    oldest_message_date = None

    if not channel.permissions_for(guild.me).manage_messages:
        log.warning(f"Skipping #{channel.name} — missing Manage Messages permission")
        return {"count": -1, "rate_limits": 0, "retries": 0, "oldest": None, "days": days_old}

    log.info(f"Starting purge on #{channel.name} | Days: {days_old} | Cutoff: {cutoff.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    while True:
        try:
            messages_to_delete = []

            async for msg in channel.history(limit=100, before=cutoff):
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
            log.info(f"#{channel.name} — deleted batch of {len(messages_to_delete)} | Running total: {total_deleted} | Retries: {retry_count}")
            await asyncio.sleep(1.5)

        except discord.errors.HTTPException as e:
            if e.status == 429:
                rate_limit_count += 1
                retry_count += 1
                retry_after = getattr(e, 'retry_after', RETRY_DELAY)
                log.warning(f"#{channel.name} — rate limited (hit #{rate_limit_count}). Retrying in {retry_after:.1f}s...")
                await asyncio.sleep(retry_after)
            else:
                log.error(f"#{channel.name} — HTTP error: {e}")
                break
        except discord.Forbidden:
            log.error(f"#{channel.name} — Forbidden. Check bot permissions.")
            return {"count": -1, "rate_limits": 0, "retries": 0, "oldest": None, "days": days_old}

    log.info(f"#{channel.name} — complete | Deleted: {total_deleted} | Rate limits: {rate_limit_count} | Retries: {retry_count}")
    return {"count": total_deleted, "rate_limits": rate_limit_count, "retries": retry_count, "oldest": oldest_message_date, "days": days_old}


async def purge_all_channels():
    setup_run_log()

    log_channel = client.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.error("Log channel not found. Check LOG_CHANNEL_ID in .env.discord_cleanup")
        return

    guild = log_channel.guild
    log.info(f"Starting cleanup run on server: {guild.name} across {len(TARGET_CHANNELS)} channel(s)...")

    results = {}
    grand_total = 0
    grand_rate_limits = 0
    has_warnings = False
    oldest_overall = None
    run_start = datetime.now()

    for channel_id, days_old in TARGET_CHANNELS.items():
        channel = client.get_channel(channel_id)
        if not channel:
            log.warning(f"Channel ID {channel_id} not found — skipping")
            results[f"Unknown ({channel_id})"] = None
            has_warnings = True
            continue

        stats = await purge_channel(channel, days_old)
        results[channel.name] = stats

        if stats["count"] > 0:
            grand_total += stats["count"]
        if stats["count"] == -1:
            has_warnings = True

        grand_rate_limits += stats["rate_limits"]

        if stats["oldest"] and (oldest_overall is None or stats["oldest"] < oldest_overall):
            oldest_overall = stats["oldest"]

        await asyncio.sleep(2)

    run_end = datetime.now()
    elapsed = run_end - run_start
    minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    timestamp = run_end.strftime('%Y-%m-%d %I:%M %p')

    # Next scheduled run — find the next upcoming time today or tomorrow
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
    for name, stats in results.items():
        if stats is None:
            breakdown_lines.append(f"⚠️ `#{name}` — channel not found")
        elif stats["count"] == -1:
            breakdown_lines.append(f"🚫 `#{name}` — skipped (missing permissions)")
        elif stats["count"] == 0:
            breakdown_lines.append(f"✅ `#{name}` — nothing to delete ({stats['days']}d retention)")
        else:
            breakdown_lines.append(f"🗑️ `#{name}` — **{stats['count']}** messages deleted ({stats['days']}d retention)")

    oldest_str = oldest_overall.strftime('%Y-%m-%d %I:%M %p') if oldest_overall else "N/A"

    summary = (
        f"🏠 Server: **{guild.name}**\n"
        f"🗑️ Total deleted: **{grand_total}**\n"
        f"📆 Oldest message deleted: **{oldest_str}**\n"
        f"⚡ Rate limits hit: **{grand_rate_limits}**\n"
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


def schedule_runner():
    def job():
        asyncio.run_coroutine_threadsafe(purge_all_channels(), client.loop)

    for t in CLEAN_TIMES:
        schedule.every().day.at(t).do(job)
        log.info(f"Scheduled daily run at {t}")

    log.info(f"Scheduler started — {len(CLEAN_TIMES)} run(s) per day: {', '.join(CLEAN_TIMES)}")

    while True:
        schedule.run_pending()
        time.sleep(30)


def start_scheduler():
    if any(t.name == "scheduler" for t in threading.enumerate()):
        log.info("Scheduler already running — skipping")
        return

    log.info("Starting scheduler thread...")
    thread = threading.Thread(target=schedule_runner, daemon=True, name="scheduler")
    thread.start()


@client.event
async def on_ready():
    log.info(f"Logged in as {client.user} | v{BOT_VERSION}")
    log.info(f"Targeting {len(TARGET_CHANNELS)} channel(s)")
    log.info(f"Cleanup scheduled {len(CLEAN_TIMES)} time(s) per day: {', '.join(CLEAN_TIMES)}")
    start_scheduler()


@client.event
async def on_resumed():
    log.info("Bot resumed connection — scheduler already running")


client.run(TOKEN)
