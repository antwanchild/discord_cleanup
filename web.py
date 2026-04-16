"""
web.py — Flask app, page routes, and web server thread management.
Registers the api Blueprint and starts the server on WEB_PORT (default 8080).
"""
import os
import secrets
import threading
import logging
import time
import flask.cli
from datetime import datetime
from flask import Flask, abort, jsonify, render_template, request, session

from config import (
    CONFIG_DIR, log
)
from config_utils import list_channel_backups, list_env_backups
from utils import (
    get_bot, get_run_owner, is_run_in_progress,
    read_cleanup_log, read_latest_cleanup_log,
)
from stats import list_stats_backups, load_stats, load_last_run
from api import api, _get_status_context
from admin import admin

# Flask app setup — templates and static files live alongside web.py
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("WEB_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.register_blueprint(api)
app.register_blueprint(admin)

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", 8080))
WEB_AUTH_HEADER_NAME = os.getenv("WEB_AUTH_HEADER_NAME", "").strip()
WEB_AUTH_HEADER_VALUE = os.getenv("WEB_AUTH_HEADER_VALUE", "")
ADMIN_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("ADMIN_RATE_LIMIT_WINDOW_SECONDS", 60))
ADMIN_RATE_LIMIT_MAX_REQUESTS = int(os.getenv("ADMIN_RATE_LIMIT_MAX_REQUESTS", 20))
RUN_RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RUN_RATE_LIMIT_MAX_REQUESTS", 5))

if bool(WEB_AUTH_HEADER_NAME) != bool(WEB_AUTH_HEADER_VALUE):
    raise RuntimeError("WEB_AUTH_HEADER_NAME and WEB_AUTH_HEADER_VALUE must either both be set or both be empty")

_rate_limit_lock = threading.Lock()
_rate_limit_state: dict[tuple[str, str], list[float]] = {}


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


def _rate_limit_identity() -> str:
    """Builds a stable requester identity for admin rate limiting."""
    if WEB_AUTH_HEADER_NAME:
        forwarded_identity = request.headers.get(WEB_AUTH_HEADER_NAME)
        if forwarded_identity:
            return forwarded_identity
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")


def _check_admin_rate_limit() -> tuple[bool, int | None]:
    """Returns whether the current admin request is allowed and retry time if blocked."""
    if not request.path.startswith("/admin/"):
        return True, None

    limit = RUN_RATE_LIMIT_MAX_REQUESTS if request.path.startswith("/admin/run/") else ADMIN_RATE_LIMIT_MAX_REQUESTS
    now = time.monotonic()
    key = (_rate_limit_identity(), request.path)

    with _rate_limit_lock:
        history = _rate_limit_state.setdefault(key, [])
        history[:] = [ts for ts in history if now - ts < ADMIN_RATE_LIMIT_WINDOW_SECONDS]
        if len(history) >= limit:
            retry_after = max(1, int(ADMIN_RATE_LIMIT_WINDOW_SECONDS - (now - history[0])))
            return False, retry_after
        history.append(now)
        return True, None


