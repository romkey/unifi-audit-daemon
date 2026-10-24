# Configuration

The app reads these environment variables at startup. Put them in `.env`
(copy `.env.example`); compose passes it to the container. An invalid
value stops the app with a message naming the variable.

## UniFi Protect

| Variable | Default | Meaning |
|----------|---------|---------|
| `UNIFI_HOST` | required | The UniFi OS console running Protect: address or host name |
| `UNIFI_PORT` | `443` | The console's HTTPS port |
| `UNIFI_USERNAME` | required | A **local** UniFi OS account (not a UI.com cloud account, which needs 2FA) that can view Protect. Use a dedicated account with the least Protect access that still shows the events you want. |
| `UNIFI_PASSWORD` | required | Its password |
| `UNIFI_VERIFY_SSL` | `true` | `false` accepts the console's self-signed certificate |

## Audit log

| Variable | Default | Meaning |
|----------|---------|---------|
| `DATA_DIR` | `data` (`/data` in the image) | Where the audit log and sync state live. Private: never serve it. Mount a volume here and back it up. |
| `SYNC_INTERVAL` | `60` | Seconds between syncs (5-86400) |
| `SYNC_OVERLAP` | `600` | Seconds each sync re-reads before the last one's end, to catch events Protect files late. Already-stored events are skipped. |
| `BACKFILL_DAYS` | `7` | Days of history to read on the very first sync (0-3650). Protect only has what its retention keeps. |
| `EVENT_TYPES` | all | Comma-separated Protect event types to record, e.g. `access,videoExported,nvrSettingsChanged`. The names are uiprotect's `EventType` values; an unknown one is an error. Empty records everything, including every motion and smart detection. |

## Contact sheet

| Variable | Default | Meaning |
|----------|---------|---------|
| `CONTACT_SHEET_DIR` | `contact-sheets` (`/contact-sheets` in the image) | Where the contact sheets, each night's full-resolution snapshots and thumbnails, and their browsing page (`index.html` and `assets/`) live, and nothing else, so this directory can be served on its own. It must not be `DATA_DIR` or contain it. |
| `CONTACT_SHEET_TOKEN` | none | A second bearer token that can only list and download contact sheets, to share with their viewers instead of `API_TOKEN`. At least 16 characters; must differ from `API_TOKEN`. |
| `CONTACT_SHEET_TIME` | `03:00` | Local time, `HH:MM`, to take the nightly sheet |
| `TZ` | system zone (UTC in a container) | IANA time zone, e.g. `America/Los_Angeles`, for `CONTACT_SHEET_TIME` and the sheet's date |
| `CONTACT_SHEET_RETENTION_DAYS` | `30` | Days of sheets (and their snapshots) to keep; `0` keeps them all |
| `CONTACT_SHEET_TILE_WIDTH` | `640` | Width in pixels of each camera's picture on the sheet, and of its thumbnail (64-3840). Snapshots are always taken at full resolution. |
| `CONTACT_SHEET_COLUMNS` | `0` | Cameras per row; `0` picks a near-square grid |

## HTTP API

| Variable | Default | Meaning |
|----------|---------|---------|
| `API_TOKEN` | required | Bearer token every `/api/` request must send; at least 16 characters |
| `HOST` | `127.0.0.1` (`0.0.0.0` in the image) | Address to listen on |
| `PORT` | `8000` | Port to listen on |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |

## Member Zone program (not read by the daemon)

Set on the Member Zone audit log source that runs
`contrib/member-zone/unifi_protect_audit.py`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `UNIFI_AUDIT_URL` | required | The daemon's base URL, e.g. `http://unifi-audit-daemon:8000` |
| `UNIFI_AUDIT_TOKEN` | required | The daemon's `API_TOKEN` |
| `UNIFI_AUDIT_SLACK` | `3600` | Seconds to reach back before `AUDIT_LOG_SINCE`, for clock differences |
| `UNIFI_AUDIT_CA_FILE` | none | PEM file to trust if the daemon is behind HTTPS with a private CA |
