"""
config_backups.py — Shared helpers for .env.discord_cleanup and backup file handling.
"""
import logging
import os
from datetime import datetime, timedelta
from io import StringIO

from dotenv import dotenv_values, load_dotenv

from config import config_lock, log
from file_utils import atomic_write_text
from validation import (
    parse_date_list,
    parse_time_list,
    parse_weekday_list,
    validate_bool,
    validate_int,
    validate_report_frequency,
    validate_time_string,
)

logger = logging.getLogger("discord-cleanup")
_STARTUP_ONLY_ENV_KEYS = {
    "WEB_HOST",
    "WEB_PORT",
    "WEB_AUTH_HEADER_NAME",
    "WEB_AUTH_HEADER_VALUE",
    "WEB_SECRET_KEY",
    "ADMIN_RATE_LIMIT_WINDOW_SECONDS",
    "ADMIN_RATE_LIMIT_MAX_REQUESTS",
    "RUN_RATE_LIMIT_MAX_REQUESTS",
}
_SENSITIVE_ENV_KEYS = {
    "DISCORD_TOKEN",
    "GITHUB_TOKEN",
    "WEB_SECRET_KEY",
    "WEB_AUTH_HEADER_VALUE",
}


def _channel_backup_dirs() -> list[str]:
    import config

    backup_root = os.path.join(config.CONFIG_DIR, "backups")
    return [os.path.join(backup_root, "channels"), backup_root]


def _env_backup_dirs() -> list[str]:
    import config

    backup_root = os.path.join(config.CONFIG_DIR, "backups")
    return [os.path.join(backup_root, "env"), backup_root]


def _prune_old_channel_backups() -> None:
    import config

    retention_days = getattr(config, "CHANNELS_BACKUP_RETENTION_DAYS", 10)
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for backup_dir in _channel_backup_dirs():
        try:
            entries = os.listdir(backup_dir)
        except FileNotFoundError:
            continue
        except PermissionError:
            log.warning("Permission denied listing channels.yml backups for cleanup")
            continue
        for filename in entries:
            if not (filename.startswith("channels-") and filename.endswith(".yml.bak")):
                continue
            path = os.path.join(backup_dir, filename)
            try:
                modified = datetime.fromtimestamp(os.path.getmtime(path))
                if modified < cutoff:
                    os.remove(path)
                    removed += 1
            except FileNotFoundError:
                continue
            except PermissionError:
                log.warning("Permission denied deleting old channels.yml backup: %s", filename)
    if removed:
        log.info("Pruned %s old channels.yml backup(s)", removed)


def _prune_old_env_backups() -> None:
    import config

    retention_days = getattr(config, "CHANNELS_BACKUP_RETENTION_DAYS", 10)
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for backup_dir in _env_backup_dirs():
        try:
            entries = os.listdir(backup_dir)
        except FileNotFoundError:
            continue
        except PermissionError:
            log.warning("Permission denied listing .env backups for cleanup")
            continue
        for filename in entries:
            if not (filename.startswith("env-") and filename.endswith(".env.bak")):
                continue
            path = os.path.join(backup_dir, filename)
            try:
                modified = datetime.fromtimestamp(os.path.getmtime(path))
                if modified < cutoff:
                    os.remove(path)
                    removed += 1
            except FileNotFoundError:
                continue
            except PermissionError:
                log.warning("Permission denied deleting old .env backup: %s", filename)
    if removed:
        log.info("Pruned %s old .env backup(s)", removed)


def list_channel_backups() -> list[dict]:
    backups = []
    seen_paths = set()
    for backup_dir in _channel_backup_dirs():
        try:
            entries = os.listdir(backup_dir)
        except FileNotFoundError:
            continue
        except PermissionError:
            log.warning("Permission denied listing channels.yml backups")
            continue
        for filename in entries:
            if not (filename.startswith("channels-") and filename.endswith(".yml.bak")):
                continue
            path = os.path.join(backup_dir, filename)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            backups.append({
                "type": "channels",
                "filename": filename,
                "path": path,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size_bytes": stat.st_size,
            })
    backups.sort(key=lambda item: item["modified"], reverse=True)
    return backups


