# Discord Cleanup Bot — API Reference

The bot exposes a REST API on port 8080 (configurable via `WEB_PORT`). All endpoints return JSON.

Base URL: `http://<your-server-ip>:8080`

---

## Endpoints

### `GET /api/status`

Bot status, uptime, next run time, and current config values.

**Response**
```json
{
  "version": "5.0.6",
  "uptime": "2d 4h 32m",
  "next_run": "2026-03-14 05:45 AM",
  "schedule": ["05:45"],
  "default_retention": 7,
  "log_level": "INFO",
  "warn_unconfigured": false,
  "report_frequency": "monthly",
  "log_max_files": 7
}
```

---

### `GET /api/stats`

Full stats payload — all-time, rolling 30-day, monthly, and per-channel breakdown.

**Response**
```json
{
  "all_time": {
    "runs": 142,
    "deleted": 18423,
    "channels": {
      "1234567890": {
        "name": "radarr-movies",
        "count": 2171,
        "category": "Radarr"
      }
    }
  },
  "rolling_30": {
    "runs": 30,
    "deleted": 3821,
    "channels": {},
    "reset": "2026-02-13"
  },
  "monthly": {
    "runs": 13,
    "deleted": 1654,
    "channels": {},
    "reset": "2026-03-01"
  },
  "last_month": {
    "runs": 28,
    "deleted": 3102,
    "reset": "2026-02-01"
  }
}
```

---

### `GET /api/last_run`

Summary of the most recent cleanup run.

**Response**

```json
{
  "timestamp": "2026-04-01 03:00:12",
  "triggered_by": "schedule",
  "duration": "1m 42s",
  "total_deleted": 211,
  "channels_checked": 18,
  "rate_limits": 0,
  "status": "ok",
  "categories": [
    { "name": "Radarr", "count": 134 },
    { "name": "Sonarr", "count": 77 }
  ]
}
```

| Status | Meaning |
|--------|---------|
| `200` | Run data returned |
| `404` | No runs recorded yet |

---

### `GET /api/schedule`

Current scheduled run times and next run.

**Response**
```json
{
  "schedule": ["03:00", "15:00"],
  "next_run": "2026-03-14 15:00 PM"
}
```

---

### `GET /api/channels`

All configured channels with category, retention, and deep clean info.

**Response**
```json
{
  "guild": "My Server",
  "total": 45,
  "channels": [
    {
      "id": 1234567890123456789,
      "name": "radarr-movies",
      "category": "Radarr",
      "days": 7,
      "is_override": false,
      "deep_clean": false
    },
    {
      "id": 9876543210987654321,
      "name": "crowdsec",
      "category": "Standalone",
      "days": 4,
      "is_override": true,
      "deep_clean": false
    }
  ]
}
```

**Error responses**

| Status | Meaning |
|--------|---------|
| `503` | Bot not ready yet |

---

### `GET /api/logs/latest`

Last N lines of the most recent log file.

**Query parameters**

| Parameter | Default | Max | Description |
|-----------|---------|-----|-------------|
| `lines` | `50` | `500` | Number of log lines to return |

**Example**
```
GET /api/logs/latest?lines=100
```

**Response**
```json
{
  "log_file": "cleanup-2026-03-14.log",
  "lines_returned": 100,
  "lines": [
    "2026-03-14 03:00:01 [INFO] discord-cleanup: Starting cleanup run on My Server...",
    "2026-03-14 03:00:03 [INFO] discord-cleanup:   ✅ #radarr-movies — deleted 12 message(s)",
    "..."
  ]
}
```

**Error responses**

| Status | Meaning |
|--------|---------|
| `500` | Could not read log file |

---

### `GET /api/run_status`

Returns whether a cleanup run is currently in progress.

**Response**
```json
{
  "run_in_progress": false
}
```

---

### `POST /run/full`

Trigger a full cleanup run on all configured channels.

