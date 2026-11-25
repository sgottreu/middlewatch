"""ambience.html and the server agree: every /api/ambience URL the page uses is
a route the plugin answers, and the page uses the shared assets and icons."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from story_pipeline.review import ambience_api

PAGE = ambience_api.PAGE
NOT_FOUND = {"error": "not found"}
LITERAL = re.compile(r"""(?P<q>["'`])(?P<url>/(?:api/ambience|ambience-audio)\b[^"'`\n]*)(?P=q)""")


def text():
    return PAGE.read_text(encoding="utf-8")


def references():
    t = text()
    out = set()
    for m in LITERAL.finditer(t):
        url = re.sub(r"\$\{[^}]*\}", "x", m.group("url")).split("?")[0]
        before, after = t[:m.start()], t[m.end():m.end() + 120]
        post = bool(re.search(r"\bapi\(\s*$", before)) and bool(re.match(r"\s*,", after))
        out.add(("POST" if post else "GET", url))
    # jobPanel builds cancel URLs it POSTs and job/log URLs it GETs.
    out = {("POST" if u.endswith("/cancel") else meth, u) for meth, u in out}
    return sorted(out)


def test_extraction_sees_the_routes():
    refs = set(references())
    assert {("GET", "/api/ambience"), ("POST", "/api/ambience/new"),
            ("GET", "/api/ambience/x"), ("POST", "/api/ambience/x/concept"),
            ("GET", "/api/ambience/x/concept"), ("POST", "/api/ambience/x/approve"),
            ("POST", "/api/ambience/x/sound"), ("GET", "/api/ambience/x/job"),
            ("GET", "/api/ambience/x/log"), ("POST", "/api/ambience/x/cancel"),
            ("POST", "/api/ambience/audition"), ("GET", "/api/ambience/audition/job"),
            ("GET", "/api/ambience/audition/log"),
            ("POST", "/api/ambience/audition/cancel")} <= refs, refs


def test_every_url_the_page_uses_is_handled(client):
    unknown = []
    for method, url in references():
        r = client.get(url) if method == "GET" else client.post(url, {})
        if r.status == 404 and r.body.startswith(b"{") and r.json == NOT_FOUND:
            unknown.append(f"{method} {url}")
    assert unknown == []


def test_page_uses_the_shared_assets_and_icons(client):
    t = text()
    for want in ('href="/common.css"', 'src="/common.js"', 'rel="manifest" href="/manifest.webmanifest"',
                 'rel="apple-touch-icon" href="/icon-180.png"', 'href="/icon.svg"',
                 'MW.nav($("pages"), "Ambience")', "MW.jobPanel(", "armed(", "@media (max-width"):
        assert want in t, want
    for asset in ("/common.css", "/common.js", "/icon.svg", "/icon-180.png", "/manifest.webmanifest"):
        assert client.get(asset).status == 200, asset
    assert not re.search(r"""(src|href)=["']https?://""", t)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_inline_script_parses(tmp_path):
    scripts = re.findall(r"<script>(.*?)</script>", text(), re.S)
    assert len(scripts) == 1
    js = tmp_path / "ambience.js"
    js.write_text(scripts[0])
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
