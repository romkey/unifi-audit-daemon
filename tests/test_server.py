import json
import threading
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
from unifi_audit_daemon.server import Api, make_server
from unifi_audit_daemon.store import AuditStore

TOKEN = "t" * 16
SHEET_TOKEN = "s" * 16
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@dataclass
class Fixture:
    url: str
    store: AuditStore
    sheets: ContactSheets
    requests: list[bool] = field(default_factory=list)
    running: bool = True


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
