"""
stats.py — Load, save, update, and reset cleanup statistics.
Stats are persisted to /config/data/stats.json across three rolling buckets:
all_time, rolling_30, and monthly.
"""

import json
import os
import logging
from copy import deepcopy
from datetime import datetime, timedelta

import config as cfg
from config import DATA_DIR, STATS_FILE, log
from file_utils import atomic_write_text

logger = logging.getLogger("discord-cleanup")
STATS_BACKUP_DIRNAME = "backups"
STATS_BACKUP_SUBDIR = "stats"
LAST_RUN_BACKUP_SUBDIR = "last-run"
REPORT_STATE_FILE = os.path.join(DATA_DIR, "report_state.json")
MONTHLY_REPORT_SOURCE_FILE = os.path.join(DATA_DIR, "monthly_report_source.json")


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
        "rolling_30": {
            "runs": 0,
            "deleted": 0,
            "catchup_runs": 0,
            "channels": {},
            "reset": now,
        },
        "monthly": {
            "runs": 0,
            "deleted": 0,
            "catchup_runs": 0,
            "channels": {},
            "reset": now,
        },
        "last_month": None,
        "previous_month": None,
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


def _normalize_month_summary(value, default_reset: str) -> dict | None:
    """Normalizes an optional month summary for monthly reporting."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return {
        "runs": _coerce_non_negative_int(value.get("runs", 0)),
        "deleted": _coerce_non_negative_int(value.get("deleted", 0)),
        "channels": _normalize_channel_stats(value.get("channels", {})),
        "reset": _coerce_reset_date(value.get("reset"), default_reset),
    }


def _normalize_monthly_report_source_payload(payload) -> dict:
    """Normalizes the frozen monthly report source payload."""
    if not isinstance(payload, dict):
        return {}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized: dict[str, object] = {
        "display": _normalize_month_summary(
            payload.get("display"), default_reset=datetime.now().strftime("%Y-%m-%d")
        ),
        "comparison": _normalize_month_summary(
            payload.get("comparison"), default_reset=datetime.now().strftime("%Y-%m-%d")
        ),
    }
    captured_at = payload.get("captured_at")
    if isinstance(captured_at, str) and captured_at.strip():
        normalized["captured_at"] = _coerce_timestamp(captured_at, now)
    else:
        normalized["captured_at"] = now

    month_key = str(payload.get("month_key") or "").strip()
    if month_key:
        normalized["month_key"] = month_key

    return normalized


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

    normalized: dict[str, list[dict[str, object]]] = {}
    for ch_id, entries in history.items():
        if not isinstance(entries, list):
            continue
        channel_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            channel_entries.append(
                {
                    "timestamp": _coerce_timestamp(
                        entry.get("timestamp"),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                    "triggered_by": str(entry.get("triggered_by") or "unknown"),
                    "count": _coerce_non_negative_int(entry.get("count", 0)),
                    "category": str(entry.get("category") or "Standalone"),
                    "status": str(
                        entry.get("status")
                        or (
                            "error"
                            if entry.get("error")
                            else (
                                "deleted"
                                if _coerce_non_negative_int(entry.get("count", 0)) > 0
                                else "clean"
                            )
                        )
                    ),
                    "rate_limits": _coerce_non_negative_int(
                        entry.get("rate_limits", 0)
                    ),
                    "dry_run": bool(entry.get("dry_run", False)),
                    "oldest": entry.get("oldest"),
                    "error": entry.get("error"),
                }
            )
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
        "rolling_30": _normalize_stats_bucket(
            payload.get("rolling_30", {}), default_reset=now
        ),
        "monthly": _normalize_stats_bucket(
            payload.get("monthly", {}), default_reset=now
        ),
        "last_month": _normalize_month_summary(
            payload.get("last_month"), default_reset=now
        ),
        "previous_month": _normalize_month_summary(
            payload.get("previous_month"), default_reset=now
        ),
        "channel_history": _normalize_channel_history(
            payload.get("channel_history", {})
        ),
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
        normalized_categories.append(
            {
                "name": str(item.get("name") or "Unknown"),
                "count": _coerce_non_negative_int(item.get("count", 0)),
            }
        )

    return {
        "timestamp": str(payload.get("timestamp") or "N/A"),
        "triggered_by": str(payload.get("triggered_by") or "unknown"),
        "duration": str(payload.get("duration") or "0s"),
        "total_deleted": _coerce_non_negative_int(payload.get("total_deleted", 0)),
        "channels_checked": _coerce_non_negative_int(
            payload.get("channels_checked", 0)
        ),
        "rate_limits": _coerce_non_negative_int(payload.get("rate_limits", 0)),
        "status": str(payload.get("status") or "unknown"),
        "categories": normalized_categories,
    }


def _latest_backup_path(backup_type: str) -> str | None:
    """Returns the newest backup path for the given type, if any."""
    newest = None
    newest_mtime: float = -1.0
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


def _latest_monthly_report_backup_path(
    reference_month_key: str | None = None,
) -> str | None:
    """Returns the newest stats backup that still contains the just-closed monthly snapshot."""
    reference_month_key = reference_month_key or datetime.now().strftime("%Y-%m")
    for backup_path in sorted(
        (
            os.path.join(backup_dir, filename)
            for backup_dir in _stats_backup_dirs("stats")
            for filename in (
                os.listdir(backup_dir) if os.path.isdir(backup_dir) else []
            )
            if filename.startswith("stats-") and filename.endswith(".json.bak")
        ),
        key=lambda path: os.path.getmtime(path) if os.path.exists(path) else 0,
        reverse=True,
    ):
        backup_stats = _load_stats_backup(backup_path)
        if not backup_stats:
            continue
        monthly = backup_stats.get("monthly") or {}
        if not monthly.get("channels"):
            continue
        month_key = str(monthly.get("reset") or "")[:7]
        if month_key == reference_month_key:
            continue
        return backup_path
    return None


def _load_stats_backup(path: str) -> dict | None:
    """Loads and normalizes a stats backup from disk."""
    try:
        with open(path, "r") as f:
            return _normalize_stats_payload(json.load(f))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _repair_monthly_snapshots_from_backup(stats: dict) -> bool:
    """Backfills monthly snapshots from the most recent stats backup when needed."""
    current_last_month = stats.get("last_month") or {}
    current_previous_month = stats.get("previous_month") or {}

    needs_last_month = not current_last_month.get("channels")
    needs_previous_month = not current_previous_month.get("channels")
    if not needs_last_month and not needs_previous_month:
        return False

    backup_path = _latest_backup_path("stats")
    if not backup_path:
        return False

    backup_stats = _load_stats_backup(backup_path)
    if not backup_stats:
        return False

    changed = False
    backup_last_month = backup_stats.get("monthly") or {}
    if needs_last_month and backup_last_month.get("channels"):
        stats["last_month"] = deepcopy(backup_last_month)
        changed = True

    backup_previous_month = backup_stats.get("last_month") or {}
    if needs_previous_month and backup_previous_month.get("channels"):
        stats["previous_month"] = deepcopy(backup_previous_month)
        changed = True

    if changed:
        log.info("Monthly stats snapshot repaired from latest backup: %s", backup_path)
    return changed


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
            backups.append(
                {
                    "type": backup_type,
                    "filename": filename,
                    "path": path,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "size_bytes": stat.st_size,
                }
            )

    backups.sort(key=lambda item: item["modified"], reverse=True)
    return backups


def _backup_existing_file(
    path: str, prefix: str, backup_type: str, new_content: str | None = None
) -> str | None:
    """Backs up an existing JSON file before overwriting it."""
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r") as f:
            current_content = f.read()
    except PermissionError:
        log.warning(
            "Permission denied reading %s before backup", os.path.basename(path)
        )
        return None

    if new_content is not None and current_content == new_content:
        return None

    backup_dir = (
        _stats_backup_dir() if backup_type == "stats" else _last_run_backup_dir()
    )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"{prefix}-{timestamp}.json.bak")
    try:
        os.makedirs(backup_dir, exist_ok=True)
        atomic_write_text(backup_path, current_content)
        return backup_path
    except PermissionError:
        log.warning("Permission denied creating backup for %s", os.path.basename(path))
        return None


def load_stats(strict: bool = False, repair_snapshots: bool = True) -> dict:
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
            stats = _normalize_stats_payload(json.load(f))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        backup_hint = _latest_backup_path("stats")
        message = f"Could not load stats file — {e}"
        if backup_hint:
            message = f"{message} | Latest backup: {backup_hint}"
        log.warning(message)
        if strict:
            raise StatsLoadError(message) from e
        return _empty_stats()
    if repair_snapshots and _repair_monthly_snapshots_from_backup(stats):
        save_stats(stats)
    return stats


def repair_stats_snapshots() -> tuple[bool, str]:
    """Repairs missing monthly snapshots from the latest stats backup if possible."""
    try:
        stats = load_stats(strict=True, repair_snapshots=False)
    except StatsLoadError as e:
        return False, f"Could not load stats safely — {e}"

    if _repair_monthly_snapshots_from_backup(stats):
        save_stats(stats)
        return True, "Monthly stats snapshots repaired from backup"

    return False, "No stats repair was needed"


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
        backup_path = _backup_existing_file(
            STATS_FILE, "stats", "stats", new_content=new_content
        )
        atomic_write_text(STATS_FILE, new_content)
        _prune_old_stats_backups()
        if backup_path:
            log.info("Saved stats file with backup: %s", backup_path)
    except (OSError, ValueError) as e:
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
        log.error(
            f"Skipping stats update to avoid overwriting unreadable stats data — {e}"
        )
        return
    now = datetime.now()

    rolling_reset = datetime.strptime(stats["rolling_30"]["reset"], "%Y-%m-%d")
    if (now - rolling_reset).days >= 30:
        log.info("Resetting rolling 30-day stats")
        stats["rolling_30"] = {
            "runs": 0,
            "deleted": 0,
            "channels": {},
            "reset": now.strftime("%Y-%m-%d"),
        }

    monthly_reset = datetime.strptime(stats["monthly"]["reset"], "%Y-%m-%d")
    if now.month != monthly_reset.month or now.year != monthly_reset.year:
        log.info("Resetting monthly stats")
        previous_last_month = stats.get("last_month")
        completed_month = {
            "runs": stats["monthly"]["runs"],
            "deleted": stats["monthly"]["deleted"],
            "channels": deepcopy(stats["monthly"].get("channels", {})),
            "reset": stats["monthly"]["reset"],
        }
        if previous_last_month:
            stats["previous_month"] = {
                "runs": previous_last_month["runs"],
                "deleted": previous_last_month["deleted"],
                "channels": deepcopy(previous_last_month.get("channels", {})),
                "reset": previous_last_month["reset"],
            }
        else:
            stats["previous_month"] = None
        stats["last_month"] = deepcopy(completed_month)
        save_monthly_report_source(
            {
                "display": completed_month,
                "comparison": stats.get("previous_month"),
                "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "month_key": completed_month["reset"][:7],
            }
        )
        stats["monthly"] = {
            "runs": 0,
            "deleted": 0,
            "channels": {},
            "reset": now.strftime("%Y-%m-%d"),
        }

    total_deleted = sum(v["count"] for v in channel_results.values() if v["count"] > 0)

    for bucket in ["all_time", "rolling_30", "monthly"]:
        stats[bucket]["runs"] += 1
        stats[bucket]["deleted"] += total_deleted
        for ch_id, ch_data in channel_results.items():
            if ch_data["count"] > 0:
                if ch_id not in stats[bucket]["channels"]:
                    stats[bucket]["channels"][ch_id] = {
                        "name": ch_data["name"],
                        "count": 0,
                        "category": ch_data.get("category", "Standalone"),
                    }
                else:
                    # Update name and category in case they changed
                    stats[bucket]["channels"][ch_id]["name"] = ch_data["name"]
                    stats[bucket]["channels"][ch_id]["category"] = ch_data.get(
                        "category", "Standalone"
                    )
                stats[bucket]["channels"][ch_id]["count"] += ch_data["count"]

    save_stats(stats)
    log.info(
        f"Stats updated | Run total: {total_deleted} | All-time: {stats['all_time']['deleted']}"
    )


def record_channel_history(channel_results: dict, run_context: dict | None = None):
    """Records per-channel run history without touching aggregate stats buckets."""
    try:
        stats = load_stats(strict=True)
    except StatsLoadError as e:
        log.error(
            f"Skipping channel history update to avoid overwriting unreadable stats data — {e}"
        )
        return

    run_context = run_context or {}
    run_timestamp = str(
        run_context.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    triggered_by = str(run_context.get("triggered_by") or "unknown")
    dry_run = bool(run_context.get("dry_run", False))

    history = stats.setdefault("channel_history", {})
    for ch_id, ch_data in channel_results.items():
        entry = {
            "timestamp": run_timestamp,
            "triggered_by": triggered_by,
            "count": _coerce_non_negative_int(ch_data.get("count", 0)),
            "category": ch_data.get("category", "Standalone"),
            "status": ch_data.get("status")
            or (
                "error"
                if ch_data.get("count", 0) < 0 or ch_data.get("error")
                else "deleted" if ch_data.get("count", 0) > 0 else "clean"
            ),
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
        clear_monthly_report_source()
        log.info("Monthly stats reset by user")
    elif scope == "all":
        stats = _empty_stats()
        clear_monthly_report_source()
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
        log.error(
            f"Skipping catchup stat update to avoid overwriting unreadable stats data — {e}"
        )
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
        backup_path = _backup_existing_file(
            path, "last-run", "last_run", new_content=new_content
        )
        atomic_write_text(path, new_content)
        _prune_old_stats_backups()
        if backup_path:
            log.info("Saved last run summary with backup: %s", backup_path)
    except (OSError, ValueError) as e:
        log.warning(f"Could not save last run summary — {e}")


def _normalize_report_period_payload(payload) -> dict:
    """Normalizes persisted report state for a single report period."""
    if not isinstance(payload, dict):
        return {}

    normalized = {}
    last_sent = str(payload.get("last_sent") or "").strip()
    if last_sent:
        normalized["last_sent"] = last_sent

    last_sent_at = payload.get("last_sent_at")
    if isinstance(last_sent_at, str) and last_sent_at.strip():
        normalized["last_sent_at"] = _coerce_timestamp(
            last_sent_at, last_sent_at.strip()
        )

    return normalized


def _normalize_report_state_payload(payload) -> dict:
    """Normalizes persisted report state to the current schema."""
    if not isinstance(payload, dict):
        return {}

    normalized = {}
    for label in ("monthly", "weekly"):
        period = _normalize_report_period_payload(payload.get(label, {}))
        if period:
            normalized[label] = period
    return normalized


def _monthly_report_source_from_stats(stats: dict) -> dict | None:
    """Builds the frozen monthly report source from a stats payload."""
    if not isinstance(stats, dict):
        return None

    current_month_key = datetime.now().strftime("%Y-%m")

    monthly = _normalize_stats_bucket(
        stats.get("monthly", {}), default_reset=datetime.now().strftime("%Y-%m-%d")
    )
    last_month = _normalize_month_summary(
        stats.get("last_month"), default_reset=datetime.now().strftime("%Y-%m-%d")
    )
    previous_month = _normalize_month_summary(
        stats.get("previous_month"), default_reset=datetime.now().strftime("%Y-%m-%d")
    )

    monthly_month_key = str(monthly.get("reset") or "")[:7]
    if (
        last_month
        and last_month.get("channels")
        and monthly_month_key == current_month_key
    ):
        # The live monthly bucket has already rolled into the new month, so the
        # report should describe the most recently completed month instead.
        display = last_month
        comparison = previous_month
    else:
        display = monthly
        comparison = last_month

    if not display.get("channels"):
        return None

    source = {
        "display": deepcopy(display),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "month_key": display.get("reset", "")[:7],
    }
    if comparison and comparison.get("channels"):
        source["comparison"] = deepcopy(comparison)
    return source


def save_monthly_report_source(source: dict) -> None:
    """Persists the frozen monthly report source."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {DATA_DIR} — check directory permissions.")
        return

    normalized = _normalize_monthly_report_source_payload(source)
    if not normalized.get("display", {}).get("channels"):
        return

    try:
        atomic_write_text(MONTHLY_REPORT_SOURCE_FILE, json.dumps(normalized, indent=2))
    except (OSError, ValueError) as e:
        log.warning(f"Could not save monthly report source — {e}")


