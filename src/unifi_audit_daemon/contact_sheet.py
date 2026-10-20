"""Nightly contact sheet: one still from every camera, on one JPEG.

DATA_DIR/contact-sheets/YYYY-MM-DD.jpg   by the local date it was taken
"""

import asyncio
import io
import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from uiprotect.exceptions import UnifiProtectError

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


@dataclass(frozen=True)
class Tile:
    name: str
    image: bytes | None  # None: offline, or the snapshot failed
    note: str = "no image"


def compose(
    tiles: Sequence[Tile], *, title: str, tile_width: int, columns: int = 0
) -> bytes:
    """Lay the tiles out in a grid under a title; returns a JPEG."""
    columns = columns or max(1, math.ceil(math.sqrt(len(tiles))))
    rows = max(1, math.ceil(len(tiles) / columns))
    tile_height = tile_width * 9 // 16
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
        picture = _decode(tile.image)
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


def _decode(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.convert("RGB")
    except UnidentifiedImageError, OSError:
        logger.warning("a snapshot could not be decoded")
        return None


class ContactSheets:
    def __init__(self, data_dir: Path) -> None:
        self.directory = data_dir / "contact-sheets"
        self.directory.mkdir(parents=True, exist_ok=True)

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

    def save(self, day: date, jpeg: bytes) -> Path:
        path = self.directory / f"{day.isoformat()}.jpg"
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(jpeg)
        temporary.replace(path)
        return path

    def prune(self, today: date, keep_days: int) -> None:
        """Delete sheets older than keep_days (0 keeps them all)."""
        if keep_days <= 0:
            return
        oldest = today - timedelta(days=keep_days - 1)
        for day in self.dates():
            if day < oldest:
                logger.info("deleting contact sheet for %s", day)
                (self.directory / f"{day.isoformat()}.jpg").unlink(missing_ok=True)


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
                return Tile(name, await source.snapshot(camera_id, tile_width))
            except UnifiProtectError as e:
                logger.warning("no snapshot from %s: %s", name, e)
                return Tile(name, None)

    tiles = await asyncio.gather(*(grab(c.id, c.name, c.connected) for c in cameras))
    title = f"{taken_at:%A %Y-%m-%d %H:%M %Z}  ·  {len(cameras) or 'no'} cameras"
    jpeg = await asyncio.to_thread(
        compose, tiles, title=title, tile_width=tile_width, columns=columns
    )
    path = sheets.save(taken_at.date(), jpeg)
    sheets.prune(taken_at.date(), keep_days)
    logger.info("saved contact sheet %s (%d cameras)", path.name, len(cameras))
    return path
