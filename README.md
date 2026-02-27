# Discord Cleanup Bot 

An automated Discord bot that cleans up old messages from configured channels on a schedule. Built for homelab setups running services like Plex, Radarr, Sonarr, and similar tools that generate frequent notifications.

## Features

- Scheduled daily cleanup runs (one or more times per day)
- Per-channel retention periods
- Category support — clean all channels under a Discord category automatically
- Channel exclusions
- Slash commands for manual runs, single channel cleanup, dry runs, stats, and status
- Startup validation — warns on boot if any configured channels are missing
- Graceful shutdown — finishes current channel before stopping on SIGTERM
- Deploy notifications — posts to log channel when a new version is detected
- Cleanup statistics — rolling 30-day, current month, and all-time tracking
- Monthly automated report — posts to report channel on the 1st of each month
- Color-coded Discord embed notifications
- Date-stamped rotating log files with run separators
- Rate limit handling with automatic retry

---

## Requirements

- Docker
- A Discord bot token
- A Discord server where you have admin access

---

## Discord Bot Setup

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Create a new application and add a Bot
3. Enable these **Privileged Gateway Intents**:
   - Presence Intent
   - Server Members Intent
   - Message Content Intent
4. Under **OAuth2 → URL Generator** select scopes:
   - `bot`
   - `applications.commands`
5. Select permissions:
   - View Channels
   - Manage Messages
   - Read Message History
6. Use the generated URL to invite the bot to your server
7. Copy the bot token for use in `.env.discord_cleanup`

---

## Folder Structure

```
/your-docker-root/
├── docker-compose.discord_cleanup.yml
└── discord_cleanup/
    ├── channels.yml
    ├── logs/
    ├── data/
    └── .env.discord_cleanup
```

---

## Configuration

### `.env.discord_cleanup`

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | ✅ | — | Bot token from Discord Developer Portal |
| `LOG_CHANNEL_ID` | ✅ | — | Channel ID where cleanup reports are posted |
| `REPORT_CHANNEL_ID` | ✅ | — | Channel ID where monthly reports are posted |
| `CLEAN_TIME` | ✅ | `03:00` | Comma-separated run times in 24hr format e.g. `03:00` or `03:00,12:00` |
| `TZ` | ✅ | `UTC` | Timezone e.g. `America/New_York` |
| `DEFAULT_RETENTION` | ❌ | `7` | Default message retention in days |
| `LOG_MAX_FILES` | ❌ | `7` | Number of daily log files to retain |
| `LOG_LEVEL` | ❌ | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `STATUS_REPORT_TIME` | ❌ | `09:00` | Time to post monthly report on the 1st (24hr format) |

### Example `.env.discord_cleanup`

```env
DISCORD_TOKEN=your_bot_token_here
LOG_CHANNEL_ID=987654321098765432
REPORT_CHANNEL_ID=123456789098765432
CLEAN_TIME=05:48
TZ=America/Chicago
DEFAULT_RETENTION=7
LOG_MAX_FILES=7
LOG_LEVEL=INFO
STATUS_REPORT_TIME=09:00
```

---

## `channels.yml` Reference

```yaml
channels:
  # Category — cleans all text channels under this Discord category
  # Uses DEFAULT_RETENTION unless days is specified
  - id: 234567890123456789
    name: Radarr
    type: category

  # Category with retention override
  - id: 345678901234567890
    name: Sonarr
    type: category
    days: 4

  # Override retention for a specific channel inside a category
  - id: 123456789012345678
    name: radarr-movies
    days: 3

  # Exclude a channel from cleanup entirely
  # Silently skipped in notification, logged in log file
  - id: 789012345678901234
    name: radarr-important
    exclude: true

  # Standalone channel — uses DEFAULT_RETENTION
  - id: 567890123456789012
    name: crowdsec

  # Standalone channel with retention override
  - id: 678901234567890123
    name: notifications
    days: 14
```

---

## Slash Commands

All commands require Administrator permissions. Responses are ephemeral (only visible to you).

| Command | Description |
|---------|-------------|
| `/cleanup run` | Trigger a full cleanup run on all configured channels |
| `/cleanup channel` | Trigger cleanup on a specific configured channel |
| `/cleanup dryrun` | Preview what would be deleted without actually deleting anything |
| `/cleanup stats` | Show rolling 30-day, current month, and all-time cleanup statistics |
| `/cleanup status` | Show current config, channel list, and next scheduled run time |

