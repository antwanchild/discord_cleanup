"""
stats.py — Load, save, update, and reset cleanup statistics.
Stats are persisted to /config/data/stats.json across three rolling buckets:
all_time, rolling_30, and monthly.
"""
import json
import os
import logging
from datetime import datetime, timedelta

import config as cfg
from config import DATA_DIR, STATS_FILE, log
from file_utils import atomic_write_json, atomic_write_text

logger = logging.getLogger("discord-cleanup")
STATS_BACKUP_DIRNAME = "backups"
STATS_BACKUP_SUBDIR = "stats"
LAST_RUN_BACKUP_SUBDIR = "last-run"


class StatsLoadError(RuntimeError):
    """Raised when persisted stats exist but cannot be read safely."""


def _backup_root() -> str:
    """Returns the root directory used for stats-related backups."""
    return os.path.join(DATA_DIR, STATS_BACKUP_DIRNAME)


def _stats_backup_dir() -> str:
    """Returns the directory used for stats.json backups."""
    return os.path.join(_backup_root(), STATS_BACKUP_SUBDIR)


def _last_run_backup_dir() -> str:
    """Returns the directory used for last_run.json backups."""
    return os.path.join(_backup_root(), LAST_RUN_BACKUP_SUBDIR)


def _stats_backup_dirs(backup_type: str) -> list[str]:
    """Returns backup directories for the given type, including legacy flat storage."""
    if backup_type == "stats":
        return [_stats_backup_dir(), _backup_root()]
    if backup_type == "last_run":
        return [_last_run_backup_dir(), _backup_root()]
    return [_stats_backup_dir(), _last_run_backup_dir(), _backup_root()]


def _empty_stats():
    now = datetime.now().strftime("%Y-%m-%d")
    return {
        "all_time": {"runs": 0, "deleted": 0, "catchup_runs": 0, "channels": {}},
        "rolling_30": {"runs": 0, "deleted": 0, "catchup_runs": 0, "channels": {}, "reset": now},
        "monthly": {"runs": 0, "deleted": 0, "catchup_runs": 0, "channels": {}, "reset": now},
        "last_month": None,
        "channel_history": {},
    }


def _coerce_non_negative_int(value, default: int = 0) -> int:
    """Best-effort int coercion for persisted numeric counters."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _coerce_reset_date(value, default: str) -> str:
    """Validates persisted YYYY-MM-DD date strings."""
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return default


def _normalize_channel_stats(channels) -> dict:
    """Normalizes persisted per-channel stats to the current schema."""
    if not isinstance(channels, dict):
        return {}

    normalized = {}
    for ch_id, ch_data in channels.items():
        channel_id = str(ch_id)
        if isinstance(ch_data, dict):
            normalized[channel_id] = {
                "name": str(ch_data.get("name") or channel_id),
                "count": _coerce_non_negative_int(ch_data.get("count", 0)),
                "category": str(ch_data.get("category") or "Standalone"),
            }
        else:
            normalized[channel_id] = {
                "name": channel_id,
                "count": _coerce_non_negative_int(ch_data),
                "category": "Standalone",
            }
    return normalized


def _normalize_stats_bucket(bucket, default_reset: str | None = None) -> dict:
    """Normalizes one stats bucket while preserving expected keys."""
    bucket = bucket if isinstance(bucket, dict) else {}
    normalized = {
        "runs": _coerce_non_negative_int(bucket.get("runs", 0)),
        "deleted": _coerce_non_negative_int(bucket.get("deleted", 0)),
        "catchup_runs": _coerce_non_negative_int(bucket.get("catchup_runs", 0)),
        "channels": _normalize_channel_stats(bucket.get("channels", {})),
    }
    if default_reset is not None:
        normalized["reset"] = _coerce_reset_date(bucket.get("reset"), default_reset)
    return normalized


def _normalize_last_month(value, default_reset: str) -> dict | None:
    """Normalizes the optional last_month summary."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return {
        "runs": _coerce_non_negative_int(value.get("runs", 0)),
        "deleted": _coerce_non_negative_int(value.get("deleted", 0)),
        "reset": _coerce_reset_date(value.get("reset"), default_reset),
    }


def _coerce_timestamp(value: str | None, default: str) -> str:
    """Normalizes persisted timestamps to the current log-friendly format."""
    if isinstance(value, str) and value.strip():
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
    return default


