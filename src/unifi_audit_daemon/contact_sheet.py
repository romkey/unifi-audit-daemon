"""Nightly contact sheet: one still from every camera, on one JPEG, and
each camera's full-resolution snapshot with a thumbnail of it.

    CONTACT_SHEET_DIR/YYYY-MM-DD.jpg   the sheet, by the local date taken
    CONTACT_SHEET_DIR/YYYY-MM-DD/      that night's snapshots:
        cameras.json                   every camera, in sheet order
        01-front-door.jpg              full resolution, as the camera sent it
        01-front-door-thumb.jpg        fitted to a sheet tile
    CONTACT_SHEET_DIR/index.html       a page for browsing them
    CONTACT_SHEET_DIR/assets/          the page's gallery script and styles

Sheets taken before snapshots were kept have no directory; the page
shows them as the single image.

The directory holds nothing else, so it can be served on its own, apart
from the audit log in DATA_DIR.
"""

import asyncio
import io
import json
import logging
import math
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from uiprotect.exceptions import UnifiProtectError

from unifi_audit_daemon.contact_sheet_index import (
    ASSETS,
    ASSETS_DIR,
    INDEX_NAME,
    Picture,
    Sheet,
    render_index,
)
from unifi_audit_daemon.protect import ProtectSource

logger = logging.getLogger(__name__)

BACKGROUND = (24, 24, 24)
EMPTY_TILE = (60, 60, 60)
TEXT = (235, 235, 235)
LABEL_HEIGHT = 32
HEADER_HEIGHT = 48
GAP = 8
# Snapshots fetched at once; the console is also busy recording.
CONCURRENCY = 4
SHEET_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GALLERY_NAME = "cameras.json"


@dataclass(frozen=True)
class Tile:
    name: str
    image: bytes | None  # None: offline, or the snapshot failed
    note: str = "no image"


def _tile_size(tile_width: int) -> tuple[int, int]:
    return tile_width, tile_width * 9 // 16


