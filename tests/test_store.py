import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from unifi_audit_daemon.store import AuditStore, SyncState, parse_time

T0 = datetime(2026, 10, 1, 23, 59, tzinfo=UTC)


def record(id: str, start: datetime, recorded_at: datetime) -> dict[str, str]:
    return {
        "id": id,
        "start": start.isoformat(),
        "recorded_at": recorded_at.isoformat(),
    }


def test_parse_time_assumes_utc() -> None:
    assert parse_time("2026-10-01T12:00:00") == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert parse_time("2026-10-01T05:00:00-07:00") == datetime(
        2026, 10, 1, 12, tzinfo=UTC
    )


def test_state_round_trips(tmp_path: Path) -> None:
    store = AuditStore(tmp_path)
    assert store.load_state() == SyncState()
    store.save_state(SyncState(cursor=T0))
    assert AuditStore(tmp_path).load_state() == SyncState(cursor=T0)
    store.save_state(SyncState())
    assert store.load_state() == SyncState()


def test_appends_by_recorded_day_and_reads_back(tmp_path: Path) -> None:
    store = AuditStore(tmp_path)
    late = T0 + timedelta(minutes=2)  # the next UTC day
    store.append([record("a", T0, T0), record("b", T0, late)])
    store.append([record("c", T0 - timedelta(days=3), late)])

    assert sorted(p.name for p in store.directory.iterdir()) == [
        "2026-10-01.jsonl",
        "2026-10-02.jsonl",
    ]
    assert [r["id"] for r in store.read()] == ["a", "b", "c"]
    # Filtered by when it was recorded, not when it happened: "c" happened
    # days earlier but was backfilled late, so a reader catching up sees it.
    assert [r["id"] for r in store.read(late)] == ["b", "c"]
    assert [r["id"] for r in store.read(late + timedelta(seconds=1))] == []


def test_known_ids_by_start(tmp_path: Path) -> None:
    store = AuditStore(tmp_path)
    store.append(
        [
            record("old", T0 - timedelta(hours=1), T0),
            record("new", T0, T0),
        ]
    )
    assert store.known_ids(T0) == {"new"}
    assert store.known_ids(T0 - timedelta(hours=1)) == {"old", "new"}


def test_skips_bad_and_unfinished_lines(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = AuditStore(tmp_path)
    store.append([record("a", T0, T0)])
    path = store.directory / "2026-10-01.jsonl"
    with path.open("a") as f:
        f.write("not json\n[1, 2]\n")
        f.write('{"id": "half')  # a write in progress
    with caplog.at_level(logging.WARNING):
        assert [r["id"] for r in store.read()] == ["a"]
    assert "is not JSON" in caplog.text


def test_drops_partial_line_left_by_crash(tmp_path: Path) -> None:
    store = AuditStore(tmp_path)
    store.append([record("a", T0, T0)])
    path = store.directory / "2026-10-01.jsonl"
    with path.open("a") as f:
        f.write('{"id": "half')
    (store.directory / "2026-09-30.jsonl").write_text("")

    reopened = AuditStore(tmp_path)
    reopened.append([record("b", T0, T0)])
    assert [r["id"] for r in reopened.read()] == ["a", "b"]


def test_partial_only_line_is_dropped(tmp_path: Path) -> None:
    directory = tmp_path / "audit"
    directory.mkdir()
    (directory / "2026-10-01.jsonl").write_text('{"id"')
    AuditStore(tmp_path)
    assert (directory / "2026-10-01.jsonl").read_text() == ""
