"""
scheduler.py — Schedule management and task rescheduling.
Handles updating CLEAN_TIME in .env and rescheduling the discord.ext.tasks loop.
"""
from zoneinfo import ZoneInfo

from config import config_lock, CONFIG_DIR, log
from file_utils import atomic_write_text
from validation import validate_time_string

# Task reference set by cleanup_bot.py via utils.register_task
_cleanup_task = None
_task_tz = None


def register_task_ref(cleanup_task, task_tz):
    """Called from utils.register_task to keep scheduler in sync."""
    global _cleanup_task, _task_tz
    _cleanup_task = cleanup_task
    _task_tz = task_tz


def get_next_run_str(cleanup_task=None, task_tz=None) -> str:
    """Returns the next scheduled run time as a formatted string."""
    import os
    from datetime import datetime, timedelta
    import config as cfg

    task = cleanup_task or _cleanup_task
    tz   = task_tz or _task_tz or ZoneInfo("UTC")

    if task and task.is_running() and task.next_iteration:
        return task.next_iteration.astimezone(tz).strftime('%Y-%m-%d %I:%M %p')

    now = datetime.now(tz)
    for t in sorted(cfg.CLEAN_TIMES):
        hour, minute = map(int, t.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate.strftime('%Y-%m-%d %I:%M %p')

    hour, minute = map(int, sorted(cfg.CLEAN_TIMES)[0].split(":"))
    return (now + timedelta(days=1)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ).strftime('%Y-%m-%d %I:%M %p')


def update_schedule(new_times: list) -> tuple[bool, str, str | None]:
    """Updates CLEAN_TIME in .env file and reschedules the cleanup task.
    Returns (success, message, reschedule_error)."""
    import os
    import config
    from datetime import time as dtime

    env_path = os.path.join(CONFIG_DIR, ".env.discord_cleanup")

    # Validate all times before writing anything
    for t in new_times:
        try:
            validate_time_string(t, "schedule time")
        except ValueError:
            return False, f"`{t}` is not a valid time — use 24hr format e.g. `03:00`", None

    with config_lock:
        try:
            with open(env_path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return False, f".env.discord_cleanup not found at `{env_path}`", None
        except PermissionError:
            return False, "Permission denied reading .env.discord_cleanup", None

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
            atomic_write_text(env_path, "".join(new_lines))
        except PermissionError:
            return False, "Permission denied writing .env.discord_cleanup", None

        config.CLEAN_TIMES = new_times

    # Reschedule the running task without restart
    reschedule_error = None
    if _cleanup_task is not None:
        tz    = _task_tz or ZoneInfo("UTC")
        times = [
            dtime(hour=int(t.split(":")[0]), minute=int(t.split(":")[1]), tzinfo=tz)
            for t in new_times
        ]
        try:
            _cleanup_task.change_interval(time=times)
            log.info(f"Cleanup task rescheduled to: {new_value}")
        except Exception as e:
            reschedule_error = str(e)
            log.warning(
                f"Could not reschedule task in memory — {e}. "
                f"Schedule saved to env, will apply on restart."
            )

    log.info(f"Schedule updated to: {new_value}")
    return True, new_value, reschedule_error
