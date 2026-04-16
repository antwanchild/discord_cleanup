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
from collections import deque

from config import (
    BOT_VERSION, DATA_DIR, GITHUB_TOKEN, LAST_VERSION_FILE, LOG_CHANNEL_ID,
    MISSED_RUN_THRESHOLD_MINUTES, REPORT_CHANNEL_ID, WARN_UNCONFIGURED,
    log
)
from file_utils import atomic_write_text
from stats import load_stats
from utils import get_next_run_str

EMBED_TITLE_LIMIT = 256
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_NAME_LIMIT = 256
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_FOOTER_LIMIT = 2048
EMBED_TOTAL_LIMIT = 6000
_recent_notification_fallbacks = deque(maxlen=25)


def _version_gt(a: str, b: str) -> bool:
    """Returns True if version a is greater than version b."""
    try:
        return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))
    except ValueError:
        return False


def _load_recent_changelog_entries(last_version: str | None = None) -> list[str]:
    """Returns markdown changelog bullets newer than the last deployed version.

    Any legacy `Unreleased` section is ignored if it appears in an older file.
    """
    try:
        with open("CHANGELOG.md", "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    entries = []
    current_version = None

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("## "):
            heading = line[3:].strip()
            version = heading.split(" - ", 1)[0].strip()
            if version == "Unreleased":
                current_version = version
                continue
            current_version = version
            continue

        if not line.startswith("- ") or not current_version:
            continue

        if current_version == "Unreleased":
            entries.append(line)
            continue

        if last_version and not _version_gt(current_version, last_version):
            continue

        entries.append(f"{line} `({current_version})`")

    return entries


def _build_notification_leaderboard(channels: dict, channel_map: dict, limit: int = 10, group_notification_groups: bool = True) -> list[dict]:
    """Aggregates report entries by notification_group while preserving standalone channels."""
    grouped = {}

    for ch_id, ch_data in channels.items():
        if isinstance(ch_data, dict):
            count = ch_data.get("count", 0)
            name = ch_data.get("name", str(ch_id))
        else:
            count = ch_data
            name = str(ch_id)

        if count <= 0:
            continue

        live_config = channel_map.get(int(ch_id)) or channel_map.get(str(ch_id)) or {}
        if live_config.get("report_exclude", False):
            continue

        report_individual = live_config.get("report_individual", False)
        notification_group = live_config.get("notification_group")
        explicit_group = live_config.get("report_group_override")
        should_group = bool(notification_group) and not report_individual and (group_notification_groups or bool(explicit_group))

        if should_group:
            key = f"group:{notification_group}"
            if key not in grouped:
                grouped[key] = {
                    "label": notification_group,
                    "count": 0,
                    "channels": set(),
                    "grouped": True,
                }
            grouped[key]["count"] += count
            grouped[key]["channels"].add(name)
            continue

        key = f"channel:{ch_id}"
        grouped[key] = {
            "label": f"#{name}",
            "count": count,
            "channels": {name},
            "grouped": False,
        }

    leaderboard = sorted(grouped.values(), key=lambda item: item["count"], reverse=True)
    return leaderboard[:limit]


def _truncate_text(value, limit: int) -> str:
    """Truncates text to Discord's limits while preserving readability."""
    if value is None:
        return ""
    value = str(value)
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def _embed_text_length(embed) -> int:
    """Calculates the total text payload size for an embed."""
    total = len(getattr(embed, "title", "") or "")
    total += len(getattr(embed, "description", "") or "")
    footer = getattr(embed, "_footer", {}) or {}
    total += len(footer.get("text", "") or "")
    for field in getattr(embed, "_fields", []) or []:
        total += len(field.get("name", "") or "")
        total += len(field.get("value", "") or "")
    return total


def sanitize_embed(embed):
    """Trims embed fields to Discord limits."""
    if getattr(embed, "title", None):
        embed.title = _truncate_text(embed.title, EMBED_TITLE_LIMIT)
    if getattr(embed, "description", None):
        embed.description = _truncate_text(embed.description, EMBED_DESCRIPTION_LIMIT)

    fields = list(getattr(embed, "_fields", []) or [])
    if fields and hasattr(embed, "clear_fields") and hasattr(embed, "add_field"):
        embed.clear_fields()
        for field in fields:
            embed.add_field(
                name=_truncate_text(field.get("name", ""), EMBED_FIELD_NAME_LIMIT),
                value=_truncate_text(field.get("value", ""), EMBED_FIELD_VALUE_LIMIT),
                inline=field.get("inline", False),
            )

    footer = getattr(embed, "_footer", {}) or {}
    if footer.get("text") and hasattr(embed, "set_footer"):
        embed.set_footer(text=_truncate_text(footer.get("text", ""), EMBED_FOOTER_LIMIT))

    while _embed_text_length(embed) > EMBED_TOTAL_LIMIT and getattr(embed, "_fields", None):
        trimmed_fields = list(embed._fields)
        last_field = trimmed_fields[-1]
        excess = _embed_text_length(embed) - EMBED_TOTAL_LIMIT
        new_limit = max(16, len(last_field.get("value", "")) - excess)
        new_value = _truncate_text(last_field.get("value", ""), new_limit)
        if new_value == last_field.get("value", ""):
            trimmed_fields.pop()
        else:
            trimmed_fields[-1] = {**last_field, "value": new_value}
        embed.clear_fields()
        for field in trimmed_fields:
            embed.add_field(name=field.get("name", ""), value=field.get("value", ""), inline=field.get("inline", False))

    return embed


async def safe_send_embed(channel, embed, *, fallback_text: str | None = None, context: str = "notification") -> bool:
    """Sends an embed safely with trimming and optional plain-text fallback."""
    sanitize_embed(embed)
    try:
        await channel.send(embed=embed)
        return True
    except discord.Forbidden:
        log.warning("Could not send %s — missing Discord permissions", context)
        return False
    except discord.HTTPException as e:
        log.warning("Could not send %s embed — %s", context, e)
        if fallback_text:
            try:
                log.warning("Falling back to plain-text send for %s", context)
                _recent_notification_fallbacks.append({
                    "context": context,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                await channel.send(content=_truncate_text(fallback_text, 2000))
            except discord.Forbidden:
                log.warning("Could not send %s fallback text — missing Discord permissions", context)
            except discord.HTTPException:
                log.exception("Could not send %s fallback text", context)
        return False


def get_recent_notification_fallbacks() -> list[dict]:
    """Returns recent notification fallback events newest-first."""
    return list(reversed(_recent_notification_fallbacks))


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
    await safe_send_embed(
        log_channel,
        embed,
        fallback_text="Bot startup notification generated, but the full embed could not be delivered.",
        context="startup notification",
    )
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

    # Read and filter markdown changelog by last_version (relative to project root working dir)
    changelog = None
    filtered = _load_recent_changelog_entries(last_version=last_version)
    if filtered:
        changelog = "\n".join(filtered)

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
    await safe_send_embed(
        log_channel,
        embed,
        fallback_text="Deploy notification generated, but the full embed could not be delivered.",
        context="deploy notification",
    )


async def post_status_report(bot, guild, label: str = "monthly"):
    """Posts a scheduled stats report to the report channel."""
    from cleanup import build_channel_map

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
    channel_map = build_channel_map(guild)
    group_notification_groups = cfg.REPORT_GROUP_MONTHLY if label == "monthly" else cfg.REPORT_GROUP_WEEKLY
    leaderboard = _build_notification_leaderboard(channels, channel_map, limit=10, group_notification_groups=group_notification_groups)

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

    if leaderboard:
        def ch_display(item):
            if item["grouped"]:
                return f"`{item['label']}` — **{item['count']}** deleted across **{len(item['channels'])}** channels"
            return f"`{item['label']}` — **{item['count']}** deleted"
        embed.add_field(
            name="🏆 Top Groups",
            value="\n".join([ch_display(item) for item in leaderboard]),
            inline=False
        )
    else:
        embed.add_field(name="🏆 Top Groups", value="No messages deleted this period", inline=False)

    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await safe_send_embed(
        report_channel,
        embed,
        fallback_text=f"{title} generated for {guild.name}, but the full embed could not be delivered. Check logs or the web UI for details.",
        context=f"{label} status report",
    )
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
    await safe_send_embed(
        log_channel,
        embed,
        fallback_text="Schedule update notification generated, but the full embed could not be delivered.",
        context="schedule notification",
    )
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
    await safe_send_embed(
        log_channel,
        embed,
        fallback_text="Scheduled run delay alert generated, but the full embed could not be delivered.",
        context="missed-run alert",
    )
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
    await safe_send_embed(
        log_channel,
        embed,
        fallback_text="Catchup run notification generated, but the full embed could not be delivered.",
        context="catchup notification",
    )
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
    await safe_send_embed(
        log_channel,
        embed,
        fallback_text="Schedule error notification generated, but the full embed could not be delivered.",
        context="schedule error notification",
    )
    log.warning(f"Schedule error notification posted — {error}")
