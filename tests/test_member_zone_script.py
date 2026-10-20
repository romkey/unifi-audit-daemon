"""contrib/member-zone/unifi_protect_audit.py, against a real daemon server."""

import importlib.util
import io
import json
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from unifi_audit_daemon.contact_sheet import ContactSheets
from unifi_audit_daemon.server import Api, make_server
from unifi_audit_daemon.store import AuditStore

TOKEN = "t" * 16
SCRIPT = Path(__file__).parent.parent / "contrib/member-zone/unifi_protect_audit.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("unifi_protect_audit", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = load()


@pytest.fixture
def daemon_url(tmp_path: Path) -> Iterator[str]:
    store = AuditStore(tmp_path)
    store.append(
        [
            {
                "id": "e1",
                "type": "access",
                "start": "2026-10-01T11:00:00+00:00",
                "end": None,
                "recorded_at": "2026-10-01T11:00:01+00:00",
                "message": "Access by Alice: via web",
                "camera": None,
                "user": {"id": "u1", "name": "Alice"},
                "event": {},
            },
            {
                "id": "e2",
                "type": "motion",
                "start": "2026-10-01T12:00:00+00:00",
                "end": "2026-10-01T12:00:09+00:00",
                "recorded_at": "2026-10-01T13:00:00+00:00",
                "message": "Motion on cam9",
                "camera": {"id": "cam9", "name": None},
                "user": None,
                "event": {},
            },
        ]
    )
    api = Api(
        token=TOKEN,
        store=store,
        sheets=ContactSheets(tmp_path),
        status=dict,
        request_contact_sheet=lambda: True,
    )
    server = make_server("127.0.0.1", 0, api)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()
    thread.join()


def run(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> tuple[int, list[dict[str, str]], str]:
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    code = script.main(env)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    return code, lines, err.getvalue()


def test_prints_everything_first_time(
    daemon_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {"UNIFI_AUDIT_URL": daemon_url, "UNIFI_AUDIT_TOKEN": TOKEN}
    code, lines, err = run(env, monkeypatch)
    assert code == 0
    assert lines == [
        {
            "timestamp": "2026-10-01T11:00:00+00:00",
            "message": "Access by Alice: via web",
            "id": "e1",
            "type": "access",
            "user": "Alice",
        },
        {
            "timestamp": "2026-10-01T12:00:00+00:00",
            "message": "Motion on cam9",
            "id": "e2",
            "type": "motion",
            "end": "2026-10-01T12:00:09+00:00",
            "camera": "cam9",
        },
    ]
    assert err == "2 entries\n"


def test_since_reaches_back_by_slack(
    daemon_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {
        "UNIFI_AUDIT_URL": daemon_url,
        "UNIFI_AUDIT_TOKEN": TOKEN,
        # e2 started at 12:00 but was recorded at 13:00: asking from Member
        # Zone's newest entry (12:00) still finds it.
        "AUDIT_LOG_SINCE": "2026-10-01T12:00:00Z",
        "UNIFI_AUDIT_SLACK": "60",
    }
    _, lines, _ = run(env, monkeypatch)
    assert [line["id"] for line in lines] == ["e2"]


def test_events_url() -> None:
    since = datetime(2026, 10, 1, 12, tzinfo=UTC).replace(tzinfo=None).isoformat()
    url = script.events_url(
        {"UNIFI_AUDIT_URL": "https://d:8000", "AUDIT_LOG_SINCE": since}
    )
    assert url == "https://d:8000/api/v1/events?since=2026-10-01T11%3A00%3A00%2B00%3A00"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "UNIFI_AUDIT_TOKEN is not set"),
        ({"UNIFI_AUDIT_TOKEN": TOKEN}, "UNIFI_AUDIT_URL is not set"),
        (
            {"UNIFI_AUDIT_TOKEN": TOKEN, "UNIFI_AUDIT_URL": "file:///etc/passwd"},
            "must start with http",
        ),
        (
            {
                "UNIFI_AUDIT_TOKEN": TOKEN,
                "UNIFI_AUDIT_URL": "http://d",
                "AUDIT_LOG_SINCE": "2026-10-01T12:00:00Z",
                "UNIFI_AUDIT_SLACK": "an hour",
            },
            "UNIFI_AUDIT_SLACK must be a number",
        ),
    ],
)
def test_not_configured_exits_2(
    env: dict[str, str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, lines, err = run(env, monkeypatch)
    assert (code, lines) == (2, [])
    assert message in err


def test_bad_token_exits_1(daemon_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"UNIFI_AUDIT_URL": daemon_url, "UNIFI_AUDIT_TOKEN": "wrong-token-value"}
    code, _, err = run(env, monkeypatch)
    assert code == 1
    assert "401" in err


def test_unreachable_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"UNIFI_AUDIT_URL": "http://127.0.0.1:1", "UNIFI_AUDIT_TOKEN": TOKEN}
    code, _, err = run(env, monkeypatch)
    assert code == 1
    assert "could not read" in err
