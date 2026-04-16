"""
utils.py — Bot state management, health checks, uptime, and logging helpers.

Config file updates → config_utils.py
Schedule management → scheduler.py
"""
import os
import logging
import threading
from datetime import datetime, timedelta
from file_utils import atomic_write_json, atomic_write_text

from config import (
    config_lock,
    BOT_START_TIME, BOT_VERSION, CLEAN_TIMES, CONFIG_DIR,
    HEALTH_FILE, LOG_DIR, LOG_MAX_FILES,
    LOG_LEVEL, numeric_level, formatter, logger, log
)

# Re-export for backwards compatibility — importers can use utils.* as before
from config_utils import (                          # noqa: F401
    reload_channels,
    update_env_value,
    update_retention,
    update_log_level,
    update_warn_unconfigured,
    update_report_frequency,
    update_log_max_files,
    update_schedule_skip_dates,
    update_schedule_skip_weekdays,
)
from scheduler import (                             # noqa: F401
    get_next_run_str,
    update_schedule,
)

# ── Bot state ─────────────────────────────────────────────────────────────────

# Set by cleanup_bot.py after tasks are created
_cleanup_task = None
_task_tz      = None
_bot          = None
_bot_loop     = None
_startup_path_status = {}

# Prevents simultaneous cleanup runs across the bot and web UI
_run_lock = threading.Lock()
_run_owner = None
run_in_progress = False


def register_task(cleanup_task, task_tz, bot):
    """Called from cleanup_bot.py after tasks are initialized."""
    from scheduler import register_task_ref
    global _cleanup_task, _task_tz, _bot
    _cleanup_task = cleanup_task
    _task_tz      = task_tz
    _bot          = bot
    register_task_ref(cleanup_task, task_tz)


def get_bot():
    """Returns the bot instance."""
    return _bot


def set_bot_loop(loop):
    """Stores the running event loop. Called from on_ready once the loop is live."""
    global _bot_loop
    _bot_loop = loop


def get_bot_loop():
    """Returns the bot's event loop for use by the web UI thread."""
    return _bot_loop


def set_startup_path_status(status: dict) -> None:
    """Stores the most recent startup writable-path check results."""
    global _startup_path_status
    _startup_path_status = dict(status)


def get_startup_path_status() -> dict:
    """Returns the most recent startup writable-path check results."""
    return dict(_startup_path_status)


def try_acquire_run(owner: str) -> bool:
    """Attempts to reserve the cleanup worker for a single run."""
    global run_in_progress, _run_owner
    acquired = _run_lock.acquire(blocking=False)
    if acquired:
        _run_owner = owner
        run_in_progress = True
    return acquired


def release_run() -> None:
    """Releases the cleanup worker reservation if held."""
    global run_in_progress, _run_owner
    if _run_lock.locked():
        _run_owner = None
        run_in_progress = False
        _run_lock.release()


def is_run_in_progress() -> bool:
    """Returns whether a cleanup run is currently active."""
    return _run_lock.locked()


def get_run_owner() -> str | None:
    """Returns the current cleanup run owner when one is active."""
    return _run_owner


# ── Health ────────────────────────────────────────────────────────────────────

def update_health():
    """Updates the health file timestamp. Used by Docker HEALTHCHECK."""
    try:
        atomic_write_text(HEALTH_FILE, datetime.now().isoformat())
    except Exception as e:
        log.warning(f"Could not update health file — {e}")


# ── Uptime ────────────────────────────────────────────────────────────────────

def get_uptime_str() -> str:
    """Returns the bot uptime as a human-readable string."""
    uptime  = datetime.now() - BOT_START_TIME
    days    = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m {seconds}s"


# ── Logging helpers ───────────────────────────────────────────────────────────

def setup_run_log(channel_count=None):
    """Creates a date-stamped log file for this run and cleans up old ones."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {LOG_DIR} — check directory permissions.")
        return

    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"cleanup-{today}.log")

    for h in logger.handlers[:]:
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            h.close()

    try:
        file_handler = logging.FileHandler(log_path, mode="a")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except PermissionError:
        log.error(f"Could not create log file {log_path} — check directory permissions.")
        return

    next_run       = get_next_run_str()
    channel_suffix = f"  |  Channels: {channel_count}" if channel_count is not None else ""
    header_line    = f"  Next run: {next_run}{channel_suffix}"
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info(f"║  Discord Cleanup Bot  v{BOT_VERSION:<34}║")
    log.info(f"║{header_line:<58}║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    log.debug(f"Log file: {log_path}")
    log.debug(
        f"Config snapshot | CLEAN_TIMES={CLEAN_TIMES} | TZ={os.getenv('TZ', 'UTC')} | "
        f"LOG_LEVEL={LOG_LEVEL} | LOG_MAX_FILES={LOG_MAX_FILES}"
    )

    # Clean up old log files
    cutoff = datetime.now() - timedelta(days=LOG_MAX_FILES)
    for filename in os.listdir(LOG_DIR):
        if filename.startswith("cleanup-") and filename.endswith(".log"):
            try:
                file_date = datetime.strptime(
                    filename.replace("cleanup-", "").replace(".log", ""), "%Y-%m-%d"
                )
                if file_date < cutoff:
                    os.remove(os.path.join(LOG_DIR, filename))
                    log.info(f"Deleted old log file: {filename}")
            except ValueError:
                pass
            except PermissionError:
                log.warning(f"Could not delete old log file {filename} — check directory permissions.")


def log_restart_separator():
    """Logs a separator line to mark a bot restart in the log file."""
    now            = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    separator_line = f" Bot Restarted | {now} | v{BOT_VERSION} "
    log.info(f"{'═' * 4}{separator_line:{'═'}<54}{'═' * 2}")
def list_cleanup_logs() -> list[str]:
    """Returns available cleanup log filenames sorted newest-first."""
    return sorted([
        f for f in os.listdir(LOG_DIR)
        if f.startswith("cleanup-") and f.endswith(".log")
    ], reverse=True)


def read_cleanup_log(filename: str, lines_requested: int = 200) -> dict:
    """Reads the requested cleanup log file and returns metadata and lines."""
    log_files = list_cleanup_logs()
    if filename not in log_files:
        raise FileNotFoundError(filename)

    path = os.path.join(LOG_DIR, filename)
    with open(path, "r") as f:
        lines = f.readlines()[-lines_requested:]

    return {
        "log_file": filename,
        "available_logs": log_files,
        "lines_returned": len(lines),
        "lines": [line.rstrip() for line in lines],
    }


def read_latest_cleanup_log(lines_requested: int = 200) -> dict:
    """Reads the most recent cleanup log file if one exists."""
    log_files = list_cleanup_logs()
    if not log_files:
        return {
            "log_file": None,
            "available_logs": [],
            "lines_returned": 0,
            "lines": [],
        }
    return read_cleanup_log(log_files[0], lines_requested=lines_requested)


def list_cleanup_logs_with_sizes() -> list[dict]:
    """Returns cleanup log metadata for API and web UI consumers."""
    files = []
    for filename in list_cleanup_logs():
        path = os.path.join(LOG_DIR, filename)
        size = os.path.getsize(path)
        files.append({
            "filename": filename,
            "date": filename.replace("cleanup-", "").replace(".log", ""),
            "size_kb": round(size / 1024, 1),
        })
    return files
