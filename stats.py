"""
stats.py — Load, save, update, and reset cleanup statistics.
Stats are persisted to /config/data/stats.json across three rolling buckets:
all_time, rolling_30, and monthly.
"""
import json
import os
import logging
from datetime import datetime, timedelta

from config import DATA_DIR, STATS_FILE, log
from file_utils import atomic_write_json, atomic_write_text

logger = logging.getLogger("discord-cleanup")
STATS_BACKUP_RETENTION_DAYS = 10
STATS_BACKUP_DIRNAME = "backups"


class StatsLoadError(RuntimeError):
    """Raised when persisted stats exist but cannot be read safely."""


def _backup_dir() -> str:
    """Returns the directory used for stats-related backups."""
    return os.path.join(DATA_DIR, STATS_BACKUP_DIRNAME)


def _empty_stats():
    now = datetime.now().strftime("%Y-%m-%d")
    return {
        "all_time": {"runs": 0, "deleted": 0, "catchup_runs": 0, "channels": {}},
        "rolling_30": {"runs": 0, "deleted": 0, "catchup_runs": 0, "channels": {}, "reset": now},
        "monthly": {"runs": 0, "deleted": 0, "catchup_runs": 0, "channels": {}, "reset": now},
        "last_month": None
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


def _latest_backup_path(prefix: str) -> str | None:
    """Returns the newest backup path for the given prefix, if any."""
    backup_dir = _backup_dir()
    try:
        entries = os.listdir(backup_dir)
    except (FileNotFoundError, PermissionError):
        return None

    newest = None
    newest_mtime = None
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
    backup_dir = _backup_dir()
    cutoff = datetime.now() - timedelta(days=STATS_BACKUP_RETENTION_DAYS)

    try:
        entries = os.listdir(backup_dir)
    except FileNotFoundError:
        return
    except PermissionError:
        log.warning("Permission denied listing stats backups for cleanup")
        return

    removed = 0
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


def _backup_existing_file(path: str, prefix: str, new_content: str | None = None) -> str | None:
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

    backup_dir = _backup_dir()
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
        backup_path = _backup_existing_file(STATS_FILE, "stats", new_content=new_content)
        atomic_write_text(STATS_FILE, new_content)
        _prune_old_stats_backups()
        if backup_path:
            log.info("Saved stats file with backup: %s", backup_path)
    except Exception as e:
        log.warning(f"Could not save stats file — {e}")


def update_stats(channel_results: dict):
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
        backup_path = _backup_existing_file(path, "last-run", new_content=new_content)
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
        backup_hint = _latest_backup_path("last-run")
        message = f"Could not load last run summary — {e}"
        if backup_hint:
            message = f"{message} | Latest backup: {backup_hint}"
        log.warning(message)
        return None
