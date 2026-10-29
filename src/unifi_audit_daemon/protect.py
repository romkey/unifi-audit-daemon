"""UniFi Protect, through its private (unofficial) API.

The private API is the one the Protect web app uses: it signs in with a
local UniFi OS account and, unlike the Integration API that API keys reach,
can list past events. We use uiprotect (https://github.com/uilibs/uiprotect),
which follows that API as UniFi changes it.

Everything else in the daemon talks to the `ProtectSource` protocol, so
tests swap in a fake.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from uiprotect import ProtectApiClient
from uiprotect.data.types import EventType

logger = logging.getLogger(__name__)

# A raw event as Protect returns it from /proxy/protect/api/events. JSON,
# so the values are whatever Protect put there.
type RawEvent = dict[str, Any]  # Any: arbitrary JSON from Protect


@dataclass(frozen=True)
class Camera:
    id: str
    name: str
    connected: bool


@dataclass(frozen=True)
class Names:
    """Display names for ids that appear in events."""

    cameras: Mapping[str, str] = field(default_factory=dict)
    users: Mapping[str, str] = field(default_factory=dict)


class ProtectSource(Protocol):
    async def events(
        self, start: datetime, end: datetime, types: tuple[str, ...]
    ) -> list[RawEvent]:
        """Events that started between start and end, oldest first."""
        ...

    async def names(self) -> Names: ...

    async def cameras(self) -> list[Camera]: ...

    async def snapshot(self, camera_id: str, width: int | None = None) -> bytes | None:
        """A JPEG from the camera now (full resolution unless `width` is
        given), or None if it couldn't give one."""
        ...

    async def close(self) -> None: ...


class UiProtect:
    """`ProtectSource` backed by uiprotect's ProtectApiClient."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        verify_ssl: bool,
        state_dir: Path,
    ) -> None:
        # uiprotect keeps a session file and caches under these directories;
        # point them into the data volume (the production container is
        # read-only elsewhere).
        self._client = ProtectApiClient(
            host,
            port,
            username,
            password,
            verify_ssl=verify_ssl,
            config_dir=state_dir / "config",
            cache_dir=state_dir / "cache",
            store_sessions=False,
        )

    async def events(
        self, start: datetime, end: datetime, types: tuple[str, ...]
    ) -> list[RawEvent]:
        return await self._client.get_events_raw(
            start=start,
            end=end,
            types=[EventType(t) for t in types] if types else None,
            sorting="asc",
        )

    async def names(self) -> Names:
        # Raw, because uiprotect's models no longer carry user names.
        bootstrap = await self._client.api_request_obj("bootstrap")
        return Names(
            cameras=_names(bootstrap.get("cameras")),
            users=_names(bootstrap.get("users")),
        )

    async def cameras(self) -> list[Camera]:
        bootstrap = await self._client.get_bootstrap()
        return sorted(
            (
                Camera(id=c.id, name=c.name or c.id, connected=c.is_connected)
                for c in bootstrap.cameras.values()
                if c.is_adopted
            ),
            key=lambda c: c.name.casefold(),
        )

    async def snapshot(self, camera_id: str, width: int | None = None) -> bytes | None:
        return await self._client.get_camera_snapshot(camera_id, width=width)

    async def close(self) -> None:
        await self._client.close_session()


def _names(objs: object) -> dict[str, str]:
    """Id to display name for a raw bootstrap list, falling back to the id."""
    if not isinstance(objs, list):
        return {}
    return {
        o["id"]: o.get("name") or o["id"]
        for o in objs
        if isinstance(o, dict) and isinstance(o.get("id"), str)
    }
