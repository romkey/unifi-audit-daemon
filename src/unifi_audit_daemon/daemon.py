"""The two background jobs: keep the audit log in sync, and take the
nightly contact sheet. Both run on one asyncio loop until `stop` is set.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, date, datetime, time, timedelta, tzinfo

import aiohttp
from uiprotect.exceptions import UnifiProtectError

from unifi_audit_daemon.config import Settings
from unifi_audit_daemon.contact_sheet import ContactSheets, make_contact_sheet
from unifi_audit_daemon.protect import ProtectSource
from unifi_audit_daemon.sync import Syncer

logger = logging.getLogger(__name__)

# What talking to the console can raise: uiprotect wraps most failures in
# its own errors, but not every aiohttp one.
PROTECT_ERRORS = (UnifiProtectError, aiohttp.ClientError, TimeoutError, OSError)


def next_run(now: datetime, at: time, zone: tzinfo | None) -> datetime:
    """The next time it is `at` o'clock in `zone` (local if None) after `now`."""

    def on(day: date) -> datetime:
        naive = datetime.combine(day, at)
        # astimezone() on a naive time picks the system zone's offset for
        # that date, so a DST change between now and then is handled too.
        return naive.replace(tzinfo=zone) if zone else naive.astimezone()

    today = now.astimezone(zone).date()
    candidate = on(today)
    return candidate if candidate > now else on(today + timedelta(days=1))


class Daemon:
    def __init__(
        self,
        settings: Settings,
        source: ProtectSource,
        syncer: Syncer,
        sheets: ContactSheets,
    ) -> None:
        self.settings = settings
        self.source = source
        self.syncer = syncer
        self.sheets = sheets
        self.next_contact_sheet: datetime | None = None
        self.last_contact_sheet_error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sheet_now = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        self._loop = asyncio.get_running_loop()
        jobs = [
            asyncio.create_task(self._sync_loop(stop)),
            asyncio.create_task(self._sheet_loop(stop)),
        ]
        stopping = asyncio.ensure_future(stop.wait())
        # Until stopped, or a job dies of something unexpected. Then cancel
        # whatever is in flight: a request to an unreachable console could
        # otherwise outlast `docker stop`'s grace period.
        await asyncio.wait([stopping, *jobs], return_when=asyncio.FIRST_COMPLETED)
        for task in (stopping, *jobs):
            task.cancel()
        for result in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(result, Exception):
                raise result

    def request_contact_sheet(self) -> bool:
        """Take a contact sheet now. Safe to call from any thread."""
        if self._loop is None:
            return False
        self._loop.call_soon_threadsafe(self._sheet_now.set)
        return True

    async def _sync_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.syncer.sync_once()
                self.syncer.status.last_error = None
            except PROTECT_ERRORS as e:
                logger.warning("sync failed, will retry: %s", e)
                self.syncer.status.last_error = str(e) or type(e).__name__
                self.syncer.status.last_error_at = datetime.now(UTC)
            await _wait(stop, self.settings.sync_interval)

    async def _sheet_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.next_contact_sheet = next_run(
                datetime.now(UTC),
                self.settings.contact_sheet_time,
                self.settings.timezone,
            )
            logger.info("next contact sheet at %s", self.next_contact_sheet)
            # Wake at least hourly, so a clock change can't strand us.
            delay = (self.next_contact_sheet - datetime.now(UTC)).total_seconds()
            woken = await _wait_any(stop, self._sheet_now, min(delay, 3600))
            if stop.is_set():
                return
            if woken or datetime.now(UTC) >= self.next_contact_sheet:
                self._sheet_now.clear()
                await self.take_contact_sheet()

    async def take_contact_sheet(self) -> None:
        settings = self.settings
        try:
            await make_contact_sheet(
                self.source,
                self.sheets,
                datetime.now(UTC).astimezone(settings.timezone),
                tile_width=settings.contact_sheet_tile_width,
                columns=settings.contact_sheet_columns,
                keep_days=settings.contact_sheet_retention_days,
            )
            self.last_contact_sheet_error = None
        except PROTECT_ERRORS as e:
            logger.warning("contact sheet failed: %s", e)
            self.last_contact_sheet_error = str(e) or type(e).__name__


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


async def _wait_any(stop: asyncio.Event, wake: asyncio.Event, seconds: float) -> bool:
    """Wait for either event or the timeout; True if `wake` was set."""
    waiters = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(wake.wait())]
    try:
        await asyncio.wait(
            waiters, timeout=max(seconds, 0), return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for waiter in waiters:
            waiter.cancel()
    return wake.is_set()
