# Discord Cleanup Bot

An automated Discord bot that cleans up old messages from configured channels on a schedule. Built for homelab setups running services like Plex, Radarr, Sonarr, and similar tools that generate frequent notifications.

> **Just want to run the bot?** See [SETUP.md](docs/SETUP.md) for a quick start guide.
> **API reference?** See [API.md](docs/API.md) for all endpoints, parameters, and response examples.

---

## Repository Structure

```
/
├── cleanup_bot.py                  # Entry point — bot setup, tasks, events
├── config.py                       # Constants, env loading, file creation, logging
├── stats.py                        # Stats load, save, update, reset, migration
├── utils.py                        # Health, uptime, next run, log setup, env updates
├── notifications.py                # Discord embed notifications
├── cleanup.py                      # Core cleanup logic, channel map, validation
├── commands.py                     # Core slash commands — run, status, reload, logs, etc.
├── commands_stats.py               # Stats slash commands — view, reset
├── config_backups.py               # Backup discovery, env snapshots, restore helpers
├── config_channels.py              # channels.yml reload, preview, save, restore helpers
├── config_settings.py              # Runtime env update helpers for config settings
├── web.py                          # Flask web UI — page routes and server thread
├── api.py                          # Flask Blueprint — read-only /api/* endpoints
├── admin.py                        # Flask Blueprint — mutating /admin/* endpoints
├── file_utils.py                   # Atomic text/JSON file helpers
├── validation.py                   # Shared validation for env values and channels.yml
├── templates/                      # Jinja2 HTML templates for web UI
│   ├── base.html                   # Base layout — nav, theme system, toast
│   ├── index.html                  # Dashboard — status, stats, run controls
│   ├── config.html                 # Config editor — settings and channels.yml
│   ├── schedule.html               # Schedule management
│   ├── stats.html                  # Statistics — summary, detail, grouped views
│   └── logs.html                   # Log viewer
├── tests/                          # Regression coverage for config, schedule, and locks
├── healthcheck.py                  # Docker health check script
├── entrypoint.sh                   # PUID/PGID entrypoint script
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Docker image definition
├── VERSION                         # Current version number
├── LICENSE                         # MIT License
├── SECURITY.md                     # Security policy and vulnerability reporting
├── channels.example.yml            # Example channels.yml configuration
├── discord_cleanup.xml             # Unraid Docker template
├── icon.png                        # Bot icon
├── docs/
│   ├── SETUP.md                    # Setup and configuration guide
│   └── API.md                      # API reference
└── .github/
    ├── dependabot.yml
    ├── pull_request_template.md
    ├── ISSUE_TEMPLATE/
    │   ├── bug_report.yml
    │   └── feature_request.yml
    └── workflows/
        ├── ci.yml                     # Validation on PRs and feature-branch pushes
        ├── release-prep.yml            # Create a release branch and prep VERSION/CHANGELOG
        ├── docker-publish.yml          # Build and publish workflow after merge
        ├── discord-notify.yml          # Build success/failure notifications
        ├── dependabot-notify.yml       # Dependabot PR notifications
        ├── dependabot-automerge.yml    # Auto-merge patch and minor Dependabot PRs
        ├── pr-notify.yml               # PR opened/merged/closed notifications
        └── github-notify.yml           # Stars, forks, and issue notifications
```

---

## CI/CD Pipeline

Feature branches and pull requests trigger validation workflows that:

1. Runs `actionlint` — validates all workflow files for syntax, expressions, and shellcheck compliance
2. Runs `py_compile` syntax checks — blocks the workflow on syntax errors
3. Runs `pytest` — keeps the regression suite green before merge
4. Runs `ruff` lint and security checks — warns on issues, build continues

Use `release-prep.yml` from the GitHub Actions tab when you are ready to create a release branch. It:

1. Reads the current `VERSION` from `main`
2. Bumps `VERSION` based on the selected patch, minor, or major release type
3. Prepends a new `CHANGELOG.md` entry from your optional summary input, one line per bullet
4. Creates and pushes a `release/<version>` branch for you

After that, open a pull request from the new release branch into `main` and merge it when you are ready.

Once that PR is merged into `main`, `docker-publish.yml` takes over:

1. Reads the merged `VERSION` and `CHANGELOG.md`
2. Builds and pushes the Docker image to GHCR with `:latest` and `:version` tags
3. Creates a GitHub Release for the merged version
4. Cleans up old GHCR images keeping the last 10
5. Posts a success or failure notification to Discord

Merges that only modify `README.md`, `CHANGELOG.md`, `docs/**`, `dependabot.yml`, `.gitignore`, or `.dockerignore` are skipped by the release workflow — no build, no version bump, no release.

