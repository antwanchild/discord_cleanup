"""
config_channels.py — channels.yml validation, preview, save, and restore helpers.
"""
import logging
import os
import yaml
from datetime import datetime

from config import config_lock, log
from config_backups import (
    _find_channel_backup,
    _channel_backup_dirs,
    _prune_old_channel_backups,
    list_channel_backups,
)
from file_utils import atomic_write_text
from validation import ChannelsConfigError, load_channels_config_file

logger = logging.getLogger("discord-cleanup")


def reload_channels() -> tuple[bool, str]:
    """Reloads channels.yml and updates raw_channels. Returns (success, message)."""
    import config
    with config_lock:
        try:
            config.raw_channels = load_channels_config_file(f"{config.CONFIG_DIR}/channels.yml")
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
    snapshot = {
        "id": channel["id"],
        "name": channel.get("name", str(channel["id"])),
        "type": channel.get("type", "channel"),
        "days": channel.get("days"),
        "exclude": channel.get("exclude", False),
        "deep_clean": channel.get("deep_clean", False),
        "report_exclude": channel.get("report_exclude", False),
        "report_individual": channel.get("report_individual", False),
        "report_group": channel.get("report_group"),
        "notification_group": channel.get("notification_group"),
    }
    return snapshot


def _channel_preview_label(channel: dict) -> str:
    prefix = "📁 " if channel.get("type") == "category" else "#"
    return f"{prefix}{channel.get('name', channel['id'])}"


def _channel_preview_overview(channels: list[dict]) -> dict:
    notification_groups = {ch.get("notification_group") for ch in channels if ch.get("notification_group")}
    report_groups = {ch.get("report_group") or ch.get("notification_group") for ch in channels if (ch.get("report_group") or ch.get("notification_group"))}
    return {
        "entries": len(channels),
        "categories": sum(1 for ch in channels if ch.get("type") == "category"),
        "excluded": sum(1 for ch in channels if ch.get("exclude", False)),
        "deep_clean": sum(1 for ch in channels if ch.get("deep_clean", False)),
        "with_notification_groups": len(notification_groups),
        "with_report_groups": len(report_groups),
        "report_excluded": sum(1 for ch in channels if ch.get("report_exclude", False)),
        "report_individual": sum(1 for ch in channels if ch.get("report_individual", False)),
    }


def _channel_preview_diff(current: list[dict], proposed: list[dict]) -> dict:
    current_by_id = {ch["id"]: ch for ch in current}
    proposed_by_id = {ch["id"]: ch for ch in proposed}
    keys = ["name", "type", "days", "exclude", "deep_clean", "report_exclude", "report_individual", "report_group", "notification_group"]
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
                changed_fields.append({"field": key, "before": before_value, "after": after_value})
                field_counts[key] = field_counts.get(key, 0) + 1
        if changed_fields:
            updated.append({
                "id": ch_id,
                "label": _channel_preview_label(after),
                "before": _channel_preview_snapshot(before),
                "after": _channel_preview_snapshot(after),
                "changes": changed_fields,
            })
    return {"added": added, "removed": removed, "updated": updated, "field_counts": field_counts}


def preview_channels_content(content: str) -> tuple[bool, str, dict | None]:
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
            "with_report_groups": proposed_overview["with_report_groups"] - current_overview["with_report_groups"],
            "report_excluded": proposed_overview["report_excluded"] - current_overview["report_excluded"],
            "report_individual": proposed_overview["report_individual"] - current_overview["report_individual"],
        },
        "counts": {
            "added": len(diff["added"]),
            "removed": len(diff["removed"]),
            "updated": len(diff["updated"]),
            "field_changes": sum(diff["field_counts"].values()),
        },
    }
    label = "entry" if len(channels) == 1 else "entries"
    preview = {"summary": summary, "changes": diff, "parsed_channels": channels, "message": f"channels.yml preview ready — {len(channels)} channel {label}"}
    return True, preview["message"], preview


def preview_channel_restore(filename: str) -> tuple[bool, str, dict | None]:
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
    preview["backup"] = {"type": backup["type"], "filename": backup["filename"], "path": backup["path"], "modified": backup["modified"], "size_bytes": backup["size_bytes"]}
    preview["message"] = f"Restore preview ready — {backup['filename']}"
    return True, preview["message"], preview


def restore_channels_backup(filename: str) -> tuple[bool, str, str | None]:
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
    log.info("channels.yml restored from backup %s%s", backup["path"], f" | backup={current_backup_path}" if current_backup_path else "")
    return True, message, current_backup_path


def save_channels_content(content: str) -> tuple[bool, str, str | None]:
    import config

    valid, message, channels = validate_channels_content(content)
    if not valid or channels is None:
        return False, message, None
    channels_path = os.path.join(config.CONFIG_DIR, "channels.yml")
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
                channel_backup_dir = _channel_backup_dirs()[0]
                os.makedirs(channel_backup_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_path = os.path.join(channel_backup_dir, f"channels-{timestamp}.yml.bak")
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
