# Discord Cleanup Bot

An automated Discord bot that cleans up old messages from configured channels on a schedule. Built for homelab setups running services like Plex, Radarr, Sonarr, and similar tools that generate frequent notifications.

> **Just want to run the bot?** See [SETUP.md](SETUP.md) for a quick start guide.

---

## Repository Structure

```
/
├── cleanup_bot.py                  # Entry point — bot setup, tasks, events
├── config.py                       # Constants, env loading, file creation, logging
├── stats.py                        # Stats load, save, update, reset
├── utils.py                        # Health, uptime, next run, log setup, reload
├── notifications.py                # Discord embed notifications
├── cleanup.py                      # Core cleanup logic, channel map, validation
├── commands.py                     # All slash commands and StatsResetView
├── healthcheck.py                  # Docker health check script
├── entrypoint.sh                   # PUID/PGID entrypoint script
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Docker image definition
├── VERSION                         # Current version number
├── docker-compose.discord_cleanup.yml
└── .github/
    ├── dependabot.yml
    └── workflows/
        ├── docker-publish.yml      # Build, test, and push workflow
        ├── discord-notify.yml      # Build success/failure notifications
        ├── dependabot-notify.yml   # Dependabot PR notifications
        ├── pr-notify.yml          # PR opened/merged/closed notifications
        └── github-notify.yml       # Stars, forks, and issue notifications
```

---

## CI/CD Pipeline

Every push to `main` triggers `docker-publish.yml` which:

1. Runs `py_compile` syntax check — blocks build on syntax errors
2. Runs `ruff` lint check — warns on style issues, build continues
3. Runs `bandit` security check — warns on security issues, build continues
4. Auto-bumps the version based on commit message tags:
   - Default — patch bump (e.g. `3.1.1` → `3.1.2`)
   - `#minor` in commit message — minor bump (e.g. `3.1.1` → `3.2.0`) — also creates a GitHub Release
   - `#major` in commit message — major bump (e.g. `3.1.1` → `4.0.0`) — also creates a GitHub Release
5. Builds and pushes Docker image to GHCR with `:latest` and `:version` tags
6. Creates a GitHub Release (minor and major only)
7. Cleans up old GHCR images keeping the last 10
8. Posts a success or failure notification to Discord

Pushes that only modify `README.md`, `SETUP.md`, `dependabot.yml`, `.gitignore`, or `.dockerignore` are skipped entirely — no build, no version bump, no release.

---

## Commit Message Conventions

| Commit message | Version bump | GitHub Release |
|----------------|-------------|----------------|
| Any message | Patch (e.g. `3.1.1` → `3.1.2`) | ❌ |
| Contains `#minor` | Minor (e.g. `3.1.1` → `3.2.0`) | ✅ |
| Contains `#major` | Major (e.g. `3.1.1` → `4.0.0`) | ✅ |

**When to use each:**

- **Patch** — bug fixes, log improvements, formatting tweaks
- **Minor** — new features, new `.env` variables, new `channels.yml` options, new slash commands
- **Major** — breaking changes that require updates to `.env` or `channels.yml`

---

## GitHub Notifications

Four separate workflows post to a Discord webhook (`DISCORD_WEBHOOK_URL` secret):

- **`discord-notify.yml`** — fires after `docker-publish.yml` completes, posts build success or failure with version, commit message, author, duration, run link, and commit SHA
- **`dependabot-notify.yml`** — fires when Dependabot opens or merges a PR
- **`pr-notify.yml`** — fires when any non-Dependabot PR is opened, reopened, merged, or closed without merging
- **`github-notify.yml`** — fires on stars, forks, and new issues

### Required Secret

| Secret | Description |
|--------|-------------|
| `DISCORD_WEBHOOK_URL` | Discord webhook URL for build and PR notifications |

---

## Docker Image Labels

The image is built with the following OCI labels:

| Label | Value |
|-------|-------|
| `org.opencontainers.image.version` | Version number e.g. `3.4.1` |
| `org.opencontainers.image.created` | Build timestamp |
| `org.opencontainers.image.title` | `Discord Cleanup Bot` |
| `org.opencontainers.image.description` | `Automated Discord message cleanup bot` |
| `org.opencontainers.image.source` | GitHub repo URL |
| `org.opencontainers.image.authors` | `antwanchild` |

---

## Dependabot

Dependabot is configured to check for updates weekly across three ecosystems:

- `pip` — Python dependencies
- `github-actions` — GitHub Actions versions
- `docker` — Base image updates

---

## Version History

See [Releases](../../releases) for full changelog.
