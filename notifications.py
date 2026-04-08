"""
notifications.py — Discord embed notifications for startup, deploy, reports, and schedule events.
All functions are async and post directly to the configured log or report channel.
Startup notification includes an update check against the latest VERSION on main.
"""
import asyncio
import os
import urllib.request
import discord
from datetime import datetime

from config import (
    BOT_VERSION, DATA_DIR, GITHUB_TOKEN, LAST_VERSION_FILE, LOG_CHANNEL_ID,
    MISSED_RUN_THRESHOLD_MINUTES, REPORT_CHANNEL_ID, WARN_UNCONFIGURED,
    log
)
from stats import load_stats
from utils import atomic_write_text, get_next_run_str


def _version_gt(a: str, b: str) -> bool:
    """Returns True if version a is greater than version b."""
    try:
        return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))
    except ValueError:
        return False


async def _fetch_latest_version() -> str | None:
    """Fetches the latest version from the VERSION file on main branch. Returns None on failure."""
    def _get():
        url = "https://raw.githubusercontent.com/antwanchild/discord_cleanup/main/VERSION"
        headers = {"User-Agent": "discord-cleanup-bot"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode().strip()
    try:
        return await asyncio.to_thread(_get)
    except Exception as e:
        log.warning(f"Version check failed — {e}. If the repo is private, set GITHUB_TOKEN in your .env.")
        return None


async def post_startup_notification(bot, guild):
    """Posts a startup notification to the log channel on every boot."""
    from cleanup import build_channel_map
    import config

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post startup notification — log channel not found")
        return

    # Check for unconfigured channels if enabled
    unaccounted = []
    if WARN_UNCONFIGURED:
        accounted_ids = {ch["id"] for ch in config.raw_channels}
        for discord_channel in guild.text_channels:
            if discord_channel.id not in accounted_ids:
                if discord_channel.category and discord_channel.category.id in accounted_ids:
                    continue
                unaccounted.append(discord_channel)

    channel_map = build_channel_map(guild)
    missing_configured = sum(1 for entry in config.raw_channels if guild.get_channel(entry["id"]) is None)
    configured_channels = len(channel_map)
    report_channel = bot.get_channel(REPORT_CHANNEL_ID)

    latest_version = await _fetch_latest_version()
    version_str = ""
    if latest_version and _version_gt(latest_version, BOT_VERSION):
        version_str = f"\n📦 Update available: vCurr: **{BOT_VERSION}** | vNext: **{latest_version}**"

    description = (
        f"🏠 Server: **{guild.name}**\n"
        f"⏭️ Next run: **{get_next_run_str()}**\n"
        f"📋 Configured active channels: **{configured_channels}**"
        f"{version_str}"
    )

    if unaccounted:
        names = ", ".join([f"`#{ch.name}`" for ch in unaccounted[:10]])
        if len(unaccounted) > 10:
            names += f" and {len(unaccounted) - 10} more"
        description += f"\n\n⚠️ **{len(unaccounted)} unconfigured channel(s):**\n{names}\nAdd to `channels.yml` or set `exclude: true` to silence this warning."

    embed = discord.Embed(
        title=f"🟢 Bot Online — v{BOT_VERSION}",
        description=description,
        color=0x2ECC71 if not unaccounted else 0xFFA500,
        timestamp=datetime.now()
    )
    embed.add_field(
        name="Startup Self-Check",
        value=(
            f"Log channel: **{'OK' if log_channel else 'Missing'}**\n"
            f"Report channel: **{'OK' if report_channel else 'Missing'}**\n"
            f"Configured entries: **{len(config.raw_channels)}**\n"
            f"Missing configured targets: **{missing_configured}**\n"
            f"Unconfigured text channels: **{len(unaccounted)}**"
        ),
        inline=False
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)
    log.info("Startup notification posted")


async def post_deploy_notification(bot, guild):
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
        atomic_write_text(LAST_VERSION_FILE, BOT_VERSION)
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

    # Read and filter changelog by last_version (relative to project root working dir)
    changelog = None
    try:
        with open("CHANGELOG", "r") as f:
            lines = f.readlines()

        filtered = []
        for line in lines:
            line = line.strip()
            if "|" not in line:
                continue
            version, msg = line.split("|", 1)
            version = version.strip()
            msg = msg.strip()
            # Only show commits newer than last_version
            if last_version and _version_gt(version, last_version):
                filtered.append(f"- {msg} `({version})`")
            elif not last_version:
                filtered.append(f"- {msg} `({version})`")

        if filtered:
            changelog = "\n".join(filtered)
    except FileNotFoundError:
        pass

    embed = discord.Embed(
        title=f"🚀 New Version Deployed — v{BOT_VERSION}",
        description=description,
        color=0x5865F2,
        timestamp=datetime.now()
    )
    if changelog:
        # Truncate if too long for Discord field limit
        if len(changelog) > 1000:
            changelog = changelog[:997] + "..."
        embed.add_field(name="📝 Changes", value=changelog, inline=False)
    embed.add_field(name="🐳 Image", value=f"`ghcr.io/antwanchild/discord_cleanup:{BOT_VERSION}`", inline=False)
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)