def compose(
    tiles: Sequence[Tile], *, title: str, tile_width: int, columns: int = 0
) -> bytes:
    """Lay the tiles out in a grid under a title; returns a JPEG."""
    columns = columns or max(1, math.ceil(math.sqrt(len(tiles))))
    rows = max(1, math.ceil(len(tiles) / columns))
    tile_height = _tile_size(tile_width)[1]
    cell_height = tile_height + LABEL_HEIGHT
    width = GAP + columns * (tile_width + GAP)
    height = HEADER_HEIGHT + rows * (cell_height + GAP)

    sheet = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    title_font = ImageFont.load_default(size=24)
    label_font = ImageFont.load_default(size=18)
    draw.text((GAP, HEADER_HEIGHT // 2), title, fill=TEXT, font=title_font, anchor="lm")

    for index, tile in enumerate(tiles):
        x = GAP + (index % columns) * (tile_width + GAP)
        y = HEADER_HEIGHT + (index // columns) * (cell_height + GAP)
        picture = _decode(tile.image, (tile_width, tile_height))
        if picture is None:
            draw.rectangle((x, y, x + tile_width - 1, y + tile_height - 1), EMPTY_TILE)
            draw.text(
                (x + tile_width // 2, y + tile_height // 2),
                tile.note,
                fill=TEXT,
                font=label_font,
                anchor="mm",
            )
        else:
            # Fit inside the 16:9 cell, centred, whatever the camera's shape.
            picture.thumbnail((tile_width, tile_height))
            sheet.paste(
                picture,
                (
                    x + (tile_width - picture.width) // 2,
                    y + (tile_height - picture.height) // 2,
                ),
            )
        draw.text(
            (x + 4, y + tile_height + LABEL_HEIGHT // 2),
            tile.name,
            fill=TEXT,
            font=label_font,
            anchor="lm",
        )

    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def thumbnail(data: bytes | None, tile_width: int) -> bytes | None:
    """A JPEG of the snapshot fitted to a tile, or None if it won't decode."""
    size = _tile_size(tile_width)
    picture = _decode(data, size)
    if picture is None:
        return None
    picture.thumbnail(size)
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def _decode(data: bytes | None, size: tuple[int, int]) -> Image.Image | None:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            # A JPEG can decode at 1/2, 1/4 or 1/8 scale, much faster than
            # decoding a 4K snapshot whole only to shrink it.
            image.draft("RGB", size)
            return image.convert("RGB")
    except UnidentifiedImageError, OSError:
        logger.warning("a snapshot could not be decoded")
        return None


class ContactSheets:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._copy_assets()
        # Brings the page up to date with whatever is already here, e.g.
        # sheets taken before the page existed.
        self.write_index()

    def dates(self) -> list[date]:
        """Dates with a sheet, newest first."""
        return sorted(
            (
                date.fromisoformat(p.stem)
                for p in self.directory.glob("*.jpg")
                if SHEET_NAME.match(p.stem)
            ),
            reverse=True,
        )

    def path(self, day: date) -> Path | None:
        path = self.directory / f"{day.isoformat()}.jpg"
        return path if path.is_file() else None

    def latest(self) -> Path | None:
        dates = self.dates()
        return self.path(dates[0]) if dates else None

    def pictures(self, day: date) -> list[Picture] | None:
        """The night's snapshots, or None if it has none (or they're unreadable)."""
        folder = day.isoformat()
        path = self.directory / folder / GALLERY_NAME
        try:
            cameras = json.loads(path.read_text())["cameras"]
            return [_picture(folder, camera) for camera in cameras]
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning("can't read %s: %s", path, e)
            return None

    def save(
        self,
        day: date,
        jpeg: bytes,
        tiles: Sequence[Tile] | None = None,
        *,
        tile_width: int = 640,
    ) -> Path:
        """Save the sheet and, given its tiles, their snapshots and thumbnails.

        Slow with full-resolution tiles; run it off the event loop.
        """
        if tiles is not None:
            self._save_pictures(day, tiles, tile_width)
        path = self.directory / f"{day.isoformat()}.jpg"
        _write_atomically(path, jpeg)
        self.write_index()
        return path

    def _save_pictures(self, day: date, tiles: Sequence[Tile], tile_width: int) -> None:
        # Built beside the night's folder and swapped in, so a page never
        # lists a snapshot that isn't there yet; a retake replaces it whole.
        final = self.directory / day.isoformat()
        building = final.with_name(f".{final.name}.new")
        old = final.with_name(f".{final.name}.old")
        shutil.rmtree(building, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        building.mkdir()
        cameras: list[dict[str, str]] = []
        for number, tile in enumerate(tiles, 1):
            thumb = thumbnail(tile.image, tile_width)
            if tile.image is None or thumb is None:
                cameras.append({"name": tile.name, "note": tile.note})
                continue
            stem = f"{number:02d}-{_slug(tile.name)}"
            (building / f"{stem}.jpg").write_bytes(tile.image)
            (building / f"{stem}-thumb.jpg").write_bytes(thumb)
            cameras.append(
                {
                    "name": tile.name,
                    "image": f"{stem}.jpg",
                    "thumb": f"{stem}-thumb.jpg",
                }
            )
        (building / GALLERY_NAME).write_text(
            json.dumps({"cameras": cameras}, indent=2, ensure_ascii=False) + "\n"
        )
        if final.exists():
            final.replace(old)
        building.replace(final)
        shutil.rmtree(old, ignore_errors=True)

    def write_index(self) -> None:
        """Rewrite index.html to list the sheets now in the directory."""
        page = render_index([Sheet(day, self.pictures(day)) for day in self.dates()])
        _write_atomically(self.directory / INDEX_NAME, page.encode())

    def _copy_assets(self) -> None:
        # Every start, so an upgrade brings its version of the page's script.
        assets = self.directory / ASSETS_DIR
        assets.mkdir(exist_ok=True)
        vendor = resources.files("unifi_audit_daemon").joinpath("vendor", "glightbox")
        for name in ASSETS:
            _write_atomically(assets / name, vendor.joinpath(name).read_bytes())

    def prune(self, today: date, keep_days: int) -> None:
        """Delete sheets older than keep_days (0 keeps them all)."""
        if keep_days <= 0:
            return
        oldest = today - timedelta(days=keep_days - 1)
        old = [day for day in self.dates() if day < oldest]
        for day in old:
            logger.info("deleting contact sheet for %s", day)
            (self.directory / f"{day.isoformat()}.jpg").unlink(missing_ok=True)
            shutil.rmtree(self.directory / day.isoformat(), ignore_errors=True)
        if old:
            self.write_index()


def _picture(folder: str, camera: Any) -> Picture:  # Any: from cameras.json
    name = str(camera["name"])
    image, thumb = camera.get("image"), camera.get("thumb")
    if isinstance(image, str) and isinstance(thumb, str):
        return Picture(name, f"{folder}/{image}", f"{folder}/{thumb}")
    return Picture(name, note=str(camera.get("note") or "no image"))


def _slug(name: str) -> str:
    """A file-name-safe form of a camera's name: "Front Door" -> "front-door"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "camera"


def _write_atomically(path: Path, data: bytes) -> None:
    # A reader (or web server) sees the old file or the new one, never half.
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


async def make_contact_sheet(
    source: ProtectSource,
    sheets: ContactSheets,
    taken_at: datetime,
    *,
    tile_width: int,
    columns: int,
    keep_days: int,
) -> Path:
    """Snapshot every camera, save the sheet, and prune old ones."""
    cameras = await source.cameras()
    limit = asyncio.Semaphore(CONCURRENCY)

    async def grab(camera_id: str, name: str, connected: bool) -> Tile:
        if not connected:
            return Tile(name, None, "offline")
        async with limit:
            try:
                # Full resolution; the sheet and thumbnails are scaled here.
                return Tile(name, await source.snapshot(camera_id))
            except UnifiProtectError as e:
                logger.warning("no snapshot from %s: %s", name, e)
                return Tile(name, None)

    tiles = await asyncio.gather(*(grab(c.id, c.name, c.connected) for c in cameras))
    title = f"{taken_at:%A %Y-%m-%d %H:%M %Z}  ·  {len(cameras) or 'no'} cameras"
    jpeg = await asyncio.to_thread(
        compose, tiles, title=title, tile_width=tile_width, columns=columns
    )
    path = await asyncio.to_thread(
        sheets.save, taken_at.date(), jpeg, tiles, tile_width=tile_width
    )
    sheets.prune(taken_at.date(), keep_days)
    logger.info("saved contact sheet %s (%d cameras)", path.name, len(cameras))
    return path
