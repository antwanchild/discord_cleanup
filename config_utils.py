"""
config_utils.py — Functions for updating .env.discord_cleanup and reloading config files.
All writes are protected by config_lock from config.py.
"""
import os
import logging
import yaml

from config import config_lock, CONFIG_DIR, log

logger = logging.getLogger("discord-cleanup")


def reload_channels() -> tuple[bool, str]:
    """Reloads channels.yml and updates raw_channels. Returns (success, message)."""
    import config
    with config_lock:
        try:
            with open(f"{CONFIG_DIR}/channels.yml", "r") as f:
                yaml_data = yaml.safe_load(f)
                config.raw_channels = yaml_data.get("channels", [])
            log.info("channels.yml reloaded successfully")
            return True, f"Loaded {len(config.raw_channels)} channel entries"
        except FileNotFoundError:
            log.error("channels.yml not found during reload")
            return False, "channels.yml not found"
        except PermissionError:
            log.error("Permission denied reading channels.yml during reload")
            return False, "Permission denied reading channels.yml"
        except yaml.YAMLError as e:
            log.error(f"channels.yml is malformed during reload — {e}")
            return False, f"channels.yml is malformed — {e}"


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
                with open(env_path, "w") as f:
                    f.writelines(new_lines)
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


def update_log_max_files(days: int) -> tuple[bool, str]:
    """Updates LOG_MAX_FILES in env and in-memory config."""
    import config
    if not 1 <= days <= 365:
        return False, "Log retention must be between 1 and 365 days"
    success, message = update_env_value("LOG_MAX_FILES", str(days))
    if success:
        config.LOG_MAX_FILES = days
    return success, message
