# Discord Cleanup Bot — Agent Guide

## Project

This is a Python Discord bot that removes old messages on a schedule, with a
Flask admin UI, JSON API, statistics, backups, and Discord reports. Docker is the
primary deployment target. Use Python 3.11 to match CI.

Read `README.md` for development and releases, `docs/SETUP.md` for configuration
and deployment, `docs/API.md` for endpoints, and `SECURITY.md` for security guidance.

## Code map

- `cleanup_bot.py`: bot startup and scheduled tasks.
- `cleanup.py`, `scheduler.py`: message cleanup and schedule calculations.
- `commands.py`, `commands_stats.py`: Discord slash commands.
- `config.py`: configuration, shared locks, logging, and startup file creation.
- `config_channels.py`, `config_settings.py`, `config_backups.py`,
  `config_utils.py`: configuration editing, validation, backups, and restore flows.
- `validation.py`, `file_utils.py`: input validation and atomic persistence.
- `stats.py`, `notifications.py`: statistics and scheduled reports.
- `web.py`, `api.py`, `admin.py`: page routes, read-only API, and admin mutations.
- `templates/`, `static/`: Flask UI templates and client assets.
- `tests/`: regression tests; `tests/support.py` provides isolated module imports
  with stub dependencies, and `tests/stats_support.py` provides stats helpers.
- `scripts/changelog.py`: release preparation and release-note extraction.
- `.github/workflows/`: CI, release preparation, publishing, and notifications.
- `.github/actions/discord-result/`: shared Discord workflow notification action
  and its jq release-note formatter.

## Working conventions

- Start work on a descriptive branch from the latest `origin/main`. Check the
  working tree first and preserve unrelated user changes.
- Keep changes focused and follow surrounding code style. Avoid unrelated
  refactors or dependency upgrades.
- Keep Discord work asynchronous; avoid blocking the bot's event loop. Preserve
  shared locking and atomic writes when changing configuration or statistics.
- Route new configuration options through validation and the appropriate config
  helpers. Keep defaults, example configuration, and user documentation aligned.
- Follow the existing authentication and request validation patterns for admin
  mutations. Keep the read-only API separate from admin actions.
- Configuration imports can create files under `/config` or exit during startup.
  Use the existing isolation helpers and temporary directories in tests rather
  than importing runtime configuration against real data.
- Do not run the live bot or send test Discord notifications unless requested.
  Use stubs and generated payload checks for local verification.
- Never commit tokens, webhook URLs, `.env.discord_cleanup`, or runtime data.

## Development and verification

Create a working virtual environment and install the pinned tools:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pre_commit install
```

Keep the environment active for commits and pushes. The hooks use `python` from
that environment. If an existing environment is broken, repair or recreate it
before relying on its checks; do not bypass hooks to conceal failures.

Useful checks:

```sh
python -m black --check --diff .
python -m ruff check --select F .
python -m pyright
python -m pytest -q --durations=10
python -m pre_commit run --all-files
```

Add regression coverage for behavior changes, including failure paths where
relevant. Use the existing unittest or pytest patterns and isolated imports.
For workflow changes, run `actionlint` when available and validate embedded shell
and jq expressions. Formatter tests need `jq`; install it to run those checks
rather than accepting their skipped results as validation.

Pre-commit hooks check whitespace, final newlines, YAML, Black, Ruff `F`, and
Pyright. The pre-push hook runs the full test suite, including documentation-only
pushes. CI validates workflows, formatting, types, and tests; its Ruff security
scan is advisory. Install test dependencies for type checking too, because tests
import pytest. Report checks actually run and any failures or limitations.

## Changelog and releases

- Meaningful changes include a concise, user-facing bullet under
  `## [Unreleased]` in `CHANGELOG.md`. Minor typo fixes can omit an entry.
- Use `### Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`,
  `Requirements`, `Performance`, or `Docs`, with `- ` bullets. Describe behavior
  and its effect on users; do not copy implementation details or commit subjects.
- Preserve existing historical entries and their original format.
- Ordinary feature and fix PRs do not bump `VERSION`. Release preparation is a
  manually started workflow that selects patch, minor, or major, moves curated
  notes into a dated `[vX.Y.Z]` section, and pushes a release branch for review.
- Only `VERSION` changes on `main` trigger image publishing and GitHub Releases.
  Empty notes, existing release tags, and invalid versions must fail clearly.
- `python scripts/changelog.py notes X.Y.Z` checks prepared release notes.
  `prepare X.Y.Z` edits both `VERSION` and `CHANGELOG.md`; preview it only in a
  disposable checkout unless preparing a release is part of the task.
- GitHub Releases use the version's changelog section. Discord summaries use
  `format-notes.jq` to remove raw headings, format category labels and bullets,
  and fit the embed field limit. Keep the full release notes intact.

## Pull requests and cleanup

Use `.github/pull_request_template.md` when creating a PR. Fill in Summary, Type
of change, Testing, and Checklist, with accurate validation results. Follow CI
through completion and fix failures on the same branch. Merge only when the user
requests it or has already authorized merging.

When cleanup is requested after merge, verify the PR is merged, fast-forward
local `main`, and delete the task's merged local and remote branches. Preserve
unrelated branches and work.
