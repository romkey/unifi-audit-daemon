"""CONTACT_SHEET_DIR/index.html: a page for browsing the contact sheets.

Static and self-contained, so any web server that serves the directory
serves the page too; the gallery script and styles are copied beside it
into assets/. Links are relative ("2026-10-20.jpg"), so it works at any
path. Without JavaScript it shows the newest sheet's cameras, each
linking to its full-resolution snapshot, and links to every sheet; with
it, Older/Newer (and the arrow keys) page through them, the URL's
#YYYY-MM-DD names the one shown, and a thumbnail opens a lightbox that
steps through that night's full-resolution snapshots. Sheets taken
before there were snapshots show as the single composite image.
"""

import html
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

INDEX_NAME = "index.html"
ASSETS_DIR = "assets"
# GLightbox (vendor/glightbox), copied into ASSETS_DIR.
ASSETS = ("glightbox.min.js", "glightbox.min.css", "LICENSE.md")


@dataclass(frozen=True)
class Picture:
    """One camera in a night's gallery; paths relative to CONTACT_SHEET_DIR."""

    name: str
    image: str | None = None  # full resolution; None: no snapshot
    thumb: str | None = None
    note: str = "no image"


@dataclass(frozen=True)
class Sheet:
    day: date
    # None: taken before snapshots were kept, so only the composite.
    pictures: Sequence[Picture] | None = None


