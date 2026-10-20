"""Copy Protect's event history into the audit store.

Each pass reads from the saved cursor (less SYNC_OVERLAP, for events
Protect files late) up to now, a day at a time, and appends the events it
hasn't stored yet. After downtime it simply has a longer window to read.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from unifi_audit_daemon.protect import Names, ProtectSource, RawEvent
from unifi_audit_daemon.store import AuditStore, Record, SyncState

logger = logging.getLogger(__name__)

CHUNK = timedelta(days=1)


def from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def describe(event: RawEvent, names: Names) -> str:
    """One line saying what happened, e.g. "Smart detect zone on Front: person"."""
    words = re.sub(r"(?<=[a-z0-9])([A-Z])", r" \1", str(event.get("type", "event")))
    parts = [words[:1].upper() + words[1:].lower()]

    if camera := event.get("camera"):
        parts.append(f"on {names.cameras.get(camera, camera)}")
    if user := event.get("user"):
        parts.append(f"by {names.users.get(user, user)}")
    message = " ".join(parts)

    details: list[str] = []
    if smart := event.get("smartDetectTypes"):
        details.append(", ".join(str(s) for s in smart))
    # System events (adoptions, settings changes...) carry their particulars
    # as metadata entries of the form {"name": {"text": "UP DoorLock"}}.
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if isinstance(value, dict) and isinstance(value.get("text"), str):
                details.append(f"{key}={value['text']}")
            elif key == "clientPlatform" and isinstance(value, str):
                details.append(f"via {value}")
    return f"{message}: {'; '.join(details)}" if details else message


def to_record(event: RawEvent, names: Names, recorded_at: datetime) -> Record:
    camera = event.get("camera")
    user = event.get("user")
    end = event.get("end")
    return {
        "id": event["id"],
        "type": event.get("type"),
        "start": from_ms(event["start"]).isoformat(),
        "end": from_ms(end).isoformat() if isinstance(end, int) else None,
        "recorded_at": recorded_at.isoformat(),
        "message": describe(event, names),
        "camera": {"id": camera, "name": names.cameras.get(camera)} if camera else None,
        "user": {"id": user, "name": names.users.get(user)} if user else None,
        "event": event,
    }


@dataclass
class SyncStatus:
    last_success: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    last_added: int = 0


class Syncer:
    def __init__(
        self,
        source: ProtectSource,
        store: AuditStore,
        *,
        overlap: timedelta,
        backfill: timedelta,
        event_types: tuple[str, ...] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.source = source
        self.store = store
        self.overlap = overlap
        self.backfill = backfill
        self.event_types = event_types
        self.clock = clock
        self.status = SyncStatus()

    async def sync_once(self) -> int:
        """Read new events from Protect and store them; returns how many."""
        now = self.clock()
        cursor = self.store.load_state().cursor or now - self.backfill
        start = cursor - self.overlap
        seen = self.store.known_ids(start)
        names = await self.source.names()
        added = 0

        while start < now:
            end = min(start + CHUNK, now)
            events = await self.source.events(start, end, self.event_types)
            records = []
            for event in sorted(events, key=lambda e: int(e["start"])):
                if event["id"] in seen:
                    continue
                seen.add(event["id"])
                records.append(to_record(event, names, self.clock()))
            self.store.append(records)
            # Save progress chunk by chunk, so a long backfill that fails
            # part way resumes where it stopped.
            self.store.save_state(SyncState(cursor=end))
            added += len(records)
            start = end

        self.status.last_success = now
        self.status.last_added = added
        if added:
            logger.info("stored %d new events", added)
        return added
