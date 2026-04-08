"""
web.py — Flask app, page routes, and web server thread management.
Registers the api Blueprint and starts the server on WEB_PORT (default 8080).
"""
import os
import secrets
import threading
import logging
import re
from datetime import datetime
from flask import Flask, abort, jsonify, render_template, request, session

from config import (
    BOT_VERSION, CONFIG_DIR, LOG_DIR, LOG_MAX_FILES, log
)
from utils import (
    get_uptime_str, get_next_run_str,
    update_retention, update_log_level, update_warn_unconfigured,
    update_report_frequency, update_log_max_files, update_schedule,
    get_bot, get_run_owner, is_run_in_progress,
    read_cleanup_log, read_latest_cleanup_log,
)
from config_utils import save_channels_content, validate_channels_content
from stats import load_stats, load_last_run
from api import api, _get_status_context

# Flask app setup — templates and static files live alongside web.py
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("WEB_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.register_blueprint(api)

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", 8080))
WEB_AUTH_HEADER_NAME = os.getenv("WEB_AUTH_HEADER_NAME", "").strip()
WEB_AUTH_HEADER_VALUE = os.getenv("WEB_AUTH_HEADER_VALUE", "")

if bool(WEB_AUTH_HEADER_NAME) != bool(WEB_AUTH_HEADER_VALUE):
    raise RuntimeError("WEB_AUTH_HEADER_NAME and WEB_AUTH_HEADER_VALUE must either both be set or both be empty")


def _csrf_token() -> str:
    """Returns the current session CSRF token, creating one when needed."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    """Exposes the CSRF token to all templates."""
    return {"csrf_token": _csrf_token()}


def _with_error_location(message: str, success: bool = False, **extra):
    """Adds parsed line/column fields when present in an error message."""
    payload = {"success": success, "message": message, **extra}
    match = re.search(r"line (\d+), column (\d+)", message)
    if match:
        payload["line"] = int(match.group(1))
        payload["column"] = int(match.group(2))
    return jsonify(payload)


@app.before_request
def protect_web_ui():
    """Applies optional proxy-header auth and CSRF checks for mutating requests."""
    if request.endpoint == "static" or request.path == "/api/health":
        return None

    if WEB_AUTH_HEADER_NAME:
        supplied = request.headers.get(WEB_AUTH_HEADER_NAME)
        if supplied != WEB_AUTH_HEADER_VALUE:
            log.warning("Rejected web request with missing or invalid proxy auth header | path=%s", request.path)
            abort(403)

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if submitted != _csrf_token():
            log.warning("Rejected web request with invalid CSRF token | path=%s", request.path)
            abort(403)
    return None


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Main dashboard — status, stats, run controls, and quick overview."""
    from cleanup import build_channel_map
    context = _get_status_context()
    stats = load_stats()
    context["stats"] = stats
    context["now"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    context["last_run"] = load_last_run()
    context["run_in_progress"] = is_run_in_progress()
    context["run_owner"] = get_run_owner()

    # Build sorted channel list for the single-channel run selector
    # Use guild.get_channel() to get the real Discord channel name
    bot = get_bot()
    configured_channels = []
    if bot and bot.guilds:
        guild = bot.guilds[0]
        channel_map = build_channel_map(guild)
        for ch_id, data in channel_map.items():
            discord_channel = guild.get_channel(ch_id)
            name     = discord_channel.name if discord_channel else str(ch_id)
            category = data.get("category_name") or "Standalone"
            label    = f"{category} / #{name}"
            configured_channels.append({"id": ch_id, "name": name, "label": label, "category": category})
        configured_channels.sort(key=lambda x: (x["category"], x["name"]))
    context["configured_channels"] = configured_channels
    return render_template("index.html", **context)


@app.route("/config", methods=["GET"])
def config_page():
    """Config editor — retention, log level, warn unconfigured, report frequency."""
    context = _get_status_context()

    # Load raw channels.yml content for the editor
    try:
        with open(f"{CONFIG_DIR}/channels.yml", "r") as f:
            context["channels_yml"] = f.read()
    except Exception:
        log.exception("Could not load channels.yml for config page")
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
    content = request.form.get("channels_yml", "")
    success, message, backup_path = save_channels_content(content)
    if not success:
        status_code = 500 if "Permission denied" in message else 400
        return _with_error_location(message, success=False, details=message), status_code

    response = {
        "success": True,
        "message": message,
        "details": message,
        "backup_path": backup_path,
    }
    return jsonify(response)


@app.route("/config/channels/validate", methods=["POST"])
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
    try:
        data = read_latest_cleanup_log(lines_requested=200)
        context["log_file"] = data["log_file"]
        context["available_logs"] = data["available_logs"]
        context["log_entries"] = data["lines"]
    except Exception as e:
        log.warning(f"Could not read log file for web UI — {e}")
        context["log_entries"] = []
    return render_template("logs.html", **context)


@app.route("/logs/<filename>")
def view_log(filename):
    """View a specific log file by date."""
    context = _get_status_context()
    try:
        data = read_cleanup_log(filename, lines_requested=200)
        context["available_logs"] = data["available_logs"]
        context["log_file"] = data["log_file"]
        context["log_entries"] = data["lines"]
    except FileNotFoundError:
        context["available_logs"] = read_latest_cleanup_log(lines_requested=0)["available_logs"]
        context["log_entries"] = []
    except Exception as e:
        log.warning(f"Could not read log file {filename} for web UI — {e}")
        context["log_entries"] = []
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

    # Build grouped data — both summary totals and per-channel detail, grouped by category
    cat_totals  = {}  # category -> {count, channels: [{name, count}]}
    standalone  = []
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
                cat_totals[category] = {"count": 0, "channels": []}
            cat_totals[category]["count"] += count
            cat_totals[category]["channels"].append({"name": name, "count": count})

    # Sort categories by total count, sort channels within each category by count
    grouped_categories = sorted(
        [
            {"name": cat, "count": data["count"], "channels": sorted(data["channels"], key=lambda x: x["count"], reverse=True)}
            for cat, data in cat_totals.items()
        ],
        key=lambda x: x["count"], reverse=True
    )
    standalone_channels = sorted(standalone, key=lambda x: x["count"], reverse=True)

    # Summary view still just needs totals
    category_summary = [(g["name"], {"count": g["count"], "channels": len(g["channels"])}) for g in grouped_categories]

    context["stats"] = stats
    context["sorted_channels"] = sorted_channels
    context["category_summary"] = category_summary
    context["grouped_categories"] = grouped_categories
    context["standalone_channels"] = standalone_channels
    return render_template("stats.html", **context)



# ── Thread management ─────────────────────────────────────────────────────────

def start_web_server():
    """Starts the Flask web server in a background thread."""
    log.info(f"Web UI starting on {WEB_HOST}:{WEB_PORT}")
    # Disable Flask's default logger to avoid duplicate log entries
    flask_log = logging.getLogger("werkzeug")
    flask_log.setLevel(logging.WARNING)
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)


def start_web_thread():
    """Launches the web server thread. Called from cleanup_bot.py on_ready."""
    thread = threading.Thread(target=start_web_server, name="web-ui", daemon=True)
    thread.start()
    return thread
