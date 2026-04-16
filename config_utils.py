"""
config_utils.py — Functions for updating .env.discord_cleanup and reloading config files.
All writes are protected by config_lock from config.py.
"""
import os
import logging
import yaml
from datetime import datetime, timedelta

from config import config_lock, CONFIG_DIR, log
from file_utils import atomic_write_text
from validation import ChannelsConfigError, load_channels_config_file

logger = logging.getLogger("discord-cleanup")
BACKUP_ROOT = os.path.join(CONFIG_DIR, "backups")
CHANNEL_BACKUP_DIR = os.path.join(BACKUP_ROOT, "channels")


def _channel_backup_dirs() -> list[str]:
    """Returns channel backup directories, including the legacy flat folder."""
    return [CHANNEL_BACKUP_DIR, BACKUP_ROOT]


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
    import time
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

        last_error = None
        for attempt in range(3):
            try:
                atomic_write_text(env_path, "".join(new_lines))
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
