# Discord Cleanup Bot — Setup Guide

An automated Discord bot that cleans up old messages from configured channels on a schedule. Built for homelab setups running services like Plex, Radarr, Sonarr, and similar tools that generate frequent notifications.

> **Contributing or building from source?** See [README.md](README.md) for the full developer guide.

## Features

- Scheduled daily cleanup runs (one or more times per day)
- Schedule exceptions for specific dates or recurring weekdays
- Per-channel retention periods
- Category support — clean all channels under a Discord category automatically
- Channel exclusions
- Deep clean — opt-in per channel/category to also delete messages older than 14 days
- Slash commands for manual runs, single channel cleanup, dry runs, stats, status, version, reload, logs, purge, and report on demand
- Web UI on port 8080 for config management, schedule editing, stats, and log viewing
- Startup validation — warns on boot if any configured channels are missing
- Startup notification — posts to log channel on every boot
- Graceful shutdown — finishes current channel before stopping on SIGTERM
- Deploy notifications — posts to log channel when a new version is detected
- Missed run alerts — posts to log channel if a scheduled run is delayed more than 15 minutes
- Error notifications — separate embed posted when errors occur during a run
- Category-summary notifications — Discord cleanup report shows totals per category rather than per channel, keeping embeds concise
- Cleanup statistics — rolling 30-day, current month, and all-time tracking with per-channel breakdown
- Stats page view toggle — switch between category summary and per-channel detail in the web UI
- Stats reset — reset any stat period via slash command with confirmation
- Monthly automated report — posts to report channel on the 1st of each month, weekly every Monday, or both, with month-over-month diff
- Color-coded Discord embed notifications
- Date-stamped rotating log files with ASCII art headers and run footers
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
| `CHANNELS_BACKUP_RETENTION_DAYS` | ❌ | `10` | Number of days to keep `channels.yml` backup files |
| `STATS_BACKUP_RETENTION_DAYS` | ❌ | `10` | Number of days to keep `stats.json` and `last_run.json` backup files |
| `SCHEDULE_SKIP_DATES` | ❌ | — | Comma-separated blackout dates in `YYYY-MM-DD` format |
| `SCHEDULE_SKIP_WEEKDAYS` | ❌ | — | Comma-separated blackout weekdays: `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun` |
| `LOG_LEVEL` | ❌ | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `STATUS_REPORT_TIME` | ❌ | `09:00` | Time to post stats report (24hr format) |
| `REPORT_FREQUENCY` | ❌ | `monthly` | Report frequency: `monthly`, `weekly`, or `both` |
| `REPORT_GROUP_MONTHLY` | ❌ | `true` | Group `notification_group` channels in monthly reports |
| `REPORT_GROUP_WEEKLY` | ❌ | `true` | Group `notification_group` channels in weekly reports |
| `WARN_UNCONFIGURED` | ❌ | `false` | Log a warning for any Discord channels not in channels.yml |
| `WEB_HOST` | ❌ | `0.0.0.0` | Host/interface the web UI binds to |
| `WEB_PORT` | ❌ | `8080` | Port the web UI listens on |
| `ADMIN_RATE_LIMIT_WINDOW_SECONDS` | ❌ | `60` | Window size for admin route rate limiting |
| `ADMIN_RATE_LIMIT_MAX_REQUESTS` | ❌ | `20` | Max mutating admin requests per window |
| `RUN_RATE_LIMIT_MAX_REQUESTS` | ❌ | `5` | Max cleanup trigger requests per window |

### Optional / Advanced Settings