**Response**
```json
{
  "success": true,
  "message": "Full cleanup run started — check the log channel for results"
}
```

**Error responses**

| Status | Body | Meaning |
|--------|------|---------|
| `409` | `"A cleanup run is already in progress"` | Run already running |
| `503` | `"Bot is not ready yet"` | Bot still starting up |
| `503` | `"Bot is not in any guilds"` | Bot not connected to a server |

---

### `POST /run/channel`

Trigger a cleanup run on a single configured channel.

**Form parameters**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `channel_id` | ✅ | Discord channel ID (integer) |

**Example**
```bash
curl -X POST http://192.168.1.4:8080/run/channel \
  -d "channel_id=1234567890123456789"
```

**Response**
```json
{
  "success": true,
  "message": "Cleanup started for #radarr-movies — check the log channel for results"
}
```

**Error responses**

| Status | Body | Meaning |
|--------|------|---------|
| `400` | `"Invalid channel ID"` | Non-integer channel_id |
| `404` | `"Channel not found in configured channels"` | Channel not in channels.yml |
| `409` | `"A cleanup run is already in progress"` | Run already running |
| `503` | `"Bot is not ready yet"` | Bot still starting up |

---

## Homepage Widget Example

```yaml
- Discord Cleanup Bot:
    href: http://192.168.1.4:8080
    description: Automated Discord message cleanup
    widget:
      type: customapi
      url: http://192.168.1.4:8080/api/status
      refreshInterval: 60000
      mappings:
        - field: version
          label: Version
        - field: uptime
          label: Uptime
        - field: next_run
          label: Next Run
```

---

## Notes

- All `POST` endpoints accept `application/x-www-form-urlencoded` (standard HTML form encoding)
- Run triggers are async — the response returns immediately and the run executes in the background
- The API has no authentication — secure access at the network level if needed

---

### `POST /api/stats/reset`

Reset stats for a given scope.

**Form parameters**

| Parameter | Required | Values | Description |
|-----------|----------|--------|-------------|
| `scope` | ✅ | `rolling`, `monthly`, `all` | Which stats period to reset |

**Response**
```json
{
  "success": true,
  "message": "Rolling 30 Days stats have been reset"
}
```

**Error responses**

| Status | Body | Meaning |
|--------|------|---------|
| `400` | `"Invalid scope"` | scope not one of rolling, monthly, all |

---

### `GET /api/health`

Simple health check for uptime monitoring tools like Uptime Kuma.

**Response**
```json
{
  "status": "ok",
  "version": "5.0.53"
}
```

---

### `GET /api/channels/unconfigured`

List of Discord channels not in channels.yml.

**Response**
```json
{
  "guild": "My Server",
  "total": 3,
  "channels": [
    {
      "id": 1234567890123456789,
      "name": "general",
      "category": "Text Channels"
    }
  ]
}
```

**Error responses**

| Status | Meaning |
|--------|---------|
| `503` | Bot not ready |

---

### `GET /api/logs`

List all available log files with name, date and size.

**Response**
```json
{
  "total": 7,
  "files": [
    {
      "filename": "cleanup-2026-03-18.log",
      "date": "2026-03-18",
      "size_kb": 12.4
    }
  ]
}
```

---

### `GET /api/logs/<filename>`

Fetch a specific log file by name.

**Query parameters**

| Parameter | Default | Max | Description |
|-----------|---------|-----|-------------|
| `lines` | `200` | `500` | Number of lines to return |

**Example**
```
GET /api/logs/cleanup-2026-03-18.log?lines=100
```

**Response**
```json
{
  "log_file": "cleanup-2026-03-18.log",
  "lines_returned": 100,
  "lines": ["2026-03-18 05:45:00 [INFO] discord-cleanup: ..."]
}
```

**Error responses**

| Status | Meaning |
|--------|---------|
| `404` | Log file not found |
| `500` | Could not read log file |
