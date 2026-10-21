import asyncio
import io
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from tests.fakes import FakeSource, jpeg
from unifi_audit_daemon.contact_sheet import (
    GAP,
    HEADER_HEIGHT,
    LABEL_HEIGHT,
    ContactSheets,
    Tile,
    compose,
    make_contact_sheet,
)

PACIFIC = ZoneInfo("America/Los_Angeles")


def size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        return image.size


def test_compose_lays_out_a_grid() -> None:
    tiles = [Tile(f"cam{i}", jpeg()) for i in range(5)]
    sheet = compose(tiles, title="t", tile_width=160)
    # 5 tiles: 3 columns (ceil sqrt), 2 rows; tiles are 160x90 plus labels.
    assert size(sheet) == (
        GAP + 3 * (160 + GAP),
        HEADER_HEIGHT + 2 * (90 + LABEL_HEIGHT + GAP),
    )


def test_compose_fixed_columns_and_odd_shapes(caplog: pytest.LogCaptureFixture) -> None:
    tiles = [
        Tile("tall", jpeg(90, 160)),
        Tile("broken", b"not a jpeg"),
        Tile("offline", None, "offline"),
    ]
    with caplog.at_level(logging.WARNING):
        sheet = compose(tiles, title="t", tile_width=160, columns=3)
    assert size(sheet)[0] == GAP + 3 * (160 + GAP)
    assert "could not be decoded" in caplog.text


def test_compose_with_no_cameras() -> None:
    assert size(compose([], title="t", tile_width=160)) == (
        GAP + 160 + GAP,
        HEADER_HEIGHT + 90 + LABEL_HEIGHT + GAP,
    )


def test_sheets_store(tmp_path: Path) -> None:
    sheets = ContactSheets(tmp_path / "sheets")
    assert sheets.dates() == []
    assert sheets.latest() is None
    for day in (1, 3, 2):
        sheets.save(date(2026, 10, day), b"jpeg")
    (sheets.directory / "notes.jpg").write_bytes(b"ignored")

    assert sheets.dates() == [date(2026, 10, d) for d in (3, 2, 1)]
    assert sheets.latest() == sheets.directory / "2026-10-03.jpg"
    assert sheets.path(date(2026, 10, 9)) is None

    sheets.prune(date(2026, 10, 3), keep_days=0)
    assert len(sheets.dates()) == 3
    sheets.prune(date(2026, 10, 3), keep_days=2)
    assert sheets.dates() == [date(2026, 10, 3), date(2026, 10, 2)]


def index(sheets: ContactSheets) -> str:
    return (sheets.directory / "index.html").read_text()


def test_index_follows_the_sheets(tmp_path: Path) -> None:
    directory = tmp_path / "sheets"
    directory.mkdir()
    (directory / "2026-09-30.jpg").write_bytes(b"from before the page existed")

    sheets = ContactSheets(directory)
    assert 'src="2026-09-30.jpg"' in index(sheets)

    sheets.save(date(2026, 10, 2), b"jpeg")
    assert 'src="2026-10-02.jpg"' in index(sheets)
    assert "All 2 contact sheets" in index(sheets)

    sheets.prune(date(2026, 10, 2), keep_days=1)
    assert sheets.dates() == [date(2026, 10, 2)]
    assert "2026-09-30" not in index(sheets)

    # Pruning nothing leaves the page alone.
    before = (directory / "index.html").stat().st_mtime_ns
    sheets.prune(date(2026, 10, 2), keep_days=1)
    assert (directory / "index.html").stat().st_mtime_ns == before
    assert sorted(p.name for p in directory.iterdir()) == [
        "2026-10-02.jpg",
        "index.html",
    ]


def test_make_contact_sheet(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source = FakeSource()
    source.failing.add("cam2")
    sheets = ContactSheets(tmp_path / "sheets")
    sheets.save(date(2026, 9, 1), b"old")
    taken_at = datetime(2026, 10, 20, 3, 0, tzinfo=PACIFIC)

    with caplog.at_level(logging.INFO):
        path = asyncio.run(
            make_contact_sheet(
                source, sheets, taken_at, tile_width=160, columns=0, keep_days=30
            )
        )

    assert path == sheets.directory / "2026-10-20.jpg"
    assert size(path.read_bytes()) == (
        GAP + 2 * (160 + GAP),
        HEADER_HEIGHT + 2 * (90 + LABEL_HEIGHT + GAP),
    )
    assert sheets.dates() == [date(2026, 10, 20)]
    assert "no snapshot from Back: console unreachable" in caplog.text


def test_make_contact_sheet_without_cameras(tmp_path: Path) -> None:
    source = FakeSource()
    source.camera_list = []
    path = asyncio.run(
        make_contact_sheet(
            source,
            ContactSheets(tmp_path / "sheets"),
            datetime(2026, 10, 20, 3, 0, tzinfo=PACIFIC),
            tile_width=160,
            columns=0,
            keep_days=0,
        )
    )
    assert path.is_file()
