from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from unifi_audit_daemon.config import ConfigError, Settings

REQUIRED = {
    "UNIFI_HOST": "192.0.2.1",
    "UNIFI_USERNAME": "audit",
    "UNIFI_PASSWORD": "dummy-password",
    "API_TOKEN": "t" * 16,
}


def test_defaults() -> None:
    settings = Settings.from_env(REQUIRED)
    assert settings.unifi_host == "192.0.2.1"
    assert settings.unifi_port == 443
    assert settings.unifi_verify_ssl is True
    assert settings.data_dir == Path("data")
    assert settings.sync_interval == 60
    assert settings.event_types == ()
    assert settings.contact_sheet_time == time(3, 0)
    assert settings.timezone is None
    assert (settings.host, settings.port, settings.log_level) == (
        "127.0.0.1",
        8000,
        "INFO",
    )


def test_reads_environment() -> None:
    env = REQUIRED | {
        "UNIFI_PORT": "7443",
        "UNIFI_VERIFY_SSL": "no",
        "DATA_DIR": "/data",
        "SYNC_INTERVAL": "30",
        "SYNC_OVERLAP": "0",
        "BACKFILL_DAYS": "90",
        "EVENT_TYPES": "access, nvrSettingsChanged",
        "CONTACT_SHEET_TIME": "04:30",
        "TZ": "America/Los_Angeles",
        "CONTACT_SHEET_RETENTION_DAYS": "0",
        "CONTACT_SHEET_TILE_WIDTH": "320",
        "CONTACT_SHEET_COLUMNS": "5",
        "PORT": "9000",
        "LOG_LEVEL": "debug",
    }
    settings = Settings.from_env(env)
    assert settings.unifi_port == 7443
    assert settings.unifi_verify_ssl is False
    assert settings.data_dir == Path("/data")
    assert settings.sync_interval == 30
    assert settings.sync_overlap == 0
    assert settings.backfill_days == 90
    assert settings.event_types == ("access", "nvrSettingsChanged")
    assert settings.contact_sheet_time == time(4, 30)
    assert settings.timezone == ZoneInfo("America/Los_Angeles")
    assert settings.contact_sheet_retention_days == 0
    assert settings.contact_sheet_tile_width == 320
    assert settings.contact_sheet_columns == 5
    assert settings.port == 9000
    assert settings.log_level == "DEBUG"


def test_secrets_stay_out_of_repr() -> None:
    text = repr(Settings.from_env(REQUIRED))
    assert "dummy-password" not in text
    assert "t" * 16 not in text


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_requires(name: str) -> None:
    env = REQUIRED | {name: "  "}
    with pytest.raises(ConfigError, match=f"{name} is required"):
        Settings.from_env(env)


def test_rejects_short_token() -> None:
    with pytest.raises(ConfigError, match="API_TOKEN must be at least"):
        Settings.from_env(REQUIRED | {"API_TOKEN": "short"})


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("PORT", "eighty", "PORT must be a number"),
        ("PORT", "0", "PORT must be 1-65535"),
        ("UNIFI_PORT", "65536", "UNIFI_PORT must be 1-65535"),
        ("SYNC_INTERVAL", "1", "SYNC_INTERVAL must be"),
        ("UNIFI_VERIFY_SSL", "maybe", "UNIFI_VERIFY_SSL must be true or false"),
        ("CONTACT_SHEET_TIME", "3am", "CONTACT_SHEET_TIME must be a time"),
        ("TZ", "Mars/Olympus_Mons", "TZ 'Mars/Olympus_Mons' is not a known"),
        ("EVENT_TYPES", "access,bogus", "EVENT_TYPES has unknown event types: bogus"),
        ("LOG_LEVEL", "loud", "LOG_LEVEL"),
    ],
)
def test_rejects_bad_values(name: str, value: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(REQUIRED | {name: value})
