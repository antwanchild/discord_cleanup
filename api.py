"""
api.py — Flask Blueprint for read-only /api/* endpoints.
Registered in web.py — do not instantiate a separate Flask app here.
"""
from flask import Blueprint, jsonify, request

from config import BOT_VERSION, log
from utils import (
    get_uptime_str,
    get_next_run_str,
    get_bot,
    get_run_owner,
    get_startup_path_status,
    is_run_in_progress,
    list_cleanup_logs_with_sizes,
    read_cleanup_log,
    read_latest_cleanup_log,
)
from config_utils import list_channel_backups
from notifications import get_recent_notification_fallbacks
from stats import StatsLoadError, list_stats_backups, load_stats, load_last_run

# All read-only /api/* routes live here, registered as a Blueprint in web.py
api = Blueprint("api", __name__)


def _internal_error_response(message: str, exc: Exception):
    """Logs the exception and returns a generic 500 response."""
    log.exception(message, exc_info=(type(exc), exc, exc.__traceback__))
    return jsonify({"error": "Internal server error"}), 500


def _format_startup_path_check() -> dict:
    """Formats startup path checks for JSON/UI consumers."""
    return {
        path: {"ok": status, "detail": detail}
        for path, (status, detail) in get_startup_path_status().items()
    }


def _get_status_context() -> dict:
    """Shared status context — imported by web.py for template rendering and exposed here as JSON."""
    import config as cfg
    fallbacks = get_recent_notification_fallbacks()
    return {
        "version":          BOT_VERSION,
        "uptime":           get_uptime_str(),
        "next_run":         get_next_run_str(),
        "schedule":         cfg.CLEAN_TIMES,
        "default_retention": cfg.DEFAULT_RETENTION,
        "log_level":        cfg.LOG_LEVEL,
        "warn_unconfigured": cfg.WARN_UNCONFIGURED,
        "report_frequency": cfg.REPORT_FREQUENCY,
        "report_group_monthly": cfg.REPORT_GROUP_MONTHLY,
        "report_group_weekly": cfg.REPORT_GROUP_WEEKLY,
        "log_max_files":    cfg.LOG_MAX_FILES,
        "stats_backup_retention_days": cfg.STATS_BACKUP_RETENTION_DAYS,
        "startup_path_check": _format_startup_path_check(),
        "notification_fallbacks_recent": len(fallbacks),
        "last_notification_fallback": fallbacks[0] if fallbacks else None,
        "run_in_progress":  is_run_in_progress(),
        "run_owner":        get_run_owner(),
    }


# ── Status & config ───────────────────────────────────────────────────────────

@api.route("/api/status")
def api_status():
    """Bot status, uptime, next run, and config values."""
    return jsonify(_get_status_context())


@api.route("/api/stats")
def api_stats():
    """Full stats payload — all-time, rolling 30-day, monthly, and per-channel breakdown."""
    try:
        return jsonify(load_stats(strict=True))
    except StatsLoadError as e:
        return _internal_error_response("Could not serve stats API", e)


@api.route("/api/last_run")
def api_last_run():
    """Last cleanup run summary — timestamp, status, deleted count, duration, top categories."""
    data = load_last_run()
    if not data:
        return jsonify({"error": "No runs recorded yet"}), 404
    return jsonify(data)


@api.route("/api/backups/stats")
def api_stats_backups():
    """Lists available stats.json and last_run.json backup files."""
    import config as cfg
    backups = list_stats_backups()
    return jsonify({
        "retention_days": cfg.STATS_BACKUP_RETENTION_DAYS,
        "total": len(backups),
        "backups": backups,
    })


@api.route("/api/backups/channels")
def api_channels_backups():
    """Lists available channels.yml backup files."""
    import config as cfg
    backups = list_channel_backups()
    return jsonify({
        "retention_days": cfg.CHANNELS_BACKUP_RETENTION_DAYS,
        "total": len(backups),
        "backups": backups,
    })


@api.route("/api/notifications/fallbacks")
def api_notification_fallbacks():
    """Lists recent notification fallback events."""
    fallbacks = get_recent_notification_fallbacks()
    return jsonify({"total": len(fallbacks), "fallbacks": fallbacks})


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
        data = read_latest_cleanup_log(lines_requested=lines_requested)
        return jsonify({
            "log_file": data["log_file"],
            "lines_returned": data["lines_returned"],
            "lines": data["lines"],
        })
    except Exception as e:
        return _internal_error_response("Could not serve latest log API", e)


@api.route("/api/run_status")
def api_run_status():
    """Returns whether a cleanup run is currently in progress."""
    return jsonify({"run_in_progress": is_run_in_progress(), "run_owner": get_run_owner()})


@api.route("/api/health")
def api_health():
    """Simple health check endpoint for uptime monitoring tools."""
    return jsonify({"status": "ok", "version": BOT_VERSION})


@api.route("/api/channels/unconfigured")
def api_channels_unconfigured():
    """List of Discord channels not in channels.yml — requires WARN_UNCONFIGURED to be meaningful."""
    import config as cfg
    bot = get_bot()
    if not bot or not bot.guilds:
        return jsonify({"error": "Bot not ready"}), 503

    guild       = bot.guilds[0]
    configured  = set()

    # Collect all configured channel IDs including category sub-channels
    from cleanup import build_channel_map
    channel_map = build_channel_map(guild)
    configured  = set(channel_map.keys())

    # Also add excluded channel IDs
    excluded_ids = {ch["id"] for ch in cfg.raw_channels if ch.get("exclude")}
    configured.update(excluded_ids)

    # Find all text channels in the guild not in configured set
    unconfigured = []
    for channel in guild.text_channels:
        if channel.id not in configured:
            unconfigured.append({
                "id":       channel.id,
                "name":     channel.name,
                "category": channel.category.name if channel.category else "No Category",
            })

    unconfigured.sort(key=lambda x: (x["category"], x["name"]))
    return jsonify({
        "guild":       guild.name,
        "total":       len(unconfigured),
        "channels":    unconfigured,
    })


@api.route("/api/logs")
def api_logs_list():
    """List all available log files with name, date and size."""
    try:
        files = list_cleanup_logs_with_sizes()
        return jsonify({"total": len(files), "files": files})
    except Exception as e:
        return _internal_error_response("Could not list logs API", e)


@api.route("/api/logs/<filename>")
def api_logs_file(filename):
    """Fetch a specific log file by name. Query param: ?lines=200 (default 200, max 500)."""
    try:
        lines_requested = min(int(request.args.get("lines", 200)), 500)
    except ValueError:
        lines_requested = 200

    try:
        data = read_cleanup_log(filename, lines_requested=lines_requested)
        return jsonify({
            "log_file": data["log_file"],
            "lines_returned": data["lines_returned"],
            "lines": data["lines"],
        })
    except FileNotFoundError:
        return jsonify({"error": "Log file not found"}), 404
    except Exception as e:
        return _internal_error_response("Could not serve log file API", e)
