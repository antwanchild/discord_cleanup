"""
admin.py — Flask Blueprint for state-changing admin endpoints under /admin/*.
"""
import asyncio
import re
from flask import Blueprint, jsonify, request

from config import log
from config_utils import (
    preview_channel_restore,
    preview_env_restore,
    preview_channels_content,
    restore_env_backup,
    restore_channels_backup,
    save_channels_content,
    update_report_grouping,
    validate_channels_content,
)
from utils import (
    get_bot,
    get_bot_loop,
    release_run,
    try_acquire_run,
)
from stats import reset_stats

admin = Blueprint("admin", __name__)


def _with_error_location(message: str, success: bool = False, **extra):
    """Adds parsed line/column fields when present in an error message."""
    payload = {"success": success, "message": message, **extra}
    match = re.search(r"line (\d+), column (\d+)", message)
    if match:
        payload["line"] = int(match.group(1))
        payload["column"] = int(match.group(2))
    return jsonify(payload)


def _augment_preview_with_effective_counts(preview: dict) -> dict:
    """Adds live-vs-proposed cleanup target counts when the bot is available."""
    try:
        from cleanup import build_channel_map
    except ModuleNotFoundError:
        log.warning("Could not import cleanup while building config preview; skipping effective counts")
        return preview

    bot = get_bot()
    if not bot or not bot.guilds or "parsed_channels" not in preview:
        return preview

    guild = bot.guilds[0]

    def summarize(channel_map: dict) -> dict:
        return {
            "targets": len(channel_map),
            "categories": len({data.get("category_name") for data in channel_map.values() if data.get("category_name")}),
            "standalone": sum(1 for data in channel_map.values() if not data.get("category_name")),
            "overrides": sum(1 for data in channel_map.values() if data.get("is_override")),
            "deep_clean": sum(1 for data in channel_map.values() if data.get("deep_clean")),
            "notification_groups": len({data.get("notification_group") for data in channel_map.values() if data.get("notification_group")}),
        }

    current_map = build_channel_map(guild)
    proposed_map = build_channel_map(guild, raw_channels=preview["parsed_channels"])
    preview["effective"] = {
        "current": summarize(current_map),
        "proposed": summarize(proposed_map),
        "delta": {
            "targets": len(proposed_map) - len(current_map),
            "categories": len({data.get("category_name") for data in proposed_map.values() if data.get("category_name")})
                - len({data.get("category_name") for data in current_map.values() if data.get("category_name")}),
            "standalone": sum(1 for data in proposed_map.values() if not data.get("category_name"))
                - sum(1 for data in current_map.values() if not data.get("category_name")),
            "overrides": sum(1 for data in proposed_map.values() if data.get("is_override"))
                - sum(1 for data in current_map.values() if data.get("is_override")),
            "deep_clean": sum(1 for data in proposed_map.values() if data.get("deep_clean"))
                - sum(1 for data in current_map.values() if data.get("deep_clean")),
            "notification_groups": len({data.get("notification_group") for data in proposed_map.values() if data.get("notification_group")})
                - len({data.get("notification_group") for data in current_map.values() if data.get("notification_group")}),
        },
    }
    return preview


@admin.route("/admin/config/retention", methods=["POST"])
def set_retention():
    """Update default message retention."""
    from utils import update_retention

    try:
        days = int(request.form.get("days", 0))
        if not 1 <= days <= 365:
            return jsonify({"success": False, "message": "Retention must be between 1 and 365 days"}), 400
        success, message = update_retention(days)
        return jsonify({"success": success, "message": message})
    except ValueError:
        return jsonify({"success": False, "message": "Invalid value"}), 400


@admin.route("/admin/config/loglevel", methods=["POST"])
def set_loglevel():
    """Update log verbosity level."""
    from utils import update_log_level

    level = request.form.get("level", "").upper()
    success, message = update_log_level(level)
    return jsonify({"success": success, "message": message})


@admin.route("/admin/config/warnunconfigured", methods=["POST"])
def set_warn_unconfigured():
    """Toggle warn unconfigured channels setting."""
    from utils import update_warn_unconfigured

    enabled = request.form.get("enabled", "false").lower() == "true"
    success, message = update_warn_unconfigured(enabled)
    return jsonify({"success": success, "message": message})


@admin.route("/admin/config/reportfrequency", methods=["POST"])
def set_report_frequency():
    """Update report frequency setting."""
    from utils import update_report_frequency

    frequency = request.form.get("frequency", "monthly").lower()
    success, message = update_report_frequency(frequency)
    return jsonify({"success": success, "message": message})


@admin.route("/admin/config/reportgrouping", methods=["POST"])
def set_report_grouping():
    """Toggle grouping for monthly or weekly scheduled reports."""
    scope = request.form.get("scope", "").lower().strip()
    enabled = request.form.get("enabled", "false").lower() == "true"
    success, message = update_report_grouping(scope, enabled)
    return jsonify({"success": success, "message": message})