These are only needed for specific deployments and can be added manually if you use them.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEB_AUTH_HEADER_NAME` | ❌ | — | Reverse-proxy header name required for web UI access |
| `WEB_AUTH_HEADER_VALUE` | ❌ | — | Expected value for `WEB_AUTH_HEADER_NAME` |
| `WEB_SECRET_KEY` | ❌ | — | Fixed secret for web sessions and CSRF protection |
| `GITHUB_TOKEN` | ❌ | — | GitHub personal access token for version update checks in private repos |

### Example `.env.discord_cleanup`

```env
DISCORD_TOKEN=your_bot_token_here
LOG_CHANNEL_ID=987654321098765432
REPORT_CHANNEL_ID=123456789098765432
CLEAN_TIME=03:00
DEFAULT_RETENTION=7
LOG_MAX_FILES=7
CHANNELS_BACKUP_RETENTION_DAYS=10
STATS_BACKUP_RETENTION_DAYS=10
SCHEDULE_SKIP_DATES=
SCHEDULE_SKIP_WEEKDAYS=
LOG_LEVEL=INFO
STATUS_REPORT_TIME=09:00
REPORT_FREQUENCY=monthly
REPORT_GROUP_MONTHLY=true
REPORT_GROUP_WEEKLY=true
WARN_UNCONFIGURED=false
WEB_HOST=0.0.0.0
WEB_PORT=8080
```

> All variables marked ❌ in the main table can also be changed at runtime via the web UI without restarting the container. Optional settings live in the section below and only need to be added manually if you use them.

### Web UI hardening

- Keep `WEB_HOST=0.0.0.0` if you are publishing the port directly from Docker.
- Put the UI behind a reverse proxy such as Authentik, Nginx Proxy Manager, Traefik, or Caddy.
- Set `WEB_AUTH_HEADER_NAME` and `WEB_AUTH_HEADER_VALUE` so the app only trusts requests forwarded by that proxy.
- Mutating UI/API routes live under `/admin/...` and are rate limited separately from read-only `/api/...` routes.

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
    notification_group: Arr Build Channels

  # Exclude a channel from cleanup entirely
  # Silently skipped in notification, logged in log file
  # Excluded channels are also blocked from /cleanup channel and web UI single-channel runs
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

  # Optional notification-only grouping for monthly/weekly Discord reports
  # Stats stay separate per channel in stats.json and the web UI
  - id: 789012345678901235
    name: repo-a-builds
    notification_group: Build Channels

  # Optional per-channel report controls
  - id: 789012345678901236
    name: audit-only
    report_individual: true
  - id: 789012345678901237
    name: silent
    report_exclude: true
  - id: 789012345678901238
    name: renamed-group
    report_group: Ops Channels
```

When `channels.yml` is invalid, the bot now reports schema errors with exact line and column numbers where possible, for example `channels[1].exclude must be true or false at line 3, column 14`.

`notification_group` is optional and only affects the Discord monthly/weekly report embed. Daily cleanup reports stay per-channel. If multiple channels share the same value, the monthly or weekly report combines them into a single line such as `Build Channels — 312 deleted across 3 channels`, while `stats.json`, `/cleanup stats channel`, and the web UI continue to track each channel separately.

`report_group`, `report_individual`, and `report_exclude` add per-channel report overrides. `report_group` replaces the shared report label for that channel, `report_individual` keeps the channel ungrouped even if grouping is enabled, and `report_exclude` omits the channel from the monthly/weekly report embed entirely.

If you want a concrete example of how those options fit together, check [channels.example.yml](../channels.example.yml).

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

Example: if retention is `14` days and a run happens on `2026-04-08`, a message from `2026-03-01` is older than both the retention cutoff and Discord's 14-day bulk-delete limit, so it will only be removed when `deep_clean: true` is enabled for that channel or its parent category.

---

## Exclusions

Set `exclude: true` on a channel to completely opt it out of cleanup.

- Full scheduled and manual runs skip it.
- `/cleanup channel` will treat it as not configured.
- The web UI single-channel run will also reject it as not configured.

Use exclusions for channels you never want touched, even by targeted runs.

---

## Slash Commands

All commands require Administrator permissions. Responses are ephemeral (only visible to you).

### Cleanup

| Command | Description |
|---------|-------------|
| `/cleanup run` | Trigger a full cleanup run on all configured channels |
| `/cleanup channel` | Trigger cleanup on a specific configured channel |
| `/cleanup dryrun` | Preview what would be deleted without actually deleting anything |
| `/cleanup purge` | Delete ALL messages in a configured channel regardless of retention (requires confirmation) |
| `/cleanup reload` | Reload channels.yml without restarting the container |
| `/cleanup version` | Show current version and uptime |
| `/cleanup status` | Show current config, channel list, and next scheduled run |
| `/cleanup report` | Post the stats report to the report channel on demand |
| `/cleanup logs` | Download today's log file as a file attachment |
| `/cleanup test` | Post a test notification to the log channel |

### Stats

| Command | Description |
|---------|-------------|
| `/cleanup stats view` | Show rolling 30-day, current month, and all-time cleanup statistics |
| `/cleanup stats channel` | Show stats for a specific channel |
| `/cleanup stats reset` | Reset stats for a chosen period (requires confirmation) |



---

## Web UI