# Filled in with str.replace rather than str.format or string.Template,
# which would trip over the braces and dollar signs in the CSS and script.
TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Contact sheets</title>
<link rel="stylesheet" href="assets/glightbox.min.css">
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0;
    background: #181818;
    color: #ebebeb;
    font: 16px/1.4 system-ui, sans-serif;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
  }
  h1 { flex: 1; margin: 0; font-size: 1.25rem; font-weight: 600; }
  nav { display: flex; gap: 8px; }
  nav[hidden] { display: none; }
  button {
    min-width: 6.5rem;
    padding: 8px 12px;
    border: 1px solid #555;
    border-radius: 6px;
    background: #2a2a2a;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  button:disabled { opacity: 0.4; cursor: default; }
  button:focus-visible, a:focus-visible {
    outline: 2px solid #8ab4f8;
    outline-offset: 2px;
  }
  main { padding: 0 16px 16px; }
  #sheet {
    display: block;
    max-width: 100%;
    height: auto;
    margin: 0 auto;
  }
  .cameras {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .cameras li { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .cameras a, .cameras .missing {
    display: flex;
    align-items: center;
    justify-content: center;
    aspect-ratio: 16 / 9;
    border-radius: 4px;
    background: #000;
    overflow: hidden;
  }
  .cameras img { width: 100%; height: 100%; object-fit: contain; }
  .cameras a:hover img { opacity: 0.85; }
  .cameras .missing { background: #3c3c3c; color: #aaa; }
  .cameras span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .whole { margin: 16px 0 0; color: #aaa; }
  /* The lightbox's caption, dark to match. */
  .glightbox-clean .gslide-description { background: #202020; }
  .glightbox-clean .gslide-title { margin: 0; color: #ebebeb; }
  .empty { padding: 48px 16px; text-align: center; color: #aaa; }
  details { margin-top: 24px; color: #aaa; }
  summary { cursor: pointer; }
  details ul {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(8rem, 1fr));
    gap: 4px 16px;
    padding: 8px 0 0;
    list-style: none;
  }
  a { color: #8ab4f8; }
</style>
</head>
<body>
<header>
  <h1 id="title">__TITLE__</h1>
  <nav id="pager" hidden>
    <button id="older" type="button">&larr; Older</button>
    <button id="newer" type="button">Newer &rarr;</button>
  </nav>
</header>
<main>
  __BODY__
</main>
<script src="assets/glightbox.min.js"></script>
<script>
(function () {
  var sheets = __SHEETS__;
  if (!sheets.length) return;
  var view = document.getElementById("view");
  var title = document.getElementById("title");
  var older = document.getElementById("older");
  var newer = document.getElementById("newer");
  document.getElementById("pager").hidden = false;

  // Without the gallery script (it failed to load), a thumbnail is
  // still a plain link to its snapshot.
  var lightbox = null;
  var lightboxOpen = false;
  var slides = [];  // the night shown
  if (window.GLightbox) {
    lightbox = window.GLightbox({ elements: [], loop: true });
    lightbox.on("open", function () { lightboxOpen = true; });
    lightbox.on("close", function () {
      lightboxOpen = false;
      // Paging to another night while it was open (e.g. Back) left its
      // slides in place, since swapping them mid-close removes the slide
      // whose animation finishes the close. GLightbox still counts itself
      // open until this handler returns, so swap them on the next tick.
      setTimeout(function () { lightbox.setElements(slides); }, 0);
    });
  }

  // GLightbox shows the title as HTML; camera names are text.
  function escape(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }
  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    for (var key in attrs) node.setAttribute(key, attrs[key]);
    if (text) node.textContent = text;
    return node;
  }

  function gallery(sheet) {
    var list = el("ul", { "class": "cameras" });
    slides = [];
    sheet.pictures.forEach(function (p) {
      var item = el("li", {});
      if (p.image) {
        var link = el("a", { href: p.image, "data-slide": slides.length });
        link.appendChild(el("img", { src: p.thumb, alt: p.name, loading: "lazy" }));
        item.appendChild(link);
        slides.push({
          href: p.image,
          type: "image",
          alt: p.name,
          title: escape(p.name + " \\u00b7 " + sheet.date),
        });
      } else {
        item.appendChild(el("div", { "class": "missing" }, p.note));
      }
      item.appendChild(el("span", {}, p.name));
      list.appendChild(item);
    });
    if (lightbox && !lightboxOpen) lightbox.setElements(slides);
    return list;
  }

  // sheets is newest first, so "older" is a higher index.
  function current() {
    for (var i = 0; i < sheets.length; i++) {
      if (sheets[i].date === location.hash.slice(1)) return i;
    }
    return 0;
  }
  function show() {
    if (lightboxOpen) lightbox.close();
    var i = current();
    var sheet = sheets[i];
    view.textContent = "";
    if (sheet.pictures) {
      view.appendChild(gallery(sheet));
      var whole = el("p", { "class": "whole" });
      whole.appendChild(
        el("a", { href: sheet.date + ".jpg" }, "The whole sheet as one image")
      );
      view.appendChild(whole);
    } else {
      view.appendChild(el("img", {
        id: "sheet",
        src: sheet.date + ".jpg",
        alt: "Contact sheet for " + sheet.date,
      }));
    }
    title.textContent = "Contact sheet: " + sheet.date;
    older.disabled = i >= sheets.length - 1;
    newer.disabled = i <= 0;
  }
  function go(step) {
    var i = current() + step;
    if (i >= 0 && i < sheets.length) location.hash = sheets[i].date;
  }
  view.addEventListener("click", function (e) {
    var link = e.target.closest("a[data-slide]");
    if (!link || !lightbox || e.button !== 0) return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    lightbox.openAt(Number(link.getAttribute("data-slide")));
  });
  older.addEventListener("click", function () { go(1); });
  newer.addEventListener("click", function () { go(-1); });
  document.addEventListener("keydown", function (e) {
    // While the lightbox is open, the arrow keys step through it.
    if (lightboxOpen || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "ArrowLeft") go(1);
    if (e.key === "ArrowRight") go(-1);
  });
  window.addEventListener("hashchange", show);
  show();
})();
</script>
</body>
</html>
"""


def render_index(sheets: Sequence[Sheet]) -> str:
    """The page for these sheets, newest first."""
    if sheets:
        latest = html.escape(sheets[0].day.isoformat())
        title = f"Contact sheet: {latest}"
        links = "\n".join(
            f'      <li><a href="{html.escape(n)}.jpg">{html.escape(n)}</a></li>'
            for n in (s.day.isoformat() for s in sheets)
        )
        body = (
            f'<div id="view">\n{_view(sheets[0])}\n  </div>\n'
            f"  <details>\n"
            f"    <summary>All {len(sheets)} contact sheets</summary>\n"
            f"    <ul>\n{links}\n    </ul>\n"
            f"  </details>"
        )
    else:
        title = "Contact sheets"
        body = '<p class="empty">No contact sheets yet.</p>'
    # "</" escaped so a camera name can never close the <script> early.
    sheets_json = json.dumps([_data(s) for s in sheets]).replace("</", "<\\/")
    return (
        TEMPLATE.replace("__TITLE__", title)
        .replace("__BODY__", body)
        .replace("__SHEETS__", sheets_json)
    )


def _view(sheet: Sheet) -> str:
    """The sheet as the script would show it, for readers without it."""
    day = html.escape(sheet.day.isoformat())
    if sheet.pictures is None:
        return f'  <img id="sheet" src="{day}.jpg" alt="Contact sheet for {day}">'
    items = []
    for p in sheet.pictures:
        name = html.escape(p.name)
        if p.image and p.thumb:
            picture = (
                f'<a href="{html.escape(p.image)}">'
                f'<img src="{html.escape(p.thumb)}" alt="{name}" loading="lazy"></a>'
            )
        else:
            picture = f'<div class="missing">{html.escape(p.note)}</div>'
        items.append(f"    <li>{picture}<span>{name}</span></li>")
    return (
        '  <ul class="cameras">\n'
        + "\n".join(items)
        + f'\n  </ul>\n  <p class="whole"><a href="{day}.jpg">'
        "The whole sheet as one image</a></p>"
    )


def _data(sheet: Sheet) -> dict[str, Any]:  # Any: JSON for the script
    pictures = None
    if sheet.pictures is not None:
        pictures = [
            {
                "name": p.name,
                "image": p.image if p.thumb else None,
                "thumb": p.thumb if p.image else None,
                "note": p.note,
            }
            for p in sheet.pictures
        ]
    return {"date": sheet.day.isoformat(), "pictures": pictures}