def clear_monthly_report_source() -> None:
    """Deletes the frozen monthly report source, if present."""
    try:
        os.remove(MONTHLY_REPORT_SOURCE_FILE)
    except FileNotFoundError:
        return
    except OSError as e:
        log.warning(f"Could not clear monthly report source — {e}")


def load_monthly_report_source() -> dict | None:
    """Loads the frozen monthly report source, deriving it from backup when needed."""
    current_month_key = datetime.now().strftime("%Y-%m")

    def _derive_from_stats_payload(payload: dict | None) -> dict | None:
        source = _monthly_report_source_from_stats(payload or {})
        if source:
            save_monthly_report_source(source)
        return source

    def _backfill_comparison(source: dict) -> dict:
        comparison = source.get("comparison") or {}
        if comparison.get("deleted"):
            return source
        backup_path = _latest_monthly_report_backup_path(current_month_key)
        if not backup_path:
            return source
        backup_stats = _load_stats_backup(backup_path)
        if not backup_stats:
            return source
        comparison = backup_stats.get("last_month") or {}
        if comparison.get("deleted") is not None:
            source["comparison"] = deepcopy(comparison)
            save_monthly_report_source(source)
        return source

    if os.path.exists(MONTHLY_REPORT_SOURCE_FILE):
        try:
            with open(MONTHLY_REPORT_SOURCE_FILE, "r") as f:
                payload = json.load(f)
            normalized = _normalize_monthly_report_source_payload(payload)
            if (
                normalized.get("display", {}).get("channels")
                and normalized.get("month_key") != current_month_key
            ):
                backup_path = _latest_monthly_report_backup_path(current_month_key)
                if backup_path:
                    backup_stats = _load_stats_backup(backup_path)
                    derived = _derive_from_stats_payload(backup_stats)
                    if derived:
                        return _backfill_comparison(derived)
                return _backfill_comparison(normalized)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    backup_path = _latest_monthly_report_backup_path(current_month_key)
    if backup_path:
        backup_stats = _load_stats_backup(backup_path)
        derived = _derive_from_stats_payload(backup_stats)
        if derived:
            return _backfill_comparison(derived)

    try:
        current_stats = load_stats(strict=False, repair_snapshots=False)
    except StatsLoadError:
        current_stats = None
    derived = _derive_from_stats_payload(current_stats)
    if derived:
        return _backfill_comparison(derived)
    return None


