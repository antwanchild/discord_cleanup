# Discord Cleanup Bot

An automated Discord bot that cleans up old messages from configured channels on a schedule. Built for homelab setups running services like Plex, Radarr, Sonarr, and similar tools that generate frequent notifications.

## Features

- Scheduled daily cleanup runs
- Per-channel retention periods
- Category support — clean all channels under a Discord category automatically
- Channel exclusions
- Slash commands for manual runs and status checks
- Color-coded Discord embed notifications
- Date-stamped rotating log files
- Rate limit handling with automatic retry
- Deploy notifications when a new version is detected

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
├── docker-compose.yml
├── docker-compose.discord_cleanup.yml
└── discord_cleanup/
    ├── channels.yml
    ├── logs/
    └── .env.discord_cleanup
```

---

## Configuration

### `.env.discord_cleanup`

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | ✅ | — | Bot token from Discord Developer Portal |
| `LOG_CHANNEL_ID` | ✅ | — | Channel ID where reports are posted |
| `CLEAN_TIME` | ✅ | `03:00` | Comma-separated run times in 24hr format e.g. `03:00` or `03:00,12:00` |
| `TZ` | ✅ | `UTC` | Timezone e.g. `America/New_York` |
| `DEFAULT_RETENTION` | ❌ | `7` | Default message retention in days |
| `LOG_MAX_FILES` | ❌ | `7` | Number of daily log files to retain |
| `LOG_LEVEL` | ❌ | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Example `.env.discord_cleanup`

```env
DISCORD_TOKEN=your_bot_token_here
LOG_CHANNEL_ID=987654321098765432
CLEAN_TIME=03:00
TZ=America/New_York
DEFAULT_RETENTION=7
LOG_MAX_FILES=7
LOG_LEVEL=INFO
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

All commands require Administrator permissions.

| Command | Description |
|---------|-------------|
| `/cleanup run` | Trigger a full cleanup run on all configured channels |
| `/cleanup channel` | Trigger cleanup on a specific configured channel |
| `/cleanup status` | Show current config and next scheduled run time |

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
```

### Starting the bot

```bash
docker compose pull
docker compose up -d
docker compose logs -f discord_cleanup
```

### Updating to latest version

```bash
docker compose pull && docker compose up -d
```

---

## Notification Colors

| Color | Status | Meaning |
|-------|--------|---------|
| 🟢 Green | ✅ Cleanup Successful | Messages deleted successfully |
| 🔵 Blue | ℹ️ Nothing to Clean | No messages met the retention threshold |
| 🟠 Orange | ⚠️ Completed with Warnings | Some channels had issues |
| 🔴 Red | ⛔ Completed with Errors | Errors with no deletions |
| 🟣 Purple | 🚀 New Version Deployed | New version detected on startup |

---

## Log Files

Logs are stored in `/app/logs` (mounted to `./discord_cleanup/logs` on the host) as date-stamped files:

```
cleanup-2026-02-22.log
cleanup-2026-02-23.log
```

Files older than `LOG_MAX_FILES` days are automatically deleted.

---

## Version History

See [Releases](../../releases) for full changelog.