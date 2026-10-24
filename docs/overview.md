# unifi-audit-daemon overview

**What:** keeps a complete, append-only audit log of UniFi Protect's
events, and takes a nightly contact sheet of every camera.
**Runs as:** a daemon with an HTTP API on port 8000.
**Talks to:** UniFi Protect's private API (via uiprotect, signed in as a
local user); serves Member Zone, which pulls the log with
`contrib/member-zone/unifi_protect_audit.py`. No database.

## How the sync stays complete

The private API can list past events, so downtime is caught up: each sync
reads from the saved cursor (less `SYNC_OVERLAP`) to now, a day at a time,
skips ids already stored, and saves the cursor after every day. Records
are stamped `recorded_at` and appended in that order, and clients ask for
"recorded since", so a late backfill is never behind a client's position.
Delivery is at-least-once (a crash between append and cursor save re-reads
a window; ids dedupe it); clients should dedupe by `id` too.

## Files

- `src/unifi_audit_daemon/__main__.py`: command line, logging, wiring, SIGTERM.
- `config.py`: `Settings` from environment variables.
- `protect.py`: `ProtectSource` protocol; `UiProtect` wraps uiprotect.
- `store.py`: `AuditStore`: `DATA_DIR/audit/*.jsonl` and `state.json`.
  Private; never in `CONTACT_SHEET_DIR`.
- `sync.py`: `Syncer` and the event → record / message conversion.
- `contact_sheet.py`: grid composition (Pillow), thumbnails, and
  `ContactSheets` in `CONTACT_SHEET_DIR` (sheets, each night's
  full-resolution snapshots), which may be served semi-publicly.
- `contact_sheet_index.py`: the static `index.html` for browsing them.
- `vendor/glightbox/`: the page's lightbox (MIT), unchanged from npm and
  copied into `CONTACT_SHEET_DIR/assets/` at startup.
- `daemon.py`: sync and contact-sheet loops; `next_run` scheduling.
- `server.py`: HTTP API (docs/api.md).
- `contrib/member-zone/`: stdlib program Member Zone runs as an audit source.
- `tests/`: pytest; `tests/fakes.py` is an in-memory Protect.
- `Dockerfile`: `dev` and `production` stages; `/data` (private) and
  `/contact-sheets` (shareable) volumes.

## Conventions

- All configuration from environment variables; see docs/configuration.md.
- Everything outside `protect.py` uses `ProtectSource`, never uiprotect.
- Audit records are never rewritten or deleted.
- The audit log and the contact sheets never share a directory or a token:
  sheets may be shared, the log must not be.
- Rules: https://github.com/romkey/python-ai-rules/blob/main/RULES.md

## Planned

- Issuing UniFi OS API keys for Member Zone's credentials manager
  (uiprotect's `create_api_key`), as a `contrib/member-zone/` credential
  program backed by new API endpoints.
