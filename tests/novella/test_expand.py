""""Expand to novella" from the real story fixture (amberlight, read-only copy),
plus the outline/expand CLI commands and page routes. No model is called by
expand; the outliner's llm.complete is faked."""
import json

import pytest

from novella_pipeline import cli, expand
from novella_pipeline.bundle import Bundle, NovellaError

from .test_outliner import make_outline


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def story(real_story, real_bibles):
    return real_story


def story_rel(story, repo_root):
    return str(story.relative_to(repo_root))


def test_expand_builds_brief_and_manifest(cfg, story, repo_root):
    o = json.loads((story / "outline.json").read_text())
    before = snapshot(story)
    b = expand.expand(cfg, story_rel(story, repo_root))
    assert snapshot(story) == before                       # source untouched
    brief = b.brief_path.read_text()
    assert brief.startswith(f"# {o['title']}: Story Brief")
    for s in (o["logline"], o["promise"], o["setting"], o["genre"], o["subgenre"],
              "amberlight/the-cracked-beam", "## Source Story Beats"):
        assert s in brief, s
    for t in o["tropes"]:
        assert f"- {t}" in brief
    for c in o["cast"]:
        assert f"**{c['name']}** ({c['role']}" in brief
    for ch in o["chapters"]:
        assert f"### {ch['n']}. {ch['title']}" in brief
        assert ch["beats"][0] in brief and ch["beats"][-1] in brief
    m = b.manifest()
    assert m["pipeline"] == "novella" and m["slug"] == "the-cracked-beam"
    p = m["pinned"]
    assert p["bible"] == "amberlight" and p["genre"] == "regency"
    assert p["series"] == o["series"] and p["target_words"] == 30000
    assert m["source_story"] == {"path": "stories/amberlight/the-cracked-beam",
                                 "arc": "amberlight", "slug": "the-cracked-beam",
                                 "title": o["title"], "length_min": o["length_min"]}
    assert b.chapters() == [] and b.root.parent.parent.name == "novellas"
    assert b.outline_path.read_text().startswith("# The Cracked Beam: Outline")


def test_expand_options_and_unique_slug(cfg, story, repo_root):
    b = expand.expand(cfg, story_rel(story, repo_root), series="Amber Books",
                      title="The Long Beam", target_words=36000)
    assert b.slug == "the-long-beam" and b.pinned("series") == "Amber Books"
    assert b.pinned("target_words") == 36000 and b.root.parent.name == "amber-books"
    expand.expand(cfg, story_rel(story, repo_root))
    with pytest.raises(NovellaError, match="unique"):
        expand.expand(cfg, story_rel(story, repo_root))


def test_expand_refuses_non_stories(cfg, book, story, repo_root, tmp_path):
    with pytest.raises(NovellaError, match="not a story"):
        expand.expand(cfg, book.root)
    with pytest.raises(FileNotFoundError):
        expand.expand(cfg, repo_root / "stories" / "amberlight" / "nope")
    bare = repo_root / "stories" / "a" / "bare"
    bare.mkdir(parents=True)
    (bare / "manifest.json").write_text('{"pipeline": "story", "slug": "bare"}')
    with pytest.raises(NovellaError, match="outline.json"):
        expand.expand(cfg, bare)


def test_expanded_book_can_be_outlined(cfg, story, repo_root, fake_llm):
    from novella_pipeline import outliner
    b = expand.expand(cfg, story_rel(story, repo_root))
    o = make_outline(cast=[{"name": "Mr Silas Rowe", "role": "lead", "recurring": True}])
    fake_llm.replies = [json.dumps(o)]
    res = outliner.outline(cfg, b)
    assert len(b.chapters()) == 12 and res.version == 1     # template outline not kept
    assert "Mr Silas Rowe" in fake_llm.calls[0]["user"]     # the brief's cast


def test_stories_listing(cfg, story, book):
    [s] = expand.stories(cfg)
    assert s["ref"] == "amberlight/the-cracked-beam" and s["bible"] == "amberlight"
    assert expand.story_path(cfg, s["ref"]) == story.relative_to(story.parents[2])
    with pytest.raises(FileNotFoundError):
        expand.story_path(cfg, "../novellas/glass-roads/glasswood")


