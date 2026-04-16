"""
config_settings.py — Runtime env update helpers for config knobs.
"""
from config_backups import update_env_value


def update_schedule_skip_dates(dates: list[str]) -> tuple[bool, str]:
    """Updates SCHEDULE_SKIP_DATES in env and in-memory config."""
    import config
    from validation import parse_date_list

    try:
        normalized = parse_date_list(",".join(dates), "SCHEDULE_SKIP_DATES") if dates else []
    except ValueError as e:
        return False, str(e)
    success, message = update_env_value("SCHEDULE_SKIP_DATES", ",".join(normalized))
    if success:
        config.SCHEDULE_SKIP_DATES = normalized
    return success, message


def update_schedule_skip_weekdays(weekdays: list[str]) -> tuple[bool, str]:
    """Updates SCHEDULE_SKIP_WEEKDAYS in env and in-memory config."""
    import config
    from validation import parse_weekday_list

    try:
        normalized = parse_weekday_list(",".join(weekdays), "SCHEDULE_SKIP_WEEKDAYS") if weekdays else []
    except ValueError as e:
        return False, str(e)
    success, message = update_env_value("SCHEDULE_SKIP_WEEKDAYS", ",".join(normalized))
    if success:
        config.SCHEDULE_SKIP_WEEKDAYS = normalized
    return success, message


def update_retention(days: int) -> tuple[bool, str]:
    """Updates DEFAULT_RETENTION in env and in-memory config."""
    import config
    success, message = update_env_value("DEFAULT_RETENTION", str(days))
    if success:
        config.DEFAULT_RETENTION = days
    return success, message


def update_log_level(level: str) -> tuple[bool, str]:
    """Updates LOG_LEVEL in env and in-memory logging config."""
    import logging
    import config

    valid = ["DEBUG", "INFO", "WARNING", "ERROR"]
    if level.upper() not in valid:
        return False, f"Invalid log level — must be one of: {', '.join(valid)}"
    success, message = update_env_value("LOG_LEVEL", level.upper())
    if success:
        config.LOG_LEVEL = level.upper()
        new_level = getattr(logging, level.upper())
        logging.getLogger().setLevel(new_level)
        for h in logging.getLogger().handlers:
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