def list_env_backups() -> list[dict]:
    backups = []
    seen_paths = set()
    for backup_dir in _env_backup_dirs():
        try:
            entries = os.listdir(backup_dir)
        except FileNotFoundError:
            continue
        except PermissionError:
            log.warning("Permission denied listing .env backups")
            continue
        for filename in entries:
            if not (filename.startswith("env-") and filename.endswith(".env.bak")):
                continue
            path = os.path.join(backup_dir, filename)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            backups.append({
                "type": "env",
                "filename": filename,
                "path": path,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size_bytes": stat.st_size,
            })
    backups.sort(key=lambda item: item["modified"], reverse=True)
    return backups


def _find_channel_backup(filename: str) -> dict | None:
    filename = filename.strip()
    if not filename:
        return None
    for backup in list_channel_backups():
        if backup["filename"] == filename:
            return backup
    return None


def _find_env_backup(filename: str) -> dict | None:
    filename = filename.strip()
    if not filename:
        return None
    for backup in list_env_backups():
        if backup["filename"] == filename:
            return backup
    return None


def _mask_env_value(key: str, value: str | None) -> str:
    if value in (None, ""):
        return "empty"
    if key in _SENSITIVE_ENV_KEYS:
        if len(value) <= 6:
            return "*" * max(len(value), 4)
        return f"{value[:2]}***{value[-2:]}"
    return str(value)


def _load_env_snapshot(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        snapshot = dotenv_values(stream=StringIO(f.read()))
    return {key: value for key, value in snapshot.items() if value is not None}


def _compare_env_snapshots(current: dict[str, str], proposed: dict[str, str]) -> dict:
    current_keys = set(current.keys())
    proposed_keys = set(proposed.keys())
    added_keys = sorted(proposed_keys - current_keys)
    removed_keys = sorted(current_keys - proposed_keys)
    shared_keys = sorted(current_keys & proposed_keys)
    added = [{"key": key, "value": _mask_env_value(key, proposed[key])} for key in added_keys]
    removed = [{"key": key, "value": _mask_env_value(key, current[key])} for key in removed_keys]
    updated = []
    sensitive_updates = 0
    for key in shared_keys:
        before = current.get(key, "")
        after = proposed.get(key, "")
        if before != after:
            if key in _SENSITIVE_ENV_KEYS:
                sensitive_updates += 1
            updated.append({
                "key": key,
                "before": _mask_env_value(key, before),
                "after": _mask_env_value(key, after),
            })
    return {
        "added": added,
        "removed": removed,
        "updated": updated,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "updated": len(updated),
            "field_changes": len(updated),
            "sensitive_updates": sensitive_updates,
        },
        "summary": {
            "current": {"keys": len(current_keys), "sensitive": sum(1 for key in current_keys if key in _SENSITIVE_ENV_KEYS)},
            "proposed": {"keys": len(proposed_keys), "sensitive": sum(1 for key in proposed_keys if key in _SENSITIVE_ENV_KEYS)},
            "delta": {
                "keys": len(proposed_keys) - len(current_keys),
                "sensitive": sum(1 for key in proposed_keys if key in _SENSITIVE_ENV_KEYS) - sum(1 for key in current_keys if key in _SENSITIVE_ENV_KEYS),
            },
        },
    }


