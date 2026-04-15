import os
import sys
import logging
import threading
import yaml
from datetime import datetime
from dotenv import load_dotenv
from validation import (
    ChannelsConfigError,
    load_channels_config_file,
    parse_time_list,
    validate_int,
    validate_report_frequency,
    validate_time_string,
)

CONFIG_DIR = "/config"
BOT_START_TIME = datetime.now()

# Shared lock for all config file reads and writes.
# Used by both the bot and the web UI to prevent simultaneous access.
config_lock = threading.Lock()


def create_default_files():
    """Creates default config files if they don't exist. Exits if any were created."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except PermissionError:
        print(f"ERROR: Could not create {CONFIG_DIR} — check directory permissions.")
        sys.exit(1)
    created = False

    if not os.path.exists(f"{CONFIG_DIR}/.env.discord_cleanup"):
        try:
            with open(f"{CONFIG_DIR}/.env.discord_cleanup", "w") as f:
                f.write("# Discord bot token from Discord Developer Portal\n"
                        "DISCORD_TOKEN=your_bot_token_here\n\n"
                        "# Channel ID where cleanup reports are posted\n"
                        "LOG_CHANNEL_ID=your_log_channel_id_here\n\n"
                        "# Channel ID where monthly reports are posted\n"
                        "REPORT_CHANNEL_ID=your_report_channel_id_here\n\n"
                        "# Comma-separated run times in 24hr format e.g. 03:00 or 03:00,12:00\n"
                        "CLEAN_TIME=03:00\n\n"
                        "# Default message retention in days\n"
                        "DEFAULT_RETENTION=7\n\n"
                        "# Number of daily log files to retain\n"
                        "LOG_MAX_FILES=7\n\n"
                        "# Number of days to keep channels.yml backups\n"
                        "CHANNELS_BACKUP_RETENTION_DAYS=10\n\n"
                        "# Number of days to keep stats.json and last_run.json backups\n"
                        "STATS_BACKUP_RETENTION_DAYS=10\n\n"
                        "# Log level: DEBUG, INFO, WARNING, ERROR\n"
                        "LOG_LEVEL=INFO\n\n"
                        "# Time to post monthly report on the 1st (24hr format)\n"
                        "STATUS_REPORT_TIME=09:00\n\n"
                        "# Report frequency: monthly, weekly, or both\n"
                        "REPORT_FREQUENCY=monthly\n\n"
                        "# Warn about Discord channels not in channels.yml (true/false)\n"
                        "WARN_UNCONFIGURED=false\n\n"
                        "# Trigger a catchup run on startup if a scheduled run was missed (true/false)\n"
                        "CATCHUP_MISSED_RUNS=true\n\n"
                        "# Web UI bind host and port\n"
                        "WEB_HOST=0.0.0.0\n"
                        "WEB_PORT=8080\n\n"
                        "# Optional reverse-proxy auth header pair for the web UI\n"
                        "# WEB_AUTH_HEADER_NAME=X-Forwarded-User\n"
                        "# WEB_AUTH_HEADER_VALUE=your-expected-authentik-username\n\n"
                        "# Admin endpoint rate limits\n"
                        "ADMIN_RATE_LIMIT_WINDOW_SECONDS=60\n"
                        "ADMIN_RATE_LIMIT_MAX_REQUESTS=20\n"
                        "RUN_RATE_LIMIT_MAX_REQUESTS=5\n\n"
                        "# Optional fixed secret for web sessions and CSRF tokens\n"
                        "# WEB_SECRET_KEY=\n\n"
                        "# GitHub personal access token for version update checks (required for private repos)\n"
                        "# GITHUB_TOKEN=\n")
            print(f"{CONFIG_DIR}/.env.discord_cleanup not found — created with default values. Please fill in your bot token and channel IDs then restart.")
            created = True
        except PermissionError:
            print(f"ERROR: Could not create {CONFIG_DIR}/.env.discord_cleanup — check directory permissions.")
            sys.exit(1)

    if not os.path.exists(f"{CONFIG_DIR}/channels.yml"):
        try:
            with open(f"{CONFIG_DIR}/channels.yml", "w") as f:
                f.write("channels:\n"
                        "  # --- CATEGORIES ---\n"
                        "  # Cleans all text channels under this Discord category\n"
                        "  # Uses DEFAULT_RETENTION from .env unless days is specified\n"
                        "  # Add deep_clean: true to also delete messages older than 14 days\n"
                        "  - id: 123456789012345678\n"
                        "    name: My Category\n"
                        "    type: category\n\n"
                        "  # Category with retention override and deep clean enabled\n"
                        "  - id: 234567890123456789\n"
                        "    name: My Category With Override\n"
                        "    type: category\n"
                        "    days: 4\n"
                        "    deep_clean: true\n\n"
                        "  # --- CHANNEL OVERRIDES ---\n"
                        "  - id: 345678901234567890\n"
                        "    name: my-channel-override\n"
                        "    days: 3\n\n"
                        "  # --- EXCLUSIONS ---\n"
                        "  # Excluded channels are skipped by full runs and cannot be targeted\n"
                        "  # by /cleanup channel or the web UI single-channel run\n"
                        "  - id: 456789012345678901\n"
                        "    name: my-excluded-channel\n"
                        "    exclude: true\n\n"
                        "  # --- STANDALONE CHANNELS ---\n"
                        "  - id: 567890123456789012\n"
                        "    name: my-standalone-channel\n"
                        "    # Optional: group channels together in monthly/weekly Discord reports\n"
                        "    # notification_group: Build Channels\n\n"
                        "  # Standalone channel with deep clean enabled\n"
                        "  - id: 678901234567890123\n"
                        "    name: my-standalone-deep\n"
                        "    days: 14\n"
                        "    deep_clean: true\n")
            print(f"{CONFIG_DIR}/channels.yml not found — created with sample config. Please update with your real channel IDs then restart.")
            created = True
        except PermissionError:
            print(f"ERROR: Could not create {CONFIG_DIR}/channels.yml — check directory permissions.")
            sys.exit(1)

    if created:
        sys.exit(0)


# Run on import so config files exist before anything else loads
create_default_files()

load_dotenv(f"{CONFIG_DIR}/.env.discord_cleanup")

# --- Logging Setup ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)

logger = logging.getLogger()
logger.setLevel(numeric_level)

console_handler = logging.StreamHandler()
console_handler.setLevel(numeric_level)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

log = logging.getLogger("discord-cleanup")

# --- Version ---
try:
    with open("VERSION", "r") as f:
        BOT_VERSION = f.read().strip()
except FileNotFoundError:
    log.error("VERSION file not found — cannot start bot.")
    sys.exit(1)
except PermissionError:
    log.error("Could not read VERSION file — check permissions.")
    sys.exit(1)

# --- Environment Variables ---
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    log.error("DISCORD_TOKEN is not set in .env.discord_cleanup — cannot start bot.")
    sys.exit(1)

_raw_log_channel = os.getenv("LOG_CHANNEL_ID")
_raw_report_channel = os.getenv("REPORT_CHANNEL_ID")
if not _raw_log_channel:
    log.error("LOG_CHANNEL_ID is not set in .env.discord_cleanup — cannot start bot.")
    sys.exit(1)
if not _raw_report_channel:
    log.error("REPORT_CHANNEL_ID is not set in .env.discord_cleanup — cannot start bot.")
    sys.exit(1)
try:
    LOG_CHANNEL_ID = int(_raw_log_channel)
    REPORT_CHANNEL_ID = int(_raw_report_channel)
except ValueError:
    log.error("LOG_CHANNEL_ID and REPORT_CHANNEL_ID must be numeric Discord channel IDs.")
    sys.exit(1)

try:
    CLEAN_TIMES = parse_time_list(os.getenv("CLEAN_TIME", "03:00"), "CLEAN_TIME")
    LOG_MAX_FILES = validate_int(os.getenv("LOG_MAX_FILES", 7), "LOG_MAX_FILES", 1, 365)
    CHANNELS_BACKUP_RETENTION_DAYS = validate_int(
        os.getenv("CHANNELS_BACKUP_RETENTION_DAYS", 10),
        "CHANNELS_BACKUP_RETENTION_DAYS",
        1,
        365,
    )
    STATS_BACKUP_RETENTION_DAYS = validate_int(
        os.getenv("STATS_BACKUP_RETENTION_DAYS", 10),
        "STATS_BACKUP_RETENTION_DAYS",
        1,
        365,
    )
    DEFAULT_RETENTION = validate_int(os.getenv("DEFAULT_RETENTION", 7), "DEFAULT_RETENTION", 1, 365)
    STATUS_REPORT_TIME = validate_time_string(os.getenv("STATUS_REPORT_TIME", "09:00"), "STATUS_REPORT_TIME")
    REPORT_FREQUENCY = validate_report_frequency(os.getenv("REPORT_FREQUENCY", "monthly"))
except ValueError as e:
    log.error(f"Invalid configuration value — {e}")
    sys.exit(1)

WARN_UNCONFIGURED    = os.getenv("WARN_UNCONFIGURED", "false").lower() == "true"
GITHUB_TOKEN         = os.getenv("GITHUB_TOKEN")
# When true, a missed scheduled run is detected on startup and triggered automatically
CATCHUP_MISSED_RUNS  = os.getenv("CATCHUP_MISSED_RUNS", "true").lower() == "true"

# --- Paths ---
LOG_DIR = f"{CONFIG_DIR}/logs"
DATA_DIR = f"{CONFIG_DIR}/data"
LAST_VERSION_FILE = f"{DATA_DIR}/last_version"
STATS_FILE = f"{DATA_DIR}/stats.json"

# --- Other Constants ---
HEALTH_FILE = "/tmp/health"
MISSED_RUN_THRESHOLD_MINUTES = 15
RETRY_DELAY = 300

# --- Channels ---
try:
    raw_channels = load_channels_config_file(f"{CONFIG_DIR}/channels.yml")
except FileNotFoundError:
    log.error(f"{CONFIG_DIR}/channels.yml not found — cannot start bot.")
    sys.exit(1)
except PermissionError:
    log.error(f"Could not read {CONFIG_DIR}/channels.yml — check directory permissions.")
    sys.exit(1)
except ChannelsConfigError as e:
    log.error(f"channels.yml validation failed — {e}")
    sys.exit(1)
except yaml.YAMLError as e:
    log.error(f"channels.yml is malformed — {e}")
    sys.exit(1)
