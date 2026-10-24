import asyncio
import io
import logging
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from tests.fakes import FakeSource, jpeg
from unifi_audit_daemon import contact_sheet
from unifi_audit_daemon.contact_sheet import (
    GAP,
    HEADER_HEIGHT,
    LABEL_HEIGHT,
    ContactSheets,
    Tile,
    compose,
    make_contact_sheet,
    thumbnail,
)
from unifi_audit_daemon.contact_sheet_index import Picture

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
        "assets",
        "index.html",
    ]
    assert sorted(p.name for p in (directory / "assets").iterdir()) == [
        "LICENSE.md",
        "glightbox.min.css",
        "glightbox.min.js",
    ]


def test_thumbnail() -> None:
    thumb = thumbnail(jpeg(1920, 1080), 160)
    assert thumb is not None
    assert size(thumb) == (160, 90)
    portrait = thumbnail(jpeg(1080, 1920), 160)
    assert portrait is not None
    assert size(portrait)[1] == 90
    assert thumbnail(None, 160) is None
    assert thumbnail(b"not a jpeg", 160) is None


def test_save_keeps_snapshots_and_thumbnails(tmp_path: Path) -> None:
    sheets = ContactSheets(tmp_path / "sheets")
    full = jpeg(1920, 1080)
    day = date(2026, 10, 20)
    tiles = [
        Tile("Front Door", full),
        Tile("Shop", None, "offline"),
        Tile("Bad", b"not a jpeg"),
        Tile("Ünï", jpeg(1920, 1080, "blue")),
    ]
    sheets.save(day, b"sheet", tiles, tile_width=160)

    folder = sheets.directory / "2026-10-20"
    assert sorted(p.name for p in folder.iterdir()) == [
        "01-front-door-thumb.jpg",
        "01-front-door.jpg",
        "04-n-thumb.jpg",
        "04-n.jpg",
        "cameras.json",
    ]
    # Full resolution exactly as the camera sent it.
    assert (folder / "01-front-door.jpg").read_bytes() == full
    assert size((folder / "01-front-door-thumb.jpg").read_bytes()) == (160, 90)
    assert sheets.pictures(day) == [
        Picture(
            "Front Door",
            "2026-10-20/01-front-door.jpg",
            "2026-10-20/01-front-door-thumb.jpg",
        ),
        Picture("Shop", note="offline"),
        Picture("Bad"),
        Picture("Ünï", "2026-10-20/04-n.jpg", "2026-10-20/04-n-thumb.jpg"),
    ]
    assert 'href="2026-10-20/01-front-door.jpg"' in index(sheets)

    # A retake replaces the night's snapshots whole.
    sheets.save(day, b"sheet", [Tile("", jpeg())], tile_width=160)
    assert sorted(p.name for p in folder.iterdir()) == [
        "01-camera-thumb.jpg",
        "01-camera.jpg",
        "cameras.json",
    ]
    assert not any(p.name.startswith(".") for p in sheets.directory.iterdir())

    sheets.prune(date(2026, 11, 30), keep_days=1)
    assert sheets.dates() == []
    assert not folder.exists()


def snapshot_links(sheets: ContactSheets) -> list[str]:
    return re.findall(r'(?:href|src)="(\d{4}-\d{2}-\d{2}/[^"]+)"', index(sheets))


def test_a_failed_save_leaves_the_page_matching_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = ContactSheets(tmp_path / "sheets")
    day = date(2026, 10, 20)
    sheets.save(day, b"sheet", [Tile("Front", jpeg())], tile_width=160)
    write = contact_sheet._write_atomically

    def no_room_for_sheets(path: Path, data: bytes) -> None:
        if path.name == "2026-10-20.jpg":
            raise OSError("disk full")
        write(path, data)

    # The sheet can't be written: the night keeps its old snapshots.
    monkeypatch.setattr(contact_sheet, "_write_atomically", no_room_for_sheets)
    with pytest.raises(OSError, match="disk full"):
        sheets.save(day, b"retake", [Tile("Back", jpeg())], tile_width=160)
    assert snapshot_links(sheets) == [
        "2026-10-20/01-front.jpg",
        "2026-10-20/01-front-thumb.jpg",
    ]

    # The snapshots can't be: the new sheet, with the old snapshots.
    monkeypatch.setattr(contact_sheet, "_write_atomically", write)

    def broken(data: bytes | None, tile_width: int) -> bytes | None:
        raise OSError("disk full")

    monkeypatch.setattr(contact_sheet, "thumbnail", broken)
    with pytest.raises(OSError, match="disk full"):
        sheets.save(day, b"retake", [Tile("Back", jpeg())], tile_width=160)
    assert (sheets.directory / "2026-10-20.jpg").read_bytes() == b"retake"
    for link in snapshot_links(sheets):
        assert (sheets.directory / link).is_file()
    assert len(snapshot_links(sheets)) == 2


def test_prune_deletes_snapshots_that_lost_their_sheet(tmp_path: Path) -> None:
    sheets = ContactSheets(tmp_path / "sheets")
    sheets.save(date(2026, 10, 1), b"sheet", [Tile("Front", jpeg())], tile_width=160)
    sheets.save(date(2026, 10, 20), b"sheet")
    (sheets.directory / "2026-10-01.jpg").unlink()

    sheets.prune(date(2026, 10, 20), keep_days=7)
    assert sorted(p.name for p in sheets.directory.iterdir()) == [
        "2026-10-20.jpg",
        "assets",
        "index.html",
    ]


def test_sheets_without_snapshots(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sheets = ContactSheets(tmp_path / "sheets")
    sheets.save(date(2026, 10, 1), b"from before snapshots were kept")
    assert sheets.pictures(date(2026, 10, 1)) is None
    assert 'src="2026-10-01.jpg"' in index(sheets)

    broken = sheets.directory / "2026-10-02"
    broken.mkdir()
    (broken / "cameras.json").write_text('{"cameras": [{"note": "no name"}]}')
    with caplog.at_level(logging.WARNING):
        assert sheets.pictures(date(2026, 10, 2)) is None
    assert "can't read" in caplog.text


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
    # Full resolution from every connected camera.
    assert source.snapshot_widths == [None, None]
    assert sheets.pictures(date(2026, 10, 20)) == [
        Picture("Front", "2026-10-20/01-front.jpg", "2026-10-20/01-front-thumb.jpg"),
        Picture("Back"),
        Picture("Shop", note="offline"),
    ]
    assert (sheets.directory / "2026-10-20" / "01-front.jpg").read_bytes() == (
        source.images["cam1"]
    )


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