def _write_env_content(content: str) -> str | None:
    import config

    env_path = os.path.join(config.CONFIG_DIR, ".env.discord_cleanup")
    previous_content = ""
    try:
        with open(env_path, "r") as f:
            previous_content = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(env_path)

    backup_path = None
    if previous_content and previous_content != content:
        try:
            os.makedirs(_env_backup_dirs()[0], exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = os.path.join(_env_backup_dirs()[0], f"env-{timestamp}.env.bak")
            atomic_write_text(backup_path, previous_content)
        except PermissionError:
            log.error("Permission denied creating .env backup")
            raise

    last_error = None
    for attempt in range(3):
        try:
            atomic_write_text(env_path, content)
            break
        except PermissionError as e:
            last_error = e
            if attempt < 2:
                log.warning("Could not write .env.discord_cleanup (attempt %s/3) — retrying...", attempt + 1)
                continue
            raise
    else:
        raise PermissionError(f"Permission denied writing .env.discord_cleanup after 3 attempts — {last_error}")

    _prune_old_env_backups()
    load_dotenv(env_path, override=True)
    _reload_runtime_env_values()
    return backup_path


def _reload_runtime_env_values() -> None:
    import config

    try:
        clean_times = parse_time_list(os.getenv("CLEAN_TIME", "03:00"), "CLEAN_TIME")
        log_max_files = validate_int(os.getenv("LOG_MAX_FILES", 7), "LOG_MAX_FILES", 1, 365)
        channels_backup_retention_days = validate_int(os.getenv("CHANNELS_BACKUP_RETENTION_DAYS", 10), "CHANNELS_BACKUP_RETENTION_DAYS", 1, 365)
        stats_backup_retention_days = validate_int(os.getenv("STATS_BACKUP_RETENTION_DAYS", 10), "STATS_BACKUP_RETENTION_DAYS", 1, 365)
        default_retention = validate_int(os.getenv("DEFAULT_RETENTION", 7), "DEFAULT_RETENTION", 1, 365)
        status_report_time = validate_time_string(os.getenv("STATUS_REPORT_TIME", "09:00"), "STATUS_REPORT_TIME")
        report_frequency = validate_report_frequency(os.getenv("REPORT_FREQUENCY", "monthly"))
        report_group_monthly = validate_bool(os.getenv("REPORT_GROUP_MONTHLY", "true"), "REPORT_GROUP_MONTHLY")
        report_group_weekly = validate_bool(os.getenv("REPORT_GROUP_WEEKLY", "true"), "REPORT_GROUP_WEEKLY")
        schedule_skip_dates = parse_date_list(os.getenv("SCHEDULE_SKIP_DATES", ""), "SCHEDULE_SKIP_DATES")
        schedule_skip_weekdays = parse_weekday_list(os.getenv("SCHEDULE_SKIP_WEEKDAYS", ""), "SCHEDULE_SKIP_WEEKDAYS")
    except ValueError as e:
        raise ValueError(f"Invalid runtime env value — {e}") from e

    config.CLEAN_TIMES = clean_times
    config.LOG_MAX_FILES = log_max_files
    config.CHANNELS_BACKUP_RETENTION_DAYS = channels_backup_retention_days
    config.STATS_BACKUP_RETENTION_DAYS = stats_backup_retention_days
    config.DEFAULT_RETENTION = default_retention
    config.STATUS_REPORT_TIME = status_report_time
    config.REPORT_FREQUENCY = report_frequency
    config.REPORT_GROUP_MONTHLY = report_group_monthly
    config.REPORT_GROUP_WEEKLY = report_group_weekly
    config.SCHEDULE_SKIP_DATES = schedule_skip_dates
    config.SCHEDULE_SKIP_WEEKDAYS = schedule_skip_weekdays
    config.WARN_UNCONFIGURED = os.getenv("WARN_UNCONFIGURED", "false").lower() == "true"
    config.GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    config.CATCHUP_MISSED_RUNS = os.getenv("CATCHUP_MISSED_RUNS", "true").lower() == "true"

    if hasattr(config, "LOG_LEVEL"):
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        config.LOG_LEVEL = log_level
        new_level = getattr(logging, log_level, logging.INFO)
        logger.setLevel(new_level)
        for h in logger.handlers:
            h.setLevel(new_level)

    if hasattr(config, "TOKEN"):
        config.TOKEN = os.getenv("DISCORD_TOKEN")
    if hasattr(config, "LOG_CHANNEL_ID"):
        try:
            log_channel_id = os.getenv("LOG_CHANNEL_ID")
            if log_channel_id is None:
                log_channel_id = getattr(config, "LOG_CHANNEL_ID", 0) or 0
            config.LOG_CHANNEL_ID = int(log_channel_id)
        except ValueError:
            pass
    if hasattr(config, "REPORT_CHANNEL_ID"):
        try:
            report_channel_id = os.getenv("REPORT_CHANNEL_ID")
            if report_channel_id is None:
                report_channel_id = getattr(config, "REPORT_CHANNEL_ID", 0) or 0
            config.REPORT_CHANNEL_ID = int(report_channel_id)
        except ValueError:
            pass


def update_env_value(key: str, value: str) -> tuple[bool, str]:
    if "\n" in value or "\r" in value:
        return False, f"Invalid value for {key} — newline characters are not allowed"
    import config

    env_path = os.path.join(config.CONFIG_DIR, ".env.discord_cleanup")
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

        try:
            _write_env_content("".join(new_lines))
        except FileNotFoundError:
            return False, f".env.discord_cleanup not found at `{env_path}`"
        except PermissionError as e:
            return False, f"Permission denied writing .env.discord_cleanup — {e}"
        except ValueError as e:
            return False, str(e)

        return True, value


def preview_env_restore(filename: str) -> tuple[bool, str, dict | None]:
    """Previews restoring .env.discord_cleanup from a specific backup file."""
    backup = _find_env_backup(filename)
    if not backup:
        return False, f"Backup not found — {filename}", None

    try:
        with open(backup["path"], "r") as f:
            proposed_content = f.read()
    except FileNotFoundError:
        return False, f"Backup not found — {filename}", None
    except PermissionError:
        return False, f"Permission denied reading backup — {filename}", None

    import config

    current_path = os.path.join(config.CONFIG_DIR, ".env.discord_cleanup")
    current_values = _load_env_snapshot(current_path)
    proposed_values = _load_env_snapshot(backup["path"])
    diff = _compare_env_snapshots(current_values, proposed_values)
    diff["backup"] = {
        "type": backup["type"],
        "filename": backup["filename"],
        "path": backup["path"],
        "modified": backup["modified"],
        "size_bytes": backup["size_bytes"],
    }
    diff["restores"] = {
        "startup_only_changed": sorted(
            key for key in set(current_values) | set(proposed_values)
            if key in _STARTUP_ONLY_ENV_KEYS and current_values.get(key, "") != proposed_values.get(key, "")
        ),
    }
    diff["restores"]["restart_required"] = bool(diff["restores"]["startup_only_changed"])
    diff["message"] = f"Restore preview ready — {backup['filename']}"
    return True, diff["message"], diff


def restore_env_backup(filename: str) -> tuple[bool, str, str | None]:
    """Restores .env.discord_cleanup from a backup file and reloads live config."""
    backup = _find_env_backup(filename)
    if not backup:
        return False, f"Backup not found — {filename}", None

    try:
        with open(backup["path"], "r") as f:
            content = f.read()
    except FileNotFoundError:
        return False, f"Backup not found — {filename}", None
    except PermissionError:
        return False, f"Permission denied reading backup — {filename}", None

    import config

    current_path = os.path.join(config.CONFIG_DIR, ".env.discord_cleanup")
    current_values_before = _load_env_snapshot(current_path)
    proposed_values = _load_env_snapshot(backup["path"])

    with config_lock:
        try:
            current_backup_path = _write_env_content(content)
        except FileNotFoundError:
            return False, f".env.discord_cleanup not found at `{current_path}`", None
        except PermissionError as e:
            return False, f"Permission denied writing .env.discord_cleanup — {e}", None
        except ValueError as e:
            return False, str(e), None

    restored_values = _load_env_snapshot(current_path)
    label = "setting" if len(restored_values) == 1 else "settings"
    message = f"Restored .env.discord_cleanup from {backup['filename']} — {len(restored_values)} {label}"
    startup_only_changed = sorted(
        key for key in _STARTUP_ONLY_ENV_KEYS
        if current_values_before.get(key, "") != proposed_values.get(key, "")
    )
    if startup_only_changed:
        message = f"{message} | Restart required for startup-only settings: {', '.join(startup_only_changed)}"
    if current_backup_path:
        message = f"{message} | Backup: {current_backup_path}"
    log.info(
        ".env.discord_cleanup restored from backup %s%s",
        backup["path"],
        f" | backup={current_backup_path}" if current_backup_path else "",
    )
    return True, message, current_backup_path
