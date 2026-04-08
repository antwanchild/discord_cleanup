"""
validation.py — Shared validation helpers for env values and channels.yml.
"""
from __future__ import annotations


def validate_int(value, label: str, minimum: int | None = None, maximum: int | None = None) -> int:
    """Parses an integer and enforces optional bounds."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc

    if minimum is not None and parsed < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{label} must be at most {maximum}")
    return parsed


def validate_time_string(value: str, label: str) -> str:
    """Validates a strict 24-hour HH:MM time string and returns it zero-padded."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string in HH:MM format")

    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"{label} must use 24-hour HH:MM format")

    hour = validate_int(parts[0], f"{label} hour", 0, 23)
    minute = validate_int(parts[1], f"{label} minute", 0, 59)
    return f"{hour:02d}:{minute:02d}"


def parse_time_list(value: str, label: str = "CLEAN_TIME") -> list[str]:
    """Parses a comma-separated list of HH:MM times."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a comma-separated string")

    parsed = [validate_time_string(item, label) for item in value.split(",") if item.strip()]
    if not parsed:
        raise ValueError(f"{label} must contain at least one valid time")
    return parsed


def validate_report_frequency(value: str) -> str:
    """Validates the configured report frequency."""
    if not isinstance(value, str):
        raise ValueError("REPORT_FREQUENCY must be a string")
    normalized = value.lower().strip()
    if normalized not in {"monthly", "weekly", "both"}:
        raise ValueError("REPORT_FREQUENCY must be one of: monthly, weekly, both")
    return normalized


def validate_channels_config(raw_channels) -> list[dict]:
    """Validates the shape of channels.yml and returns normalized entries."""
    if raw_channels is None:
        return []
    if not isinstance(raw_channels, list):
        raise ValueError("channels.yml 'channels' must be a list")

    normalized = []
    for index, entry in enumerate(raw_channels, start=1):
        label = f"channels[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{label} must be a mapping")
        if "id" not in entry:
            raise ValueError(f"{label} is missing required key 'id'")

        item = dict(entry)
        item["id"] = validate_int(item["id"], f"{label}.id", 1)

        if "name" in item and not isinstance(item["name"], str):
            raise ValueError(f"{label}.name must be a string")
        if "type" in item and item["type"] != "category":
            raise ValueError(f"{label}.type must be 'category' when provided")
        if "days" in item:
            item["days"] = validate_int(item["days"], f"{label}.days", 1, 365)
        if "exclude" in item and not isinstance(item["exclude"], bool):
            raise ValueError(f"{label}.exclude must be true or false")
        if "deep_clean" in item and not isinstance(item["deep_clean"], bool):
            raise ValueError(f"{label}.deep_clean must be true or false")

        normalized.append(item)

    return normalized
