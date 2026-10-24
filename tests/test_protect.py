"""UiProtect, against a stand-in for uiprotect's ProtectApiClient."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from uiprotect.data.types import EventType

from unifi_audit_daemon import protect
from unifi_audit_daemon.protect import Camera, Names, UiProtect

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@dataclass
class Device:
    id: str
    name: str
    is_connected: bool = True
    is_adopted: bool = True


@dataclass
class Bootstrap:
    cameras: dict[str, Device]
    users: dict[str, Device]


@dataclass
class FakeClient:
    args: tuple[Any, ...]  # Any: whatever UiProtect passes through
    kwargs: dict[str, Any]
    calls: list[tuple[str, Any]] = field(default_factory=list)
    closed: bool = False

    async def get_events_raw(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("events", kwargs))
        return [{"id": "e1"}]

    async def get_bootstrap(self) -> Bootstrap:
        return Bootstrap(
            cameras={
                "c2": Device("c2", "front"),
                "c1": Device("c1", "Back", is_connected=False),
                "c3": Device("c3", "Elsewhere", is_adopted=False),
            },
            users={"u1": Device("u1", "Alice")},
        )

    async def get_camera_snapshot(self, camera_id: str, width: int | None) -> bytes:
        self.calls.append(("snapshot", (camera_id, width)))
        return b"jpeg"

    async def close_session(self) -> None:
        self.closed = True


@pytest.fixture
def wrapped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> UiProtect:
    monkeypatch.setattr(
        protect, "ProtectApiClient", lambda *a, **k: FakeClient(args=a, kwargs=k)
    )
    return UiProtect(
        "192.0.2.1", 443, "audit", "pw", verify_ssl=False, state_dir=tmp_path
    )


def client(wrapped: UiProtect) -> FakeClient:
    fake = wrapped._client
    assert isinstance(fake, FakeClient)
    return fake


def test_builds_client(wrapped: UiProtect, tmp_path: Path) -> None:
    fake = client(wrapped)
    assert fake.args == ("192.0.2.1", 443, "audit", "pw")
    assert fake.kwargs == {
        "verify_ssl": False,
        "config_dir": tmp_path / "config",
        "cache_dir": tmp_path / "cache",
        "store_sessions": False,
    }


def test_events(wrapped: UiProtect) -> None:
    assert asyncio.run(wrapped.events(T0, T0, ())) == [{"id": "e1"}]
    asyncio.run(wrapped.events(T0, T0, ("access",)))
    calls = [kwargs for name, kwargs in client(wrapped).calls]
    assert calls[0] == {"start": T0, "end": T0, "types": None, "sorting": "asc"}
    assert calls[1]["types"] == [EventType.ACCESS]


def test_names_and_cameras(wrapped: UiProtect) -> None:
    assert asyncio.run(wrapped.names()) == Names(
        cameras={"c2": "front", "c1": "Back", "c3": "Elsewhere"},
        users={"u1": "Alice"},
    )
    assert asyncio.run(wrapped.cameras()) == [
        Camera("c1", "Back", connected=False),
        Camera("c2", "front", connected=True),
    ]


def test_snapshot_and_close(wrapped: UiProtect) -> None:
    assert asyncio.run(wrapped.snapshot("c1", 640)) == b"jpeg"
    assert asyncio.run(wrapped.snapshot("c1")) == b"jpeg"
    assert client(wrapped).calls == [
        ("snapshot", ("c1", 640)),
        ("snapshot", ("c1", None)),
    ]
    asyncio.run(wrapped.close())
    assert client(wrapped).closed
