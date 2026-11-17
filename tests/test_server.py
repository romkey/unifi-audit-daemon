import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from unifi_audit_daemon import __version__
from unifi_audit_daemon.contact_sheet import ContactSheets
from unifi_audit_daemon.metrics import Metrics
from unifi_audit_daemon.server import Api, make_server, route
from unifi_audit_daemon.store import AuditStore

TOKEN = "t" * 16
SHEET_TOKEN = "s" * 16
METRICS_TOKEN = "m" * 16
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@dataclass
class Fixture:
    url: str
    store: AuditStore
    sheets: ContactSheets
    requests: list[bool] = field(default_factory=list)
    running: bool = True
    metrics: Metrics = field(default_factory=Metrics)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Fixture]:
    store = AuditStore(tmp_path)
    sheets = ContactSheets(tmp_path / "sheets")
    fixture = Fixture("", store, sheets)

    def request_contact_sheet() -> bool:
        fixture.requests.append(True)
        return fixture.running

    api = Api(
        token=TOKEN,
        store=store,
        sheets=sheets,
        status=lambda: {"sync": {"last_error": None}},
        request_contact_sheet=request_contact_sheet,
        contact_sheet_token=SHEET_TOKEN,
        metrics_token=METRICS_TOKEN,
        metrics=fixture.metrics,
    )
    server = make_server("127.0.0.1", 0, api)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    fixture.url = f"http://127.0.0.1:{server.server_port}"
    yield fixture
    server.shutdown()
    server.server_close()
    thread.join()


def call(
    url: str, *, token: str | None = TOKEN, method: str = "GET"
) -> tuple[int, str, bytes]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, response.headers["Content-Type"], response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def call_json(url: str, **kwargs: Any) -> tuple[int, Any]:  # Any: JSON
    status, _, body = call(url, **kwargs)
    return status, json.loads(body)


def test_healthz_needs_no_token(app: Fixture) -> None:
    assert call_json(f"{app.url}/healthz", token=None) == (
        200,
        {"status": "ok", "version": __version__},
    )


@pytest.mark.parametrize("token", [None, "wrong-token-value", ""])
def test_api_needs_token(app: Fixture, token: str | None) -> None:
    assert call_json(f"{app.url}/api/v1/status", token=token) == (
        401,
        {"error": "unauthorized"},
    )
    status, _ = call_json(
        f"{app.url}/api/v1/contact-sheets", method="POST", token=token
    )
    assert status == 401
    assert call(f"{app.url}/api/v1/contact-sheets/latest.jpg", token=token)[0] == 401


def test_status(app: Fixture) -> None:
    assert call_json(f"{app.url}/api/v1/status") == (
        200,
        {"sync": {"last_error": None}},
    )


def test_events(app: Fixture) -> None:
    later = T0 + timedelta(hours=1)
    app.store.append(
        [
            {"id": "a", "start": T0.isoformat(), "recorded_at": T0.isoformat()},
            {"id": "b", "start": T0.isoformat(), "recorded_at": later.isoformat()},
        ]
    )

    status, kind, body = call(f"{app.url}/api/v1/events")
    assert (status, kind) == (200, "application/x-ndjson")
    assert [json.loads(line)["id"] for line in body.splitlines()] == ["a", "b"]

    _, _, body = call(f"{app.url}/api/v1/events?since=2026-10-01T12:30:00Z")
    assert [json.loads(line)["id"] for line in body.splitlines()] == ["b"]

    assert call(f"{app.url}/api/v1/events?since=2099-01-01T00:00:00Z")[2] == b""


def test_events_rejects_bad_since(app: Fixture) -> None:
    assert call_json(f"{app.url}/api/v1/events?since=yesterday") == (
        400,
        {"error": "since must be ISO 8601"},
    )


def test_contact_sheets(app: Fixture) -> None:
    assert call_json(f"{app.url}/api/v1/contact-sheets") == (200, {"dates": []})
    assert call(f"{app.url}/api/v1/contact-sheets/latest.jpg")[0] == 404

    app.sheets.save(date(2026, 10, 19), b"older")
    app.sheets.save(date(2026, 10, 20), b"newer")
    assert call_json(f"{app.url}/api/v1/contact-sheets") == (
        200,
        {"dates": ["2026-10-20", "2026-10-19"]},
    )
    assert call(f"{app.url}/api/v1/contact-sheets/latest.jpg") == (
        200,
        "image/jpeg",
        b"newer",
    )
    assert call(f"{app.url}/api/v1/contact-sheets/2026-10-19.jpg")[2] == b"older"
    assert call(f"{app.url}/api/v1/contact-sheets/2026-10-18.jpg")[0] == 404
    assert call(f"{app.url}/api/v1/contact-sheets/2026-13-45.jpg")[0] == 404


def test_take_contact_sheet_now(app: Fixture) -> None:
    assert call_json(f"{app.url}/api/v1/contact-sheets", method="POST") == (
        202,
        {"status": "started"},
    )
    app.running = False
    assert call_json(f"{app.url}/api/v1/contact-sheets", method="POST") == (
        503,
        {"error": "not running yet"},
    )
    assert app.requests == [True, True]


