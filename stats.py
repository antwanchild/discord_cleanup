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
        "monthly": {"runs": 0, "deleted": 0, "channels": {}, "reset": now}
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
        stats["monthly"] = {"runs": 0, "deleted": 0, "channels": {}, "reset": now.strftime("%Y-%m-%d")}

    total_deleted = sum(v["count"] for v in channel_results.values() if v["count"] > 0)

    for bucket in ["all_time", "rolling_30", "monthly"]:
        stats[bucket]["runs"] += 1
        stats[bucket]["deleted"] += total_deleted
        for ch_id, ch_data in channel_results.items():
            if ch_data["count"] > 0:
                if ch_id not in stats[bucket]["channels"]:
                    stats[bucket]["channels"][ch_id] = {"name": ch_data["name"], "count": 0}
                else:
                    # Update name in case it changed
                    stats[bucket]["channels"][ch_id]["name"] = ch_data["name"]
                stats[bucket]["channels"][ch_id]["count"] += ch_data["count"]

    save_stats(stats)
    log.info(f"Stats updated | Run total: {total_deleted} | All-time: {stats['all_time']['deleted']}")


def migrate_stats(guild) -> None:
    """Migrates stats from name-keyed to ID-keyed format. Runs once, marked with migrated flag."""
    stats = load_stats()

    if stats.get("migrated"):
        return

    log.info("Migrating stats to ID-keyed format...")

    # Build name -> id map from guild channels
    name_to_id = {ch.name: str(ch.id) for ch in guild.text_channels}

    migrated_count = 0
    for bucket in ["all_time", "rolling_30", "monthly"]:
        old_channels = stats[bucket].get("channels", {})
        new_channels = {}
        orphaned = {}

        # First pass — collect all already-migrated entries
        for key, value in old_channels.items():
            if isinstance(value, dict):
                new_channels[key] = value

        # Second pass — convert old name-keyed entries
        for key, value in old_channels.items():
            if isinstance(value, dict):
                continue
            ch_id = name_to_id.get(key)
            if ch_id:
                if ch_id in new_channels:
                    # Merge counts
                    new_channels[ch_id]["count"] += value
                else:
                    new_channels[ch_id] = {"name": key, "count": value}
                migrated_count += 1
            else:
                orphaned[key] = value

        # Keep orphaned entries that couldn't be matched
        new_channels.update(orphaned)
        stats[bucket]["channels"] = new_channels

    stats["migrated"] = True
    save_stats(stats)
    log.info(f"Stats migration complete — {migrated_count} entries converted")


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
