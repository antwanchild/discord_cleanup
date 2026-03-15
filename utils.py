import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

from config import (
    config_lock,
    BOT_START_TIME, BOT_VERSION, CLEAN_TIMES, CONFIG_DIR,
    DEFAULT_RETENTION, HEALTH_FILE, LOG_DIR, LOG_MAX_FILES,
    LOG_LEVEL, numeric_level, formatter, logger, log
)

# Set by cleanup_bot.py after tasks are created
_cleanup_task = None
_task_tz = None
_bot = None
_bot_loop = None

# Prevents simultaneous manual runs triggered from web UI or slash commands
run_in_progress = False


def register_task(cleanup_task, task_tz, bot):
    """Called from cleanup_bot.py after tasks are initialized."""
    global _cleanup_task, _task_tz, _bot
    _cleanup_task = cleanup_task
    _task_tz = task_tz
    _bot = bot


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
    log.debug(f"Log file: {log_path}")
    log.debug(
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


def log_restart_separator():
    """Logs a separator line to mark a bot restart in the log file."""
    now = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    _line = f" Bot Restarted | {now} | v{BOT_VERSION} "
    log.info(f"{'═' * 4}{_line:{'═'}<54}{'═' * 2}")


def reload_channels():
    """Reloads channels.yml and updates raw_channels. Returns (success, message)."""
    import config
    with config_lock:
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


def update_env_value(key: str, value: str) -> tuple[bool, str]:
    """Updates a single key in .env.discord_cleanup. Returns (success, message)."""
    import time
    env_path = os.path.join(CONFIG_DIR, ".env.discord_cleanup")
    with config_lock:
        try:
            with open(env_path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return False, f".env.discord_cleanup not found at `{env_path}`"
        except PermissionError:
            return False, "Permission denied reading .env.discord_cleanup"

        found = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={value}\n")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}\n")

        last_error = None
        for attempt in range(3):
            try:
                with open(env_path, "w") as f:
                    f.writelines(new_lines)
                return True, value
            except PermissionError as e:
                last_error = e
                if attempt < 2:
                    log.warning(f"Could not write .env.discord_cleanup (attempt {attempt + 1}/3) — retrying...")
                    time.sleep(0.5)

        return False, f"Permission denied writing .env.discord_cleanup after 3 attempts — {last_error}"


def update_retention(days: int) -> tuple[bool, str]:
    """Updates DEFAULT_RETENTION in env and in-memory config."""
    import config
    success, message = update_env_value("DEFAULT_RETENTION", str(days))
    if success:
        config.DEFAULT_RETENTION = days
    return success, message


def update_log_level(level: str) -> tuple[bool, str]:
    """Updates LOG_LEVEL in env and in-memory logging config."""
    import config
    valid = ["DEBUG", "INFO", "WARNING", "ERROR"]
    if level.upper() not in valid:
        return False, f"Invalid log level — must be one of: {', '.join(valid)}"
    success, message = update_env_value("LOG_LEVEL", level.upper())
    if success:
        config.LOG_LEVEL = level.upper()
        new_level = getattr(logging, level.upper())
        logger.setLevel(new_level)
        for h in logger.handlers:
            h.setLevel(new_level)
    return success, message


def update_warn_unconfigured(enabled: bool) -> tuple[bool, str]:
    """Updates WARN_UNCONFIGURED in env and in-memory config."""
    import config
    value = "true" if enabled else "false"
    success, message = update_env_value("WARN_UNCONFIGURED", value)
    if success:
        config.WARN_UNCONFIGURED = enabled
    return success, message


def update_report_frequency(frequency: str) -> tuple[bool, str]:
    """Updates REPORT_FREQUENCY in env and in-memory config."""
    import config
    valid = ["monthly", "weekly", "both"]
    if frequency.lower() not in valid:
        return False, f"Invalid frequency — must be one of: {', '.join(valid)}"
    success, message = update_env_value("REPORT_FREQUENCY", frequency.lower())
    if success:
        config.REPORT_FREQUENCY = frequency.lower()
    return success, message


def update_log_max_files(days: int) -> tuple[bool, str]:
    """Updates LOG_MAX_FILES in env and in-memory config."""
    import config
    if not 1 <= days <= 365:
        return False, "Log retention must be between 1 and 365 days"
    success, message = update_env_value("LOG_MAX_FILES", str(days))
    if success:
        config.LOG_MAX_FILES = days
    return success, message


def update_schedule(new_times: list) -> tuple[bool, str]:
    """Updates CLEAN_TIME in .env file and reschedules the cleanup task. Returns (success, message)."""
    import config
    from datetime import time as dtime

    env_path = os.path.join(CONFIG_DIR, ".env.discord_cleanup")

    for t in new_times:
        try:
            hour, minute = map(int, t.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            return False, f"`{t}` is not a valid time — use 24hr format e.g. `03:00`"

    with config_lock:
        try:
            with open(env_path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return False, f".env.discord_cleanup not found at `{env_path}`"
        except PermissionError:
            return False, "Permission denied reading .env.discord_cleanup"

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

        config.CLEAN_TIMES = new_times

    reschedule_error = None
    if _cleanup_task is not None:
        tz = _task_tz or ZoneInfo("UTC")
        times = [dtime(hour=int(t.split(":")[0]), minute=int(t.split(":")[1]), tzinfo=tz) for t in new_times]
        try:
            _cleanup_task.change_interval(time=times)
            log.info(f"Cleanup task rescheduled to: {new_value}")
        except Exception as e:
            reschedule_error = str(e)
            log.warning(f"Could not reschedule task in memory — {e}. Schedule saved to env, will apply on restart.")

    log.info(f"Schedule updated to: {new_value}")
    return True, new_value, reschedule_error
