import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

from config import (
    BOT_START_TIME, BOT_VERSION, CLEAN_TIMES, CONFIG_DIR,
    DEFAULT_RETENTION, HEALTH_FILE, LOG_DIR, LOG_MAX_FILES,
    LOG_LEVEL, numeric_level, formatter, logger, log
)

# Set by cleanup_bot.py after tasks are created
_cleanup_task = None
_task_tz = None
_bot = None


def register_task(cleanup_task, task_tz, bot):
    """Called from cleanup_bot.py after tasks are initialized."""
    global _cleanup_task, _task_tz, _bot
    _cleanup_task = cleanup_task
    _task_tz = task_tz
    _bot = bot


def get_bot():
    """Returns the bot instance."""
    return _bot


def update_health():
    """Updates the health file timestamp. Used by Docker HEALTHCHECK."""
    try:
        with open(HEALTH_FILE, "w") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        log.warning(f"Could not update health file — {e}")


def get_next_run_str(cleanup_task=None, task_tz=None):
    """Returns the next scheduled run time as a formatted string."""
    task = cleanup_task or _cleanup_task
    tz = task_tz or _task_tz or ZoneInfo("UTC")
    if task and task.is_running() and task.next_iteration:
        return task.next_iteration.astimezone(tz).strftime('%Y-%m-%d %I:%M %p')
    # Fallback before task starts
    now = datetime.now(tz)
    for t in sorted(CLEAN_TIMES):
        hour, minute = map(int, t.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate.strftime('%Y-%m-%d %I:%M %p')
    hour, minute = map(int, sorted(CLEAN_TIMES)[0].split(":"))
    return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0).strftime('%Y-%m-%d %I:%M %p')


def get_uptime_str():
    """Returns the bot uptime as a formatted string."""
    uptime = datetime.now() - BOT_START_TIME
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m {seconds}s"


def setup_run_log(channel_count=None):
    """Creates a date-stamped log file for this run and cleans up old ones."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {LOG_DIR} — check directory permissions.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
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

    _next = get_next_run_str()
    _ch = f"  |  Channels: {channel_count}" if channel_count is not None else ""
    _line2 = f"  Next run: {_next}{_ch}"
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info(f"║  Discord Cleanup Bot  v{BOT_VERSION:<34}║")
    log.info(f"║{_line2:<58}║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    log.info(f"Log file: {log_path}")
    log.info(
        f"Config snapshot | CLEAN_TIMES={CLEAN_TIMES} | TZ={os.getenv('TZ', 'UTC')} | "
        f"LOG_LEVEL={LOG_LEVEL} | LOG_MAX_FILES={LOG_MAX_FILES} | DEFAULT_RETENTION={DEFAULT_RETENTION}"
    )

    cutoff = datetime.now() - timedelta(days=LOG_MAX_FILES)
    for filename in os.listdir(LOG_DIR):
        if filename.startswith("cleanup-") and filename.endswith(".log"):
            try:
                file_date = datetime.strptime(filename.replace("cleanup-", "").replace(".log", ""), "%Y-%m-%d")
                if file_date < cutoff:
                    os.remove(os.path.join(LOG_DIR, filename))
                    log.info(f"Deleted old log file: {filename}")
            except ValueError:
                pass
            except PermissionError:
                log.warning(f"Could not delete old log file {filename} — check directory permissions.")


def reload_channels():
    """Reloads channels.yml and updates raw_channels. Returns (success, message)."""
    import config
    try:
        with open(f"{CONFIG_DIR}/channels.yml", "r") as f:
            cfg = yaml.safe_load(f)
            config.raw_channels = cfg.get("channels", [])
        log.info("channels.yml reloaded successfully")
        return True, f"Loaded {len(config.raw_channels)} channel entries"
    except FileNotFoundError:
        log.error("channels.yml not found during reload")
        return False, "channels.yml not found"
    except PermissionError:
        log.error("Permission denied reading channels.yml during reload")
        return False, "Permission denied reading channels.yml"
    except yaml.YAMLError as e:
        log.error(f"channels.yml is malformed during reload — {e}")
        return False, f"channels.yml is malformed — {e}"


def update_schedule(new_times: list) -> tuple[bool, str]:
    """Updates CLEAN_TIME in .env file and reschedules the cleanup task. Returns (success, message)."""
    import config
    from zoneinfo import ZoneInfo
    from datetime import time as dtime

    env_path = os.path.join(CONFIG_DIR, ".env.discord_cleanup")

    # Validate time formats
    for t in new_times:
        try:
            hour, minute = map(int, t.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            return False, f"`{t}` is not a valid time — use 24hr format e.g. `03:00`"

    # Read existing env file
    try:
        with open(env_path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return False, f".env.discord_cleanup not found at `{env_path}`"
    except PermissionError:
        return False, "Permission denied reading .env.discord_cleanup"

    # Update or append CLEAN_TIME
    new_value = ",".join(new_times)
    found = False
    new_lines = []
    for line in lines:
        if line.startswith("CLEAN_TIME="):
            new_lines.append(f"CLEAN_TIME={new_value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"CLEAN_TIME={new_value}\n")

    try:
        with open(env_path, "w") as f:
            f.writelines(new_lines)
    except PermissionError:
        return False, "Permission denied writing .env.discord_cleanup"

    # Update in-memory config
    config.CLEAN_TIMES = new_times

    # Restart the cleanup task with new times
    if _cleanup_task is not None:
        tz = _task_tz or ZoneInfo("UTC")
        times = [dtime(hour=int(t.split(":")[0]), minute=int(t.split(":")[1]), tzinfo=tz) for t in new_times]
        if _cleanup_task.is_running():
            _cleanup_task.cancel()
        _cleanup_task.change_interval(time=times)
        _cleanup_task.start()
        log.info(f"Schedule updated to: {new_value}")

    return True, new_value
