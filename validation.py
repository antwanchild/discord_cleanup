"""
validation.py — Shared validation helpers for env values and channels.yml.
"""
from __future__ import annotations

from datetime import datetime
import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


class ChannelsConfigError(ValueError):
    """Raised when channels.yml fails schema validation."""


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


def validate_date_string(value: str, label: str) -> str:
    """Validates a strict YYYY-MM-DD date string and returns it normalized."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string in YYYY-MM-DD format")

    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format") from exc
    return parsed.strftime("%Y-%m-%d")


def parse_date_list(value: str, label: str = "SCHEDULE_SKIP_DATES") -> list[str]:
    """Parses a comma-separated list of YYYY-MM-DD dates."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a comma-separated string")

    parsed = [validate_date_string(item, label) for item in value.split(",") if item.strip()]
    return parsed


_WEEKDAY_ALIASES = {
    "0": "mon",
    "1": "tue",
    "2": "wed",
    "3": "thu",
    "4": "fri",
    "5": "sat",
    "6": "sun",
    "mon": "mon",
    "monday": "mon",
    "tue": "tue",
    "tues": "tue",
    "tuesday": "tue",
    "wed": "wed",
    "wednesday": "wed",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "thursday": "thu",
    "fri": "fri",
    "friday": "fri",
    "sat": "sat",
    "saturday": "sat",
    "sun": "sun",
    "sunday": "sun",
}


def validate_weekday_string(value: str, label: str) -> str:
    """Validates a weekday string and returns a canonical three-letter label."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a weekday name")
    normalized = value.strip().lower()
    if normalized not in _WEEKDAY_ALIASES:
        raise ValueError(f"{label} must be one of: Mon, Tue, Wed, Thu, Fri, Sat, Sun")
    return _WEEKDAY_ALIASES[normalized]


def parse_weekday_list(value: str, label: str = "SCHEDULE_SKIP_WEEKDAYS") -> list[str]:
    """Parses a comma-separated list of weekday names."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a comma-separated string")

    return [validate_weekday_string(item, label) for item in value.split(",") if item.strip()]


def validate_report_frequency(value: str) -> str:
    """Validates the configured report frequency."""
    if not isinstance(value, str):
        raise ValueError("REPORT_FREQUENCY must be a string")
    normalized = value.lower().strip()
    if normalized not in {"monthly", "weekly", "both"}:
        raise ValueError("REPORT_FREQUENCY must be one of: monthly, weekly, both")
    return normalized


def validate_bool(value, label: str) -> bool:
    """Validates a boolean-like environment value."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{label} must be true or false")
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{label} must be true or false")
    return normalized == "true"


def _location(node) -> str:
    """Formats a node's starting line and column for user-facing errors."""
    mark = getattr(node, "start_mark", None)
    if mark is None:
        return ""
    return f" at line {mark.line + 1}, column {mark.column + 1}"


def _schema_error(message: str, node=None) -> ChannelsConfigError:
    """Creates a schema error with optional source location."""
    suffix = _location(node)
    return ChannelsConfigError(f"{message}{suffix}")


def _expect_mapping(node, label: str) -> MappingNode:
    if not isinstance(node, MappingNode):
        raise _schema_error(f"{label} must be a mapping", node)
    return node


def _expect_sequence(node, label: str) -> SequenceNode:
    if not isinstance(node, SequenceNode):
        raise _schema_error(f"{label} must be a list", node)
    return node


def _mapping_dict(node: MappingNode, label: str) -> dict[str, tuple[ScalarNode, object]]:
    """Converts a mapping node into a scalar-keyed dict while preserving child nodes."""
    result = {}
    for key_node, value_node in node.value:
        if not isinstance(key_node, ScalarNode):
            raise _schema_error(f"{label} keys must be strings", key_node)
        result[key_node.value] = (key_node, value_node)
    return result


def _scalar_string(node, label: str) -> str:
    if not isinstance(node, ScalarNode):
        raise _schema_error(f"{label} must be a string", node)
    if node.tag != "tag:yaml.org,2002:str":
        raise _schema_error(f"{label} must be a string", node)
    return node.value


def _scalar_int(node, label: str, minimum: int | None = None, maximum: int | None = None) -> int:
    if not isinstance(node, ScalarNode):
        raise _schema_error(f"{label} must be an integer", node)
    try:
        parsed = int(node.value)
    except (TypeError, ValueError) as exc:
        raise _schema_error(f"{label} must be an integer", node) from exc
    if minimum is not None and parsed < minimum:
        raise _schema_error(f"{label} must be at least {minimum}", node)
    if maximum is not None and parsed > maximum:
        raise _schema_error(f"{label} must be at most {maximum}", node)
    return parsed


def _scalar_bool(node, label: str) -> bool:
    if not isinstance(node, ScalarNode):
        raise _schema_error(f"{label} must be true or false", node)
    if node.tag != "tag:yaml.org,2002:bool":
        raise _schema_error(f"{label} must be true or false", node)
    return node.value.lower() in {"true", "yes", "on"}


def _parse_channel_entry(node, index: int) -> dict:
    label = f"channels[{index}]"
    mapping = _mapping_dict(_expect_mapping(node, label), label)

    id_pair = mapping.get("id")
    if id_pair is None:
        raise _schema_error(f"{label} is missing required key 'id'", node)

    item = {"id": _scalar_int(id_pair[1], f"{label}.id", 1)}

    if "name" in mapping:
        item["name"] = _scalar_string(mapping["name"][1], f"{label}.name")
    if "type" in mapping:
        item_type = _scalar_string(mapping["type"][1], f"{label}.type")
        if item_type != "category":
            raise _schema_error(f"{label}.type must be 'category' when provided", mapping["type"][1])
        item["type"] = item_type
    if "days" in mapping:
        item["days"] = _scalar_int(mapping["days"][1], f"{label}.days", 1, 365)
    if "exclude" in mapping:
        item["exclude"] = _scalar_bool(mapping["exclude"][1], f"{label}.exclude")
    if "deep_clean" in mapping:
        item["deep_clean"] = _scalar_bool(mapping["deep_clean"][1], f"{label}.deep_clean")
    if "notification_group" in mapping:
        item["notification_group"] = _scalar_string(mapping["notification_group"][1], f"{label}.notification_group")

    for key, (_, value_node) in mapping.items():
        if key not in item:
            item[key] = yaml.safe_load(yaml.serialize(value_node))

    return item


def load_channels_config(text: str) -> list[dict]:
    """Parses and validates channels.yml content with line-aware errors."""
    root = yaml.compose(text)
    if root is None:
        raise ChannelsConfigError("channels.yml must not be empty")

    root_mapping = _mapping_dict(_expect_mapping(root, "channels.yml root"), "channels.yml root")
    channels_pair = root_mapping.get("channels")
    if channels_pair is None:
        raise _schema_error("channels.yml root must contain a 'channels' key", root)

    channels_node = _expect_sequence(channels_pair[1], "channels.yml 'channels'")
    return [_parse_channel_entry(node, index) for index, node in enumerate(channels_node.value, start=1)]


def load_channels_config_file(path: str) -> list[dict]:
    """Loads and validates a channels.yml file from disk."""
    with open(path, "r") as f:
        return load_channels_config(f.read())
