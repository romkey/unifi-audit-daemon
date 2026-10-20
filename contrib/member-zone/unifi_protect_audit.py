#!/usr/bin/env python3
"""Member Zone audit log program: UniFi Protect, via unifi-audit-daemon.

Copy this file into the `audit/` directory under Member Zone's
ACCESS_SCRIPTS_DIR, make it executable, and add an audit log source that
runs it. It fetches what the daemon has recorded since Member Zone's
newest entry and prints one JSON line per event, in the format described
in Member Zone's docs/audit-logs.md. Member Zone de-duplicates by `id`, so
fetching overlapping ranges is harmless.

Environment (set on the audit log source):
    UNIFI_AUDIT_URL        required. The daemon, e.g. http://unifi-audit-daemon:8000
    UNIFI_AUDIT_TOKEN      required. The daemon's API_TOKEN.
    UNIFI_AUDIT_SLACK      optional. Seconds to reach back before
                           AUDIT_LOG_SINCE, for clock differences between
                           the console and the daemon (default 3600).
    UNIFI_AUDIT_CA_FILE    optional. PEM file to trust, if the daemon is
                           behind HTTPS with a private CA.
    AUDIT_LOG_SINCE        set by Member Zone: its newest entry's time.

Exit codes: 0 success, 1 failure (daemon unreachable, bad token), 2 not
configured. Standard library only.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

TIMEOUT = 120
DEFAULT_SLACK = 3600


class NotConfiguredError(Exception):
    pass


def events_url(env: Mapping[str, str]) -> str:
    base = env.get("UNIFI_AUDIT_URL", "").strip().rstrip("/")
    if not base:
        raise NotConfiguredError("UNIFI_AUDIT_URL is not set")
    if not base.startswith(("http://", "https://")):
        raise NotConfiguredError("UNIFI_AUDIT_URL must start with http:// or https://")
    url = f"{base}/api/v1/events"

    since_text = env.get("AUDIT_LOG_SINCE", "").strip()
    if not since_text:
        return url
    slack_text = env.get("UNIFI_AUDIT_SLACK", "").strip() or str(DEFAULT_SLACK)
    try:
        slack = timedelta(seconds=int(slack_text))
    except ValueError:
        raise NotConfiguredError("UNIFI_AUDIT_SLACK must be a number") from None
    since = datetime.fromisoformat(since_text)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    query = urllib.parse.urlencode({"since": (since - slack).isoformat()})
    return f"{url}?{query}"


def fetch(env: Mapping[str, str]) -> Iterator[dict[str, Any]]:
    token = env.get("UNIFI_AUDIT_TOKEN", "").strip()
    if not token:
        raise NotConfiguredError("UNIFI_AUDIT_TOKEN is not set")
    request = urllib.request.Request(  # noqa: S310 - scheme checked in events_url
        events_url(env), headers={"Authorization": f"Bearer {token}"}
    )
    ca_file = env.get("UNIFI_AUDIT_CA_FILE", "").strip() or None
    context = ssl.create_default_context(cafile=ca_file)
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:  # noqa: S310
        for line in response:
            if line.strip():
                yield json.loads(line)


def to_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    """A daemon record in Member Zone's audit log line format."""
    camera = record.get("camera") or {}
    user = record.get("user") or {}
    entry = {
        "timestamp": record["start"],
        "message": record["message"],
        "id": record["id"],
        "type": record.get("type"),
        "end": record.get("end"),
        "camera": camera.get("name") or camera.get("id"),
        "user": user.get("name") or user.get("id"),
    }
    return {k: v for k, v in entry.items() if v is not None}


def write(records: Iterable[Mapping[str, Any]], out: TextIO) -> int:
    count = 0
    for record in records:
        out.write(json.dumps(to_entry(record)) + "\n")
        count += 1
    return count


def main(env: Mapping[str, str] = os.environ) -> int:
    try:
        count = write(fetch(env), sys.stdout)
    except NotConfiguredError as e:
        print(f"not configured: {e}", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as e:
        print(f"unifi-audit-daemon answered {e.code} {e.reason}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"could not read from unifi-audit-daemon: {e}", file=sys.stderr)
        return 1
    print(f"{count} entries", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
