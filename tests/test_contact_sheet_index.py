import json
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any

from unifi_audit_daemon.contact_sheet_index import Picture, Sheet, render_index

# Sheets taken before snapshots were kept.
SHEETS = [Sheet(date(2026, 10, d)) for d in (21, 20, 18)]
GALLERY = Sheet(
    date(2026, 10, 22),
    [
        Picture("Front", "2026-10-22/01-front.jpg", "2026-10-22/01-front-thumb.jpg"),
        Picture("Shop", note="offline"),
    ],
)


class Tags(HTMLParser):
    """Collects each start tag's attributes, to check the page parses."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def tags(page: str, name: str) -> list[dict[str, str | None]]:
    parser = Tags()
    parser.feed(page)
    return [attrs for tag, attrs in parser.tags if tag == name]


def embedded(page: str) -> list[dict[str, Any]]:  # Any: the page's JSON
    match = re.search(r"var sheets = (\[.*?\]);\n", page)
    assert match
    sheets: list[dict[str, Any]] = json.loads(match.group(1))
    return sheets


def embedded_dates(page: str) -> list[str]:
    return [sheet["date"] for sheet in embedded(page)]


def test_shows_the_newest_and_links_them_all() -> None:
    page = render_index(SHEETS)
    assert tags(page, "img") == [
        {
            "id": "sheet",
            "src": "2026-10-21.jpg",
            "alt": "Contact sheet for 2026-10-21",
        }
    ]
    assert [a["href"] for a in tags(page, "a")] == [
        "2026-10-21.jpg",
        "2026-10-20.jpg",
        "2026-10-18.jpg",
    ]
    assert "<title>Contact sheets</title>" in page
    assert "Contact sheet: 2026-10-21</h1>" in page
    assert "All 3 contact sheets" in page
    assert embedded_dates(page) == ["2026-10-21", "2026-10-20", "2026-10-18"]


def test_shows_the_newest_nights_cameras() -> None:
    page = render_index([GALLERY, *SHEETS])
    assert tags(page, "img") == [
        {
            "src": "2026-10-22/01-front-thumb.jpg",
            "alt": "Front",
            "loading": "lazy",
        }
    ]
    links = [a["href"] for a in tags(page, "a")]
    assert links[:2] == ["2026-10-22/01-front.jpg", "2026-10-22.jpg"]
    assert '<div class="missing">offline</div><span>Shop</span>' in page
    assert embedded(page)[0] == {
        "date": "2026-10-22",
        "pictures": [
            {
                "name": "Front",
                "image": "2026-10-22/01-front.jpg",
                "thumb": "2026-10-22/01-front-thumb.jpg",
                "note": "no image",
            },
            {"name": "Shop", "image": None, "thumb": None, "note": "offline"},
        ],
    }
    assert embedded(page)[1] == {"date": "2026-10-21", "pictures": None}
    assert {"href": "assets/glightbox.min.css", "rel": "stylesheet"} in tags(
        page, "link"
    )
    assert {"src": "assets/glightbox.min.js"} in tags(page, "script")


def test_camera_names_are_escaped() -> None:
    name = '</script><b x="1">'
    page = render_index(
        [Sheet(date(2026, 10, 22), [Picture(name, "a.jpg", "b.jpg"), Picture(name)])]
    )
    # Escaped in the HTML, and can't close the script it's embedded in.
    assert "<span>&lt;/script&gt;&lt;b x=&quot;1&quot;&gt;</span>" in page
    assert "</script><b" not in page
    assert embedded(page)[0]["pictures"][0]["name"] == name


def test_links_are_relative() -> None:
    page = render_index([GALLERY, *SHEETS])
    for attrs in tags(page, "a") + tags(page, "img") + tags(page, "link"):
        target = attrs.get("href") or attrs.get("src") or ""
        assert not target.startswith("/")
        assert ":" not in target


def test_empty() -> None:
    page = render_index([])
    assert tags(page, "img") == []
    assert "No contact sheets yet." in page
    assert embedded_dates(page) == []
    assert "__" not in page  # every placeholder was filled in
