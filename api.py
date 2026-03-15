import os
import asyncio
from flask import Blueprint, jsonify, request

from config import BOT_VERSION, LOG_DIR, log
from utils import get_uptime_str, get_next_run_str, get_bot, get_bot_loop
from stats import load_stats
import utils

# All /api/* and /run/* routes live here, registered as a Blueprint in web.py
api = Blueprint("api", __name__)


def _get_status_context() -> dict:
    """Shared status context — imported by web.py for template rendering and exposed here as JSON."""
    import config as cfg
    return {
        "version":          BOT_VERSION,
        "uptime":           get_uptime_str(),
        "next_run":         get_next_run_str(),
        "schedule":         cfg.CLEAN_TIMES,
        "default_retention": cfg.DEFAULT_RETENTION,
        "log_level":        cfg.LOG_LEVEL,
        "warn_unconfigured": cfg.WARN_UNCONFIGURED,
        "report_frequency": cfg.REPORT_FREQUENCY,
        "log_max_files":    cfg.LOG_MAX_FILES,
    }


# ── Status & config ───────────────────────────────────────────────────────────

@api.route("/api/status")
def api_status():
    """Bot status, uptime, next run, and config values."""
    return jsonify(_get_status_context())


@api.route("/api/stats")
def api_stats():
    """Full stats payload — all-time, rolling 30-day, monthly, and per-channel breakdown."""
    return jsonify(load_stats())


@api.route("/api/schedule")
def api_schedule():
    """Current schedule and next run time."""
    import config as cfg
    return jsonify({
        "schedule": cfg.CLEAN_TIMES,
        "next_run": get_next_run_str(),
    })


@api.route("/api/channels")
def api_channels():
    """Configured channel list with category, retention, and deep clean info."""
    from cleanup import build_channel_map
    bot = get_bot()
    if not bot or not bot.guilds:
        return jsonify({"error": "Bot not ready"}), 503

    guild       = bot.guilds[0]
    channel_map = build_channel_map(guild)
    channels    = []
    for ch_id, data in channel_map.items():
        discord_channel = guild.get_channel(ch_id)
        channels.append({
            "id":          ch_id,
            "name":        discord_channel.name if discord_channel else str(ch_id),
            "category":    data.get("category_name") or "Standalone",
            "days":        data.get("days"),
            "is_override": data.get("is_override", False),
            "deep_clean":  data.get("deep_clean", False),
        })

    channels.sort(key=lambda x: (x["category"], x["name"]))
    return jsonify({"guild": guild.name, "channels": channels, "total": len(channels)})


@api.route("/api/logs/latest")
def api_logs_latest():
    """Last N lines of the most recent log file. Query param: ?lines=50 (default 50, max 500)."""
    try:
        lines_requested = min(int(request.args.get("lines", 50)), 500)
    except ValueError:
        lines_requested = 50

    try:
        log_files = sorted([
            f for f in os.listdir(LOG_DIR)
            if f.startswith("cleanup-") and f.endswith(".log")
        ], reverse=True)
        if not log_files:
            return jsonify({"log_file": None, "lines": []})

        latest = os.path.join(LOG_DIR, log_files[0])
        with open(latest, "r") as f:
            lines = f.readlines()[-lines_requested:]
        return jsonify({
            "log_file":      log_files[0],
            "lines_returned": len(lines),
            "lines":         [line.rstrip() for line in lines],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api.route("/api/run_status")
def api_run_status():
    """Returns whether a cleanup run is currently in progress."""
    return jsonify({"run_in_progress": utils.run_in_progress})


# ── Run triggers ──────────────────────────────────────────────────────────────

@api.route("/run/full", methods=["POST"])
def trigger_full_run():
    """Trigger a full cleanup run via the web UI."""
    from cleanup import run_cleanup

    if utils.run_in_progress:
        return jsonify({"success": False, "message": "A cleanup run is already in progress"}), 409

    bot  = get_bot()
    loop = get_bot_loop()
    if not bot or not loop:
        return jsonify({"success": False, "message": "Bot is not ready yet"}), 503

    if not bot.guilds:
        return jsonify({"success": False, "message": "Bot is not in any guilds"}), 503

    guild = bot.guilds[0]

    async def _run():
        utils.run_in_progress = True
        try:
            await run_cleanup(bot, guild, triggered_by="web UI")
        finally:
            utils.run_in_progress = False

    asyncio.run_coroutine_threadsafe(_run(), loop)
    log.info("Full cleanup run triggered from web UI")
    return jsonify({"success": True, "message": "Full cleanup run started — check the log channel for results"})


@api.route("/run/channel", methods=["POST"])
def trigger_channel_run():
    """Trigger a cleanup run on a specific configured channel via the web UI."""
    from cleanup import run_cleanup, build_channel_map

    if utils.run_in_progress:
        return jsonify({"success": False, "message": "A cleanup run is already in progress"}), 409

    bot  = get_bot()
    loop = get_bot_loop()
    if not bot or not loop:
        return jsonify({"success": False, "message": "Bot is not ready yet"}), 503

    if not bot.guilds:
        return jsonify({"success": False, "message": "Bot is not in any guilds"}), 503

    guild = bot.guilds[0]

    try:
        channel_id = int(request.form.get("channel_id", 0))
    except ValueError:
        return jsonify({"success": False, "message": "Invalid channel ID"}), 400

    channel_map  = build_channel_map(guild)
    if channel_id not in channel_map:
        return jsonify({"success": False, "message": "Channel not found in configured channels"}), 404

    discord_channel = guild.get_channel(channel_id)
    channel_name    = discord_channel.name if discord_channel else str(channel_id)

    async def _run():
        utils.run_in_progress = True
        try:
            await run_cleanup(bot, guild, single_channel_id=channel_id, triggered_by="web UI")
        finally:
            utils.run_in_progress = False

    asyncio.run_coroutine_threadsafe(_run(), loop)
    log.info(f"Channel cleanup run triggered from web UI for #{channel_name}")
    return jsonify({"success": True, "message": f"Cleanup started for #{channel_name} — check the log channel for results"})