def load_report_state() -> dict:
    """Loads report state. Returns an empty structure if not found."""
    if not os.path.exists(REPORT_STATE_FILE):
        return {}
    try:
        with open(REPORT_STATE_FILE, "r") as f:
            return _normalize_report_state_payload(json.load(f))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        log.warning(f"Could not load report state — {e}")
        return {}


def record_monthly_report_sent(sent_at: datetime | None = None):
    """Marks the current month as having posted a monthly report."""
    record_report_sent("monthly", sent_at=sent_at)


def record_report_sent(label: str, sent_at: datetime | None = None):
    """Marks the current period for the given report label as sent."""
    moment = sent_at or datetime.now()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {DATA_DIR} — check directory permissions.")
        return

    state = load_report_state()
    if label == "monthly":
        target_key = moment.strftime("%Y-%m")
    elif label == "weekly":
        iso_year, iso_week, _weekday = moment.isocalendar()
        target_key = f"{iso_year}-W{iso_week:02d}"
    else:
        log.warning("Unknown report label for report state update: %s", label)
        return

    if state.get(label, {}).get("last_sent") == target_key:
        return

    state[label] = {
        "last_sent": target_key,
        "last_sent_at": moment.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        atomic_write_text(REPORT_STATE_FILE, json.dumps(state, indent=2))
        log.info(
            "%s report state recorded for %s",
            label.capitalize(),
            state[label]["last_sent"],
        )
    except (OSError, ValueError) as e:
        log.warning(f"Could not save report state — {e}")


def load_last_run() -> dict | None:
    """Loads last run summary. Returns None if not found."""
    path = os.path.join(DATA_DIR, "last_run.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return _normalize_last_run_payload(json.load(f))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        backup_hint = _latest_backup_path("last_run")
        message = f"Could not load last run summary — {e}"
        if backup_hint:
            message = f"{message} | Latest backup: {backup_hint}"
        log.warning(message)
        return None