@admin.route("/admin/config/logmaxfiles", methods=["POST"])
def set_log_max_files():
    """Update number of log files to retain."""
    from utils import update_log_max_files

    try:
        days = int(request.form.get("days", 0))
        if not 1 <= days <= 365:
            return jsonify({"success": False, "message": "Log retention must be between 1 and 365 days"}), 400
        success, message = update_log_max_files(days)
        return jsonify({"success": success, "message": message})
    except ValueError:
        return jsonify({"success": False, "message": "Invalid value"}), 400


@admin.route("/admin/config/channels", methods=["POST"])
def save_channels():
    """Save updated channels.yml content."""
    content = request.form.get("channels_yml", "")
    success, message, backup_path = save_channels_content(content)
    if not success:
        status_code = 500 if "Permission denied" in message else 400
        return _with_error_location(message, success=False, details=message), status_code

    return jsonify({
        "success": True,
        "message": message,
        "details": message,
        "backup_path": backup_path,
    })


@admin.route("/admin/config/channels/validate", methods=["POST"])
def validate_channels_route():
    """Validate channels.yml content without saving it."""
    content = request.form.get("channels_yml", "")
    success, message, channels = validate_channels_content(content)
    if not success:
        return _with_error_location(message, success=False, details=message), 400
    return jsonify({
        "success": True,
        "message": message,
        "details": message,
        "channel_count": len(channels or []),
    })


@admin.route("/admin/config/channels/preview", methods=["POST"])
def preview_channels_route():
    """Preview how proposed channels.yml content differs from the live config."""
    content = request.form.get("channels_yml", "")
    success, message, preview = preview_channels_content(content)
    if not success:
        return _with_error_location(message, success=False, details=message), 400
    preview = _augment_preview_with_effective_counts(preview)

    return jsonify({
        "success": True,
        "message": message,
        "details": message,
        "preview": preview,
    })


@admin.route("/admin/config/channels/restore/preview", methods=["POST"])
def preview_channels_restore_route():
    """Preview restoring channels.yml from a backup file."""
    backup_filename = request.form.get("backup_filename", "").strip()
    success, message, preview = preview_channel_restore(backup_filename)
    if not success:
        return _with_error_location(message, success=False, details=message), 400
    preview = _augment_preview_with_effective_counts(preview)

    return jsonify({
        "success": True,
        "message": message,
        "details": message,
        "preview": preview,
    })


@admin.route("/admin/config/channels/restore", methods=["POST"])
def restore_channels_route():
    """Restore channels.yml from a backup file."""
    backup_filename = request.form.get("backup_filename", "").strip()
    success, message, backup_path = restore_channels_backup(backup_filename)
    if not success:
        return _with_error_location(message, success=False, details=message), 400

    return jsonify({
        "success": True,
        "message": message,
        "details": message,
        "backup_path": backup_path,
    })


@admin.route("/admin/config/env/restore/preview", methods=["POST"])
def preview_env_restore_route():
    """Preview restoring .env.discord_cleanup from a backup file."""
    backup_filename = request.form.get("backup_filename", "").strip()
    success, message, preview = preview_env_restore(backup_filename)
    if not success:
        return _with_error_location(message, success=False, details=message), 400

    return jsonify({
        "success": True,
        "message": message,
        "details": message,
        "preview": preview,
    })


@admin.route("/admin/config/env/restore", methods=["POST"])
def restore_env_route():
    """Restore .env.discord_cleanup from a backup file."""
    backup_filename = request.form.get("backup_filename", "").strip()
    success, message, backup_path = restore_env_backup(backup_filename)
    if not success:
        return _with_error_location(message, success=False, details=message), 400

    return jsonify({
        "success": True,
        "message": message,
        "details": message,
        "backup_path": backup_path,
    })


@admin.route("/admin/schedule/add", methods=["POST"])
def add_schedule():
    """Add a new cleanup run time."""
    import config as cfg
    from utils import update_schedule

    time_str = request.form.get("time", "").strip()
    current = list(cfg.CLEAN_TIMES)
    if time_str in current:
        return jsonify({"success": False, "message": f"{time_str} is already in the schedule"}), 400
    current.append(time_str)
    current.sort()
    success, message, reschedule_error = update_schedule(current)
    return jsonify({"success": success, "message": message, "reschedule_error": reschedule_error})


@admin.route("/admin/schedule/remove", methods=["POST"])
def remove_schedule():
    """Remove a cleanup run time."""
    import config as cfg
    from utils import update_schedule

    time_str = request.form.get("time", "").strip()
    current = list(cfg.CLEAN_TIMES)
    if time_str not in current:
        return jsonify({"success": False, "message": f"{time_str} is not in the schedule"}), 400
    if len(current) == 1:
        return jsonify({"success": False, "message": "Cannot remove the last scheduled run time"}), 400
    current.remove(time_str)
    success, message, reschedule_error = update_schedule(current)
    return jsonify({"success": success, "message": message, "reschedule_error": reschedule_error})


