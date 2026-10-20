"""HTTP API. Everything but /healthz needs `Authorization: Bearer API_TOKEN`.

    GET  /healthz                             up, and its version
    GET  /api/v1/status                       sync and contact sheet status
    GET  /api/v1/events?since=ISO8601         audit records, JSON Lines
    GET  /api/v1/contact-sheets               dates with a sheet
    GET  /api/v1/contact-sheets/latest.jpg    the newest sheet
    GET  /api/v1/contact-sheets/DATE.jpg      one night's sheet
    POST /api/v1/contact-sheets               take one now

docs/api.md describes them in full.
"""

import hmac
import json
import logging
import re
import shutil
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from unifi_audit_daemon import __version__
from unifi_audit_daemon.contact_sheet import ContactSheets
from unifi_audit_daemon.store import AuditStore, parse_time

logger = logging.getLogger(__name__)

SHEET_PATH = re.compile(r"^/api/v1/contact-sheets/(latest|\d{4}-\d{2}-\d{2})\.jpg$")

type Json = dict[str, Any]  # Any: a JSON response body


@dataclass(frozen=True)
class Api:
    """What the handlers need, so the server can be built around fakes."""

    token: str
    store: AuditStore
    sheets: ContactSheets
    status: Callable[[], Json]
    request_contact_sheet: Callable[[], bool]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], api: Api) -> None:
        super().__init__(address, Handler)
        self.api = api


class Handler(BaseHTTPRequestHandler):
    server_version = f"unifi-audit-daemon/{__version__}"
    server: Server

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz":
            self.send_json(HTTPStatus.OK, {"status": "ok", "version": __version__})
            return
        if not self.authorized():
            return
        api = self.server.api
        if url.path == "/api/v1/status":
            self.send_json(HTTPStatus.OK, api.status())
        elif url.path == "/api/v1/events":
            self.events(urllib.parse.parse_qs(url.query))
        elif url.path == "/api/v1/contact-sheets":
            dates = [d.isoformat() for d in api.sheets.dates()]
            self.send_json(HTTPStatus.OK, {"dates": dates})
        elif match := SHEET_PATH.match(url.path):
            self.contact_sheet(match.group(1))
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path != "/api/v1/contact-sheets":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        elif self.authorized():
            if self.server.api.request_contact_sheet():
                self.send_json(HTTPStatus.ACCEPTED, {"status": "started"})
            else:
                self.send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE, {"error": "not running yet"}
                )

    def authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(
            token.strip().encode(), self.server.api.token.encode()
        ):
            return True
        self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def events(self, query: dict[str, list[str]]) -> None:
        since: datetime | None = None
        if text := query.get("since", [""])[0]:
            try:
                since = parse_time(text)
            except ValueError:
                self.send_json(
                    HTTPStatus.BAD_REQUEST, {"error": "since must be ISO 8601"}
                )
                return
        self.send_lines(self.server.api.store.read(since))

    def contact_sheet(self, which: str) -> None:
        sheets = self.server.api.sheets
        path: Path | None
        if which == "latest":
            path = sheets.latest()
        else:
            try:
                path = sheets.path(date.fromisoformat(which))
            except ValueError:
                path = None
        if path is None:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "no such contact sheet"})
            return
        with path.open("rb") as f:
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
            self.end_headers()
            shutil.copyfileobj(f, self.wfile)

    def send_lines(self, records: Iterable[Json]) -> None:
        # Streamed, as the whole log may be large; no Content-Length, so the
        # end of the response is the end of the data.
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        for record in records:
            self.wfile.write(json.dumps(record, separators=(",", ":")).encode() + b"\n")

    def send_json(self, status: HTTPStatus, body: Json) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        # http.server writes to stderr; route it through logging instead.
        logger.info("%s %s", self.address_string(), format % args)


def make_server(host: str, port: int, api: Api) -> Server:
    """A server bound to host:port (port 0 picks a free one)."""
    return Server((host, port), api)
