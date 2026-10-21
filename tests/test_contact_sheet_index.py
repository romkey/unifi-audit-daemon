import json
import re
from datetime import date
from html.parser import HTMLParser

from unifi_audit_daemon.contact_sheet_index import render_index

DATES = [date(2026, 10, 21), date(2026, 10, 20), date(2026, 10, 18)]


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


def embedded_dates(page: str) -> list[str]:
    match = re.search(r"var dates = (\[.*?\]);", page)
    assert match
    dates: list[str] = json.loads(match.group(1))
    return dates


def test_shows_the_newest_and_links_them_all() -> None:
    page = render_index(DATES)
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


def test_links_are_relative() -> None:
    page = render_index(DATES)
    for attrs in tags(page, "a") + tags(page, "img"):
        target = attrs.get("href") or attrs.get("src") or ""
        assert "/" not in target


def test_empty() -> None:
    page = render_index([])
    assert tags(page, "img") == []
    assert "No contact sheets yet." in page
    assert embedded_dates(page) == []
    assert "__" not in page  # every placeholder was filled in
