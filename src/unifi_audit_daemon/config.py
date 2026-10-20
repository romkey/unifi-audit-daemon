"""Settings, read once at startup from environment variables.

Every variable is documented in docs/configuration.md and listed, with a
safe example value, in .env.example.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from uiprotect.data.types import EventType


class ConfigError(ValueError):
    """An environment variable is set to something unusable."""


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    text = env.get(name, "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        raise ConfigError(f"{name} must be a number, not {text!r}") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be {low}-{high}, not {value}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    text = env.get(name, "").strip().lower()
    if not text:
        return default
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false, not {text!r}")


def _time(env: Mapping[str, str], name: str, default: time) -> time:
    text = env.get(name, "").strip()
    if not text:
        return default
    try:
        return time.fromisoformat(text)
    except ValueError:
        raise ConfigError(f"{name} must be a time like 03:00, not {text!r}") from None


def _zone(env: Mapping[str, str], name: str) -> ZoneInfo | None:
    text = env.get(name, "").strip()
    if not text:
        return None
    try:
        return ZoneInfo(text)
    except ZoneInfoNotFoundError, ValueError:
        raise ConfigError(f"{name} {text!r} is not a known time zone") from None


def _event_types(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    names = [t.strip() for t in env.get(name, "").split(",") if t.strip()]
    known = {t.value for t in EventType}
    unknown = [t for t in names if t not in known]
    if unknown:
        raise ConfigError(f"{name} has unknown event types: {', '.join(unknown)}")
    return tuple(names)


@dataclass(frozen=True)
class Settings:
    unifi_host: str
    unifi_username: str
    # Secrets stay out of repr(), so logging the settings can't leak them.
    unifi_password: str = field(repr=False)
    api_token: str = field(repr=False)
    unifi_port: int = 443
    unifi_verify_ssl: bool = True
    data_dir: Path = Path("data")
    contact_sheet_dir: Path = Path("contact-sheets")
    # A second, read-only token for the contact sheets alone, so they can be
    # shared without the token that reads the audit log. None: API_TOKEN only.
    contact_sheet_token: str | None = field(default=None, repr=False)
    sync_interval: int = 60
    sync_overlap: int = 600
    backfill_days: int = 7
    event_types: tuple[str, ...] = ()
    contact_sheet_time: time = time(3, 0)
    # None means the system's local zone (UTC in a container unless TZ is set).
    timezone: ZoneInfo | None = None
    contact_sheet_retention_days: int = 30
    contact_sheet_tile_width: int = 640
    contact_sheet_columns: int = 0
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        api_token = _required(env, "API_TOKEN")
        if len(api_token) < 16:
            raise ConfigError("API_TOKEN must be at least 16 characters")

        sheet_token = env.get("CONTACT_SHEET_TOKEN", "").strip() or None
        if sheet_token is not None:
            if len(sheet_token) < 16:
                raise ConfigError("CONTACT_SHEET_TOKEN must be at least 16 characters")
            if sheet_token == api_token:
                raise ConfigError("CONTACT_SHEET_TOKEN must differ from API_TOKEN")

        data_dir = Path(env.get("DATA_DIR", "").strip() or cls.data_dir)
        sheet_dir = Path(
            env.get("CONTACT_SHEET_DIR", "").strip() or cls.contact_sheet_dir
        )
        # Whatever serves the contact sheets must not reach the audit log.
        if data_dir.resolve().is_relative_to(sheet_dir.resolve()):
            raise ConfigError(
                "DATA_DIR must not be CONTACT_SHEET_DIR or inside it: "
                "the contact sheets may be served, the audit log must not be"
            )

        log_level = env.get("LOG_LEVEL", cls.log_level).upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ConfigError(f"LOG_LEVEL {log_level!r} is not a logging level")

        return cls(
            unifi_host=_required(env, "UNIFI_HOST"),
            unifi_username=_required(env, "UNIFI_USERNAME"),
            unifi_password=_required(env, "UNIFI_PASSWORD"),
            api_token=api_token,
            unifi_port=_int(env, "UNIFI_PORT", cls.unifi_port, 1, 65535),
            unifi_verify_ssl=_bool(env, "UNIFI_VERIFY_SSL", cls.unifi_verify_ssl),
            data_dir=data_dir,
            contact_sheet_dir=sheet_dir,
            contact_sheet_token=sheet_token,
            sync_interval=_int(env, "SYNC_INTERVAL", cls.sync_interval, 5, 86400),
            sync_overlap=_int(env, "SYNC_OVERLAP", cls.sync_overlap, 0, 86400),
            backfill_days=_int(env, "BACKFILL_DAYS", cls.backfill_days, 0, 3650),
            event_types=_event_types(env, "EVENT_TYPES"),
            contact_sheet_time=_time(env, "CONTACT_SHEET_TIME", cls.contact_sheet_time),
            timezone=_zone(env, "TZ"),
            contact_sheet_retention_days=_int(
                env,
                "CONTACT_SHEET_RETENTION_DAYS",
                cls.contact_sheet_retention_days,
                0,
                36500,
            ),
            contact_sheet_tile_width=_int(
                env, "CONTACT_SHEET_TILE_WIDTH", cls.contact_sheet_tile_width, 64, 3840
            ),
            contact_sheet_columns=_int(
                env, "CONTACT_SHEET_COLUMNS", cls.contact_sheet_columns, 0, 64
            ),
            host=env.get("HOST", cls.host),
            port=_int(env, "PORT", cls.port, 1, 65535),
            log_level=log_level,
        )
