"""The real HTTP server: every GET and POST branch in do_GET / do_POST."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from story_pipeline import casting
from story_pipeline.bundle import Bundle
from story_pipeline.review import runner, server

SLUG = "the-cracked-beam"
ARC = "amberlight"
REVIEW_DIR = Path(server.__file__).parent
NOT_FOUND = {"error": "not found"}


def assert_not_found(r):
    assert r.status == 404 and r.json == NOT_FOUND


def assert_json_error(r, status=500, contains=""):
    assert r.status == status
    assert r.header("Content-Type") == "application/json"
    assert contains in r.json["error"]


# --- pages, manifest, icons -------------------------------------------------


@pytest.mark.parametrize("path,file", [
    ("/", "app.html"), ("/index.html", "app.html"),
    ("/casting", "casting.html"), ("/casting.html", "casting.html"),
    ("/watch/anything-at-all", "watch.html"), ("/watch/", "watch.html"),
    ("/watch/the-cracked-beam", "watch.html"),
])
def test_pages_are_served_from_disk(client, path, file):
    r = client.get(path)
    assert r.status == 200
    assert r.header("Content-Type") == "text/html; charset=utf-8"
    assert r.body == (REVIEW_DIR / file).read_bytes()
    assert int(r.header("Content-Length")) == len(r.body)


def test_query_string_does_not_change_routing(client):
    assert client.get("/?x=1").body == (REVIEW_DIR / "app.html").read_bytes()
    assert client.get("/casting?arc=amberlight").status == 200


def test_manifest_webmanifest(client):
    r = client.get("/manifest.webmanifest")
    assert r.status == 200 and r.header("Content-Type") == "application/manifest+json"
    assert r.json == server.MANIFEST
    assert r.json["start_url"] == "/" and r.json["display"] == "standalone"


def test_manifest_icons_are_all_served(client):
    for icon in server.MANIFEST["icons"]:
        r = client.get(icon["src"])
        assert r.status == 200, icon["src"]
        assert r.header("Content-Type") == icon["type"]


@pytest.mark.parametrize("name,ctype", sorted(server.ICONS.items()))
def test_every_allowlisted_icon_is_served_with_its_type(client, name, ctype):
    r = client.get("/" + name)
    assert r.status == 200 and r.header("Content-Type") == ctype
    assert r.body == (REVIEW_DIR / name).read_bytes() and r.body


def test_icon_allowlist_files_all_exist_on_disk():
    for name in server.ICONS:
        assert (REVIEW_DIR / name).is_file(), name


@pytest.mark.parametrize("path", [
    "/app.html.bak", "/server.py", "/runner.py", "/__init__.py", "/icon.png",
    "/icons/icon.svg", "/icon.svg/", "/Icon.svg", "/../server.py", "/%2e%2e/server.py",
    "/icon-512.png/../server.py", "/casting_api.py", "/bible_api.py", "/watch.html",
    "/config.yaml", "/.env", "/favicon.ico",
])
def test_non_allowlisted_paths_404_and_leak_nothing(client, path):
    r = client.get(path)
    assert_not_found(r)
    assert b"import " not in r.body


def test_icon_path_with_traversal_cannot_reach_other_files(client):
    for p in ("/icon.svg/../server.py", "/./server.py", "//server.py"):
        assert_not_found(client.get(p))


def test_unknown_get_route_is_generic_404_json(client):
    r = client.get("/api/nope")
    assert_not_found(r)
    assert r.header("Content-Type") == "application/json"


# --- GET api: queue, bibles, keepable --------------------------------------


def test_api_queue(client, real_story):
    q = client.get("/api/queue").json
    assert q["total"] == 1 and q["groups"]["publish"][0]["slug"] == SLUG


def test_api_queue_empty(client):
    assert client.get("/api/queue").json["total"] == 0


def test_api_bibles(client, real_bibles, sample_draft):
    j = client.get("/api/bibles").json
    assert [b["name"] for b in j["bibles"]] == ["amberlight"]
    assert j["drafts"] == [sample_draft] and j["lengths"] == [15, 30]


def test_api_keepable(client):
    j = client.get("/api/keepable").json
    assert [x["key"] for x in j] == [k for k, _ in server.KEEPABLE]
    assert all(set(x) == {"key", "label"} for x in j)


# --- GET api: story, job, log, estimate -------------------------------------


def test_api_story(client, real_story):
    j = client.get(f"/api/story/{SLUG}").json
    assert j["slug"] == SLUG and len(j["chapter_detail"]) == 8


def test_api_story_unknown_slug_is_json_500_naming_the_error(client, repo_root):
    # not a 404: the handler's catch-all reports FileNotFoundError as a 500.
    r = client.get("/api/story/nope")
    assert_json_error(r, 500, "FileNotFoundError: no story bundle for nope")


@pytest.mark.parametrize("path", [
    "/api/story/..", "/api/story/../..", "/api/story/%2e%2e", "/api/story/..%2f..",
    "/api/story/a%2fb", "/api/story/", "/api/story/%00",
])
def test_api_story_traversal_attempts_are_refused(client, real_story, path):
    r = client.get(path)
    assert r.status in (404, 500) and "error" in r.json
    assert "outline" not in r.json and "manifest" not in r.text


def test_api_job_and_log_without_runs(client, real_story):
    assert client.get(f"/api/job/{SLUG}").json == {"state": "none"}
    assert client.get(f"/api/log/{SLUG}").json == {
        "offset": 0, "text": "", "job_id": None, "state": "none"}


def test_api_log_offsets(client, real_story, runs_dir):
    d = runs_dir / SLUG / "20260101T000000-write"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({
        "job_id": d.name, "slug": SLUG, "stage": "write", "kind": "write",
        "status": "done", "exit_code": 0, "pid": None, "started": 1, "finished": 2}))
    (d / "output.log").write_text("one\ntwo\n")
    assert client.get(f"/api/log/{SLUG}").json["text"] == "one\ntwo"
    assert client.get(f"/api/log/{SLUG}?from=4").json["text"] == "two"
    assert_json_error(client.get(f"/api/log/{SLUG}?from=abc"), 500, "ValueError")
    j = client.get(f"/api/job/{SLUG}").json
    assert j["state"] == "done" and j["kind"] == "write"


def test_api_estimate(client, real_story):
    j = client.get(f"/api/estimate/{SLUG}").json
    assert j["slug"] == SLUG and {"record", "design", "direct"} <= set(j)


def test_api_estimate_unknown(client, repo_root):
    assert_json_error(client.get("/api/estimate/ghost"), 500, "FileNotFoundError")


# --- GET api: package zip ---------------------------------------------------


def test_api_package_zip_contents(client, real_story):
    b = Bundle.open(real_story)
    b.audio_path(1).parent.mkdir(parents=True, exist_ok=True)
    b.audio_path(1).write_bytes(b"AUDIO")
    (b.root / "audio" / ".DS_Store").write_bytes(b"junk")
    r = client.get(f"/api/package/{SLUG}")
    assert r.status == 200 and r.header("Content-Type") == "application/zip"
    assert r.header("Content-Disposition") == f'attachment; filename="{SLUG}-assets.zip"'
    z = zipfile.ZipFile(io.BytesIO(r.body))
    names = z.namelist()
    assert f"{ARC}/{SLUG}/audio/01.mp3" in names
    assert z.read(f"{ARC}/{SLUG}/audio/01.mp3") == b"AUDIO"
    assert not any(".DS_Store" in n for n in names)
    assert all(n.startswith(f"{ARC}/{SLUG}/") for n in names)
    assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
    assert z.testzip() is None


def test_api_package_without_assets_is_json_404(client, real_story):
    import shutil
    shutil.rmtree(real_story / "images", ignore_errors=True)
    assert_json_error(client.get(f"/api/package/{SLUG}"), 404, "no audio or images yet")


def test_api_package_unknown_slug(client, repo_root):
    assert_json_error(client.get("/api/package/ghost"), 500, "FileNotFoundError")


def test_api_package_traversal(client, real_story):
    r = client.get("/api/package/..")
    assert r.status in (404, 500) and "error" in r.json


# --- GET video / subtitles / Range -----------------------------------------


@pytest.fixture
def video(real_story):
    b = Bundle.open(real_story)
    b.video_path.parent.mkdir(parents=True, exist_ok=True)
    data = bytes(range(256)) * 4          # 1024 bytes, every value distinct per offset%256
    b.video_path.write_bytes(data)
    return data


def test_video_full(client, video):
    r = client.get(f"/video/{SLUG}/final.mp4")
    assert r.status == 200 and r.body == video
    assert r.header("Content-Type") == "video/mp4"
    assert r.header("Accept-Ranges") == "bytes"
    assert r.header("Content-Length") == "1024" and r.header("Content-Range") is None


@pytest.mark.parametrize("rng,lo,hi", [
    ("bytes=0-99", 0, 99),
    ("bytes=100-199", 100, 199),
    ("bytes=1000-", 1000, 1023),
    ("bytes=-24", 1000, 1023),
    ("bytes=1020-99999", 1020, 1023),
    ("bytes=0-0", 0, 0),
    ("bytes=1023-1023", 1023, 1023),
    ("bytes=-99999", 0, 1023),
])
def test_video_single_range(client, video, rng, lo, hi):
    r = client.get(f"/video/{SLUG}/final.mp4", headers={"Range": rng})
    assert r.status == 206
    assert r.body == video[lo:hi + 1]
    assert r.header("Content-Range") == f"bytes {lo}-{hi}/1024"
    assert r.header("Content-Length") == str(hi - lo + 1)


@pytest.mark.parametrize("rng", ["bytes=5000-", "bytes=1024-", "bytes=50-10", "bytes=-0"])
def test_video_unsatisfiable_range_is_416(client, video, rng):
    r = client.get(f"/video/{SLUG}/final.mp4", headers={"Range": rng})
    assert r.status == 416
    assert r.header("Content-Range") == "bytes */1024"
    assert r.body == b""


@pytest.mark.parametrize("rng", [
    "bytes=abc-def", "bytes=-abc", "bytes=1-2,5-6", "items=0-5", "bytes", "garbage", "",
])
def test_video_ignores_malformed_or_multi_ranges_and_serves_whole_file(client, video, rng):
    r = client.get(f"/video/{SLUG}/final.mp4", headers={"Range": rng})
    assert r.status == 200 and r.body == video


def test_video_missing_is_404_with_message(client, real_story):
    assert_json_error(client.get(f"/video/{SLUG}/final.mp4"), 404, "no rendered video yet")


def test_video_unknown_name_and_slug(client, video):
    assert_not_found(client.get(f"/video/{SLUG}/other.mp4"))
    assert_not_found(client.get(f"/video/{SLUG}/"))
    assert_not_found(client.get(f"/video/{SLUG}"))
    assert_json_error(client.get("/video/ghost/final.mp4"), 404, "no story bundle")


@pytest.mark.parametrize("path", [
    "/video/../final.mp4", "/video/..%2f..%2fmanifest.json/final.mp4",
    "/video/%2e%2e/final.mp4", f"/video/{SLUG}/../manifest.json",
    f"/video/{SLUG}/..%2fmanifest.json", f"/video/{SLUG}/video/final.mp4",
])
def test_video_traversal_attempts_404(client, video, path):
    r = client.get(path)
    assert r.status == 404 and "error" in r.json


def test_subtitles_converted_to_vtt(client, real_story):
    b = Bundle.open(real_story)
    b.srt_path.parent.mkdir(parents=True, exist_ok=True)
    b.srt_path.write_text("1\n00:00:01,250 --> 00:00:02,500\nHello\n", encoding="utf-8")
    r = client.get(f"/video/{SLUG}/subtitles.vtt")
    assert r.status == 200 and r.header("Content-Type") == "text/vtt; charset=utf-8"
    assert r.text.startswith("WEBVTT\n\n") and "00:00:01.250 --> 00:00:02.500" in r.text


def test_subtitles_missing(client, real_story):
    assert_json_error(client.get(f"/video/{SLUG}/subtitles.vtt"), 404, "no subtitles yet")


# --- casting GET -----------------------------------------------------------

V1 = "ABCDEFGHIJKLMNOPQRST"


@pytest.fixture
def casting_arc(real_story, real_bibles):
    stories = Path("stories")
    rec = casting.load(ARC, stories)
    casting.note_voice(rec, V1, "Samara X")
    p = casting.casting_dir(ARC, stories) / "audio" / SLUG / "narrator.mp3"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"0123456789")
    casting.add_try(rec, V1, story=SLUG, speaker="narrator", model="eleven_v4",
                    line="Hello.", file=f"audio/{SLUG}/narrator.mp3")
    casting.save(rec, stories)
    return V1


def test_api_casting_index(client, casting_arc):
    j = client.get("/api/casting").json
    assert j["arcs"][0]["arc"] == ARC and j["arcs"][0]["auditioned"] == 1


def test_api_casting_index_empty(client, repo_root):
    assert client.get("/api/casting").json == {"arcs": []}


def test_api_casting_view_and_story_query(client, casting_arc):
    j = client.get(f"/api/casting/{ARC}").json
    assert j["arc"] == ARC and j["story"] == SLUG and "estimate" in j
    assert client.get(f"/api/casting/{ARC}?story={SLUG}").json["story"] == SLUG
    assert client.get(f"/api/casting/{ARC}?story=zzz").json["story"] == SLUG


def test_api_casting_view_unknown_arc(client, casting_arc):
    assert_json_error(client.get("/api/casting/nope"), 500, "no arc called 'nope'")
    assert_json_error(client.get("/api/casting/"), 500, "no arc called ''")


def test_api_casting_job_and_log(client, casting_arc):
    assert client.get(f"/api/casting/{ARC}/job").json["state"] == "none"
    assert client.get(f"/api/casting/{ARC}/log").json["state"] == "none"
    assert client.get(f"/api/casting/{ARC}/log?from=3").json["offset"] == 0
    assert_json_error(client.get(f"/api/casting/{ARC}/log?from=x"), 500, "ValueError")


def test_api_casting_apply_plan_flags(client, casting_arc):
    assert_json_error(client.get(f"/api/casting/{ARC}/apply"), 500, "Nobody is cast yet")
    client.post("/api/casting/verdict", {"arc": ARC, "speaker": "narrator",
                                         "voice": casting_arc, "verdict": "cast"})
    j = client.get(f"/api/casting/{ARC}/apply").json
    assert "model" in j["config"] and "mode" in j["config"]
    for q in ("?v4=0", "?v4=false"):
        assert "model" not in client.get(f"/api/casting/{ARC}/apply{q}").json["config"]
    for q in ("?cast=0", "?cast=false"):
        assert "mode" not in client.get(f"/api/casting/{ARC}/apply{q}").json["config"]
    j = client.get(f"/api/casting/{ARC}/apply?v4=1&cast=yes").json
    assert "model" in j["config"] and "mode" in j["config"]


@pytest.mark.parametrize("q,key", [("?v4=", "model"), ("?cast=", "mode")])
def test_api_casting_apply_plan_blank_flag_is_false(client, casting_arc, q, key):
    client.post("/api/casting/verdict", {"arc": ARC, "speaker": "narrator",
                                         "voice": casting_arc, "verdict": "cast"})
    assert key not in client.get(f"/api/casting/{ARC}/apply{q}").json["config"]


def test_api_casting_unknown_subroute_404(client, casting_arc):
    assert_not_found(client.get(f"/api/casting/{ARC}/bogus"))
    assert_not_found(client.get(f"/api/casting/{ARC}/job/extra"))


def test_casting_audio_served_with_range(client, casting_arc):
    url = f"/casting-audio/{ARC}/audio/{SLUG}/narrator.mp3"
    r = client.get(url)
    assert r.status == 200 and r.body == b"0123456789"
    assert r.header("Content-Type") == "audio/mpeg"
    r = client.get(url, headers={"Range": "bytes=2-4"})
    assert r.status == 206 and r.body == b"234"
    assert r.header("Content-Range") == "bytes 2-4/10"


@pytest.mark.parametrize("rel", [
    f"{ARC}/audio/{SLUG}/missing.mp3",
    f"{ARC}/auditions.json",
    f"{ARC}/../auditions.json",
    f"{ARC}/audio/../auditions.json",
    f"{ARC}/audio/../../the-cracked-beam/manifest.json",
    f"{ARC}/audio/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
    f"{ARC}/..%2f..%2fthe-cracked-beam%2fmanifest.json",
    f"{ARC}/audio/{SLUG}/",
    f"{ARC}",
    "nope/audio/x.mp3",
    "",
])
def test_casting_audio_refuses_escapes(client, casting_arc, rel):
    r = client.get(f"/casting-audio/{rel}")
    assert_not_found(r)


# --- bibles GET -------------------------------------------------------------


def test_api_bibles_job_apply_log(client, real_bibles, sample_draft):
    assert client.get("/api/bibles/salt/job").json == {"state": "none", "name": "salt"}
    plan = client.get("/api/bibles/salt/apply").json
    assert plan["name"] == "salt" and plan["changed"] is True
    assert client.get("/api/bibles/salt/log").json["state"] == "none"
    assert client.get("/api/bibles/salt/log?from=5").json["offset"] == 0


def test_api_bibles_view(client, real_bibles, sample_draft):
    v = client.get("/api/bibles/salt/view").json
    assert v["name"] == "salt" and v["draft"] and v["agents"] is None
    assert client.get("/api/bibles/amberlight/view").json["agents"]


def test_api_bibles_get_errors(client, real_bibles, sample_draft):
    assert_not_found(client.get("/api/bibles/salt/other"))
    assert_not_found(client.get("/api/bibles/salt"))
    assert_json_error(client.get("/api/bibles/ghost/apply"), 500, "no draft ghost.draft.md")
    assert_not_found(client.get("/api/bibles/..%2f../job"))   # splits into name '..' + '../job'
    assert_not_found(client.get("/api/bibles/..%2fx/job"))
    assert_json_error(client.get("/api/bibles/Bad_Name/job"), 500, "is not a bible name")
    assert_json_error(client.get("/api/bibles/salt/log?from=zz"), 500, "ValueError")


# --- POST: generic errors ----------------------------------------------------


def test_unknown_post_route_404(client):
    assert_not_found(client.post("/api/nope", {}))
    assert_not_found(client.post("/", {}))
    assert_not_found(client.post("/api/bibles/salt/other", {}))


def test_post_invalid_json_is_500_json_error(client):
    r = client.request("POST", "/api/approve", raw=b"{nope", headers={"Content-Length": "5"})
    assert_json_error(r, 500, "JSONDecodeError")


def test_post_empty_body_missing_slug_is_keyerror(client):
    r = client.request("POST", "/api/approve", raw=b"")
    assert_json_error(r, 500, "KeyError: 'slug'")
    assert_json_error(client.post("/api/reject", {}), 500, "KeyError")


def test_post_unknown_slug(client, repo_root):
    for route in ("approve", "revise", "ask", "write", "critique", "record", "design",
                  "direct", "reject", "publish"):
        r = client.post(f"/api/{route}", {"slug": "ghost"})
        assert r.status == 500 and "FileNotFoundError" in r.json["error"], route


def test_post_slug_traversal_never_touches_files(client, real_story):
    for route in ("reject", "approve", "publish", "write"):
        r = client.post(f"/api/{route}", {"slug": "../.."})
        assert r.status == 500 and "not a story slug" in r.json["error"], route
    assert real_story.exists() and (real_story / "manifest.json").exists()


# --- POST: approve / publish / reject / chapter ------------------------------


def test_post_approve_outline_default(client, real_story):
    m = json.loads((real_story / "manifest.json").read_text())
    m["stages"]["ideate"] = "awaiting_review"
    (real_story / "manifest.json").write_text(json.dumps(m))
    r = client.post("/api/approve", {"slug": SLUG})
    assert r.json == {"ok": True, "slug": SLUG, "what": "outline"}
    assert Bundle.open(real_story).stage_status("ideate") == "approved"


def test_post_approve_chapters_one_at_a_time_and_undo(client, real_story):
    r = client.post("/api/approve", {"slug": SLUG, "what": "chapters",
                                     "chapters": [3], "undo": True})
    assert r.json["all_approved"] is False and 3 not in r.json["approved"]
    r = client.post("/api/approve", {"slug": SLUG, "what": "chapters"})
    assert r.json["all_approved"] is True


def test_post_approve_video_and_publish_roundtrip(client, real_story):
    assert client.post("/api/approve", {"slug": SLUG, "what": "video", "undo": True}
                       ).json["approve"] == "awaiting_review"
    r = client.post("/api/publish", {"slug": SLUG, "url": "dQw4w9WgXcQ"})
    assert_json_error(r, 500, "approve the video before publishing")
    client.post("/api/approve", {"slug": SLUG, "what": "video"})
    r = client.post("/api/publish", {"slug": SLUG, "url": "https://youtu.be/dQw4w9WgXcQ",
                                     "date": "2026-02-03"})
    assert r.json["published"]["published"] == "2026-02-03"
    r = client.post("/api/publish", {"slug": SLUG, "undo": True})
    assert r.json == {"ok": True, "slug": SLUG, "published": {}}


def test_post_publish_bad_url_and_date(client, real_story):
    assert_json_error(client.post("/api/publish", {"slug": SLUG, "url": "nope"}),
                      500, "not a YouTube video URL")
    assert_json_error(client.post("/api/publish", {"slug": SLUG, "url": "dQw4w9WgXcQ",
                                                    "date": "31/12/2026"}), 500, "ValueError")


def test_post_reject_deletes_bundle(client, real_story):
    assert client.post("/api/reject", {"slug": SLUG}).json == {"ok": True, "slug": SLUG}
    assert not real_story.exists()
    assert client.get("/api/queue").json["total"] == 0


def test_post_chapter_save(client, real_story):
    segs = Bundle.open(real_story).chapter(1)["segments"]
    segs[0] = {**segs[0], "text": "Rewritten opening."}
    r = client.post("/api/chapter", {"slug": SLUG, "n": 1, "segments": segs})
    assert r.status == 200 and r.json["segments"][0]["text"] == "Rewritten opening."
    assert "Rewritten opening." in (real_story / "chapters" / "01.md").read_text()
    assert r.json["approval_lapsed"] is True


def test_post_chapter_errors(client, real_story):
    assert_json_error(client.post("/api/chapter", {"slug": SLUG, "n": 99, "segments": []}),
                      500, "chapter 99 has not been written")
    assert_json_error(client.post("/api/chapter", {"slug": SLUG}), 500, "KeyError: 'n'")


# --- POST: paid calls (mocked) ----------------------------------------------


def test_post_new(client, real_bibles, real_story, monkeypatch):
    from story_pipeline.agents import text as textagent
    seen = {}
    monkeypatch.setattr(textagent, "ideate", lambda *a, **k: seen.update(a=a, k=k) or Bundle.open(real_story))
    r = client.post("/api/new", {"bible": "amberlight", "length": 30, "notes": "storm"})
    assert r.json == {"slug": SLUG, "title": "The Cracked Beam"}
    assert seen["a"][4] == "storm" and seen["k"]["length_min"] == 30


def test_post_new_errors(client, real_bibles):
    assert_json_error(client.post("/api/new", {}), 500, "no usable bible ''")
    assert_json_error(client.post("/api/new", {"bible": "amberlight", "length": 99}),
                      500, "length must be one of")


def test_post_revise_and_nothing_to_revise(client, real_story, monkeypatch):
    from story_pipeline.agents import text as textagent
    monkeypatch.setattr(textagent, "revise_outline",
                        lambda c, b, f: {"title": "T", "chapters": [], "revisions": []})
    r = client.post("/api/revise", {"slug": SLUG, "keep": ["logline"], "note": "n"})
    assert r.status == 200 and r.json["title"] == "T"
    r = client.post("/api/revise", {"slug": SLUG, "keep": [k for k, _ in server.KEEPABLE]})
    assert_json_error(r, 500, "Nothing to revise")


def test_post_ask(client, real_story, real_bibles, monkeypatch):
    from story_pipeline.agents import text as textagent
    monkeypatch.setattr(textagent.llm, "complete", lambda *a, **k: ("42", {}))
    r = client.post("/api/ask", {"slug": SLUG, "question": "why?", "stories": True})
    assert r.json["answer"] == "42" and r.json["read"] is not None
    assert_json_error(client.post("/api/ask", {"slug": SLUG}), 500, "Write the question")


def test_post_critique(client, real_story, monkeypatch):
    from story_pipeline.agents import text as textagent
    monkeypatch.setattr(textagent, "review_chapter", lambda c, b, n: {"pass": True, "score": 7})
    r = client.post("/api/critique", {"slug": SLUG, "chapters": [1]})
    assert r.json["failed"] == [] and [x["n"] for x in r.json["results"]] == [1]
    assert_json_error(client.post("/api/critique", {"slug": SLUG, "chapters": [77]}),
                      500, "have not been written")


# --- POST: stages as jobs ----------------------------------------------------


@pytest.fixture
def stage_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "start",
                        lambda cfg, slug, stage, **kw: calls.append((stage, kw)) or {})
    return calls


def test_post_write(client, real_story, stage_calls):
    r = client.post("/api/write", {"slug": SLUG, "restart": True})
    assert r.json["ok"] is True and stage_calls == [("write", {"restart": True})]


def test_post_write_gate(client, real_story, stage_calls):
    m = json.loads((real_story / "manifest.json").read_text())
    m["stages"]["ideate"] = "awaiting_review"
    (real_story / "manifest.json").write_text(json.dumps(m))
    assert_json_error(client.post("/api/write", {"slug": SLUG}), 500, "has not been approved")
    assert stage_calls == []


def test_post_redraft(client, real_story, stage_calls):
    assert_json_error(client.post("/api/redraft", {"slug": SLUG, "n": 1}), 500, "note is empty")
    assert_json_error(client.post("/api/redraft", {"slug": SLUG, "note": "x"}), 500, "KeyError: 'n'")
    client.post("/api/redraft", {"slug": SLUG, "n": 1, "note": "tighter"})
    assert stage_calls == [("redraft", {"chapter": 1, "note": "tighter"})]


def test_post_record_design_direct_gates(client, real_story, stage_calls):
    client.post("/api/approve", {"slug": SLUG, "what": "chapters", "chapters": [1], "undo": True})
    assert_json_error(client.post("/api/record", {"slug": SLUG}), 500, "chapters are approved")
    client.post("/api/record", {"slug": SLUG, "force": True})
    assert_json_error(client.post("/api/design", {"slug": SLUG}), 500, "no narration yet")
    assert stage_calls == [("record", {"force": True})]


def test_post_direct_gate_when_design_not_done(client, real_story, stage_calls):
    m = json.loads((real_story / "manifest.json").read_text())
    m["stages"]["design"] = "pending"
    (real_story / "manifest.json").write_text(json.dumps(m))
    assert_json_error(client.post("/api/direct", {"slug": SLUG}), 500, "design is not finished")


def test_post_cancel_running_job(client, real_story, runs_dir):
    import sys
    runner.start_command(SLUG, "write", [sys.executable, "-c", "import time;time.sleep(30)"])
    r = client.post("/api/cancel", {"slug": SLUG})
    assert r.json["ok"] is True and r.json["state"] == "cancelled"
    assert_json_error(client.post("/api/cancel", {"slug": SLUG}), 500, "nothing is running")


# --- POST: casting ----------------------------------------------------------


def test_post_casting_verdict_roundtrip(client, casting_arc):
    r = client.post("/api/casting/verdict", {"arc": ARC, "speaker": "narrator",
                                             "voice": casting_arc, "verdict": "maybe",
                                             "note": " hmm "})
    assert r.json == {"ok": True}
    rec = casting.load(ARC, Path("stories"))
    assert rec["voices"][casting_arc]["verdicts"]["narrator"]["note"] == "hmm"
    view = client.get(f"/api/casting/{ARC}").json
    narr = next(s for s in view["speakers"] if s["speaker"] == "narrator")
    assert any(e["verdict"] == "maybe" for e in narr["entries"])
    client.post("/api/casting/verdict", {"arc": ARC, "speaker": "narrator",
                                         "voice": casting_arc, "verdict": "clear"})
    assert casting.load(ARC, Path("stories"))["voices"][casting_arc]["verdicts"] == {}


def test_post_casting_verdict_errors(client, casting_arc):
    assert_json_error(client.post("/api/casting/verdict", {
        "arc": ARC, "speaker": "narrator", "voice": casting_arc, "verdict": "love"}),
        500, "verdict must be one of")
    assert_json_error(client.post("/api/casting/verdict", {"arc": ARC}), 500, "KeyError")
    assert_json_error(client.post("/api/casting/verdict", {
        "arc": "zzz", "speaker": "n", "voice": "v"}), 500, "no arc called")


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_post_casting_verdict_unknown_voice_returns_json_error(client, casting_arc):
    r = client.post("/api/casting/verdict", {"arc": ARC, "speaker": "narrator",
                                             "voice": "No Such Voice", "verdict": "cast"})
    assert r.status == 500 and "error" in r.json


def test_post_casting_candidate_add_remove(client, casting_arc):
    vid = "ZYXWVUTSRQPONMLKJIHG"
    r = client.post("/api/casting/candidate", {"arc": ARC, "speaker": "narrator", "id": vid,
                                               "name": "Zed", "note": "gravelly"})
    assert r.json == {"ok": True}
    cands = casting.load_candidates(ARC, Path("stories"))
    assert cands["narrator"] == [{"id": vid, "name": "Zed", "note": "gravelly"}]
    client.post("/api/casting/candidate", {"arc": ARC, "speaker": "narrator", "id": vid,
                                           "remove": True})
    assert casting.load_candidates(ARC, Path("stories")) == {}


def test_post_casting_candidate_errors(client, casting_arc):
    assert_json_error(client.post("/api/casting/candidate", {"arc": ARC, "id": "x" * 20}),
                      500, "Which character")
    assert_json_error(client.post("/api/casting/candidate", {
        "arc": ARC, "speaker": "narrator", "id": "short"}), 500, "20 letters and digits")


def test_post_casting_run_and_cancel(client, casting_arc, repo_root, runs_dir):
    assert_json_error(client.post("/api/casting/run", {"arc": ARC, "story": SLUG}),
                      500, "check_voices.py isn't in the working directory")
    (repo_root / "check_voices.py").write_text("import time\nprint('::progress {\"event\":\"plan\",\"total\":3}')\ntime.sleep(30)\n")
    # run the real runner against the stub script (harmless; cancelled below)
    r = client.post("/api/casting/run", {"arc": ARC, "story": SLUG, "speakers": ["narrator"]})
    assert r.status == 200 and r.json["ok"] is True
    again = client.post("/api/casting/run", {"arc": ARC, "story": SLUG})
    assert_json_error(again, 500, "Auditions are already running")
    r = client.post("/api/casting/cancel", {"arc": ARC})
    assert r.json["ok"] is True and r.json["job"]["state"] == "cancelled"
    assert_json_error(client.post("/api/casting/cancel", {"arc": ARC}), 500, "nothing is running")


def test_post_casting_run_validation(client, casting_arc, repo_root):
    (repo_root / "check_voices.py").write_text("")
    assert_json_error(client.post("/api/casting/run", {"arc": ARC, "story": "zzz"}),
                      500, "isn't a written story")
    assert_json_error(client.post("/api/casting/run", {"arc": ARC}), 500, "KeyError: 'story'")


def test_post_casting_apply_flags_translate(client, casting_arc, monkeypatch):
    from story_pipeline import casting_apply
    seen = {}
    monkeypatch.setattr(casting_apply, "apply", lambda cfg, arc, **kw: seen.update(kw) or {"done": 1})
    assert client.post("/api/casting/apply", {"arc": ARC}).json == {"done": 1}
    assert seen == {"use_v4": True, "cast_mode": True, "commit": True, "push": True,
                    "config_path": "config.yaml"}
    client.post("/api/casting/apply", {"arc": ARC, "v4": False, "cast_mode": False,
                                       "commit": False, "push": False})
    assert (seen["use_v4"], seen["cast_mode"], seen["commit"], seen["push"]) == (False,) * 4
    # anything other than literal false keeps the default, including null and 0
    client.post("/api/casting/apply", {"arc": ARC, "v4": None, "commit": 0})
    assert seen["use_v4"] is True and seen["commit"] is True


def test_post_casting_apply_nobody_cast(client, casting_arc):
    assert_json_error(client.post("/api/casting/apply", {"arc": ARC, "commit": False}),
                      500, "Nobody is cast yet")


# --- POST: bibles -----------------------------------------------------------


@pytest.fixture
def bible_starts(monkeypatch):
    calls = []

    def fake(key, stage, argv, busy=None, **extra):
        calls.append((key, stage, extra))
        return {}
    monkeypatch.setattr(runner, "start_command", fake)
    return calls


def test_post_bibles_preview(client, real_bibles, sample_draft, bible_starts):
    r = client.post("/api/bibles/salt/preview", {})
    assert r.json["ok"] is True and r.json["job"]["name"] == "salt"
    assert bible_starts == [("_bible-salt", "promote-preview", {"bible": "salt"})]


def test_post_bibles_fill(client, real_bibles, sample_draft, bible_starts):
    r = client.post("/api/bibles/salt/fill",
                    {"field": "series", "value": "The Salt Roads Cycle"})
    assert r.json["ok"] is True and len(bible_starts) == 1
    assert "The Salt Roads Cycle" in (real_bibles / "salt" / "salt.draft.md").read_text()
    assert_json_error(client.post("/api/bibles/salt/fill", {}), 500, "")


def test_post_bibles_apply_flags(client, real_bibles, sample_draft):
    r = client.post("/api/bibles/salt/apply", {"commit": False, "push": False,
                                                    "author": "Tester"})
    assert r.json["written"] is True
    assert (real_bibles / "salt" / "salt.md").exists()
    assert "Tester" in (real_bibles / "salt" / "salt.changelog.md").read_text()


def test_post_bibles_apply_default_commit_and_push_are_true(client, real_bibles, sample_draft, monkeypatch):
    from story_pipeline import bible_promote as bp
    seen = {}
    monkeypatch.setattr(bp, "commit", lambda files, name, msg, push: seen.update(push=push) or {"committed": True})
    r = client.post("/api/bibles/salt/apply", {})
    assert r.json["git"] == {"committed": True} and seen == {"push": True}
    seen.clear()
    monkeypatch.setattr(bp, "commit", lambda *a: pytest.fail("committed"))
    client.post("/api/bibles/salt/apply", {"commit": False})


def test_post_bibles_push_waiting_outside_repo(client, real_bibles, sample_draft):
    assert_json_error(client.post("/api/bibles/salt/push-waiting", {}), 500,
                      "not a git checkout")


def test_post_bibles_errors(client, real_bibles):
    assert_json_error(client.post("/api/bibles/ghost/preview", {}), 500, "no draft ghost")
    assert_not_found(client.post("/api/bibles/..%2fx/apply", {}))
    assert_json_error(client.post("/api/bibles/Bad_Name/apply", {}), 500, "is not a bible name")


# --- server resilience -------------------------------------------------------


def test_server_keeps_serving_after_errors(client, real_story):
    client.get("/api/story/nope")
    client.request("POST", "/api/approve", raw=b"{")
    assert client.get("/api/queue").status == 200


def test_inflight_counter_returns_to_zero(client, real_story):
    for _ in range(3):
        client.get("/api/queue")
        client.get("/api/story/nope")
        client.post("/api/reject", {})
    import time
    for _ in range(50):
        if server._inflight_count == 0:
            break
        time.sleep(0.05)
    assert server._inflight_count == 0


def test_api_casting_voices_route_is_not_an_arc(client, repo_root):
    # Network is blocked in tests, so the account listing fails and it falls back.
    j = client.get("/api/casting/voices").json
    assert j["warning"] and j["source"] in ("saved", "voices.yaml")
    assert isinstance(j["voices"], list)
