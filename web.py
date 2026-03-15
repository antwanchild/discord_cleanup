import os
import threading
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify

from config import (
    BOT_VERSION, CONFIG_DIR, LOG_DIR, LOG_MAX_FILES, log
)
from utils import (
    get_uptime_str, get_next_run_str, reload_channels,
    update_retention, update_log_level, update_warn_unconfigured,
    update_report_frequency, update_log_max_files, update_schedule,
    get_bot, get_bot_loop
)
import utils
from stats import load_stats

# Flask app setup — templates and static files live alongside web.py
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.urandom(24)

WEB_PORT = int(os.getenv("WEB_PORT", 8080))


def _get_status_context() -> dict:
    """Builds the shared context dict used across dashboard and status endpoints."""
    import config as cfg
    return {
        "version": BOT_VERSION,
        "uptime": get_uptime_str(),
        "next_run": get_next_run_str(),
        "schedule": cfg.CLEAN_TIMES,
        "default_retention": cfg.DEFAULT_RETENTION,
        "log_level": cfg.LOG_LEVEL,
        "warn_unconfigured": cfg.WARN_UNCONFIGURED,
        "report_frequency": cfg.REPORT_FREQUENCY,
        "log_max_files": cfg.LOG_MAX_FILES,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Main dashboard — status, stats, run controls, and quick overview."""
    from cleanup import build_channel_map
    context = _get_status_context()
    stats = load_stats()
    context["stats"] = stats
    context["now"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    context["run_in_progress"] = utils.run_in_progress

    # Build sorted channel list for the single-channel run selector
    bot = get_bot()
    configured_channels = []
    if bot and bot.guilds:
        channel_map = build_channel_map(bot.guilds[0])
        configured_channels = sorted(
            [{"id": ch_id, "name": data.get("name", str(ch_id))} for ch_id, data in channel_map.items()],
            key=lambda x: x["name"].lower()
        )
    context["configured_channels"] = configured_channels
    return render_template("index.html", **context)


@app.route("/config", methods=["GET"])
def config_page():
    """Config editor — retention, log level, warn unconfigured, report frequency."""
    import config as cfg
    import yaml
    context = _get_status_context()

    # Load raw channels.yml content for the editor
    try:
        with open(f"{CONFIG_DIR}/channels.yml", "r") as f:
            context["channels_yml"] = f.read()
    except Exception:
        context["channels_yml"] = ""

    return render_template("config.html", **context)


@app.route("/config/retention", methods=["POST"])
def set_retention():
    """Update default message retention."""
    try:
        days = int(request.form.get("days", 0))
        if not 1 <= days <= 365:
            return jsonify({"success": False, "message": "Retention must be between 1 and 365 days"}), 400
        success, message = update_retention(days)
        return jsonify({"success": success, "message": message})
    except ValueError:
        return jsonify({"success": False, "message": "Invalid value"}), 400


@app.route("/config/loglevel", methods=["POST"])
def set_loglevel():
    """Update log verbosity level."""
    level = request.form.get("level", "").upper()
    success, message = update_log_level(level)
    return jsonify({"success": success, "message": message})


@app.route("/config/warnunconfigured", methods=["POST"])
def set_warn_unconfigured():
    """Toggle warn unconfigured channels setting."""
    enabled = request.form.get("enabled", "false").lower() == "true"
    success, message = update_warn_unconfigured(enabled)
    return jsonify({"success": success, "message": message})


@app.route("/config/reportfrequency", methods=["POST"])
def set_report_frequency():
    """Update report frequency setting."""
    frequency = request.form.get("frequency", "monthly").lower()
    success, message = update_report_frequency(frequency)
    return jsonify({"success": success, "message": message})


@app.route("/config/logmaxfiles", methods=["POST"])
def set_log_max_files():
    """Update number of log files to retain."""
    try:
        days = int(request.form.get("days", 0))
        if not 1 <= days <= 365:
            return jsonify({"success": False, "message": "Log retention must be between 1 and 365 days"}), 400
        success, message = update_log_max_files(days)
        return jsonify({"success": success, "message": message})
    except ValueError:
        return jsonify({"success": False, "message": "Invalid value"}), 400


@app.route("/config/channels", methods=["POST"])
def save_channels():
    """Save updated channels.yml content."""
    import yaml
    from config import config_lock
    content = request.form.get("channels_yml", "")
    try:
        # Validate YAML before saving
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        return jsonify({"success": False, "message": f"Invalid YAML — {e}"}), 400

    with config_lock:
        try:
            with open(f"{CONFIG_DIR}/channels.yml", "w") as f:
                f.write(content)
        except PermissionError:
            return jsonify({"success": False, "message": "Permission denied writing channels.yml"}), 500

    success, message = reload_channels()
    return jsonify({"success": success, "message": message})


@app.route("/schedule", methods=["GET"])
def schedule_page():
    """Schedule management page."""
    context = _get_status_context()
    return render_template("schedule.html", **context)


@app.route("/schedule/add", methods=["POST"])
def add_schedule():
    """Add a new cleanup run time."""
    import config as cfg
    time_str = request.form.get("time", "").strip()
    current = list(cfg.CLEAN_TIMES)
    if time_str in current:
        return jsonify({"success": False, "message": f"{time_str} is already in the schedule"}), 400
    current.append(time_str)
    current.sort()
    success, message, reschedule_error = update_schedule(current)
    return jsonify({"success": success, "message": message, "reschedule_error": reschedule_error})


@app.route("/schedule/remove", methods=["POST"])
def remove_schedule():
    """Remove a cleanup run time."""
    import config as cfg
    time_str = request.form.get("time", "").strip()
    current = list(cfg.CLEAN_TIMES)
    if time_str not in current:
        return jsonify({"success": False, "message": f"{time_str} is not in the schedule"}), 400
    if len(current) == 1:
        return jsonify({"success": False, "message": "Cannot remove the last scheduled run time"}), 400
    current.remove(time_str)
    success, message, reschedule_error = update_schedule(current)
    return jsonify({"success": success, "message": message, "reschedule_error": reschedule_error})


@app.route("/logs")
def logs_page():
    """Log viewer — shows the most recent log file."""
    context = _get_status_context()
    log_entries = []
    try:
        log_files = sorted([
            f for f in os.listdir(LOG_DIR)
            if f.startswith("cleanup-") and f.endswith(".log")
        ], reverse=True)
        if log_files:
            latest = os.path.join(LOG_DIR, log_files[0])
            with open(latest, "r") as f:
                # Show last 200 lines
                lines = f.readlines()[-200:]
                log_entries = [line.rstrip() for line in lines]
            context["log_file"] = log_files[0]
            context["available_logs"] = log_files
    except Exception as e:
        log.warning(f"Could not read log file for web UI — {e}")

    context["log_entries"] = log_entries
    return render_template("logs.html", **context)


@app.route("/logs/<filename>")
def view_log(filename):
    """View a specific log file by date."""
    context = _get_status_context()
    log_entries = []
    try:
        log_files = sorted([
            f for f in os.listdir(LOG_DIR)
            if f.startswith("cleanup-") and f.endswith(".log")
        ], reverse=True)
        context["available_logs"] = log_files
        log_path = os.path.join(LOG_DIR, filename)
        if os.path.exists(log_path) and filename in log_files:
            with open(log_path, "r") as f:
                lines = f.readlines()[-200:]
                log_entries = [line.rstrip() for line in lines]
            context["log_file"] = filename
    except Exception as e:
        log.warning(f"Could not read log file {filename} for web UI — {e}")

    context["log_entries"] = log_entries
    return render_template("logs.html", **context)


@app.route("/stats")
def stats_page():
    """Statistics page — provides both per-channel detail and category summary views."""
    context = _get_status_context()
    stats = load_stats()

    # Pre-sort channels by count descending for the detail view
    raw_channels = stats.get("all_time", {}).get("channels", {})
    sorted_channels = sorted(
        raw_channels.items(),
        key=lambda x: x[1]["count"] if isinstance(x[1], dict) else x[1],
        reverse=True
    )

    # Build category summary using the category field stored in each channel's stats entry
    cat_totals = {}
    standalone = []
    for ch_id, ch_data in raw_channels.items():
        if not isinstance(ch_data, dict):
            continue
        count    = ch_data.get("count", 0)
        name     = ch_data.get("name", str(ch_id))
        category = ch_data.get("category", "Standalone")
        if category == "Standalone":
            standalone.append({"name": name, "count": count})
        else:
            if category not in cat_totals:
                cat_totals[category] = {"count": 0, "channels": 0}
            cat_totals[category]["count"]    += count
            cat_totals[category]["channels"] += 1

    category_summary    = sorted(cat_totals.items(), key=lambda x: x[1]["count"], reverse=True)
    standalone_channels = sorted(standalone, key=lambda x: x["count"], reverse=True)

    context["stats"] = stats
    context["sorted_channels"] = sorted_channels
    context["category_summary"] = category_summary
    context["standalone_channels"] = standalone_channels
    return render_template("stats.html", **context)




@app.route("/api/status")
def api_status():
    """JSON status endpoint for health checks or external tools."""
    return jsonify(_get_status_context())


@app.route("/run/full", methods=["POST"])
def trigger_full_run():
    """Trigger a full cleanup run via the web UI."""
    import asyncio
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


@app.route("/run/channel", methods=["POST"])
def trigger_channel_run():
    """Trigger a cleanup run on a specific channel via the web UI."""
    import asyncio
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

    channel_map = build_channel_map(guild)
    if channel_id not in channel_map:
        return jsonify({"success": False, "message": "Channel not found in configured channels"}), 404

    channel_name = channel_map[channel_id].get("name", str(channel_id))

    async def _run():
        utils.run_in_progress = True
        try:
            await run_cleanup(bot, guild, single_channel_id=channel_id, triggered_by="web UI")
        finally:
            utils.run_in_progress = False

    asyncio.run_coroutine_threadsafe(_run(), loop)
    log.info(f"Channel cleanup run triggered from web UI for #{channel_name}")
    return jsonify({"success": True, "message": f"Cleanup started for #{channel_name} — check the log channel for results"})


@app.route("/api/run_status")
def api_run_status():
    """Returns whether a cleanup run is currently in progress."""
    return jsonify({"run_in_progress": utils.run_in_progress})


# ── Thread management ─────────────────────────────────────────────────────────

def start_web_server():
    """Starts the Flask web server in a background thread."""
    log.info(f"Web UI starting on port {WEB_PORT}")
    # Disable Flask's default logger to avoid duplicate log entries
    flask_log = logging.getLogger("werkzeug")
    flask_log.setLevel(logging.WARNING)
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)


def start_web_thread():
    """Launches the web server thread. Called from cleanup_bot.py on_ready."""
    thread = threading.Thread(target=start_web_server, name="web-ui", daemon=True)
    thread.start()
    return thread
