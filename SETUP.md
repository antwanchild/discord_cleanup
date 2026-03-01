# Discord Cleanup Bot — Setup Guide

An automated Discord bot that cleans up old messages from configured channels on a schedule. Built for homelab setups running services like Plex, Radarr, Sonarr, and similar tools that generate frequent notifications.

> **Contributing or building from source?** See [README.md](README.md) for the full developer guide.

## Features

- Scheduled daily cleanup runs (one or more times per day)
- Per-channel retention periods
- Category support — clean all channels under a Discord category automatically
- Channel exclusions
- Deep clean — opt-in per channel/category to also delete messages older than 14 days
- Slash commands for manual runs, single channel cleanup, dry runs, stats, status, version, and reload
- Startup validation — warns on boot if any configured channels are missing
- Startup notification — posts to log channel on every boot
- Graceful shutdown — finishes current channel before stopping on SIGTERM
- Deploy notifications — posts to log channel when a new version is detected
- Missed run alerts — posts to log channel if a scheduled run is delayed more than 15 minutes
- Cleanup statistics — rolling 30-day, current month, and all-time tracking
- Stats reset — reset any stat period via slash command with confirmation
- Monthly automated report — posts to report channel on the 1st of each month
- Color-coded Discord embed notifications
- Date-stamped rotating log files with run separators
- Rate limit handling with automatic retry
- Docker health check — container marked unhealthy if bot stops responding
- Auto-generates default config files on first run if they don't exist

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
| `DEFAULT_RETENTION` | ❌ | `7` | Default message retention in days |
| `LOG_MAX_FILES` | ❌ | `7` | Number of daily log files to retain |
| `LOG_LEVEL` | ❌ | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `STATUS_REPORT_TIME` | ❌ | `09:00` | Time to post monthly report on the 1st (24hr format) |
| `PUID` | ❌ | `1000` | User ID for file ownership |
| `PGID` | ❌ | `1000` | Group ID for file ownership |

### Example `.env.discord_cleanup`

```env
DISCORD_TOKEN=your_bot_token_here
LOG_CHANNEL_ID=987654321098765432
REPORT_CHANNEL_ID=123456789098765432
CLEAN_TIME=03:00
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

  # Category with deep clean enabled — also deletes messages older than 14 days
  # Deep clean uses individual deletion (slower, more rate limits)
  - id: 456789012345678901
    name: Plex
    type: category
    deep_clean: true

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

  # Standalone channel with deep clean enabled
  - id: 678901234567890123
    name: notifications
    days: 14
    deep_clean: true
```

---

## Deep Clean

By default the bot only deletes messages using Discord's bulk delete API, which cannot delete messages older than 14 days. If a channel has messages older than 14 days that you want removed, enable deep clean for that channel or category.

```yaml
- id: 123456789012345678
  name: My Category
  type: category
  deep_clean: true
```

Deep clean runs after the bulk delete pass and deletes old messages one at a time. This is significantly slower and more likely to hit rate limits, so only enable it on channels where you need it.

---

## Slash Commands

All commands require Administrator permissions. Responses are ephemeral (only visible to you).

| Command | Description |
|---------|-------------|
| `/cleanup run` | Trigger a full cleanup run on all configured channels |
| `/cleanup channel` | Trigger cleanup on a specific configured channel |
| `/cleanup dryrun` | Preview what would be deleted without actually deleting anything |
| `/cleanup reload` | Reload channels.yml without restarting the container |
| `/cleanup version` | Show current version and uptime |
| `/cleanup status` | Show current config, channel list, and next scheduled run time |
| `/cleanup stats view` | Show rolling 30-day, current month, and all-time cleanup statistics |
| `/cleanup stats reset` | Reset stats for a chosen period (requires confirmation) |

---

## Deployment

### Docker Compose

```yaml
services:
  discord_cleanup:
    image: ghcr.io/antwanchild/discord_cleanup:latest
    container_name: discord_cleanup
    restart: unless-stopped
    env_file:
      - ./discord_cleanup/.env.discord_cleanup
    environment:
      - TZ=${TZ}
      - PUID=1000
      - PGID=1000
    volumes:
      - ./discord_cleanup:/config
```

### First time setup

```bash
mkdir -p /your-docker-root/discord_cleanup
```

On first run the bot will automatically create `.env.discord_cleanup` and `channels.yml` with default values if they don't exist, then exit. Fill in your bot token and channel IDs then restart the container.

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

---

## Graceful Shutdown

When Docker stops the container (SIGTERM) the bot finishes processing the current channel before shutting down. This prevents partial cleanup runs from leaving channels in an inconsistent state.

---

## Statistics Tracking

After each cleanup run the bot updates a `stats.json` file in `/config/data` tracking:

- **Rolling 30 days** — resets every 30 days, always shows the last month of activity
- **Current month** — resets on the 1st of each month
- **All time** — never resets, cumulative totals since first run

Stats are available on demand via `/cleanup stats` and as an automated monthly report posted to the report channel on the 1st of each month.

---

## Notification Colors

| Color | Status | Meaning |
|-------|--------|---------|
| 🟢 Green | ✅ Cleanup Successful | Messages deleted successfully |
| 🟢 Green | 🟢 Bot Online | Bot started successfully |
| 🔵 Blue | ℹ️ Nothing to Clean | No messages met the retention threshold |
| 🟠 Orange | ⚠️ Completed with Warnings | Some channels had issues |
| 🟠 Orange | ⚠️ Scheduled Run Delayed | A cleanup run did not start within 15 minutes of its scheduled time |
| 🔴 Red | ⛔ Completed with Errors | Errors with no deletions |
| ⚫ Gray | 🔍 Dry Run Complete | Preview of what would be deleted |
| 🟣 Purple | 🚀 New Version Deployed | New version detected on startup |
| 🟣 Purple | ℹ️ Version | Version and uptime info |
| 🟠 Orange | 📊 Monthly Report | Monthly stats posted on the 1st |

---

## Log Files

Logs are stored in `/config/logs` (mounted to `./discord_cleanup/logs/`) as date-stamped files:

```
cleanup-2026-02-22.log
cleanup-2026-02-23.log
```

Each run is separated by a `====` divider line so multiple runs on the same day are easy to distinguish. Files older than `LOG_MAX_FILES` days are automatically deleted.