@admin.route("/admin/run/full", methods=["POST"])
def trigger_full_run():
    """Trigger a full cleanup run via the web UI."""
    from cleanup import run_cleanup
    from utils import is_run_in_progress

    if is_run_in_progress():
        return jsonify({"success": False, "message": "A cleanup run is already in progress"}), 409

    bot = get_bot()
    loop = get_bot_loop()
    if not bot or not loop:
        return jsonify({"success": False, "message": "Bot is not ready yet"}), 503
    if not bot.guilds:
        return jsonify({"success": False, "message": "Bot is not in any guilds"}), 503

    guild = bot.guilds[0]
    if not try_acquire_run("web UI full run"):
        return jsonify({"success": False, "message": "A cleanup run is already in progress"}), 409

    async def _run():
        try:
            await run_cleanup(bot, guild, triggered_by="web UI")
        finally:
            release_run()

    try:
        asyncio.run_coroutine_threadsafe(_run(), loop)
    except Exception:
        release_run()
        log.exception("Failed to schedule full cleanup run from web UI")
        return jsonify({"success": False, "message": "Could not schedule cleanup run"}), 500
    log.info("Full cleanup run triggered from web UI")
    return jsonify({"success": True, "message": "Full cleanup run started — check the log channel for results"})


@admin.route("/admin/run/channel", methods=["POST"])
def trigger_channel_run():
    """Trigger a cleanup run on a specific configured channel via the web UI."""
    from cleanup import build_channel_map, run_cleanup
    from utils import is_run_in_progress

    if is_run_in_progress():
        return jsonify({"success": False, "message": "A cleanup run is already in progress"}), 409

    bot = get_bot()
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

    channel_map = build_channel_map(guild)
    if channel_id not in channel_map:
        return jsonify({"success": False, "message": "Channel not found in configured channels"}), 404

    discord_channel = guild.get_channel(channel_id)
    channel_name = discord_channel.name if discord_channel else str(channel_id)
    if not try_acquire_run(f"web UI channel run #{channel_name}"):
        return jsonify({"success": False, "message": "A cleanup run is already in progress"}), 409

    async def _run():
        try:
            await run_cleanup(bot, guild, single_channel_id=channel_id, triggered_by="web UI")
        finally:
            release_run()

    try:
        asyncio.run_coroutine_threadsafe(_run(), loop)
    except Exception:
        release_run()
        log.exception("Failed to schedule channel cleanup run from web UI for #%s", channel_name)
        return jsonify({"success": False, "message": "Could not schedule cleanup run"}), 500
    log.info(f"Channel cleanup run triggered from web UI for #{channel_name}")
    return jsonify({"success": True, "message": f"Cleanup started for #{channel_name} — check the log channel for results"})


@admin.route("/admin/config/channels/dry-run", methods=["POST"])
def preview_dry_run():
    """Run a dry cleanup preview against proposed channels.yml content."""
    from cleanup import run_cleanup
    from utils import is_run_in_progress

    content = request.form.get("channels_yml", "")
    success, message, preview = preview_channels_content(content)
    if not success or preview is None:
        status_code = 500 if "Permission denied" in message else 400
        return _with_error_location(message, success=False, details=message), status_code
    preview = _augment_preview_with_effective_counts(preview)

    if is_run_in_progress():
        return jsonify({"success": False, "message": "A cleanup run is already in progress"}), 409

    bot = get_bot()
    loop = get_bot_loop()
    if not bot or not loop:
        return jsonify({"success": False, "message": "Bot is not ready yet"}), 503
    if not bot.guilds:
        return jsonify({"success": False, "message": "Bot is not in any guilds"}), 503

    guild = bot.guilds[0]
    if not try_acquire_run("web UI preview dry run"):
        return jsonify({"success": False, "message": "A cleanup run is already in progress"}), 409

    async def _run():
        try:
            await run_cleanup(bot, guild, dry_run=True, triggered_by="web UI preview", raw_channels=preview["parsed_channels"])
        finally:
            release_run()

    try:
        asyncio.run_coroutine_threadsafe(_run(), loop)
    except Exception:
        release_run()
        log.exception("Failed to schedule preview dry run from web UI")
        return jsonify({"success": False, "message": "Could not schedule cleanup run"}), 500

    log.info("Preview dry run triggered from web UI")
    return jsonify({"success": True, "message": "Preview dry run started — check the log channel for results", "preview": preview})


@admin.route("/admin/api/stats/reset", methods=["POST"])
def stats_reset():
    """Reset stats for a given scope: rolling, monthly, or all."""
    scope = request.form.get("scope", "").lower()
    valid = ["rolling", "monthly", "all"]
    if scope not in valid:
        return jsonify({"success": False, "message": f"Invalid scope — must be one of: {', '.join(valid)}"}), 400
    success = reset_stats(scope)
    label = {"rolling": "Rolling 30 Days", "monthly": "This Month", "all": "All Time"}[scope]
    if success:
        log.info(f"Stats reset via web UI — scope: {scope}")
        return jsonify({"success": True, "message": f"{label} stats have been reset"})
    return jsonify({"success": False, "message": "Reset failed — invalid scope"}), 400