def _normalize_channel_history(history) -> dict:
    """Normalizes persisted channel history entries to the current schema."""
    if not isinstance(history, dict):
        return {}

    normalized = {}
    for ch_id, entries in history.items():
        if not isinstance(entries, list):
            continue
        channel_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            channel_entries.append({
                "timestamp": _coerce_timestamp(entry.get("timestamp"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "triggered_by": str(entry.get("triggered_by") or "unknown"),
                "count": _coerce_non_negative_int(entry.get("count", 0)),
                "category": str(entry.get("category") or "Standalone"),
                "status": str(entry.get("status") or ("error" if entry.get("error") else "deleted" if _coerce_non_negative_int(entry.get("count", 0)) > 0 else "clean")),
                "rate_limits": _coerce_non_negative_int(entry.get("rate_limits", 0)),
                "dry_run": bool(entry.get("dry_run", False)),
                "oldest": entry.get("oldest"),
                "error": entry.get("error"),
            })
        if channel_entries:
            normalized[str(ch_id)] = channel_entries[-50:]
    return normalized


def _normalize_stats_payload(payload) -> dict:
    """Normalizes stats.json content to the current schema."""
    if not isinstance(payload, dict):
        raise StatsLoadError("Stats file root must be a JSON object")

    now = datetime.now().strftime("%Y-%m-%d")
    return {
        "all_time": _normalize_stats_bucket(payload.get("all_time", {})),
        "rolling_30": _normalize_stats_bucket(payload.get("rolling_30", {}), default_reset=now),
        "monthly": _normalize_stats_bucket(payload.get("monthly", {}), default_reset=now),
        "last_month": _normalize_last_month(payload.get("last_month"), default_reset=now),
        "channel_history": _normalize_channel_history(payload.get("channel_history", {})),
    }


def _normalize_last_run_payload(payload) -> dict | None:
    """Normalizes last_run.json content to the shape expected by UI and bot logic."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return None

    categories = payload.get("categories", [])
    if not isinstance(categories, list):
        categories = []

    normalized_categories = []
    for item in categories:
        if not isinstance(item, dict):
            continue
        normalized_categories.append({
            "name": str(item.get("name") or "Unknown"),
            "count": _coerce_non_negative_int(item.get("count", 0)),
        })

    return {
        "timestamp": str(payload.get("timestamp") or "N/A"),
        "triggered_by": str(payload.get("triggered_by") or "unknown"),
        "duration": str(payload.get("duration") or "0s"),
        "total_deleted": _coerce_non_negative_int(payload.get("total_deleted", 0)),
        "channels_checked": _coerce_non_negative_int(payload.get("channels_checked", 0)),
        "rate_limits": _coerce_non_negative_int(payload.get("rate_limits", 0)),
        "status": str(payload.get("status") or "unknown"),
        "categories": normalized_categories,
    }


def _latest_backup_path(backup_type: str) -> str | None:
    """Returns the newest backup path for the given type, if any."""
    newest = None
    newest_mtime = None
    prefixes = {"stats": "stats-", "last_run": "last-run-"}
    prefix = prefixes.get(backup_type)
    if not prefix:
        return None

    for backup_dir in _stats_backup_dirs(backup_type):
        try:
            entries = os.listdir(backup_dir)
        except (FileNotFoundError, PermissionError):
            continue

        for filename in entries:
            if not filename.startswith(prefix):
                continue
            path = os.path.join(backup_dir, filename)
            try:
                modified = os.path.getmtime(path)
            except OSError:
                continue
            if newest is None or modified > newest_mtime:
                newest = path
                newest_mtime = modified
    return newest


def _prune_old_stats_backups() -> None:
    """Deletes stats backups older than the retention window."""
    retention_days = getattr(cfg, "STATS_BACKUP_RETENTION_DAYS", 10)
    cutoff = datetime.now() - timedelta(days=retention_days)

    removed = 0
    for backup_dir in _stats_backup_dirs("all"):
        try:
            entries = os.listdir(backup_dir)
        except FileNotFoundError:
            continue
        except PermissionError:
            log.warning("Permission denied listing stats backups for cleanup")
            continue

        for filename in entries:
            if not filename.endswith(".json.bak"):
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
                log.warning("Permission denied deleting old stats backup: %s", filename)

    if removed:
        log.info("Pruned %s old stats backup(s)", removed)


def list_stats_backups() -> list[dict]:
    """Lists available stats and last-run backup files newest-first."""
    backups = []
    seen_paths = set()
    for backup_dir in _stats_backup_dirs("all"):
        try:
            entries = os.listdir(backup_dir)
        except FileNotFoundError:
            continue
        except PermissionError:
            log.warning("Permission denied listing stats backups")
            continue

        for filename in entries:
            if filename.startswith("stats-"):
                backup_type = "stats"
            elif filename.startswith("last-run-"):
                backup_type = "last_run"
            else:
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
                "type": backup_type,
                "filename": filename,
                "path": path,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size_bytes": stat.st_size,
            })

    backups.sort(key=lambda item: item["modified"], reverse=True)
    return backups


def _backup_existing_file(path: str, prefix: str, backup_type: str, new_content: str | None = None) -> str | None:
    """Backs up an existing JSON file before overwriting it."""
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r") as f:
            current_content = f.read()
    except PermissionError:
        log.warning("Permission denied reading %s before backup", os.path.basename(path))
        return None

    if new_content is not None and current_content == new_content:
        return None

    backup_dir = _stats_backup_dir() if backup_type == "stats" else _last_run_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"{prefix}-{timestamp}.json.bak")
    try:
        os.makedirs(backup_dir, exist_ok=True)
        atomic_write_text(backup_path, current_content)
        return backup_path
    except PermissionError:
        log.warning("Permission denied creating backup for %s", os.path.basename(path))
        return None


def load_stats(strict: bool = False) -> dict:
    """Loads stats from disk.

    Missing files return an empty structure. When ``strict`` is true, existing but
    unreadable/corrupt files raise ``StatsLoadError`` instead of silently resetting.
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        message = f"Could not create {DATA_DIR} — check directory permissions."
        log.error(message)
        if strict:
            raise StatsLoadError(message)
        return _empty_stats()
    if not os.path.exists(STATS_FILE):
        return _empty_stats()
    try:
        with open(STATS_FILE, "r") as f:
            return _normalize_stats_payload(json.load(f))
    except Exception as e:
        backup_hint = _latest_backup_path("stats")
        message = f"Could not load stats file — {e}"
        if backup_hint:
            message = f"{message} | Latest backup: {backup_hint}"
        log.warning(message)
        if strict:
            raise StatsLoadError(message) from e
        return _empty_stats()


def save_stats(stats: dict):
    """Saves stats to the stats file."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {DATA_DIR} — check directory permissions.")
        return
    try:
        normalized = _normalize_stats_payload(stats)
        new_content = json.dumps(normalized, indent=2)
        backup_path = _backup_existing_file(STATS_FILE, "stats", "stats", new_content=new_content)
        atomic_write_text(STATS_FILE, new_content)
        _prune_old_stats_backups()
        if backup_path:
            log.info("Saved stats file with backup: %s", backup_path)
    except Exception as e:
        log.warning(f"Could not save stats file — {e}")


def _append_channel_history(history: dict, ch_id: str, entry: dict) -> None:
    """Appends a history entry and keeps the channel timeline bounded."""
    history.setdefault(ch_id, [])
    history[ch_id].append(entry)
    history[ch_id] = history[ch_id][-20:]


def update_stats(channel_results: dict, run_context: dict | None = None):
    """Updates stats after a cleanup run."""
    try:
        stats = load_stats(strict=True)
    except StatsLoadError as e:
        log.error(f"Skipping stats update to avoid overwriting unreadable stats data — {e}")
        return
    now = datetime.now()

    rolling_reset = datetime.strptime(stats["rolling_30"]["reset"], "%Y-%m-%d")
    if (now - rolling_reset).days >= 30:
        log.info("Resetting rolling 30-day stats")
        stats["rolling_30"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now.strftime("%Y-%m-%d")}

    monthly_reset = datetime.strptime(stats["monthly"]["reset"], "%Y-%m-%d")
    if now.month != monthly_reset.month or now.year != monthly_reset.year:
        log.info("Resetting monthly stats")
        stats["last_month"] = {
            "runs": stats["monthly"]["runs"],
            "deleted": stats["monthly"]["deleted"],
            "reset": stats["monthly"]["reset"]
        }
        stats["monthly"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now.strftime("%Y-%m-%d")}

    total_deleted = sum(v["count"] for v in channel_results.values() if v["count"] > 0)

    for bucket in ["all_time", "rolling_30", "monthly"]:
        stats[bucket]["runs"] += 1
        stats[bucket]["deleted"] += total_deleted
        for ch_id, ch_data in channel_results.items():
            if ch_data["count"] > 0:
                if ch_id not in stats[bucket]["channels"]:
                    stats[bucket]["channels"][ch_id] = {"name": ch_data["name"], "count": 0, "category": ch_data.get("category", "Standalone")}
                else:
                    # Update name and category in case they changed
                    stats[bucket]["channels"][ch_id]["name"] = ch_data["name"]
                    stats[bucket]["channels"][ch_id]["category"] = ch_data.get("category", "Standalone")
                stats[bucket]["channels"][ch_id]["count"] += ch_data["count"]

    save_stats(stats)
    log.info(f"Stats updated | Run total: {total_deleted} | All-time: {stats['all_time']['deleted']}")


def record_channel_history(channel_results: dict, run_context: dict | None = None):
    """Records per-channel run history without touching aggregate stats buckets."""
    try:
        stats = load_stats(strict=True)
    except StatsLoadError as e:
        log.error(f"Skipping channel history update to avoid overwriting unreadable stats data — {e}")
        return

    run_context = run_context or {}
    run_timestamp = str(run_context.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    triggered_by = str(run_context.get("triggered_by") or "unknown")
    dry_run = bool(run_context.get("dry_run", False))

    history = stats.setdefault("channel_history", {})
    for ch_id, ch_data in channel_results.items():
        entry = {
            "timestamp": run_timestamp,
            "triggered_by": triggered_by,
            "count": _coerce_non_negative_int(ch_data.get("count", 0)),
            "category": ch_data.get("category", "Standalone"),
            "status": ch_data.get("status") or ("error" if ch_data.get("count", 0) < 0 or ch_data.get("error") else "deleted" if ch_data.get("count", 0) > 0 else "clean"),
            "rate_limits": _coerce_non_negative_int(ch_data.get("rate_limits", 0)),
            "dry_run": dry_run,
            "oldest": ch_data.get("oldest"),
            "error": ch_data.get("error"),
        }
        _append_channel_history(history, str(ch_id), entry)

    save_stats(stats)
    log.info("Channel history updated | entries=%s", len(channel_results))


def reset_stats(scope: str) -> bool:
    """Resets stats for the given scope: 'rolling', 'monthly', or 'all'."""
    try:
        stats = load_stats(strict=True)
    except StatsLoadError as e:
        log.error(f"Refusing to reset stats while stats storage is unreadable — {e}")
        return False
    now = datetime.now().strftime("%Y-%m-%d")

    if scope == "rolling":
        stats["rolling_30"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now}
        log.info("Rolling 30-day stats reset by user")
    elif scope == "monthly":
        stats["monthly"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now}
        log.info("Monthly stats reset by user")
    elif scope == "all":
        stats = _empty_stats()
        log.info("All stats reset by user")
    else:
        return False

    save_stats(stats)
    return True


def record_catchup_run():
    """Increments the catchup_runs counter in all three stat buckets."""
    try:
        stats = load_stats(strict=True)
    except StatsLoadError as e:
        log.error(f"Skipping catchup stat update to avoid overwriting unreadable stats data — {e}")
        return
    for bucket in ["all_time", "rolling_30", "monthly"]:
        stats[bucket]["catchup_runs"] = stats[bucket].get("catchup_runs", 0) + 1
    save_stats(stats)
    log.info("Catchup run recorded in stats")


def migrate_stats_categories(guild):
    """One-time migration to backfill missing category fields in existing stats entries.
    Runs on startup — skips entries that already have a category set."""
    from cleanup import build_channel_map
    try:
        stats = load_stats(strict=True)
    except StatsLoadError as e:
        log.error(f"Skipping stats migration because stats storage is unreadable — {e}")
        return
    changed = False

    channel_map = build_channel_map(guild)
    # Build id -> category_name lookup from the live channel map
    id_to_category = {
        str(ch_id): data.get("category_name") or "Standalone"
        for ch_id, data in channel_map.items()
    }

    for bucket in ["all_time", "rolling_30", "monthly"]:
        for ch_id, ch_data in stats[bucket].get("channels", {}).items():
            if isinstance(ch_data, dict) and "category" not in ch_data:
                ch_data["category"] = id_to_category.get(ch_id, "Standalone")
                changed = True

    if changed:
        save_stats(stats)
        log.info("Stats migration complete — backfilled category fields")
    else:
        log.debug("Stats migration — no entries needed updating")


def save_last_run(data: dict):
    """Persists last run summary to /config/data/last_run.json."""
    path = os.path.join(DATA_DIR, "last_run.json")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        normalized = _normalize_last_run_payload(data)
        new_content = json.dumps(normalized, indent=2)
        backup_path = _backup_existing_file(path, "last-run", "last_run", new_content=new_content)
        atomic_write_text(path, new_content)
        _prune_old_stats_backups()
        if backup_path:
            log.info("Saved last run summary with backup: %s", backup_path)
    except Exception as e:
        log.warning(f"Could not save last run summary — {e}")


def load_last_run() -> dict:
    """Loads last run summary. Returns None if not found."""
    path = os.path.join(DATA_DIR, "last_run.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return _normalize_last_run_payload(json.load(f))
    except Exception as e:
        backup_hint = _latest_backup_path("last_run")
        message = f"Could not load last run summary — {e}"
        if backup_hint:
            message = f"{message} | Latest backup: {backup_hint}"
        log.warning(message)
        return None