def test_unknown_paths_are_404(app: Fixture) -> None:
    assert call_json(f"{app.url}/api/v1/nope") == (404, {"error": "not found"})
    assert call_json(f"{app.url}/nope", method="POST") == (404, {"error": "not found"})


def test_contact_sheet_token_reads_sheets_only(app: Fixture) -> None:
    app.sheets.save(date(2026, 10, 20), b"jpeg")
    sheet = f"{app.url}/api/v1/contact-sheets"
    assert call(f"{sheet}/latest.jpg", token=SHEET_TOKEN)[:2] == (200, "image/jpeg")
    assert call(f"{sheet}/2026-10-20.jpg", token=SHEET_TOKEN)[0] == 200
    assert call_json(sheet, token=SHEET_TOKEN) == (200, {"dates": ["2026-10-20"]})
    # The audit log, status and taking a sheet stay behind API_TOKEN.
    for url in (f"{app.url}/api/v1/events", f"{app.url}/api/v1/status"):
        assert call(url, token=SHEET_TOKEN)[0] == 401
    assert call(sheet, token=SHEET_TOKEN, method="POST")[0] == 401
    assert app.requests == []


def test_api_token_still_reads_sheets(app: Fixture) -> None:
    assert call_json(f"{app.url}/api/v1/contact-sheets") == (200, {"dates": []})


def test_metrics_with_either_token(app: Fixture) -> None:
    for token in (METRICS_TOKEN, TOKEN):
        status, kind, body = call(f"{app.url}/metrics", token=token)
        assert (status, kind) == (200, "text/plain; version=0.0.4; charset=utf-8")
        assert b"# TYPE unifi_audit_job_runs_total counter" in body


@pytest.mark.parametrize("token", [None, SHEET_TOKEN, "wrong-token-value"])
def test_metrics_needs_its_token(app: Fixture, token: str | None) -> None:
    assert call_json(f"{app.url}/metrics", token=token) == (
        401,
        {"error": "unauthorized"},
    )


def test_metrics_token_reads_metrics_only(app: Fixture) -> None:
    app.sheets.save(date(2026, 10, 20), b"jpeg")
    for path in (
        "/api/v1/events",
        "/api/v1/status",
        "/api/v1/contact-sheets",
        "/api/v1/contact-sheets/latest.jpg",
    ):
        assert call(f"{app.url}{path}", token=METRICS_TOKEN)[0] == 401
    status, _ = call_json(
        f"{app.url}/api/v1/contact-sheets", token=METRICS_TOKEN, method="POST"
    )
    assert status == 401
    assert app.requests == []


def test_requests_are_counted_by_route(app: Fixture) -> None:
    call(f"{app.url}/healthz", token=None)
    call(f"{app.url}/api/v1/status", token=None)
    call(f"{app.url}/api/v1/contact-sheets/2026-10-20.jpg")
    call(f"{app.url}/api/v1/contact-sheets/2026-10-21.jpg")
    call(f"{app.url}/no/such/thing")
    call(f"{app.url}/api/v1/contact-sheets", method="POST")

    requests = app.metrics.http_requests
    assert requests.get(method="GET", route="/healthz", status="200") == 1
    assert requests.get(method="GET", route="/api/v1/status", status="401") == 1
    sheet = "/api/v1/contact-sheets/{sheet}.jpg"
    assert requests.get(method="GET", route=sheet, status="404") == 2
    assert requests.get(method="GET", route="other", status="404") == 1
    assert (
        requests.get(method="POST", route="/api/v1/contact-sheets", status="202") == 1
    )
    assert app.metrics.contact_sheet_requests.get() == 1
    assert app.metrics.http_duration.count(route=sheet) == 2

    _, _, body = call(f"{app.url}/metrics", token=METRICS_TOKEN)
    text = body.decode()
    assert (
        'unifi_audit_http_requests_total{method="GET",route="/healthz",status="200"} 1'
        in text
    )
    assert "2026-10-20" not in text  # one route per endpoint, not per URL


def test_unparseable_requests_are_counted(app: Fixture) -> None:
    host, port = app.url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as conn:
        conn.sendall(b"NONSENSE\r\n\r\n")
        conn.recv(1000)  # the error response; its wording varies by Python
    with socket.create_connection((host, int(port)), timeout=5):
        pass  # connects and sends nothing: not a request
    # Counted once the handler thread finishes, just after it answers.
    deadline = time.monotonic() + 5
    counted = app.metrics.http_requests.get
    while not counted(method="other", route="other", status="400"):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_route() -> None:
    assert route("/metrics") == "/metrics"
    assert route("/api/v1/contact-sheets/latest.jpg") == (
        "/api/v1/contact-sheets/{sheet}.jpg"
    )
    assert route("/api/v1/contact-sheets/x.jpg") == "other"
