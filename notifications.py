import os
import discord
from datetime import datetime

from config import (
    BOT_VERSION, DATA_DIR, LAST_VERSION_FILE, LOG_CHANNEL_ID,
    MISSED_RUN_THRESHOLD_MINUTES, REPORT_CHANNEL_ID, WARN_UNCONFIGURED,
    log, raw_channels
)
from stats import load_stats
from utils import get_next_run_str


async def post_startup_notification(bot, guild):
    """Posts a startup notification to the log channel on every boot."""
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.warning("Could not post startup notification — log channel not found")
        return

    # Check for unconfigured channels if enabled
    unaccounted = []
    if WARN_UNCONFIGURED:
        import config
        accounted_ids = {ch["id"] for ch in config.raw_channels}
        for discord_channel in guild.text_channels:
            if discord_channel.id not in accounted_ids:
                if discord_channel.category and discord_channel.category.id in accounted_ids:
                    continue
                unaccounted.append(discord_channel)

    description = f"🏠 Server: **{guild.name}**\n⏭️ Next run: **{get_next_run_str()}**"

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


async def post_status_report(bot, guild):
    """Posts a monthly stats report to the report channel."""
    report_channel = bot.get_channel(REPORT_CHANNEL_ID)
    if not report_channel:
        log.warning("Could not post status report — report channel not found")
        return

    stats = load_stats()
    monthly = stats.get("monthly", {})
    last_month = stats.get("last_month")
    channels = monthly.get("channels", {})
    top_channels = sorted(
        channels.items(),
        key=lambda x: x[1]["count"] if isinstance(x[1], dict) else x[1],
        reverse=True
    )[:10]

    # Build diff string if last month data exists
    diff_str = ""
    if last_month:
        prev = last_month.get("deleted", 0)
        curr = monthly.get("deleted", 0)
        delta = curr - prev
        if delta > 0:
            diff_str = f"\n📈 vs last month: **+{delta}** ({prev} → {curr})"
        elif delta < 0:
            diff_str = f"\n📉 vs last month: **{delta}** ({prev} → {curr})"
        else:
            diff_str = f"\n➡️ vs last month: **no change** ({prev})"

    embed = discord.Embed(
        title="📊 Monthly Cleanup Report",
        description=(
            f"🏠 Server: **{guild.name}**\n"
            f"📅 Period: **Since {monthly.get('reset', 'N/A')}**\n"
            f"🔁 Runs completed: **{monthly.get('runs', 0)}**\n"
            f"🗑️ Total deleted: **{monthly.get('deleted', 0)}**{diff_str}\n"
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
    log.info("Monthly status report posted")


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
