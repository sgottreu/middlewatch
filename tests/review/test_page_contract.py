"""The three pages and the server agree: every URL a page fetches or navigates
to is a route the server handles, and the install metadata is consistent."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from story_pipeline.review import server

REVIEW_DIR = Path(server.__file__).parent
PAGES = ["app.html", "casting.html", "watch.html"]
MIN_ROUTES = {"app.html": 20, "casting.html": 8, "watch.html": 3}
NOT_FOUND = {"error": "not found"}

LITERAL = re.compile(r"""(?P<q>["'`])(?P<url>/(?:api|video|watch|casting-audio|casting)\b[^"'`\n]*)(?P=q)""")


def page_text(name):
    return (REVIEW_DIR / name).read_text(encoding="utf-8")


def dynamic_kinds(text):
    """`fetch("/api/" + kind)` is filled from the literals its callers pass."""
    acts = set(re.findall(r'\bact\("([a-z]+)"\)', text))
    stages = set(re.findall(r'\bopenStage\("([a-z]+)"\)', text))
    return acts | stages


def references(name):
    """[(method, normalised url)] for every /api, /video, /watch or /casting URL
    literal in the page. Template parts become a placeholder slug."""
    text = page_text(name)
    out = []
    for m in LITERAL.finditer(text):
        raw = m.group("url")
        url = re.sub(r"\$\{[^}]*\}", "x", raw).split("?")[0]
        before, after = text[:m.start()], text[m.end():m.end() + 250]
        caller = re.search(r"\b(api|postJSON|fetch)\(\s*$", before)
        post = bool(re.search(r'method:\s*"POST"', after)) or bool(
            caller and caller.group(1) in ("api", "postJSON")
            and re.match(r"\s*,\s*\{", after))
        method = "POST" if post else "GET"
        if url == "/api/":                       # "/api/" + kind
            kinds = dynamic_kinds(text)
            assert kinds, f"{name}: '/api/' + <expr> but no kinds found"
            out += [(method, f"/api/{k}") for k in sorted(kinds)]
        elif url.endswith("/"):                  # "/api/story/" + encodeURIComponent(..)
            out.append((method, url + "x"))
        else:
            out.append((method, url))
    return out


def unique_refs(name):
    return sorted(set(references(name)))


@pytest.mark.parametrize("name", PAGES)
def test_pages_reference_a_plausible_number_of_routes(name):
    # guards the extraction itself: a regex that matches nothing would pass vacuously
    assert len(unique_refs(name)) >= MIN_ROUTES[name], unique_refs(name)


@pytest.mark.parametrize("name", PAGES)
def test_extraction_sees_known_routes(name):
    refs = {u for _, u in references(name)}
    expected = {"app.html": {"/api/queue", "/api/story/x", "/api/approve", "/api/reject",
                             "/api/record", "/api/design", "/api/direct", "/api/write",
                             "/api/bibles/x/apply", "/watch/x"},
                "casting.html": {"/api/casting", "/api/casting/x/job",
                                 "/api/casting/verdict", "/api/casting/run"},
                "watch.html": {"/api/story/x", "/video/x/final.mp4",
                               "/video/x/subtitles.vtt", "/api/approve"}}[name]
    assert expected <= refs, expected - refs


@pytest.mark.parametrize("name", PAGES)
def test_every_url_a_page_uses_is_a_route_the_server_handles(client, name):
    unknown = []
    for method, url in unique_refs(name):
        r = client.get(url) if method == "GET" else client.post(url, {})
        if r.status == 404 and r.body.startswith(b"{") and r.json == NOT_FOUND:
            unknown.append(f"{method} {url}")
    assert unknown == [], f"{name} calls routes the server answers with the generic 404"


def test_the_generic_404_really_is_how_unknown_routes_answer(client):
    # makes the contract test above meaningful: a bogus route does hit that shape
    r = client.get("/api/definitely-not-a-route")
    assert r.status == 404 and r.json == NOT_FOUND
    r = client.post("/api/definitely-not-a-route", {})
    assert r.status == 404 and r.json == NOT_FOUND


def test_methods_are_not_interchangeable(client):
    # GET on a POST-only route and vice versa fall through to the generic 404,
    # which is what lets the contract test catch a page using the wrong verb.
    assert client.get("/api/approve").json == NOT_FOUND
    assert client.post("/api/queue", {}).json == NOT_FOUND


@pytest.mark.parametrize("name", PAGES)
def test_page_links_manifest_and_apple_touch_icon(name):
    text = page_text(name)
    assert re.search(r'<link\s+rel="manifest"\s+href="/manifest\.webmanifest"', text)
    assert re.search(r'<link\s+rel="apple-touch-icon"\s+href="/icon-180\.png"', text)
    assert re.search(r'<link\s+rel="icon"\s+href="/icon\.svg"', text)


@pytest.mark.parametrize("name", PAGES)
def test_every_icon_the_page_uses_is_allowlisted_and_served(client, name):
    text = page_text(name)
    hrefs = re.findall(r'<link[^>]*\brel="[^"]*icon[^"]*"[^>]*\bhref="([^"]+)"', text)
    hrefs += re.findall(r'["\'](/icon[^"\']*)["\']', text)
    assert hrefs
    for href in set(hrefs):
        assert href.startswith("/") and href.lstrip("/") in server.ICONS, href
        assert client.get(href).status == 200, href


@pytest.mark.parametrize("name", PAGES)
def test_manifest_link_target_is_the_real_manifest(client, name):
    href = re.search(r'<link\s+rel="manifest"\s+href="([^"]+)"', page_text(name)).group(1)
    r = client.get(href)
    assert r.status == 200 and r.json["name"] == server.MANIFEST["name"]


def test_icon_allowlist_covers_every_manifest_icon():
    for icon in server.MANIFEST["icons"]:
        assert icon["src"].lstrip("/") in server.ICONS


def test_no_page_loads_scripts_or_styles_from_the_network():
    # a local, offline review tool: nothing should need a CDN
    for name in PAGES:
        text = page_text(name)
        assert not re.search(r'<script[^>]+src="https?://', text), name
        assert not re.search(r'<link[^>]+rel="stylesheet"[^>]+href="https?://', text), name
