"""The audit log on disk: append-only JSON Lines, no database.

    DATA_DIR/audit/YYYY-MM-DD.jsonl   one record per line, by the UTC day it
                                      was recorded
    DATA_DIR/state.json               how far Protect has been read

A record is written once, the first time the daemon sees the event, and
never changed. `recorded_at` only ever grows down the files, so a reader
asking for "everything recorded since T" can't miss a record that was
backfilled after a later one: that is what makes catching up after
downtime safe for clients (see docs/overview.md).
"""

import json
import logging
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

type Record = dict[str, Any]  # Any: JSON, including Protect's raw event


def parse_time(text: str) -> datetime:
    """An ISO 8601 time; one without a zone is taken as UTC."""
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class SyncState:
    # Protect has been read up to here. None: never synced.
    cursor: datetime | None = None


class AuditStore:
    def __init__(self, data_dir: Path) -> None:
        self.directory = data_dir / "audit"
        self.state_file = data_dir / "state.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        for path in self.directory.glob("*.jsonl"):
            _drop_partial_line(path)

    def load_state(self) -> SyncState:
        try:
            data = json.loads(self.state_file.read_text())
        except FileNotFoundError:
            return SyncState()
        cursor = data.get("cursor")
        return SyncState(cursor=parse_time(cursor) if cursor else None)

    def save_state(self, state: SyncState) -> None:
        data = {"cursor": state.cursor.isoformat() if state.cursor else None}
        _write_atomically(self.state_file, json.dumps(data).encode())

    def append(self, records: list[Record]) -> None:
        """Add records, which must carry `recorded_at`, in order."""
        by_file: dict[Path, list[str]] = {}
        for record in records:
            day = parse_time(record["recorded_at"]).astimezone(UTC).date()
            line = json.dumps(record, separators=(",", ":")) + "\n"
            by_file.setdefault(self._file(day.isoformat()), []).append(line)
        for path, lines in by_file.items():
            with path.open("a", encoding="utf-8") as f:
                f.write("".join(lines))
                f.flush()
                os.fsync(f.fileno())

    def read(self, since: datetime | None = None) -> Iterator[Record]:
        """Records recorded at or after `since` (all of them if None)."""
        first_day = since.astimezone(UTC).date().isoformat() if since else ""
        for path in sorted(self.directory.glob("*.jsonl")):
            if path.stem < first_day:
                continue
            for record in _read_lines(path):
                if since is None or parse_time(record["recorded_at"]) >= since:
                    yield record

    def known_ids(self, started_since: datetime) -> set[str]:
        """Ids of stored events that started at or after `started_since`.

        Such an event can't have been recorded before it started, so only
        files from that day on need reading.
        """
        return {
            record["id"]
            for record in self.read(started_since)
            if parse_time(record["start"]) >= started_since
        }

    def _file(self, day: str) -> Path:
        return self.directory / f"{day}.jsonl"


def _read_lines(path: Path) -> Iterator[Record]:
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            # A line without its newline is still being written (or was cut
            # short by a crash); it isn't a record yet.
            if not line.endswith("\n"):
                break
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("%s:%d is not JSON; skipping it", path, number)
                continue
            if isinstance(record, Mapping):
                yield dict(record)


def _drop_partial_line(path: Path) -> None:
    """Cut off a last line a crash left without its newline.

    Otherwise the next append would join it, spoiling a good record.
    """
    with path.open("rb+") as f:
        size = f.seek(0, os.SEEK_END)
        if size == 0:
            return
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return
        f.seek(0)
        keep = f.read().rfind(b"\n") + 1
        logger.warning("%s ends in a partial line; removing it", path)
        f.truncate(keep)


def _write_atomically(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
