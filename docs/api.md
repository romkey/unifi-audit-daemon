# HTTP API

Every path under `/api/` needs `Authorization: Bearer <API_TOKEN>`;
without it the answer is `401 {"error": "unauthorized"}`.

## `GET /healthz`

No token. `200 {"status": "ok", "version": "X.Y.Z"}` while the process is
up. Says nothing about Protect; see `/api/v1/status` for that.

## `GET /api/v1/status`

```json
{
  "version": "0.1.0",
  "sync": {
    "cursor": "2026-10-20T22:14:03+00:00",
    "last_success": "2026-10-20T22:14:03+00:00",
    "last_added": 3,
    "last_error": null,
    "last_error_at": null
  },
  "contact_sheet": {
    "next": "2026-10-21T03:00:00-07:00",
    "latest": "2026-10-20",
    "last_error": null
  }
}
```

`cursor` is how far Protect has been read. `last_error` is the latest
failure's message, cleared by the next success.

## `GET /api/v1/events?since=ISO8601`

The audit log as JSON Lines (`application/x-ndjson`), oldest recorded
first, streamed. `since` (optional; a time without a zone is UTC) returns
only records **recorded** at or after that time. That's when the daemon
stored them, not when they happened, so a client that remembers how far
it has read never misses an event backfilled after downtime. Bad `since`:
`400`.

One record:

```json
{
  "id": "6730b5af01029603e4453bdb",
  "type": "access",
  "start": "2026-10-20T21:02:11.418000+00:00",
  "end": null,
  "recorded_at": "2026-10-20T21:02:40.112903+00:00",
  "message": "Access by Alice: via web",
  "camera": null,
  "user": {"id": "8593657a25b7826a4288b6af", "name": "Alice"},
  "event": {"id": "6730b5af01029603e4453bdb", "type": "access", "...": "..."}
}
```

- `id` is Protect's event id, unique in the log.
- `start`/`end` are from Protect; `end` is null if the event hadn't ended
  when it was recorded. Records are never updated afterwards.
- `camera`/`user` carry the name Protect had for the id at the time (null
  name if unknown).
- `event` is Protect's raw event, kept whole.

## `GET /api/v1/contact-sheets`

`{"dates": ["2026-10-20", "2026-10-19"]}`: local dates with a sheet,
newest first.

## `GET /api/v1/contact-sheets/latest.jpg`, `GET /api/v1/contact-sheets/YYYY-MM-DD.jpg`

The JPEG, or `404`.

## `POST /api/v1/contact-sheets`

Take a contact sheet now, replacing today's: `202 {"status": "started"}`.
Watch `/api/v1/status` or the list for the result.
