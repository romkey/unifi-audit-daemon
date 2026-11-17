"""HTTP API. Everything but /healthz needs `Authorization: Bearer <token>`:
API_TOKEN for everything, CONTACT_SHEET_TOKEN (if set) for the
contact-sheet GETs only (marked *), or METRICS_TOKEN (if set) for
/metrics only (marked +).

    GET  /healthz                             up, and its version
    GET  /metrics                             Prometheus metrics +
    GET  /api/v1/status                       sync and contact sheet status
    GET  /api/v1/events?since=ISO8601         audit records, JSON Lines
    GET  /api/v1/contact-sheets               dates with a sheet *
    GET  /api/v1/contact-sheets/latest.jpg    the newest sheet *
    GET  /api/v1/contact-sheets/DATE.jpg      one night's sheet *
    POST /api/v1/contact-sheets               take one now

docs/api.md describes them in full.
"""

import hmac
import json
import logging
import re
import shutil
import time
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from unifi_audit_daemon import __version__
from unifi_audit_daemon.contact_sheet import ContactSheets
from unifi_audit_daemon.metrics import CONTENT_TYPE, Metrics
from unifi_audit_daemon.store import AuditStore, parse_time

logger = logging.getLogger(__name__)

SHEET_PATH = re.compile(r"^/api/v1/contact-sheets/(latest|\d{4}-\d{2}-\d{2})\.jpg$")

ROUTES = {
    "/healthz",
    "/metrics",
    "/api/v1/status",
    "/api/v1/events",
    "/api/v1/contact-sheets",
}

type Json = dict[str, Any]  # Any: a JSON response body


def route(path: str) -> str:
    """The path as a metric label: one value per endpoint, not per URL."""
    if path in ROUTES:
        return path
    if SHEET_PATH.match(path):
        return "/api/v1/contact-sheets/{sheet}.jpg"
    return "other"


@dataclass(frozen=True)
class Api:
    """What the handlers need, so the server can be built around fakes."""

    token: str
    store: AuditStore
    sheets: ContactSheets
    status: Callable[[], Json]
    request_contact_sheet: Callable[[], bool]
    contact_sheet_token: str | None = None
    metrics_token: str | None = None
    metrics: Metrics = field(default_factory=Metrics)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], api: Api) -> None:
        super().__init__(address, Handler)
        self.api = api


class Handler(BaseHTTPRequestHandler):
    server_version = f"unifi-audit-daemon/{__version__}"
    server: Server
    status_sent: int = 0

    def handle_one_request(self) -> None:
        # Count and time every request, whatever answered it.
        self.status_sent = 0
        started = time.monotonic()
        super().handle_one_request()
        if not self.status_sent:
            return  # no request: the client closed the connection
        metrics = self.server.api.metrics
        # A request line too broken to parse leaves no path.
        path = route(urllib.parse.urlsplit(getattr(self, "path", "")).path)
        metrics.http_requests.inc(
            method=self.command if self.command in ("GET", "POST") else "other",
            route=path,
            status=str(self.status_sent),
        )
        metrics.http_duration.observe(time.monotonic() - started, route=path)

    def send_response(self, code: int, message: str | None = None) -> None:
        self.status_sent = int(code)
        super().send_response(code, message)

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz":
            self.send_json(HTTPStatus.OK, {"status": "ok", "version": __version__})
            return
        api = self.server.api
        if url.path == "/metrics":
            if self.authorized(api.metrics_token):
                self.send_metrics()
            return
        if url.path == "/api/v1/contact-sheets" or SHEET_PATH.match(url.path):
            if not self.authorized(api.contact_sheet_token):
                return
        elif not self.authorized():
            return
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
            self.server.api.metrics.contact_sheet_requests.inc()
            if self.server.api.request_contact_sheet():
                self.send_json(HTTPStatus.ACCEPTED, {"status": "started"})
            else:
                self.send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE, {"error": "not running yet"}
                )

    def authorized(self, also: str | None = None) -> bool:
        """True if the request carries API_TOKEN, or `also` when given."""
        header = self.headers.get("Authorization", "")
        scheme, _, sent = header.partition(" ")
        accepted = [self.server.api.token, *([also] if also else [])]
        # Check every token, so the timing doesn't say which one matched.
        matches = [
            hmac.compare_digest(sent.strip().encode(), token.encode())
            for token in accepted
        ]
        if scheme.lower() == "bearer" and any(matches):
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

    def send_metrics(self) -> None:
        data = self.server.api.metrics.render().encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
