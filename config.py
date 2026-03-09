import os
import sys
import logging
import yaml
from datetime import datetime
from dotenv import load_dotenv

CONFIG_DIR = "/config"
BOT_START_TIME = datetime.now()


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
                        "# Log level: DEBUG, INFO, WARNING, ERROR\n"
                        "LOG_LEVEL=INFO\n\n"
                        "# Time to post monthly report on the 1st (24hr format)\n"
                        "STATUS_REPORT_TIME=09:00\n\n"
                        "# Report frequency: monthly, weekly, or both\n"
                        "REPORT_FREQUENCY=monthly\n\n"
                        "# Warn about Discord channels not in channels.yml (true/false)\n"
                        "WARN_UNCONFIGURED=false\n")
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
                        "  - id: 456789012345678901\n"
                        "    name: my-excluded-channel\n"
                        "    exclude: true\n\n"
                        "  # --- STANDALONE CHANNELS ---\n"
                        "  - id: 567890123456789012\n"
                        "    name: my-standalone-channel\n\n"
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

# --- Version ---
try:
    with open("VERSION", "r") as f:
        BOT_VERSION = f.read().strip()
except FileNotFoundError:
    print("ERROR: VERSION file not found — cannot start bot.")
    sys.exit(1)
except PermissionError:
    print("ERROR: Could not read VERSION file — check permissions.")
    sys.exit(1)

# --- Environment Variables ---
TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID"))
CLEAN_TIMES = [t.strip() for t in os.getenv("CLEAN_TIME", "03:00").split(",") if t.strip()]
LOG_MAX_FILES = int(os.getenv("LOG_MAX_FILES", 7))
DEFAULT_RETENTION = int(os.getenv("DEFAULT_RETENTION", 7))
STATUS_REPORT_TIME = os.getenv("STATUS_REPORT_TIME", "09:00")
REPORT_FREQUENCY = os.getenv("REPORT_FREQUENCY", "monthly").lower()
WARN_UNCONFIGURED = os.getenv("WARN_UNCONFIGURED", "false").lower() == "true"

# --- Paths ---
LOG_DIR = f"{CONFIG_DIR}/logs"
DATA_DIR = f"{CONFIG_DIR}/data"
LAST_VERSION_FILE = f"{DATA_DIR}/last_version"
STATS_FILE = f"{DATA_DIR}/stats.json"

# --- Other Constants ---
HEALTH_FILE = "/tmp/health"  # noqa: S108
MISSED_RUN_THRESHOLD_MINUTES = 15
RETRY_DELAY = 300

# --- Channels ---
try:
    with open(f"{CONFIG_DIR}/channels.yml", "r") as f:
        _config = yaml.safe_load(f)
        raw_channels = _config.get("channels", [])
except FileNotFoundError:
    print(f"ERROR: {CONFIG_DIR}/channels.yml not found — cannot start bot.")
    sys.exit(1)
except PermissionError:
    print(f"ERROR: Could not read {CONFIG_DIR}/channels.yml — check directory permissions.")
    sys.exit(1)
except yaml.YAMLError as e:
    print(f"ERROR: channels.yml is malformed — {e}")
    sys.exit(1)

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
