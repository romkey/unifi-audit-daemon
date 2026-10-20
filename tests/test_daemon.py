import asyncio
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from uiprotect.exceptions import NvrError

from tests.fakes import FakeSource
from unifi_audit_daemon import daemon as daemon_module
from unifi_audit_daemon.config import Settings
from unifi_audit_daemon.contact_sheet import ContactSheets
from unifi_audit_daemon.daemon import Daemon, next_run
from unifi_audit_daemon.store import AuditStore
from unifi_audit_daemon.sync import Syncer

PACIFIC = ZoneInfo("America/Los_Angeles")
THREE = time(3, 0)


def pacific(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=PACIFIC)


def test_next_run_later_today() -> None:
    assert next_run(pacific(2026, 10, 20, 1, 0), THREE, PACIFIC) == pacific(
        2026, 10, 20, 3, 0
    )


def test_next_run_tomorrow() -> None:
    assert next_run(pacific(2026, 10, 20, 3, 0), THREE, PACIFIC) == pacific(
        2026, 10, 21, 3, 0
    )


def test_next_run_uses_the_zone_not_the_input_offset() -> None:
    # 09:00 UTC is 02:00 in Portland, so 03:00 local is an hour away.
    now = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    assert next_run(now, THREE, PACIFIC) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_next_run_across_dst_end() -> None:
    # The night US clocks fall back (2026-11-01): 03:00 PST, not PDT.
    run = next_run(pacific(2026, 10, 31, 12, 0), THREE, PACIFIC)
    assert run == pacific(2026, 11, 1, 3, 0)
    assert run.utcoffset() == timedelta(hours=-8)


def test_next_run_in_system_zone() -> None:
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    run = next_run(now, THREE, None)
    assert run > now
    assert run - now <= timedelta(days=1)
    assert run.astimezone().time() == THREE


def make_daemon(tmp_path: Path) -> tuple[FakeSource, Daemon]:
    settings = Settings.from_env(
        {
            "UNIFI_HOST": "192.0.2.1",
            "UNIFI_USERNAME": "audit",
            "UNIFI_PASSWORD": "x",  # not-a-secret
            "API_TOKEN": "t" * 16,
            "DATA_DIR": str(tmp_path),
            "SYNC_INTERVAL": "3600",
            "CONTACT_SHEET_TILE_WIDTH": "64",
        }
    )
    source = FakeSource()
    store = AuditStore(tmp_path)
    syncer = Syncer(source, store, overlap=timedelta(0), backfill=timedelta(hours=1))
    return source, Daemon(settings, source, syncer, ContactSheets(tmp_path / "sheets"))


def test_request_before_running_is_refused(tmp_path: Path) -> None:
    _, daemon = make_daemon(tmp_path)
    assert daemon.request_contact_sheet() is False


def test_runs_syncs_and_takes_a_requested_sheet(tmp_path: Path) -> None:
    _, daemon = make_daemon(tmp_path)

    async def scenario() -> None:
        stop = asyncio.Event()
        running = asyncio.create_task(daemon.run(stop))
        while daemon.syncer.status.last_success is None:
            await asyncio.sleep(0)
        assert daemon.request_contact_sheet()
        while not daemon.sheets.dates():
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(running, 5)

    asyncio.run(scenario())
    assert daemon.next_contact_sheet is not None
    assert daemon.last_contact_sheet_error is None
    assert daemon.syncer.status.last_error is None


def test_failures_are_recorded_and_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, daemon = make_daemon(tmp_path)

    async def broken_names() -> None:
        raise NvrError("console unreachable")

    async def broken_cameras() -> None:
        raise TimeoutError

    monkeypatch.setattr(source, "names", broken_names)
    monkeypatch.setattr(source, "cameras", broken_cameras)

    async def scenario() -> None:
        stop = asyncio.Event()
        running = asyncio.create_task(daemon.run(stop))
        while daemon.syncer.status.last_error is None:
            await asyncio.sleep(0)
        daemon.request_contact_sheet()
        while daemon.last_contact_sheet_error is None:
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(running, 5)

    asyncio.run(scenario())
    assert daemon.syncer.status.last_error == "console unreachable"
    assert daemon.syncer.status.last_error_at is not None
    assert daemon.last_contact_sheet_error == "TimeoutError"


def test_takes_the_sheet_when_its_time_comes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, daemon = make_daemon(tmp_path)
    # Pretend 03:00 was a moment ago, so the scheduled run is due at once.
    monkeypatch.setattr(
        daemon_module,
        "next_run",
        lambda now, at, zone: datetime.now(UTC) - timedelta(seconds=1),
    )

    async def scenario() -> None:
        stop = asyncio.Event()
        running = asyncio.create_task(daemon.run(stop))
        while not daemon.sheets.dates():
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(running, 5)

    asyncio.run(scenario())
    assert daemon.sheets.dates()


def test_stop_cancels_a_hung_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, daemon = make_daemon(tmp_path)
    started = asyncio.Event()

    async def hang() -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(source, "names", hang)

    async def scenario() -> None:
        stop = asyncio.Event()
        running = asyncio.create_task(daemon.run(stop))
        await started.wait()
        stop.set()
        await asyncio.wait_for(running, 5)

    asyncio.run(scenario())


def test_unexpected_errors_stop_the_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, daemon = make_daemon(tmp_path)

    async def broken() -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(source, "names", broken)
    with pytest.raises(RuntimeError, match="bug"):
        asyncio.run(asyncio.wait_for(daemon.run(asyncio.Event()), 5))
