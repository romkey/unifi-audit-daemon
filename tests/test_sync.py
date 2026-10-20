import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.fakes import T0, FakeSource, event
from unifi_audit_daemon.protect import Names
from unifi_audit_daemon.store import AuditStore, SyncState
from unifi_audit_daemon.sync import Syncer, describe, to_record

NAMES = Names(cameras={"cam1": "Front"}, users={"u1": "Alice"})


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make(
    tmp_path: Path, clock: Clock, event_types: tuple[str, ...] = ()
) -> tuple[FakeSource, Syncer]:
    source = FakeSource()
    syncer = Syncer(
        source,
        AuditStore(tmp_path),
        overlap=timedelta(minutes=10),
        backfill=timedelta(days=2),
        event_types=event_types,
        clock=clock,
    )
    return source, syncer


def test_describe_camera_event() -> None:
    raw = event("e1", T0, type="smartDetectZone") | {"smartDetectTypes": ["person"]}
    assert describe(raw, NAMES) == "Smart detect zone on Front: person"


def test_describe_user_and_system_metadata() -> None:
    raw = event("e1", T0, type="access", camera=None, user="u1") | {
        "metadata": {"clientPlatform": "web"}
    }
    assert describe(raw, NAMES) == "Access by Alice: via web"
    adopted = event("e2", T0, type="deviceAdopted", camera=None) | {
        "metadata": {
            "name": {"text": "UP DoorLock"},
            "adoptedBy": {"text": "Pat"},
            "host": {"text": None},
            "clickUrl": {"url": "protect/devices"},
        }
    }
    assert describe(adopted, NAMES) == "Device adopted: name=UP DoorLock; adoptedBy=Pat"


def test_describe_unknown_ids_fall_back_to_id() -> None:
    raw = event("e1", T0, camera="cam9", user="u9")
    assert describe(raw, NAMES) == "Motion on cam9 by u9"


def test_to_record() -> None:
    raw = event("e1", T0, user="u1") | {"end": None}
    recorded = to_record(raw, NAMES, T0 + timedelta(seconds=5))
    assert recorded == {
        "id": "e1",
        "type": "motion",
        "start": "2026-10-01T12:00:00+00:00",
        "end": None,
        "recorded_at": "2026-10-01T12:00:05+00:00",
        "message": "Motion on Front by Alice",
        "camera": {"id": "cam1", "name": "Front"},
        "user": {"id": "u1", "name": "Alice"},
        "event": raw,
    }
    assert to_record(event("e2", T0, camera=None), NAMES, T0)["camera"] is None


def test_first_sync_backfills_in_day_chunks(tmp_path: Path) -> None:
    clock = Clock(T0)
    source, syncer = make(tmp_path, clock)
    source.all_events = [
        event("old", T0 - timedelta(days=3)),  # before the backfill window
        event("b", T0 - timedelta(hours=1)),
        event("a", T0 - timedelta(days=1, hours=12)),
    ]

    assert asyncio.run(syncer.sync_once()) == 2

    store = syncer.store
    assert [r["id"] for r in store.read()] == ["a", "b"]
    assert store.load_state() == SyncState(cursor=T0)
    start = T0 - timedelta(days=2, minutes=10)
    assert [q[0] for q in source.queries] == [
        start,
        start + timedelta(days=1),
        start + timedelta(days=2),
    ]
    assert syncer.status.last_success == T0
    assert syncer.status.last_added == 2


def test_resync_overlaps_without_duplicates(tmp_path: Path) -> None:
    clock = Clock(T0)
    source, syncer = make(tmp_path, clock)
    source.all_events = [event("a", T0 - timedelta(minutes=1))]
    asyncio.run(syncer.sync_once())

    # Protect files an event late, inside the overlap window.
    clock.now = T0 + timedelta(minutes=1)
    source.all_events.append(event("late", T0 - timedelta(minutes=5)))
    assert asyncio.run(syncer.sync_once()) == 1
    assert source.queries[-1][0] == T0 - timedelta(minutes=10)

    records = list(syncer.store.read())
    assert [r["id"] for r in records] == ["a", "late"]
    assert records[1]["recorded_at"] == clock.now.isoformat()
    assert asyncio.run(syncer.sync_once()) == 0


def test_catches_up_after_downtime(tmp_path: Path) -> None:
    clock = Clock(T0)
    source, syncer = make(tmp_path, clock)
    asyncio.run(syncer.sync_once())

    clock.now = T0 + timedelta(days=3)
    source.all_events = [event(f"e{d}", T0 + timedelta(days=d)) for d in (1, 2)]
    assert asyncio.run(syncer.sync_once()) == 2
    assert syncer.store.load_state() == SyncState(cursor=clock.now)


def test_passes_event_types(tmp_path: Path) -> None:
    clock = Clock(T0)
    source, syncer = make(tmp_path, clock, event_types=("access",))
    source.all_events = [
        event("m", T0 - timedelta(hours=1)),
        event("x", T0 - timedelta(hours=1), type="access"),
    ]
    asyncio.run(syncer.sync_once())
    assert {q[2] for q in source.queries} == {("access",)}
    assert [r["id"] for r in syncer.store.read()] == ["x"]


def test_default_clock_is_utc_now(tmp_path: Path) -> None:
    syncer = Syncer(
        FakeSource(),
        AuditStore(tmp_path),
        overlap=timedelta(0),
        backfill=timedelta(0),
    )
    assert syncer.clock().tzinfo is UTC