---

## Deployment

### Docker Compose

```yaml
services:
  discord_cleanup:
    image: ghcr.io/yourusername/discord_cleanup:latest
    container_name: discord_cleanup
    restart: unless-stopped
    env_file:
      - ./discord_cleanup/.env.discord_cleanup
    environment:
      - TZ=${TZ}
    volumes:
      - ./discord_cleanup/logs:/app/logs
      - ./discord_cleanup/channels.yml:/app/channels.yml
      - ./discord_cleanup/data:/app/data
```

### First time setup

```bash
mkdir -p /your-docker-root/discord_cleanup/logs
mkdir -p /your-docker-root/discord_cleanup/data
```

### Starting the bot

```bash
docker compose -f docker-compose.discord_cleanup.yml pull
docker compose -f docker-compose.discord_cleanup.yml up -d
docker compose -f docker-compose.discord_cleanup.yml logs -f discord_cleanup
```

### Updating to latest version

```bash
docker compose -f docker-compose.discord_cleanup.yml pull && docker compose -f docker-compose.discord_cleanup.yml up -d
```

---

## Startup Validation

On every boot the bot validates all configured channel and category IDs against the live Discord server. Any missing channels are logged as warnings before the first cleanup run. Check the logs after restarting if you suspect a misconfiguration.

---

## Deploy Notifications

When the bot detects a new version on startup it posts a notification embed to the log channel showing the old and new version numbers and the image tag. This only fires when the version changes — not on every restart.

Version state is persisted in `/app/data/last_version` (mounted to `./discord_cleanup/data/`).

---

## Graceful Shutdown

When Docker stops the container (SIGTERM) the bot finishes processing the current channel before shutting down. This prevents partial cleanup runs from leaving channels in an inconsistent state.

---

## Statistics Tracking

After each cleanup run the bot updates a `stats.json` file in `/app/data` tracking:

- **Rolling 30 days** — resets every 30 days, always shows the last month of activity
- **Current month** — resets on the 1st of each month
- **All time** — never resets, cumulative totals since first run

Stats are available on demand via `/cleanup stats` and as an automated monthly report posted to the report channel on the 1st of each month.

---

## Notification Colors

| Color | Status | Meaning |
|-------|--------|---------|
| 🟢 Green | ✅ Cleanup Successful | Messages deleted successfully |
| 🔵 Blue | ℹ️ Nothing to Clean | No messages met the retention threshold |
| 🟠 Orange | ⚠️ Completed with Warnings | Some channels had issues |
| 🔴 Red | ⛔ Completed with Errors | Errors with no deletions |
| ⚫ Gray | 🔍 Dry Run Complete | Preview of what would be deleted |
| 🟣 Purple | 🚀 New Version Deployed | New version detected on startup |
| 🟠 Orange | 📊 Monthly Report | Monthly stats posted on the 1st |

---

## Log Files

Logs are stored in `/app/logs` (mounted to `./discord_cleanup/logs/`) as date-stamped files:

```
cleanup-2026-02-22.log
cleanup-2026-02-23.log
```

Each run is separated by a `====` divider line so multiple runs on the same day are easy to distinguish. Files older than `LOG_MAX_FILES` days are automatically deleted.

---

## CI/CD Pipeline

Every push to `main` triggers the GitHub Actions workflow which:

1. Runs a `ruff` lint check (warn only, does not block build)
2. Auto-bumps the version based on commit message tags:
   - Default — patch bump (e.g. `3.1.1` → `3.1.2`)
   - `#minor` in commit message — minor bump (e.g. `3.1.1` → `3.2.0`)
   - `#major` in commit message — major bump (e.g. `3.1.1` → `4.0.0`)
3. Builds and pushes Docker image to GHCR with `:latest` and `:version` tags
4. Creates a GitHub Release
5. Cleans up old GHCR images keeping the last 10

Pushes that only modify `README.md`, `dependabot.yml`, `.gitignore`, or `.dockerignore` are skipped entirely — no build, no version bump, no release.

---

## Version History

See [Releases](../../releases) for full changelog.
