import json
import os
import logging
from datetime import datetime

from config import DATA_DIR, STATS_FILE, log

logger = logging.getLogger("discord-cleanup")


def _empty_stats():
    now = datetime.now().strftime("%Y-%m-%d")
    return {
        "all_time": {"runs": 0, "deleted": 0, "channels": {}},
        "rolling_30": {"runs": 0, "deleted": 0, "channels": {}, "reset": now},
        "monthly": {"runs": 0, "deleted": 0, "channels": {}, "reset": now},
        "last_month": None
    }


def load_stats() -> dict:
    """Loads stats from the stats file, returns empty structure if not found."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {DATA_DIR} — check directory permissions.")
        return _empty_stats()
    if not os.path.exists(STATS_FILE):
        return _empty_stats()
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Could not load stats file — {e}")
        return _empty_stats()


def save_stats(stats: dict):
    """Saves stats to the stats file."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except PermissionError:
        log.error(f"Could not create {DATA_DIR} — check directory permissions.")
        return
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        log.warning(f"Could not save stats file — {e}")


def update_stats(channel_results: dict):
    """Updates stats after a cleanup run."""
    stats = load_stats()
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
    stats = load_stats()
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


def migrate_stats_categories(guild):
    """One-time migration to backfill missing category fields in existing stats entries.
    Runs on startup — skips entries that already have a category set."""
    from cleanup import build_channel_map
    stats = load_stats()
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
