"""
config_utils.py — Functions for updating .env.discord_cleanup and reloading config files.
All writes are protected by config_lock from config.py.
"""
import os
import logging
import yaml
from io import StringIO
from datetime import datetime, timedelta
from dotenv import dotenv_values, load_dotenv

from config import config_lock, CONFIG_DIR, log
from file_utils import atomic_write_text
from validation import (
    ChannelsConfigError,
    load_channels_config_file,
    parse_time_list,
    validate_bool,
    validate_int,
    validate_report_frequency,
    validate_time_string,
)

logger = logging.getLogger("discord-cleanup")
BACKUP_ROOT = os.path.join(CONFIG_DIR, "backups")
CHANNEL_BACKUP_DIR = os.path.join(BACKUP_ROOT, "channels")
ENV_BACKUP_DIR = os.path.join(BACKUP_ROOT, "env")
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
    """Returns channel backup directories, including the legacy flat folder."""
    return [CHANNEL_BACKUP_DIR, BACKUP_ROOT]


def _env_backup_dirs() -> list[str]:
    """Returns env backup directories, including the legacy flat folder."""
    return [ENV_BACKUP_DIR, BACKUP_ROOT]


def _prune_old_channel_backups() -> None:
    """Deletes channels.yml backups older than the configured retention window."""
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
    """Deletes .env backups older than the configured retention window."""
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
    """Lists available channels.yml backup files newest-first."""
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
    """Lists available .env backup files newest-first."""
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
    """Returns the newest backup entry matching the given filename."""
    filename = filename.strip()
    if not filename:
        return None

    for backup in list_channel_backups():
        if backup["filename"] == filename:
            return backup
    return None


def _find_env_backup(filename: str) -> dict | None:
    """Returns the newest env backup entry matching the given filename."""
    filename = filename.strip()
    if not filename:
        return None

    for backup in list_env_backups():
        if backup["filename"] == filename:
            return backup
    return None


def _mask_env_value(key: str, value: str | None) -> str:
    """Masks sensitive env values for display in the preview UI."""
    if value in (None, ""):
        return "empty"
    if key in _SENSITIVE_ENV_KEYS:
        if len(value) <= 6:
            return "*" * max(len(value), 4)
        return f"{value[:2]}***{value[-2:]}"
    return str(value)


def _load_env_snapshot(path: str) -> dict[str, str]:
    """Loads a .env file into a plain string dictionary."""
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        snapshot = dotenv_values(stream=StringIO(f.read()))
    return {key: value for key, value in snapshot.items() if value is not None}


