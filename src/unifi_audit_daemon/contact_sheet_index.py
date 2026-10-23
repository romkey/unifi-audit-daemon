"""CONTACT_SHEET_DIR/index.html: a page for browsing the contact sheets.

Static and self-contained, so any web server that serves the directory
serves the page too. Links are relative ("2026-10-20.jpg"), so it works
at any path. Without JavaScript it shows the newest sheet and links to
every one; with it, Older/Newer (and the arrow keys) page through them,
and the URL's #YYYY-MM-DD names the one shown.
"""

import html
import json
from collections.abc import Sequence
from datetime import date

INDEX_NAME = "index.html"

# Filled in with str.replace rather than str.format or string.Template,
# which would trip over the braces and dollar signs in the CSS and script.
TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Contact sheets</title>
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
  button:focus-visible { outline: 2px solid #8ab4f8; outline-offset: 2px; }
  main { padding: 0 16px 16px; }
  main img {
    display: block;
    max-width: 100%;
    height: auto;
    margin: 0 auto;
  }
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
<script>
(function () {
  var dates = __DATES__;
  if (!dates.length) return;
  var img = document.getElementById("sheet");
  var title = document.getElementById("title");
  var older = document.getElementById("older");
  var newer = document.getElementById("newer");
  document.getElementById("pager").hidden = false;

  // dates is newest first, so "older" is a higher index.
  function current() {
    var i = dates.indexOf(location.hash.slice(1));
    return i < 0 ? 0 : i;
  }
  function show() {
    var i = current();
    img.src = dates[i] + ".jpg";
    img.alt = "Contact sheet for " + dates[i];
    title.textContent = "Contact sheet: " + dates[i];
    older.disabled = i >= dates.length - 1;
    newer.disabled = i <= 0;
  }
  function go(step) {
    var i = current() + step;
    if (i >= 0 && i < dates.length) location.hash = dates[i];
  }
  older.addEventListener("click", function () { go(1); });
  newer.addEventListener("click", function () { go(-1); });
  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
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


def render_index(dates: Sequence[date]) -> str:
    """The page for these sheet dates, newest first."""
    names = [d.isoformat() for d in dates]
    if names:
        latest = html.escape(names[0])
        title = f"Contact sheet: {latest}"
        links = "\n".join(
            f'      <li><a href="{html.escape(n)}.jpg">{html.escape(n)}</a></li>'
            for n in names
        )
        body = (
            f'<img id="sheet" src="{latest}.jpg" '
            f'alt="Contact sheet for {latest}">\n'
            f"  <details>\n"
            f"    <summary>All {len(names)} contact sheets</summary>\n"
            f"    <ul>\n{links}\n    </ul>\n"
            f"  </details>"
        )
    else:
        title = "Contact sheets"
        body = '<p class="empty">No contact sheets yet.</p>'
    # "</" can't appear in ISO dates, but escaping it keeps the JSON from
    # ever closing the <script> early.
    dates_json = json.dumps(names).replace("</", "<\\/")
    return (
        TEMPLATE.replace("__TITLE__", title)
        .replace("__BODY__", body)
        .replace("__DATES__", dates_json)
    )