@app.before_request
def protect_web_ui():
    """Applies optional proxy-header auth, CSRF checks, and rate limits."""
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

    if request.path.startswith("/admin/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        allowed, retry_after = _check_admin_rate_limit()
        if not allowed:
            log.warning("Rejected admin request due to rate limiting | path=%s | requester=%s", request.path, _rate_limit_identity())
            response = jsonify({
                "success": False,
                "message": f"Rate limit exceeded — retry in {retry_after}s",
                "retry_after": retry_after,
            })
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
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
    context["channel_backups"] = list_channel_backups()[:10]
    context["env_backups"] = list_env_backups()[:10]

    # Load raw channels.yml content for the editor
    try:
        with open(f"{CONFIG_DIR}/channels.yml", "r") as f:
            context["channels_yml"] = f.read()
    except Exception:
        log.exception("Could not load channels.yml for config page")
        context["channels_yml"] = ""

    return render_template("config.html", **context)


@app.route("/schedule", methods=["GET"])
def schedule_page():
    """Schedule management page."""
    context = _get_status_context()
    return render_template("schedule.html", **context)


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
    from cleanup import build_channel_map

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

    channel_history = stats.get("channel_history", {})
    bot = get_bot()
    history_channels = []
    if bot and bot.guilds:
        guild = bot.guilds[0]
        channel_map = build_channel_map(guild)
        for ch_id, data in channel_map.items():
            discord_channel = guild.get_channel(ch_id)
            name = discord_channel.name if discord_channel else str(ch_id)
            entries = list(reversed(channel_history.get(str(ch_id), [])))
            latest = entries[0] if entries else None
            history_channels.append({
                "id": ch_id,
                "name": name,
                "category": data.get("category_name") or "Standalone",
                "days": data.get("days"),
                "is_override": data.get("is_override", False),
                "deep_clean": data.get("deep_clean", False),
                "notification_group": data.get("notification_group"),
                "history": entries[:10],
                "history_total": len(entries),
                "latest": latest,
            })
        history_channels.sort(key=lambda item: item["latest"]["timestamp"] if item["latest"] else "", reverse=True)

    context["stats"] = stats
    context["sorted_channels"] = sorted_channels
    context["category_summary"] = category_summary
    context["grouped_categories"] = grouped_categories
    context["standalone_channels"] = standalone_channels
    context["history_channels"] = history_channels
    context["stats_backups"] = list_stats_backups()[:10]
    context["channel_backups"] = list_channel_backups()[:10]
    return render_template("stats.html", **context)


@app.route("/audit")
def audit_page():
    """Retention audit page — shows the live cleanup configuration at a glance."""
    from cleanup import build_channel_map
    import config as cfg

    context = _get_status_context()
    bot = get_bot()
    audit_rows = []
    summary = {
        "configured": 0,
        "categories": 0,
        "standalone": 0,
        "excluded": 0,
        "overrides": 0,
        "deep_clean": 0,
        "notification_groups": 0,
    }

    if bot and bot.guilds:
        guild = bot.guilds[0]
        channel_map = build_channel_map(guild)
        excluded_ids = {ch["id"] for ch in cfg.raw_channels if ch.get("exclude", False)}
        notification_groups = {ch.get("notification_group") for ch in cfg.raw_channels if ch.get("notification_group")}

        summary["configured"] = len(channel_map)
        summary["categories"] = len({data.get("category_name") for data in channel_map.values() if data.get("category_name")})
        summary["standalone"] = sum(1 for data in channel_map.values() if not data.get("category_name"))
        summary["overrides"] = sum(1 for data in channel_map.values() if data.get("is_override"))
        summary["deep_clean"] = sum(1 for data in channel_map.values() if data.get("deep_clean"))
        summary["notification_groups"] = len(notification_groups)
        summary["excluded"] = len(excluded_ids)

        for ch in cfg.raw_channels:
            ch_id = ch["id"]
            discord_channel = guild.get_channel(ch_id)
            if ch.get("exclude"):
                audit_rows.append({
                    "name": ch.get("name", str(ch_id)),
                    "kind": "Excluded",
                    "category": discord_channel.category.name if discord_channel and discord_channel.category else "Standalone",
                    "days": "—",
                    "effective_days": "Excluded",
                    "override": False,
                    "deep_clean": bool(ch.get("deep_clean", False)),
                    "notification_group": ch.get("notification_group"),
                    "status": "excluded",
                })
                continue

            if ch.get("type") == "category":
                audit_rows.append({
                    "name": ch.get("name", str(ch_id)),
                    "kind": "Category",
                    "category": "Category",
                    "days": ch.get("days", cfg.DEFAULT_RETENTION),
                    "effective_days": f"{ch.get('days', cfg.DEFAULT_RETENTION)}d",
                    "override": bool(ch.get("days") and ch.get("days") != cfg.DEFAULT_RETENTION),
                    "deep_clean": bool(ch.get("deep_clean", False)),
                    "notification_group": ch.get("notification_group"),
                    "status": "category",
                })
                continue

            mapped = channel_map.get(ch_id, {})
            audit_rows.append({
                "name": ch.get("name", discord_channel.name if discord_channel else str(ch_id)),
                "kind": "Channel",
                "category": mapped.get("category_name") or (discord_channel.category.name if discord_channel and discord_channel.category else "Standalone"),
                "days": ch.get("days", cfg.DEFAULT_RETENTION),
                "effective_days": f"{mapped.get('days', ch.get('days', cfg.DEFAULT_RETENTION))}d",
                "override": mapped.get("is_override", False),
                "deep_clean": mapped.get("deep_clean", False),
                "notification_group": mapped.get("notification_group") or ch.get("notification_group"),
                "status": "configured",
            })

    audit_rows.sort(key=lambda row: (row["category"], row["name"]))
    context["audit_summary"] = summary
    context["audit_rows"] = audit_rows
    return render_template("audit.html", **context)



# ── Thread management ─────────────────────────────────────────────────────────

def start_web_server():
    """Starts the Flask web server in a background thread."""
    log.info(f"Web UI starting on {WEB_HOST}:{WEB_PORT}")
    if WEB_HOST == "0.0.0.0" and not WEB_AUTH_HEADER_NAME:
        log.warning("Web UI is listening on 0.0.0.0 without proxy-header auth configured. Prefer WEB_HOST=127.0.0.1 behind a reverse proxy.")
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    # Disable Flask's default logger to avoid duplicate log entries
    flask_log = logging.getLogger("werkzeug")
    flask_log.setLevel(logging.WARNING)
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)


def start_web_thread():
    """Launches the web server thread. Called from cleanup_bot.py on_ready."""
    thread = threading.Thread(target=start_web_server, name="web-ui", daemon=True)
    thread.start()
    return thread