def _compare_env_snapshots(current: dict[str, str], proposed: dict[str, str]) -> dict:
    """Computes a diff between two .env snapshots."""
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
    """Writes .env.discord_cleanup with backup, reload, and pruning."""
    env_path = os.path.join(CONFIG_DIR, ".env.discord_cleanup")
    previous_content = ""
    try:
        with open(env_path, "r") as f:
            previous_content = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(env_path)

    backup_path = None
    if previous_content and previous_content != content:
        try:
            os.makedirs(ENV_BACKUP_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = os.path.join(ENV_BACKUP_DIR, f"env-{timestamp}.env.bak")
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
    """Refreshes the in-memory config values that are safe to update live."""
    import config

    try:
        clean_times = parse_time_list(os.getenv("CLEAN_TIME", "03:00"), "CLEAN_TIME")
        log_max_files = validate_int(os.getenv("LOG_MAX_FILES", 7), "LOG_MAX_FILES", 1, 365)
        channels_backup_retention_days = validate_int(
            os.getenv("CHANNELS_BACKUP_RETENTION_DAYS", 10),
            "CHANNELS_BACKUP_RETENTION_DAYS",
            1,
            365,
        )
        stats_backup_retention_days = validate_int(
            os.getenv("STATS_BACKUP_RETENTION_DAYS", 10),
            "STATS_BACKUP_RETENTION_DAYS",
            1,
            365,
        )
        default_retention = validate_int(os.getenv("DEFAULT_RETENTION", 7), "DEFAULT_RETENTION", 1, 365)
        status_report_time = validate_time_string(os.getenv("STATUS_REPORT_TIME", "09:00"), "STATUS_REPORT_TIME")
        report_frequency = validate_report_frequency(os.getenv("REPORT_FREQUENCY", "monthly"))
        report_group_monthly = validate_bool(os.getenv("REPORT_GROUP_MONTHLY", "true"), "REPORT_GROUP_MONTHLY")
        report_group_weekly = validate_bool(os.getenv("REPORT_GROUP_WEEKLY", "true"), "REPORT_GROUP_WEEKLY")
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


def reload_channels() -> tuple[bool, str]:
    """Reloads channels.yml and updates raw_channels. Returns (success, message)."""
    import config
    with config_lock:
        try:
            config.raw_channels = load_channels_config_file(f"{CONFIG_DIR}/channels.yml")
            log.info("channels.yml reloaded successfully")
            return True, f"Loaded {len(config.raw_channels)} channel entries"
        except FileNotFoundError:
            log.error("channels.yml not found during reload")
            return False, "channels.yml not found"
        except PermissionError:
            log.error("Permission denied reading channels.yml during reload")
            return False, "Permission denied reading channels.yml"
        except ChannelsConfigError as e:
            log.error(f"channels.yml validation failed during reload — {e}")
            return False, f"channels.yml validation failed — {e}"
        except yaml.YAMLError as e:
            log.error(f"channels.yml is malformed during reload — {e}")
            return False, f"channels.yml is malformed — {e}"


def validate_channels_content(content: str) -> tuple[bool, str, list[dict] | None]:
    """Validates raw channels.yml content without saving it."""
    from validation import load_channels_config

    try:
        channels = load_channels_config(content)
        label = "entry" if len(channels) == 1 else "entries"
        return True, f"channels.yml is valid — {len(channels)} channel {label}", channels
    except ChannelsConfigError as e:
        return False, f"Invalid channels.yml — {e}", None
    except yaml.YAMLError as e:
        return False, f"Invalid YAML — {e}", None


def _channel_preview_snapshot(channel: dict) -> dict:
    """Returns a compact, JSON-friendly snapshot of a channels.yml entry."""
    snapshot = {
        "id": channel["id"],
        "name": channel.get("name", str(channel["id"])),
        "type": channel.get("type", "channel"),
        "days": channel.get("days"),
        "exclude": channel.get("exclude", False),
        "deep_clean": channel.get("deep_clean", False),
        "notification_group": channel.get("notification_group"),
    }
    return snapshot


def _channel_preview_label(channel: dict) -> str:
    """Returns a human-readable label for a channels.yml entry."""
    prefix = "📁 " if channel.get("type") == "category" else "#"
    return f"{prefix}{channel.get('name', channel['id'])}"


def _channel_preview_overview(channels: list[dict]) -> dict:
    """Summarizes a channels.yml list for the config preview."""
    notification_groups = {ch.get("notification_group") for ch in channels if ch.get("notification_group")}
    return {
        "entries": len(channels),
        "categories": sum(1 for ch in channels if ch.get("type") == "category"),
        "excluded": sum(1 for ch in channels if ch.get("exclude", False)),
        "deep_clean": sum(1 for ch in channels if ch.get("deep_clean", False)),
        "with_notification_groups": len(notification_groups),
    }


def _channel_preview_diff(current: list[dict], proposed: list[dict]) -> dict:
    """Computes added, removed, and updated channels between two configs."""
    current_by_id = {ch["id"]: ch for ch in current}
    proposed_by_id = {ch["id"]: ch for ch in proposed}
    keys = ["name", "type", "days", "exclude", "deep_clean", "notification_group"]

    added = [_channel_preview_snapshot(proposed_by_id[ch_id]) for ch_id in proposed_by_id.keys() if ch_id not in current_by_id]
    removed = [_channel_preview_snapshot(current_by_id[ch_id]) for ch_id in current_by_id.keys() if ch_id not in proposed_by_id]
    updated = []
    field_counts: dict[str, int] = {}

    for ch_id in proposed_by_id.keys():
        before = current_by_id.get(ch_id)
        after = proposed_by_id[ch_id]
        if before is None:
            continue

        changed_fields = []
        for key in keys:
            before_value = before.get(key, "channel" if key == "type" else None)
            after_value = after.get(key, "channel" if key == "type" else None)
            if before_value != after_value:
                changed_fields.append({
                    "field": key,
                    "before": before_value,
                    "after": after_value,
                })
                field_counts[key] = field_counts.get(key, 0) + 1

        if changed_fields:
            updated.append({
                "id": ch_id,
                "label": _channel_preview_label(after),
                "before": _channel_preview_snapshot(before),
                "after": _channel_preview_snapshot(after),
                "changes": changed_fields,
            })

    return {
        "added": added,
        "removed": removed,
        "updated": updated,
        "field_counts": field_counts,
    }


def preview_channels_content(content: str) -> tuple[bool, str, dict | None]:
    """Validates proposed channels.yml content and builds a config diff preview."""
    import config

    valid, message, channels = validate_channels_content(content)
    if not valid or channels is None:
        return False, message, None

    current_channels = getattr(config, "raw_channels", []) or []
    diff = _channel_preview_diff(current_channels, channels)
    current_overview = _channel_preview_overview(current_channels)
    proposed_overview = _channel_preview_overview(channels)
    summary = {
        "current": current_overview,
        "proposed": proposed_overview,
        "delta": {
            "entries": proposed_overview["entries"] - current_overview["entries"],
            "categories": proposed_overview["categories"] - current_overview["categories"],
            "excluded": proposed_overview["excluded"] - current_overview["excluded"],
            "deep_clean": proposed_overview["deep_clean"] - current_overview["deep_clean"],
            "with_notification_groups": proposed_overview["with_notification_groups"] - current_overview["with_notification_groups"],
        },
        "counts": {
            "added": len(diff["added"]),
            "removed": len(diff["removed"]),
            "updated": len(diff["updated"]),
            "field_changes": sum(diff["field_counts"].values()),
        },
    }
    label = "entry" if len(channels) == 1 else "entries"
    preview = {
        "summary": summary,
        "changes": diff,
        "parsed_channels": channels,
        "message": f"channels.yml preview ready — {len(channels)} channel {label}",
    }
    return True, preview["message"], preview


def preview_channel_restore(filename: str) -> tuple[bool, str, dict | None]:
    """Previews restoring channels.yml from a specific backup file."""
    backup = _find_channel_backup(filename)
    if not backup:
        return False, f"Backup not found — {filename}", None

    try:
        with open(backup["path"], "r") as f:
            content = f.read()
    except FileNotFoundError:
        return False, f"Backup not found — {filename}", None
    except PermissionError:
        return False, f"Permission denied reading backup — {filename}", None

    valid, message, preview = preview_channels_content(content)
    if not valid or preview is None:
        return False, message, None

    preview["backup"] = {
        "type": backup["type"],
        "filename": backup["filename"],
        "path": backup["path"],
        "modified": backup["modified"],
        "size_bytes": backup["size_bytes"],
    }
    preview["message"] = f"Restore preview ready — {backup['filename']}"
    return True, preview["message"], preview


def restore_channels_backup(filename: str) -> tuple[bool, str, str | None]:
    """Restores channels.yml from a backup file and reloads the live config."""
    import config

    backup = _find_channel_backup(filename)
    if not backup:
        return False, f"Backup not found — {filename}", None

    try:
        with open(backup["path"], "r") as f:
            content = f.read()
    except FileNotFoundError:
        return False, f"Backup not found — {filename}", None
    except PermissionError:
        return False, f"Permission denied reading backup — {filename}", None

    success, message, current_backup_path = save_channels_content(content)
    if not success:
        return False, message, None

    label = "entry" if len(config.raw_channels) == 1 else "entries"
    message = f"Restored channels.yml from {backup['filename']} — {len(config.raw_channels)} channel {label}"
    if current_backup_path:
        message = f"{message} | Backup: {current_backup_path}"
    log.info(
        "channels.yml restored from backup %s%s",
        backup["path"],
        f" | backup={current_backup_path}" if current_backup_path else "",
    )
    return True, message, current_backup_path


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

    current_path = os.path.join(CONFIG_DIR, ".env.discord_cleanup")
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
    if diff["restores"]["startup_only_changed"]:
        diff["restores"]["restart_required"] = True
    else:
        diff["restores"]["restart_required"] = False
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

    current_path = os.path.join(CONFIG_DIR, ".env.discord_cleanup")
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


def save_channels_content(content: str) -> tuple[bool, str, str | None]:
    """Validates, backs up, and saves channels.yml content."""
    import config

    valid, message, channels = validate_channels_content(content)
    if not valid or channels is None:
        return False, message, None

    channels_path = os.path.join(CONFIG_DIR, "channels.yml")
    backup_path = None

    with config_lock:
        previous_content = ""
        try:
            if os.path.exists(channels_path):
                with open(channels_path, "r") as f:
                    previous_content = f.read()
        except PermissionError:
            log.error("Permission denied reading channels.yml before save")
            return False, "Permission denied reading channels.yml", None

        if previous_content and previous_content != content:
            try:
                os.makedirs(CHANNEL_BACKUP_DIR, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_path = os.path.join(CHANNEL_BACKUP_DIR, f"channels-{timestamp}.yml.bak")
                atomic_write_text(backup_path, previous_content)
            except PermissionError:
                log.error("Permission denied creating channels.yml backup")
                return False, "Permission denied creating channels.yml backup", None

        try:
            atomic_write_text(channels_path, content)
        except PermissionError:
            log.error("Permission denied writing channels.yml")
            return False, "Permission denied writing channels.yml", None

        _prune_old_channel_backups()

        config.raw_channels = channels

    label = "entry" if len(channels) == 1 else "entries"
    message = f"Saved and reloaded channels.yml — {len(channels)} channel {label}"
    if backup_path:
        message = f"{message} | Backup: {backup_path}"
    log.info("channels.yml saved successfully%s", f" | backup={backup_path}" if backup_path else "")
    return True, message, backup_path


def update_env_value(key: str, value: str) -> tuple[bool, str]:
    """Updates a single key in .env.discord_cleanup. Returns (success, message).
    Rejects values containing newline characters to prevent env injection."""
    # Guard: newlines in a value would silently inject additional env entries
    if "\n" in value or "\r" in value:
        return False, f"Invalid value for {key} — newline characters are not allowed"
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

        try:
            _write_env_content("".join(new_lines))
        except FileNotFoundError:
            return False, f".env.discord_cleanup not found at `{env_path}`"
        except PermissionError as e:
            return False, f"Permission denied writing .env.discord_cleanup — {e}"
        except ValueError as e:
            return False, str(e)

        return True, value


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


def update_report_grouping(scope: str, enabled: bool) -> tuple[bool, str]:
    """Updates report grouping toggles for monthly or weekly reports."""
    import config

    scope = scope.lower().strip()
    if scope not in {"monthly", "weekly"}:
        return False, "Invalid grouping scope — must be monthly or weekly"

    key = "REPORT_GROUP_MONTHLY" if scope == "monthly" else "REPORT_GROUP_WEEKLY"
    value = "true" if enabled else "false"
    success, message = update_env_value(key, value)
    if success:
        if scope == "monthly":
            config.REPORT_GROUP_MONTHLY = enabled
        else:
            config.REPORT_GROUP_WEEKLY = enabled
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
