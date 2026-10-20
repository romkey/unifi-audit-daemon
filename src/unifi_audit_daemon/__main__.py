"""Command line: `unifi-audit-daemon` or `python -m unifi_audit_daemon`."""

import argparse
import asyncio
import logging
import signal
import sys
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta

from unifi_audit_daemon import __version__
from unifi_audit_daemon.config import ConfigError, Settings
from unifi_audit_daemon.contact_sheet import ContactSheets
from unifi_audit_daemon.daemon import Daemon
from unifi_audit_daemon.protect import ProtectSource, UiProtect
from unifi_audit_daemon.server import Api, Json, make_server
from unifi_audit_daemon.store import AuditStore
from unifi_audit_daemon.sync import Syncer

logger = logging.getLogger("unifi_audit_daemon")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unifi-audit-daemon", description=__doc__)
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ConfigError as e:
        parser.exit(2, f"unifi-audit-daemon: {e}\n")

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    source = UiProtect(
        settings.unifi_host,
        settings.unifi_port,
        settings.unifi_username,
        settings.unifi_password,
        verify_ssl=settings.unifi_verify_ssl,
        state_dir=settings.data_dir / "uiprotect",
    )
    return asyncio.run(run(settings, source))


async def run(settings: Settings, source: ProtectSource) -> int:
    store = AuditStore(settings.data_dir)
    sheets = ContactSheets(settings.contact_sheet_dir)
    syncer = Syncer(
        source,
        store,
        overlap=timedelta(seconds=settings.sync_overlap),
        backfill=timedelta(days=settings.backfill_days),
        event_types=settings.event_types,
    )
    daemon = Daemon(settings, source, syncer, sheets)

    def status() -> Json:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        sync = syncer.status
        return {
            "version": __version__,
            "sync": {
                "cursor": iso(store.load_state().cursor),
                "last_success": iso(sync.last_success),
                "last_added": sync.last_added,
                "last_error": sync.last_error,
                "last_error_at": iso(sync.last_error_at),
            },
            "contact_sheet": {
                "next": iso(daemon.next_contact_sheet),
                "latest": next((d.isoformat() for d in sheets.dates()), None),
                "last_error": daemon.last_contact_sheet_error,
            },
        }

    server = make_server(
        settings.host,
        settings.port,
        Api(
            token=settings.api_token,
            contact_sheet_token=settings.contact_sheet_token,
            store=store,
            sheets=sheets,
            status=status,
            request_contact_sheet=daemon.request_contact_sheet,
        ),
    )
    serving = threading.Thread(target=server.serve_forever, name="http")
    serving.start()

    # `docker stop` sends SIGTERM; Ctrl-C sends SIGINT. Either stops cleanly.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)

    logger.info(
        "unifi-audit-daemon %s listening on %s:%d, syncing %s",
        __version__,
        settings.host,
        server.server_port,
        settings.unifi_host,
    )
    try:
        await daemon.run(stop)
    finally:
        server.shutdown()
        server.server_close()
        serving.join()
        await source.close()
    logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