async def post_status_report(bot, guild, label: str = "monthly"):
    """Posts a scheduled stats report to the report channel."""
    report_channel = bot.get_channel(REPORT_CHANNEL_ID)
    if not report_channel:
        log.warning("Could not post status report — report channel not found")
        return

    stats = load_stats()
    monthly = stats.get("monthly", {})
    last_month = stats.get("last_month")

    # If monthly stats were reset today, the cleanup already ran before the report.
    # Display last month's completed data instead of the partial current-month data.
    today = datetime.now().strftime("%Y-%m-%d")
    display = monthly
    if label == "monthly" and last_month and monthly.get("reset") == today:
        display = last_month

    channels = display.get("channels", {})
    top_channels = sorted(
        channels.items(),
        key=lambda x: x[1]["count"] if isinstance(x[1], dict) else x[1],
        reverse=True
    )[:10]

    # Build diff string — only meaningful when showing current month vs prior month
    diff_str = ""
    if last_month and display is monthly:
        prev = last_month.get("deleted", 0)
        curr = monthly.get("deleted", 0)
        delta = curr - prev
        if delta > 0:
            diff_str = f"\n📈 vs last month: **+{delta}** ({prev} → {curr})"
        elif delta < 0:
            diff_str = f"\n📉 vs last month: **{delta}** ({prev} → {curr})"
        else:
            diff_str = f"\n➡️ vs last month: **no change** ({prev})"

    title = f"📊 {'Weekly' if label == 'weekly' else 'Monthly'} Cleanup Report"

    embed = discord.Embed(
        title=title,
        description=(
            f"🏠 Server: **{guild.name}**\n"
            f"📅 Period: **Since {display.get('reset', 'N/A')}**\n"
            f"🔁 Runs completed: **{display.get('runs', 0)}**\n"
            f"🗑️ Total deleted: **{display.get('deleted', 0)}**{diff_str}\n"
            f"📋 Active channels: **{len(channels)}**"
        ),
        color=0xE67E22,
        timestamp=datetime.now()
    )

    if top_channels:
        def ch_display(ch_id, ch_data):
            if isinstance(ch_data, dict):
                return f"`#{ch_data['name']}` — **{ch_data['count']}** deleted"
            return f"`#{ch_id}` — **{ch_data}** deleted"
        embed.add_field(
            name="🏆 Top Channels",
            value="\n".join([ch_display(ch_id, ch_data) for ch_id, ch_data in top_channels]),
            inline=False
        )
    else:
        embed.add_field(name="🏆 Top Channels", value="No messages deleted this period", inline=False)

    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await report_channel.send(embed=embed)
    log.info(f"{label.capitalize()} status report posted")


async def post_schedule_notification(bot, guild, old_times: list, new_times: list, changed_by: str):
    """Posts a notification when the cleanup schedule is changed."""
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post schedule notification — log channel not found")
        return
    embed = discord.Embed(
        title="🕐 Schedule Updated",
        description=(
            f"🏠 Server: **{guild.name}**\n"
            f"👤 Changed by: **{changed_by}**\n\n"
            f"**Before:** `{', '.join(old_times)}`\n"
            f"**After:** `{', '.join(new_times)}`"
        ),
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)
    log.info(f"Schedule notification posted — {', '.join(old_times)} -> {', '.join(new_times)}")


async def post_missed_run_alert(bot, guild, scheduled_time: str):
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


async def post_catchup_notification(bot, guild, missed_time_str: str):
    """Posts a notification when a missed scheduled run is detected on startup."""
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post catchup notification — log channel not found")
        return
    embed = discord.Embed(
        title="🔄 Missed Run Detected — Running Now",
        description=(
            f"🏠 Server: **{guild.name}**\n"
            f"🕐 Missed scheduled time: **{missed_time_str}**\n\n"
            f"A cleanup run was missed while the bot was offline. "
            f"Running now to catch up."
        ),
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)
    log.info(f"Catchup notification posted for missed run at {missed_time_str}")


async def post_schedule_error_notification(bot, guild, error: str):
    """Posts a notification when the cleanup task fails to reschedule after a schedule change."""
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post schedule error notification — log channel not found")
        return
    embed = discord.Embed(
        title="⚠️ Schedule Saved — Task Reschedule Failed",
        description=(
            f"🏠 Server: **{guild.name}**\n\n"
            f"The schedule was saved to the env file but the cleanup task could not be rescheduled in memory.\n\n"
            f"**Error:** `{error}`\n\n"
            f"The new schedule will take effect on the next container restart."
        ),
        color=0xFFA500,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)
    log.warning(f"Schedule error notification posted — {error}")
