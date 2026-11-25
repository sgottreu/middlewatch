"""novellas.html and the server agree: every /api/novella URL the page uses is
a route, and the page loads the shared assets and install metadata."""
import re
from pathlib import Path

import pytest

from story_pipeline.review import server

PAGE = Path(server.__file__).with_name("novellas.html")
URL = re.compile(r"`(/api/novella[^`]*)`")


def page():
    return PAGE.read_text(encoding="utf-8")


def refs():
    """(method, url) for each /api/novella template literal. POST when it is the
    first argument of api(...) followed by a body, or the job panel's cancel."""
    text, out = page(), set()
    for m in URL.finditer(text):
        before, after = text[:m.start()], text[m.end():m.end() + 3]
        post = (bool(re.search(r"\bapi\(\s*$", before)) and after.lstrip().startswith(",")) \
            or bool(re.search(r"cancel:\s*$", before))
        out.add(("POST" if post else "GET", m.group(1)))
    return sorted(out)


def test_links_shared_assets_and_icons():
    t = page()
    for s in ('href="/common.css"', 'src="/common.js"', 'rel="apple-touch-icon" href="/icon-180.png"',
              'rel="manifest" href="/manifest.webmanifest"', 'rel="icon" href="/icon.svg"',
              'name="viewport"', 'MW.nav($("pages"), "Novellas")', "MW.jobPanel(", "armed("):
        assert s in t


def test_page_does_not_edit_shared_page_list():
    assert "PAGES" not in page()


def test_found_enough_routes():
    methods = {m for m, _ in refs()}
    assert len(refs()) >= 14 and methods == {"GET", "POST"}


def test_every_api_url_is_a_route(client, book, started, runs_dir):
    book.save_draft(1, text="x", summary="", new_details="", flags="")
    for method, url in refs():
        real = (url.replace("${S}", "glasswood").replace("${N}", "1")
                   .replace("${n}", "1").replace("${r.slug}", "glasswood")
                   .replace("${kind}", "draft"))
        assert "${" not in real, url
        r = client.get(real) if method == "GET" else client.post(real, {})
        assert not (r.status == 404 and r.json == {"error": "not found"}), (method, url)
        assert r.status != 500, (method, url, r.text)
