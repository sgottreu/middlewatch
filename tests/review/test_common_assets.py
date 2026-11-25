"""common.css / common.js: served from the allowlist, valid, and consistent with
the server's routes."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from story_pipeline.review import server

from .test_server_http import REVIEW_DIR, assert_not_found

has_node = shutil.which("node") is not None


@pytest.mark.parametrize("name,ctype", sorted(server.ASSETS.items()))
def test_assets_served_with_their_type(client, name, ctype):
    r = client.get("/" + name)
    assert r.status == 200 and r.header("Content-Type") == ctype
    assert r.body == (REVIEW_DIR / name).read_bytes() and r.body


def test_asset_files_exist():
    for name in server.ASSETS:
        assert (REVIEW_DIR / name).is_file(), name


@pytest.mark.parametrize("path", ["/common.css/", "/Common.js", "/static/common.js",
                                  "/common.js/../server.py", "/common.map"])
def test_near_miss_asset_paths_404(client, path):
    assert_not_found(client.get(path))


UI_PAGES = ["app.html", "casting.html", "ambience.html", "novellas.html"]


def test_css_defines_the_shared_palette():
    css = (REVIEW_DIR / "common.css").read_text()
    for var in ("--bg", "--panel", "--accent", "--danger", "--ok", "--btn"):
        assert re.search(rf"{var}\s*:", css), var


@pytest.mark.parametrize("name", UI_PAGES)
def test_pages_use_the_shared_files_and_not_their_own_palette(name):
    # common.css is the one palette: this used to compare it against
    # casting.html's copy, which is gone now every page links the shared files.
    text = (REVIEW_DIR / name).read_text()
    assert '<link rel="stylesheet" href="/common.css">' in text
    assert '<script src="/common.js"></script>' in text
    assert not re.search(r":root\s*\{", text), "palette belongs in common.css"
    assert "MW.nav(" in text, "every page shows the shared tabs"


def _node(js: str) -> str:
    stub = """
      const listeners = [];
      globalThis.window = globalThis;
      globalThis.document = { addEventListener: (...a) => listeners.push(a),
                              getElementById: () => null, querySelectorAll: () => [] };
    """
    src = (REVIEW_DIR / "common.js").read_text()
    out = subprocess.run(["node", "-e", stub + src + "\n" + js],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.mark.skipif(not has_node, reason="node not installed")
def test_js_parses():
    subprocess.run(["node", "--check", str(REVIEW_DIR / "common.js")], check=True)


@pytest.mark.skipif(not has_node, reason="node not installed")
def test_js_exports_and_pure_helpers():
    got = json.loads(_node("""
      console.log(JSON.stringify({
        keys: Object.keys(MW).sort(),
        esc: MW.esc(`<a href="x">'&'</a>`),
        money: [MW.money(1.5), MW.money("x"), MW.money(null)],
        explain: [MW.explain("ValueError: bad voice"), MW.explain("not found"), MW.explain("")],
        pages: MW.PAGES,
      }));
    """))
    assert got["keys"] == sorted(["$", "esc", "money", "toast", "explain", "api",
                                  "armed", "nav", "jobPanel", "PAGES",
                                  "artifacts", "renderArtifacts"])
    assert got["esc"] == "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;"
    assert got["money"] == ["$1.50", "$0.00", "$0.00"]
    assert got["explain"][0] == "bad voice" and "Restart it" in got["explain"][1]
    assert got["explain"][2] == ""


@pytest.mark.skipif(not has_node, reason="node not installed")
def test_every_tab_points_at_a_served_page(client):
    pages = json.loads(_node("console.log(JSON.stringify(MW.PAGES))"))
    assert pages and all(len(p) == 2 for p in pages)
    for name, href in pages:
        r = client.get(href)
        assert r.status == 200 and "text/html" in r.header("Content-Type"), (name, href)