The bot includes a built-in web interface accessible on port 8080. It provides full config management without needing Discord.

If you publish the web UI through a reverse proxy, keep the container on an internal network and consider setting `WEB_AUTH_HEADER_NAME` and `WEB_AUTH_HEADER_VALUE` so the app only trusts requests that arrive through your proxy.

On the Config page, `channels.yml` can be previewed before saving, validated without saving, restored from a previous backup, and schema errors include exact line and column numbers where possible. You can also launch a dry run from the preview modal to test the proposed config without writing it to disk. The backup browser also lets you restore `.env.discord_cleanup` snapshots, with startup-only settings calling out when a restart is still needed. Saving or restoring creates a backup of the previous file before applying changes.

The Stats page now includes a per-channel history timeline and drilldown view, the Schedule page can skip specific dates or weekdays, and the Audit page provides a read-only retention review of the live cleanup configuration.

**Pages:**

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Bot status, uptime, next run, stats summary, startup path checks, and notification fallback visibility |
| Config | `/config` | Edit retention, log level, warn unconfigured, report frequency, report grouping, browse backups, and `channels.yml` directly |
| Audit | `/audit` | Read-only retention audit showing categories, overrides, deep clean, exclusions, and grouping |
| Schedule | `/schedule` | Add and remove scheduled run times, blackout dates, and skipped weekdays |
| Stats | `/stats` | Full statistics breakdown — toggle between category summary, per-channel detail, channel history, and drilldown, plus recent stats and channels.yml backup visibility |
| Logs | `/logs` | Log viewer with file selector, search/filter controls, and color-coded entries |

**API:**
- `GET /api/status` — JSON status endpoint for health checks or external tools
- `GET /api/backups/stats` and `GET /api/backups/channels` — read-only backup listings
- `POST /admin/...` — Mutating web actions such as config saves, schedule changes, manual cleanup runs, and stats reset

The web UI runs in a background thread alongside the bot. Config changes made in the web UI take effect immediately and persist to `.env.discord_cleanup`, just like slash commands.

The dashboard also shows the active cleanup run owner when a run is in progress, recent startup path-check results, and whether any recent Discord notification embeds had to fall back to plain text.

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
    ports:
      - "8080:8080"
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

Stats are available on demand via `/cleanup stats view` and as an automated monthly report posted to the report channel on the 1st of each month.

The bot also creates timestamped backups of `stats.json` and `last_run.json` before replacing existing files. These backups are kept in `/config/data/backups/stats` and `/config/data/backups/last-run`, and are pruned automatically after `STATS_BACKUP_RETENTION_DAYS`.

---

## Notification Colors

| Color | Status | Meaning |
|-------|--------|---------|
| 🟢 Green | ✅ Cleanup Successful | Messages deleted successfully |
| 🟢 Green | 🟢 Bot Online | Bot started successfully |
| 🔵 Blue | ℹ️ Nothing to Clean | No messages met the retention threshold |
| 🔵 Blue | 🕐 Schedule Updated | Cleanup schedule changed via slash command |
| 🟠 Orange | ⚠️ Completed with Warnings | Some channels had issues but some deletions succeeded |
| 🟠 Orange | ⚠️ Scheduled Run Delayed | A cleanup run did not start within 15 minutes of its scheduled time |
| 🔴 Red | ⛔ Completed with Errors | Errors occurred and nothing was deleted |
| 🔴 Red | ⚠️ Run Errors | Separate embed listing specific errors — missing permissions, channel not found, rate limits |
| ⚫ Gray | 🔍 Dry Run Complete | Preview of what would be deleted |
| 🟣 Purple | 🚀 New Version Deployed | New version detected on startup |
| 🟣 Purple | ℹ️ Version | Version and uptime info |
| 🟠 Orange | 📊 Monthly Report | Monthly stats posted on the 1st |
| 🟢 Green | 🗑️ Purge Complete | All messages deleted from a channel via `/cleanup purge` |

---

## Log Files

Logs are stored in `/config/logs` (mounted to `./discord_cleanup/logs/`) as date-stamped files:

```
cleanup-2026-02-22.log
cleanup-2026-02-23.log
```

Each run opens with an ASCII art header showing the version and next scheduled run time, and closes with a footer showing total deleted and duration. Multiple runs on the same day are easy to distinguish. Files older than `LOG_MAX_FILES` days are automatically deleted.

Today's log file can be downloaded directly from Discord via `/cleanup logs`.
