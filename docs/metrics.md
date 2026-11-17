# Prometheus metrics

`GET /metrics` serves the daemon's metrics in Prometheus's text format
(`text/plain; version=0.0.4`). It needs a bearer token: `METRICS_TOKEN`,
if it's set, or `API_TOKEN`. Set `METRICS_TOKEN` and give Prometheus that
one: it reads `/metrics` and nothing else, not the audit log, the status
or the contact sheets.

The metrics carry counts, sizes and times only, never the content of an
audit record: no event ids, messages, camera or user names. The one name
in them is the Protect console's own (`unifi_audit_protect_console_info`).

## Scraping

```yaml
scrape_configs:
  - job_name: unifi-audit-daemon
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/unifi-audit-daemon.token
    static_configs:
      - targets: ["unifi-audit-daemon:8000"]
```

Counters start at zero on every restart; use `rate()` / `increase()`,
which handle that. Series that depend on a label value seen at run time
(an error type, an event type, an HTTP status) appear the first time it
happens.

## Jobs

The daemon runs two jobs, labelled `job="sync"` (read new events from
Protect, every `SYNC_INTERVAL`) and `job="contact_sheet"` (the nightly
sheet, or one asked for with `POST /api/v1/contact-sheets`).

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `unifi_audit_job_runs_total` | counter | `job`, `trigger`, `result` | Finished runs. `trigger` is `scheduled`, or `manual` for a sheet asked for over the API; `result` is `success` or `failure`. A run cut short by shutdown isn't counted. |
| `unifi_audit_job_errors_total` | counter | `job`, `error` | Failed runs by the exception's class, e.g. `NvrError`, `TimeoutError`, `ClientConnectorError`. |
| `unifi_audit_job_duration_seconds` | histogram | `job` | How long runs took, success or failure (buckets 0.1 s to 30 min). |
| `unifi_audit_job_running` | gauge | `job` | 1 while a run is in progress. |
| `unifi_audit_job_last_success_timestamp_seconds` | gauge | `job` | Unix time of the last successful run. Absent until there's been one. |
| `unifi_audit_job_last_failure_timestamp_seconds` | gauge | `job` | Unix time of the last failed run. |
| `unifi_audit_job_next_run_timestamp_seconds` | gauge | `job` | When the job next runs on its own. |

## Sync and the audit log

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `unifi_audit_sync_events_stored_total` | counter | `type` | New events appended to the log, by Protect event type (`motion`, `access`, `smartDetectZone`...). |
| `unifi_audit_sync_events_fetched_total` | counter | | Events read from Protect, including ones skipped as already stored (the `SYNC_OVERLAP` re-reads). |
| `unifi_audit_sync_last_added` | gauge | | Events the last successful sync stored. |
| `unifi_audit_sync_cursor_timestamp_seconds` | gauge | | How far Protect has been read. `time() -` this is how far behind the log is. |
| `unifi_audit_audit_log_files` | gauge | | Files in the log (one per UTC day recorded). |
| `unifi_audit_audit_log_bytes` | gauge | | Their total size. |

## Protect

From the bootstrap each sync reads.

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `unifi_audit_protect_console_info` | gauge | `name`, `version` | Always 1; the console's name and Protect version. Empty labels until the first sync. |
| `unifi_audit_protect_users` | gauge | | Users Protect knows. |
| `unifi_audit_protect_cameras` | gauge | | Cameras Protect knows. |

## Contact sheets

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `unifi_audit_contact_sheet_cameras` | gauge | `result` | Cameras on the latest sheet: `ok` (snapshot taken), `offline`, `failed` (no usable snapshot). |
| `unifi_audit_contact_sheet_snapshots_total` | counter | `result` | The same, summed over every sheet taken. |
| `unifi_audit_contact_sheet_requests_total` | counter | | Sheets asked for with `POST /api/v1/contact-sheets`. |
| `unifi_audit_contact_sheets` | gauge | | Sheets kept in `CONTACT_SHEET_DIR`. |
| `unifi_audit_contact_sheet_dir_bytes` | gauge | | Size of everything in `CONTACT_SHEET_DIR`: sheets, snapshots, thumbnails, page. |

## HTTP API

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `unifi_audit_http_requests_total` | counter | `method`, `route`, `status` | Requests answered. `route` is the endpoint (`/api/v1/contact-sheets/{sheet}.jpg` for every sheet image, `other` for unknown paths); `method` is `GET`, `POST` or `other`. 401s show failed authentication. |
| `unifi_audit_http_request_duration_seconds` | histogram | `route` | Time to answer, including streaming `/api/v1/events`. |

## Process

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `unifi_audit_build_info` | gauge | `version`, `python_version` | Always 1. |
| `unifi_audit_start_time_seconds` | gauge | | When the daemon started. |
| `unifi_audit_uptime_seconds` | gauge | | Seconds since then. |
| `unifi_audit_process_cpu_seconds_total` | counter | `mode` | CPU time, `user` and `system`. |
| `unifi_audit_process_max_resident_memory_bytes` | gauge | | Peak resident memory. |
| `unifi_audit_process_open_fds` | gauge | | Open file descriptors (Linux only). |
| `unifi_audit_process_threads` | gauge | | Python threads. |

## Example alerts

```yaml
groups:
  - name: unifi-audit-daemon
    rules:
      - alert: UnifiAuditSyncStale
        # The log is more than 15 minutes behind Protect.
        expr: time() - unifi_audit_sync_cursor_timestamp_seconds > 900
        for: 5m
      - alert: UnifiAuditSyncFailing
        expr: increase(unifi_audit_job_runs_total{job="sync",result="failure"}[30m]) > 5
      - alert: UnifiAuditContactSheetMissed
        expr: time() - unifi_audit_job_last_success_timestamp_seconds{job="contact_sheet"} > 26 * 3600
      - alert: UnifiAuditCamerasWithoutSnapshot
        expr: unifi_audit_contact_sheet_cameras{result!="ok"} > 0
```
