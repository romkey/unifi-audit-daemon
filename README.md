# UniFi Protect audit daemon

[![CI](https://github.com/romkey/unifi-audit-daemon/actions/workflows/ci.yaml/badge.svg)](https://github.com/romkey/unifi-audit-daemon/actions/workflows/ci.yaml)
[![Lint](https://github.com/romkey/unifi-audit-daemon/actions/workflows/lint.yaml/badge.svg)](https://github.com/romkey/unifi-audit-daemon/actions/workflows/lint.yaml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/romkey/unifi-audit-daemon?color=blue)](https://github.com/romkey/unifi-audit-daemon/releases)

A small daemon that keeps a complete, append-only audit log of UniFi
Protect's events, and every night takes a contact sheet: one still from
every camera on a single JPEG. Protect's official Integration API only
streams events live, so anything that happens while a listener is down is
lost. This daemon uses Protect's private API instead, which can list past
events, so it catches up after downtime. It serves the log and the sheets
over a small HTTP API, and [Member Zone](https://github.com/pdxhackerspace/member-zone)
pulls the log into its audit logs with a bundled program. No database:
the log is JSON Lines files on a volume. The contact sheets are kept in a
separate directory, with an optional token of their own, so they can be
shared without exposing the audit log.

> **AI disclosure:** This project was written by AI under human oversight,
> following [these rules](https://github.com/romkey/python-ai-rules/blob/main/RULES.md).

## What's here

| Path | What it is |
|------|------------|
| `src/unifi_audit_daemon/config.py` | Settings from environment variables |
| `src/unifi_audit_daemon/protect.py` | Talks to Protect through [uiprotect](https://github.com/uilibs/uiprotect) |
| `src/unifi_audit_daemon/sync.py` | Reads new events and turns them into audit records |
| `src/unifi_audit_daemon/store.py` | The audit log on disk |
| `src/unifi_audit_daemon/contact_sheet.py` | Builds and keeps the nightly contact sheets |
| `src/unifi_audit_daemon/daemon.py` | The sync and contact-sheet loops and their schedule |
| `src/unifi_audit_daemon/server.py` | The HTTP API |
| `contrib/member-zone/unifi_protect_audit.py` | Member Zone audit log program that pulls from the daemon |
| `tests/` | pytest tests, with an in-memory Protect |
| `docs/` | [configuration](docs/configuration.md), [API](docs/api.md), [overview](docs/overview.md) |

## Technical details

**Sync.** Every `SYNC_INTERVAL` seconds the daemon signs in to the console
as a local UniFi OS user and reads `/proxy/protect/api/events` from where
it last stopped, less `SYNC_OVERLAP`, up to now, a day at a time. New
events are appended to `DATA_DIR/audit/YYYY-MM-DD.jsonl` with a readable
message ("Smart detect zone on Front Door: person", "Access by Alice: via
web") and Protect's raw event, and the position is saved in
`DATA_DIR/state.json`. On the first run it reaches back `BACKFILL_DAYS`.
Records are never changed or deleted. `EVENT_TYPES` limits what's
recorded; by default it's everything, motion included.

**Contact sheet.** At `CONTACT_SHEET_TIME` (default 03:00) in `TZ`, it
snapshots every adopted camera, lays them out in a labelled grid and
saves `CONTACT_SHEET_DIR/YYYY-MM-DD.jpg`. Offline or failed cameras
get a grey tile. Old sheets are deleted after
`CONTACT_SHEET_RETENTION_DAYS`.

**Keeping the audit log private.** The audit log and sync state live in
`DATA_DIR` (`/data`). The contact sheets live in `CONTACT_SHEET_DIR`
(`/contact-sheets`), a separate volume holding only the JPEGs, so it can be
mounted read-only into a web server. The daemon refuses to start if
`DATA_DIR` is inside `CONTACT_SHEET_DIR`. Over the API, `CONTACT_SHEET_TOKEN`
can list and download sheets and nothing else.

**API.** Bearer-token auth (`API_TOKEN`) on everything but `/healthz`; the
contact-sheet GETs also accept `CONTACT_SHEET_TOKEN`. Full details in
[docs/api.md](docs/api.md).

```
GET  /healthz
GET  /api/v1/status
GET  /api/v1/events?since=ISO8601       JSON Lines, by time recorded
GET  /api/v1/contact-sheets
GET  /api/v1/contact-sheets/latest.jpg  (or YYYY-MM-DD.jpg)
POST /api/v1/contact-sheets             take one now
```

**Member Zone.** Copy `contrib/member-zone/unifi_protect_audit.py` into
the `audit/` directory under Member Zone's `ACCESS_SCRIPTS_DIR`, make it
executable, and add an audit log source for it with `UNIFI_AUDIT_URL` and
`UNIFI_AUDIT_TOKEN` in its environment. Each run fetches what the daemon
recorded since Member Zone's newest entry (`AUDIT_LOG_SINCE`, less
`UNIFI_AUDIT_SLACK`) and prints it in Member Zone's JSON line format with
Protect's event id, so overlaps are de-duplicated. It uses only the
standard library. It replaces Member Zone's `unifi_protect.rb`, which
listens live and can't backfill.

**Planned.** Issuing UniFi OS API keys for Member Zone's credentials
manager.

## Running it

Requires Docker. Configuration comes from environment variables, kept in
`.env` (see [docs/configuration.md](docs/configuration.md)):

```bash
cp .env.example .env   # then edit it
docker compose -f docker-compose.prod.yaml up -d
```

That runs the latest release from `ghcr.io/romkey/unifi-audit-daemon`;
set `APP_VERSION=X.Y.Z` to pin one. The API answers on
http://localhost:8000. The audit log lives in the `data` volume (back it
up) and the contact sheets in the `contact-sheets` volume.

Put the daemon on a Docker network Member Zone can reach rather than
publishing the port widely; the API token is the only protection.

### With pip

Needs Python 3.14 or newer. Install into a virtual environment, from a
release's wheel:

```bash
python3 -m venv ~/.venvs/unifi-audit-daemon
~/.venvs/unifi-audit-daemon/bin/pip install https://github.com/romkey/unifi-audit-daemon/releases/download/vX.Y.Z/unifi_audit_daemon-X.Y.Z-py3-none-any.whl
~/.venvs/unifi-audit-daemon/bin/unifi-audit-daemon --version
```

Set its environment variables yourself (see
[docs/configuration.md](docs/configuration.md)); it doesn't read `.env`.

## Development

With Docker, nothing else to install:

```bash
docker compose up --build                                  # run from the working tree
docker compose -f docker-compose.test.yaml run --rm test   # tests with coverage
docker compose -f docker-compose.lint.yaml run --rm lint   # every lint check, as CI runs them
```

With [uv](https://docs.astral.sh/uv/), which installs the exact versions
CI tests (`uv.lock`):

```bash
uv sync
uv run pytest
uv run mypy
uv run unifi-audit-daemon
```

With pip (25.1 or newer, for `--group`). pip doesn't read `uv.lock`, so it
may pick newer dependency versions than CI tested:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e . --group dev
pytest
mypy
unifi-audit-daemon
```

Either way, `uvx pre-commit install` (or `pip install pre-commit &&
pre-commit install`) runs the lint checks on every commit.

## License

[MIT](LICENSE)
