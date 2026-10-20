import asyncio
import json
import os
import signal
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from tests.fakes import FakeSource
from unifi_audit_daemon import __main__ as cli
from unifi_audit_daemon import __version__
from unifi_audit_daemon.config import Settings
from unifi_audit_daemon.protect import ProtectSource
from unifi_audit_daemon.server import Api, Server, make_server

ENV = {
    "UNIFI_HOST": "192.0.2.1",
    "UNIFI_USERNAME": "audit",
    "UNIFI_PASSWORD": "dummy-password",
    "API_TOKEN": "t" * 16,
}


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"unifi-audit-daemon {__version__}\n"


def test_bad_config_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2
    assert "is required" in capsys.readouterr().err


def test_main_builds_the_client_and_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ran: list[tuple[Settings, ProtectSource]] = []

    async def fake_run(settings: Settings, source: ProtectSource) -> int:
        ran.append((settings, source))
        return 0

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(cli, "UiProtect", lambda *a, **k: FakeSource())
    assert cli.main([]) == 0
    assert ran[0][0].data_dir == tmp_path


def running_settings(tmp_path: Path) -> Settings:
    # Port 0 picks a free one; PORT itself must be 1-65535.
    return replace(Settings.from_env(ENV | {"DATA_DIR": str(tmp_path)}), port=0)


def test_run_serves_until_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apis: list[Api] = []
    ports: list[int] = []

    def spy(host: str, port: int, api: Api) -> Server:
        server = make_server(host, port, api)
        apis.append(api)
        ports.append(server.server_port)
        return server

    monkeypatch.setattr(cli, "make_server", spy)
    source = FakeSource()

    def healthz(port: int) -> int:
        url = f"http://127.0.0.1:{port}/healthz"
        with urllib.request.urlopen(url, timeout=5) as response:
            return int(response.status)

    async def scenario() -> int:
        running = asyncio.create_task(cli.run(running_settings(tmp_path), source))
        # Wait for the first sync to save its cursor.
        while not (tmp_path / "state.json").exists():
            await asyncio.sleep(0.01)

        assert await asyncio.to_thread(healthz, ports[0]) == 200
        status = apis[0].status()
        assert status["version"] == __version__
        assert status["sync"]["cursor"] is not None
        assert status["sync"]["last_error"] is None
        assert status["contact_sheet"]["latest"] is None
        assert json.dumps(status)

        os.kill(os.getpid(), signal.SIGTERM)
        return await asyncio.wait_for(running, 5)

    assert asyncio.run(scenario()) == 0
    assert source.closed