# --------------------------------------------------------------- CLI

def run(*argv):
    cli.main([str(a) for a in argv])


def test_cli_expand(story, repo_root, capsys):
    before = snapshot(story)
    run("expand", "stories/amberlight/the-cracked-beam", "--series", "Amber Novellas")
    out = capsys.readouterr().out
    assert "created" in out and "outline" in out
    b = Bundle.open(repo_root / "novellas" / "amber-novellas" / "the-cracked-beam")
    assert b.manifest()["source_story"]["slug"] == "the-cracked-beam"
    assert snapshot(story) == before


def test_cli_expand_bad_path_exits(repo_root, capsys):
    with pytest.raises(SystemExit):
        run("expand", "stories/x/y")
    assert "error" in capsys.readouterr().err


def test_cli_outline(book, fake_llm, monkeypatch, capsys):
    monkeypatch.setenv("MW_PROGRESS", "1")
    book.save_draft(1, text="x", summary="", new_details="", flags="")
    fake_llm.replies = [json.dumps(make_outline(n=3)), json.dumps(make_outline())]
    run("outline", "glasswood", "--note", "darker")
    out = capsys.readouterr().out
    assert "rejected; asking once more" in out and '"event": "retry"' in out
    assert "Outline v2: Glasswood, 12 chapters, 30,000 words." in out
    assert "skipped (past Outlined" in out and '"event": "done"' in out
    assert '"Glasswood"' in out                                # streamed reply
    assert book.outline_versions()[-1]["note"] == "darker"


def test_cli_outline_failure_exits(book, fake_llm, capsys):
    fake_llm.replies = [json.dumps(make_outline(n=3))] * 2
    with pytest.raises(SystemExit):
        run("outline", "glasswood")
    assert "outline rejected" in capsys.readouterr().err


# --------------------------------------------------------------- API

def test_api_stories_and_expand(client, story):
    d = client.get("/api/novella/stories").json
    assert [s["ref"] for s in d["stories"]] == ["amberlight/the-cracked-beam"]
    r = client.post("/api/novella/expand", {"story": "amberlight/the-cracked-beam",
                                            "target_words": 32000})
    assert r.status == 200 and r.json["slug"] == "the-cracked-beam"
    n = client.get("/api/novella/the-cracked-beam").json
    assert n["source_story"]["arc"] == "amberlight" and n["target_words"] == 32000
    assert client.post("/api/novella/expand", {"story": "../../bibles"}).status == 404
    assert client.post("/api/novella/expand", {"story": "amberlight/the-cracked-beam"}).status == 400


def test_api_run_outline(client, book, started, cfg):
    r = client.post("/api/novella/glasswood/run-outline", {"note": "darker"})
    assert r.status == 200, r.text
    [c] = started
    assert c["key"] == "novella-glasswood" and c["stage"] == "outline"
    assert c["extra"]["pipeline"] == "novella"
    argv = c["argv"]
    assert argv[argv.index("-m") + 1] == "novella_pipeline.cli"
    assert argv[argv.index("outline") + 1] == str(book.root)
    assert argv[-2:] == ["--note", "darker"]


def test_api_run_outline_refuses_empty_brief(client, book, started):
    book.brief_path.write_text("# Glasswood: Story Brief\n\n## Premise\n")
    r = client.post("/api/novella/glasswood/run-outline", {})
    assert r.status == 400 and "brief" in r.json["error"] and started == []


def test_api_outline_versions(client, book, cfg, fake_llm):
    from novella_pipeline import outliner
    fake_llm.replies = [json.dumps(make_outline())]
    outliner.outline(cfg, book)
    n = client.get("/api/novella/glasswood").json
    assert [v["version"] for v in n["outline_versions"]] == [1, 2]
    assert client.get("/api/novella/glasswood/outlines").json["versions"][1]["source"] == "outliner"
    r = client.get("/api/novella/glasswood/outlines?v=1").json
    assert r["version"] == 1 and "OUTLINE-MARKER" in r["text"]
    assert client.get("/api/novella/glasswood/outlines?v=9").status == 404
    assert client.get("/api/novella/glasswood/outlines?v=..%2F..").status == 404
