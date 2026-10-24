"""Stand-ins for UniFi Protect, shared by the tests."""

import io
from datetime import UTC, datetime

from PIL import Image
from uiprotect.exceptions import NvrError

from unifi_audit_daemon.protect import Camera, Names, RawEvent


def jpeg(width: int = 160, height: int = 90, color: str = "red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return out.getvalue()


def ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def event(
    id: str,
    start: datetime,
    type: str = "motion",
    camera: str | None = "cam1",
    user: str | None = None,
) -> RawEvent:
    return {
        "id": id,
        "type": type,
        "start": ms(start),
        "end": ms(start) + 1000,
        "camera": camera,
        "user": user,
        "metadata": {},
        "smartDetectTypes": [],
    }


T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FakeSource:
    """In-memory Protect: events, cameras and snapshots."""

    def __init__(self) -> None:
        self.all_events: list[RawEvent] = []
        self.queries: list[tuple[datetime, datetime, tuple[str, ...]]] = []
        self.camera_list = [
            Camera("cam1", "Front", connected=True),
            Camera("cam2", "Back", connected=True),
            Camera("cam3", "Shop", connected=False),
        ]
        self.images: dict[str, bytes | None] = {"cam1": jpeg(), "cam2": None}
        self.failing: set[str] = set()
        self.snapshot_widths: list[int | None] = []
        self.closed = False

    async def events(
        self, start: datetime, end: datetime, types: tuple[str, ...]
    ) -> list[RawEvent]:
        self.queries.append((start, end, types))
        return [
            e
            for e in self.all_events
            if ms(start) <= e["start"] <= ms(end) and (not types or e["type"] in types)
        ]

    async def names(self) -> Names:
        return Names(cameras={"cam1": "Front"}, users={"u1": "Alice"})

    async def cameras(self) -> list[Camera]:
        return self.camera_list

    async def snapshot(self, camera_id: str, width: int | None = None) -> bytes | None:
        self.snapshot_widths.append(width)
        if camera_id in self.failing:
            raise NvrError("console unreachable")
        return self.images.get(camera_id)

    async def close(self) -> None:
        self.closed = True
