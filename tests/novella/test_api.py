"""The Novellas page's API through the real review server."""
import pytest

from story_pipeline.review import novella_api, server
from novella_pipeline.workflow import Stage

from .conftest import REVIEW_REPLY


def test_registered_as_plugin_with_releaser():
    assert novella_api in server.PLUGINS
    assert server.RELEASERS["novella"] is novella_api.release
    assert novella_api.release({}, {"slug": "novella-x", "stage": "draft"}) is None


def test_page_served(client):
    r = client.get("/novellas")
    assert r.status == 200 and "text/html" in r.header("Content-Type")
    assert "MW.nav(" in r.text
    assert client.get("/novellas.html").status == 200


def test_board(client, book):
    d = client.get("/api/novella").json
    [s] = d["series"]
    assert s["dir"] == "glass-roads" and s["name"] == "Glass Roads"
    [n] = s["novellas"]
    assert n["slug"] == "glasswood" and n["chapters"][0]["stage"] == "outlined"
    assert "glass" in d["bibles"] and d["models"]["writer"] == "claude-opus-5"


def test_create(client, bible, repo_root):
    r = client.post("/api/novella/new", {"title": "Glasswood", "bible": bible,
                                         "target_words": 32000})
    assert r.status == 200 and r.json["slug"] == "glasswood"
    m = (repo_root / "novellas" / "glass-roads" / "glasswood" / "manifest.json")
    assert m.exists()
    r = client.post("/api/novella/new", {"title": "Glasswood", "bible": bible})
    assert r.status == 400 and "unique" in r.json["error"]
    assert client.post("/api/novella/new", {"title": "", "series": "S"}).status == 400


@pytest.mark.parametrize("path", [
    "/api/novella/..%2F..%2Fbibles", "/api/novella/%2e%2e", "/api/novella/GLASS",
    "/api/novella/nope", "/api/novella/..%2Fglass-roads/chapter/1",
    "/api/novella/glasswood/chapter/..%2F1", "/api/novella/glasswood/chapter/999",
])
def test_traversal_and_bad_refs_refused(client, book, path):
    r = client.get(path)
    assert r.status in (400, 404)
    assert "glass.md" not in r.text and "BIBLE" not in r.text


def test_traversal_post_refused(client, book, repo_root):
    r = client.post("/api/novella/..%2F..%2Fbibles%2Fglass/brief", {"text": "pwned"})
    assert r.status in (400, 404)
    assert "pwned" not in (repo_root / "bibles" / "glass" / "glass.md").read_text()


def test_novella_detail_and_docs(client, book):
    assert client.post("/api/novella/glasswood/brief", {"text": "New brief"}).json["ok"]
    assert client.post("/api/novella/glasswood/outline", {"text": "New outline"}).json["ok"]
    d = client.get("/api/novella/glasswood").json
    assert d["brief"] == "New brief\n" and d["outline"] == "New outline\n"
    assert d["job"]["state"] == "none"


def test_beats_and_chapter_read(client, book):
    r = client.post("/api/novella/glasswood/chapter/2/beats",
                    {"title": "Two", "target_words": 3500, "beats": "- b2"})
    assert r.status == 200 and r.json["chapter"]["beats"] == "- b2\n"
    c = client.get("/api/novella/glasswood/chapter/2").json
    assert c["title"] == "Two" and c["target_words"] == 3500
    assert c["can"]["draft"] and not c["can"]["edit"] and c["versions"] == []


def test_save_author_edit_moves_stage(client, book):
    r = client.post("/api/novella/glasswood/chapter/1", {"text": "x"})
    assert r.status == 400 and "Cannot edit" in r.json["error"]
    book.save_draft(1, text="Draft.", summary="- s", new_details="- **Ferry**: x", flags="")
    r = client.post("/api/novella/glasswood/chapter/1", {"text": "My edit here."})
    assert r.json == {"ok": True, "stage": "author_edited", "words": 3}
    c = client.get("/api/novella/glasswood/chapter/1").json
    assert c["edited"] == "My edit here.\n" and c["draft"] == "Draft.\n" and c["edit_saved"]
    assert c["versions"][0]["edited"] and c["writer"]["summary"] == "- s"
    assert [e["value"] for e in c["earlier"]] == ["outlined", "drafted"]


def test_start_draft_job(client, book, started):
    r = client.post("/api/novella/glasswood/chapter/1/draft", {"note": "More frost."})
    assert r.status == 200 and r.json["ok"]
    [call] = started
    assert call["key"] == "novella-glasswood" and call["stage"] == "draft"
    assert call["extra"]["pipeline"] == "novella" and call["extra"]["chapter"] == 1
    argv = call["argv"]
    assert argv[1:3] == ["-m", "novella_pipeline.cli"]
    assert argv[argv.index("draft") + 1] == str(book.root)
    assert argv[-4:] == ["--chapter", "1", "--note", "More frost."]