Workflow linting runs in CI on pull requests and non-`main` pushes using `actionlint`, which validates YAML syntax, expression correctness, and shellcheck compliance across all `.github/workflows/` files.

---

## Local Formatting and Type Checks

The recommended local workflow is:

1. Install the hooks once:
   ```bash
   pre-commit install
   ```
2. Let `pre-commit` run Black automatically before each commit.
3. Let GitHub Actions verify formatting with the `Black` workflow and run type checks with `Pyright`.

Black is the formatter that rewrites Python files in place. The GitHub Black workflow is check-only, so formatting happens locally through `pre-commit` rather than in CI.

---

## Release Prep Conventions

The release-prep workflow is started manually from GitHub Actions, and you choose the bump type there:

- **Patch** — bug fixes, log improvements, formatting tweaks
- **Minor** — new features, new `.env` variables, new `channels.yml` options, new slash commands
- **Major** — breaking changes that require updates to `.env` or `channels.yml`

When `release-prep.yml` runs, it prepends a new `CHANGELOG.md` entry from the optional summary input, turning each line into a bullet. If you leave it blank, it falls back to a simple release note.

---

## GitHub Notifications

Five separate workflows post to a Discord webhook (`DISCORD_WEBHOOK_URL` secret):

- **`discord-notify.yml`** — fires after `docker-publish.yml` completes, posts build success or failure with version, commit message, author, duration, run link, and commit SHA
- **`dependabot-notify.yml`** — fires when Dependabot opens or merges a PR
- **`dependabot-automerge.yml`** — automatically approves and merges patch and minor Dependabot PRs, comments on major updates for manual review
- **`pr-notify.yml`** — fires when any non-Dependabot PR is opened, reopened, merged, or closed without merging
- **`github-notify.yml`** — fires on stars, forks, new issues, and issue comments


### Required Secret

| Secret | Description |
|--------|-------------|
| `DISCORD_WEBHOOK_URL` | Discord webhook URL for build and PR notifications |

---

## Web UI and API

The web UI can sit behind a reverse proxy such as Authentik, but it still defaults to binding on `0.0.0.0` so the published Docker port works out of the box. Mutating admin routes live under `/admin/*` while read-only routes live under `/api/*`. `channels.yml` backups and stats/last-run backups are pruned automatically after their configured retention windows, which default to 10 days. `channels.yml` backups live under `/config/backups/channels/`, while generated data backups live under `/config/data/backups/stats/` and `/config/data/backups/last-run/`. The Logs page now includes search and level filters for the visible log file.

The Config page supports preview-before-save for `channels.yml`, validate-before-save, per-report grouping controls, a dry run from the preview modal, and restoring `channels.yml` or `.env.discord_cleanup` from recent backups. Schema errors include line and column details when possible. Saving or restoring `channels.yml` creates a backup before replacing the live file, and `.env` backups are created when a `.env` setting is saved or restored. `channels.yml` also supports per-channel report overrides such as `report_group`, `report_individual`, and `report_exclude`, and the shipped [channels.example.yml](channels.example.yml) includes examples of each.

The Stats page includes a per-channel history timeline plus a click-through drilldown, a monthly report snapshot panel for checking the resolved Discord report data, the Schedule page supports blackout dates and weekday skips, and the Audit page provides a read-only retention review of the live cleanup configuration.

The dashboard also shows the active cleanup run owner when a run is in progress, which makes it easier to tell whether a scheduler, slash command, or web action is holding the cleanup lock. It now also surfaces the most recent startup path-check results and recent notification fallback activity. The Stats page shows the 10 most recent stats backups and the 10 most recent last-run backups, plus `channels.yml` backups, and the API exposes them at `GET /api/backups/stats`, `GET /api/backups/channels`, and `GET /api/monthly-report-source`.

Regression tests cover validation, schedule persistence, config reloads, and run-lock behavior. See the `tests/` directory for the current suite.

---

## Docker Image Labels

The image is built with the following OCI labels:

| Label | Value |
|-------|-------|
| `org.opencontainers.image.version` | Version number e.g. `4.5.0` |
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

Patch and minor updates are automatically merged via `dependabot-automerge.yml`. Major updates open a PR with a comment requesting manual review.

---

## Unraid

An Unraid Docker template is included at `discord_cleanup.xml`. To install on Unraid:

1. Go to **Docker → Add Container**
2. Paste the template URL in the **Template URL** field:
   ```
   https://raw.githubusercontent.com/antwanchild/discord_cleanup/main/discord_cleanup.xml
   ```
3. Fill in your bot token and channel IDs
4. Click **Apply**

---

## Version History

See [Releases](../../releases) for full changelog.