def test_start_jobs_refused_by_stage(client, book, started):
    r = client.post("/api/novella/glasswood/chapter/1/review", {})
    assert r.status == 400 and "Save your edits" in r.json["error"]
    book.save_draft(1, text="x", summary="", new_details="", flags="")
    book.save_edit(1, "y")
    r = client.post("/api/novella/glasswood/chapter/1/draft", {})
    assert r.status == 400 and "Send it back" in r.json["error"]
    assert client.post("/api/novella/glasswood/chapter/1/review", {}).status == 200
    assert [c["stage"] for c in started] == ["review"]


def test_job_log_cancel_with_nothing_running(client, book, runs_dir):
    assert client.get("/api/novella/glasswood/job").json["state"] == "none"
    assert client.get("/api/novella/glasswood/log?from=0").json["text"] == ""
    r = client.post("/api/novella/glasswood/cancel", {})
    assert r.status == 400 and "nothing is running" in r.json["error"]


def test_job_view_reads_markers_and_log(client, book, runs_dir):
    """A finished job's markers and log as the CLI leaves them (no process is run:
    a real child would reach for the network)."""
    import json
    d = runs_dir / "novella-glasswood" / "20261125T100000-draft"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({
        "job_id": d.name, "slug": "novella-glasswood", "stage": "draft", "kind": "draft",
        "status": "done", "pid": None, "exit_code": 0, "chapter": 1, "pipeline": "novella"}))
    (d / "output.log").write_text(
        '::progress {"event": "start", "stage": "draft", "n": 1, "total": 1, "done": 0}\n'
        "Drafting chapter 1 of Glasswood\nGarret listened.\n"
        '::progress {"event": "done", "stage": "draft", "n": 1, "version": 1, "total": 1, "done": 1}\n')
    j = client.get("/api/novella/glasswood/job").json
    assert j["state"] == "done" and j["kind"] == "draft" and j["n"] == 1
    assert j["total"] == 1 and j["done"] == 1 and j["detail"]["version"] == 1
    text = client.get("/api/novella/glasswood/log?from=0").json["text"]
    assert "Garret listened." in text and "::progress" not in text


def test_approve_and_send_back(client, book, repo_root):
    book.save_draft(1, text="x", summary="- s", new_details="- **Hollin Ferry:** a crossing",
                    flags="")
    book.save_edit(1, "e")
    book.save_review(1, REVIEW_REPLY, {})
    c = client.get("/api/novella/glasswood/chapter/1").json
    assert c["suggested_details"] == ["**Hollin Ferry:** a crossing",
                                      "the Mill Pond, east of Hollin."]
    r = client.post("/api/novella/glasswood/chapter/1/approve", {"summary": "", "details": []})
    assert r.status == 400
    r = client.post("/api/novella/glasswood/chapter/1/approve",
                    {"summary": "- s", "details": ["the Mill Pond, east of Hollin."]})
    assert r.status == 200 and r.json["stage"] == "approved" and r.json["changelog"]
    log = (repo_root / "bibles" / "glass" / "glass.changelog.md").read_text()
    assert "Mill Pond" in log
    r = client.post("/api/novella/glasswood/chapter/1/send-back", {"to": "approved"})
    assert r.status == 400
    r = client.post("/api/novella/glasswood/chapter/1/send-back", {"to": "author_edited"})
    assert r.json == {"ok": True, "stage": "author_edited"}


def test_prompt_preview(client, book):
    p = client.get("/api/novella/glasswood/chapter/1/prompt/draft").json
    assert p["system"].startswith("# Novella draft writer") and "<target_chapter" in p["user"]
    p = client.get("/api/novella/glasswood/chapter/1/prompt/review").json
    assert p["system"].startswith("# Novella editor")
    assert client.get("/api/novella/glasswood/chapter/1/prompt/bogus").status == 400


def test_canon(client, book, repo_root):
    from story_pipeline import changelog
    changelog.append(repo_root / "bibles" / "glass" / "glass.md", "made it", "Tester")
    c = client.get("/api/novella/glasswood/canon").json
    assert c["bible"] == "glass" and "# Series bible: Glass Roads" in c["prompt_block"]
    assert "SECRET-WORKING-NOTE" not in c["prompt_block"]
    assert c["changelog"][0]["summary"] == "made it"


def test_unknown_novella_routes_fall_through(client, book):
    from tests.review.test_server_http import assert_not_found
    assert_not_found(client.get("/api/novella/glasswood/bogus"))
    assert_not_found(client.post("/api/novella/glasswood/chapter/1/bogus", {}))
