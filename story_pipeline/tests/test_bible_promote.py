"""Checks on bible promote, the expanded template, and who sees which section.

    python3 -m story_pipeline.tests.test_bible_promote

Run from the repo root. Synthetic drafts are built in a temporary directory; the
real drafts (broken-edges, north-star) are only ever previewed, never written,
and amberlight is only read. Deliberately not pytest, like the ambience tests:
the repo has no test dependency and this needs none.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import traceback
from pathlib import Path

from .. import bible_promote as bp
from .. import bibles, casting_apply, changelog, genres

REPO_BIBLES = Path("bibles")

DRAFT = """# Test Series Bible (Working Draft)

Working title: Salt Roads Status: Early worldbuilding.

## Premise

A coast of salt towns linked by one road that floods at spring tide.

## Canon

- The road floods at every spring tide.
- Salt is the only currency the
  towns all accept.

## Open Questions

- SECRET-OPEN-QUESTION: who built the road?

## Setting

### Technology

Carts, sails and windmills.

## Regions

- **Brinehold:** the largest town. It owns the pans.
- **Marrow Flats:** mud and birds.

## Recurring Cast

A small cast that rotates by town.

### Ada Penrose (road warden)

A woman in her forties who keeps the tide tables.

### The carters: Hal Venn and Ottoline Shaw

Hal Venn, a man of sixty, drives. Ottoline Shaw, a girl of fourteen, reads.

## Story Ideas

- SEED-IDEA: a cart is caught by an early tide.

## Minor Names Register

- SECRET-MINOR-NAME

## Changelog

- 2026-11-01: created.
"""


def _tmp_bibles(text: str = DRAFT, name: str = "salt") -> Path:
    root = Path(tempfile.mkdtemp(prefix="mw-promote-")) / "bibles"
    (root / name).mkdir(parents=True)
    (root / name / f"{name}.draft.md").write_text(text)
    return root


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #

def test_amberlight_unchanged():
    path = bibles.path_for("amberlight", REPO_BIBLES)
    before = _sha(path)
    b = bibles.load("amberlight", REPO_BIBLES)
    assert not bibles.fatal(bibles.validate(path, {})), "amberlight has errors"
    # No working-only or ideator-only sections, so both views are the same text
    # the agents were given before this change.
    assert b.prompt_block() == b.prompt_block(ideator=True)
    assert b.guide.strip() in b.prompt_block()
    assert _sha(path) == before


def test_scaffolds_still_pass():
    for example in (False, True):
        root = Path(tempfile.mkdtemp()) / "bibles"
        path = bibles.scaffold("demo", "fantasy", None, root, example=example)
        assert not bibles.fatal(bibles.validate(path, {})), f"example={example}"
    text = bibles.TEMPLATE.format(series="X", genre="fantasy")
    for heading in ("## Premise", "## Setting", "### Technology", "### Magic",
                    "### Flora and Fauna", "### Travel and Trade", "## Regions",
                    "## Map layout", "## Institutions", "## Recurring Cast",
                    "## What each story does on its own", "## Story Ideas"):
        assert heading in text, heading
    assert "any group that shapes the world" in text


def test_optional_fields_validate():
    root = _tmp_bibles()
    path = root / "x" / "x.md"
    path.parent.mkdir()
    path.write_text("---\nseries: X\ngenre: fantasy\nregions:\n  - name: A\n"
                    "factions: nope\nrecurring_cast:\n  - name: Ann Lee\n    gender: female\n"
                    "    role: 3\n---\n\nBody.\n")
    where = {p.where for p in bibles.fatal(bibles.validate(path, {}))}
    assert "factions" in where and "cast 'Ann Lee'" in where, where


def test_preview_extracts():
    root = _tmp_bibles()
    pv = bp.preview("salt", {}, root)
    d = pv.to_dict()
    assert d["canon"] == ["The road floods at every spring tide.",
                          "Salt is the only currency the towns all accept."]
    names = [c["name"] for c in d["cast"]]
    assert names == ["Ada Penrose", "Hal Venn", "Ottoline Shaw"], names
    g = {c["name"]: c.get("gender") for c in d["cast"]}
    assert g == {"Ada Penrose": "female", "Hal Venn": "male", "Ottoline Shaw": "female"}, g
    assert d["regions"] == ["Brinehold", "Marrow Flats"]
    dropped = {x["title"] for x in d["dropped"]}
    assert {"Open Questions", "Minor Names Register", "Changelog"} <= dropped
    assert "SECRET-OPEN-QUESTION" not in pv.text
    assert "SECRET-MINOR-NAME" not in pv.text
    assert "SEED-IDEA" in pv.text
    assert d["series"] == "Salt Roads"
    # No genre anywhere: one fatal problem, and it maps to a genre dropdown.
    fatal = [p for p in d["problems"] if p["fatal"]]
    assert [p["where"] for p in fatal] == ["genre"], fatal
    assert fatal[0]["fields"][0]["id"] == "genre"
    assert "fantasy" in fatal[0]["fields"][0]["options"]
    assert d["blocked"] and d["changed"]
    assert not (root / "salt" / "salt.md").exists(), "preview wrote a file"
    try:
        bp.promote("salt", {}, author="test", bibles_dir=root)
        raise AssertionError("promote wrote a bible with a fatal problem")
    except bp.PromoteError:
        pass


def test_fill_updates_draft_and_bible():
    root = _tmp_bibles()
    bp.fill("salt", "genre", "fantasy", root)
    bp.fill("salt", "cast:Hal Venn:age", "60", root)
    bp.fill("salt", "cast:Ada Penrose:station", "warden of the tide road", root)
    draft = (root / "salt" / "salt.draft.md").read_text()
    assert "Genre: fantasy" in draft
    assert "- Age (Hal Venn): 60" in draft, draft
    assert "- Station: warden of the tide road" in draft
    # Filling the same field again replaces, not duplicates.
    bp.fill("salt", "cast:Hal Venn:age", "61", root)
    draft = (root / "salt" / "salt.draft.md").read_text()
    assert draft.count("Age (Hal Venn)") == 1 and "61" in draft

    out = bp.promote("salt", {}, author="test", bibles_dir=root)
    assert out["written"]
    b = bibles.load("salt", root)
    assert b.genre == "fantasy"
    hal = b.member("Hal Venn")
    assert hal["age"] == 61, hal
    assert b.member("Ottoline Shaw").get("age") is None, "shared field leaked"
    assert b.member("Ada Penrose")["station"] == "warden of the tide road"
    assert not bibles.fatal(bibles.validate(b.path, {}))
    assert changelog.entries(b.path)[0].kind == "promote"


def test_rename_field():
    text = bp.fill_draft(DRAFT, "cast:Hal Venn:name", "Hal Vennor")
    assert "### The carters: Hal Vennor and Ottoline Shaw" in text
    names = [c.name for c in bp.parse_draft(text).cast]
    assert "Hal Vennor" in names


def test_second_promote_is_noop():
    root = _tmp_bibles(DRAFT.replace("Working title: Salt Roads",
                                     "Genre: fantasy\nWorking title: Salt Roads"))
    assert bp.promote("salt", {}, author="t", bibles_dir=root)["written"]
    path = root / "salt" / "salt.md"
    sha, n = _sha(path), len(changelog.entries(path))
    again = bp.promote("salt", {}, author="t", bibles_dir=root)
    assert not again["written"]
    assert _sha(path) == sha and len(changelog.entries(path)) == n
    assert not bp.preview("salt", {}, root).changed


def test_promote_keeps_casting_voices():
    root = _tmp_bibles(DRAFT.replace("Working title: Salt Roads",
                                     "Genre: fantasy\nWorking title: Salt Roads"))
    bp.promote("salt", {}, author="t", bibles_dir=root)
    path = root / "salt" / "salt.md"
    # What casting apply does to a bible, line by line.
    path.write_text(casting_apply.bible_set_voices(
        path.read_text(), {"Ada Penrose": "Matilda", "Ottoline Shaw": "Emilia"},
        "Charlotte"))
    cast_text = path.read_text()
    b = bibles.load("salt", root)
    assert b.member("Ada Penrose")["voice"] == "Matilda"

    # Re-promoting the unchanged draft is a no-op, voices and narrator intact.
    pv = bp.preview("salt", {}, root)
    assert not pv.changed, pv.diff()
    # A draft edit re-promotes without touching the voices either.
    draft = root / "salt" / "salt.draft.md"
    draft.write_text(draft.read_text().replace("tide tables.", "tide tables, badly."))
    assert bp.promote("salt", {}, author="t", bibles_dir=root)["written"]
    b = bibles.load("salt", root)
    assert b.member("Ada Penrose")["voice"] == "Matilda"
    assert b.member("Ottoline Shaw")["voice"] == "Emilia"
    assert b.meta["voices"]["narrator"] == "Charlotte"
    assert "badly" in b.member("Ada Penrose")["note"]
    assert path.read_text() != cast_text


def test_story_ideas_reach_ideator_only():
    from ..agents import text as textagent
    root = _tmp_bibles(DRAFT.replace("Working title: Salt Roads",
                                     "Genre: fantasy\nWorking title: Salt Roads"))
    bp.promote("salt", {}, author="t", bibles_dir=root)
    # Hand-add an Open Questions section to the bible itself: it must still
    # reach no agent.
    path = root / "salt" / "salt.md"
    path.write_text(path.read_text() + "\n## Open Questions\n\n- SECRET-OPEN-QUESTION\n")
    b = bibles.load("salt", root)
    g = genres.load("fantasy")
    seen = {name: textagent._render(name, g, b) for name in ("ideator", "writer", "editor")}
    assert "SEED-IDEA" in seen["ideator"]
    for name in ("writer", "editor"):
        assert "SEED-IDEA" not in seen[name], name
    for name, text in seen.items():
        assert "SECRET-OPEN-QUESTION" not in text, name
        assert "SECRET-MINOR-NAME" not in text, name
    assert "SEED-IDEA" not in b.prompt_block()      # designer's view


def test_real_drafts_preview():
    for name in ("broken-edges", "north-star"):
        draft = bp.draft_path(name, REPO_BIBLES)
        if not draft.exists():
            print(f"  (skipped {name}: no draft)")
            continue
        sha = _sha(draft)
        pv = bp.preview(name, {}, REPO_BIBLES)
        d = pv.to_dict()
        assert d["cast"], name
        assert all(not c.get("voice") for c in d["cast"]), name
        for p in d["problems"]:
            if p["fatal"]:
                assert p["fields"], f"{name}: {p['where']} has no field to fix it"
        assert _sha(draft) == sha
        assert not (REPO_BIBLES / name / f"{name}.md").exists()
        assert d["series"] == {"broken-edges": "Mapping the Broken Edges",
                               "north-star": "North Star"}[name], d["series"]
        if name == "broken-edges":
            assert len(d["cast"]) == 8, [c["name"] for c in d["cast"]]
        print(f"  {name}: {len(d['canon'])} canon, {len(d['cast'])} cast, "
              f"{sum(p['fatal'] for p in d['problems'])} error(s)")


def test_fields_clear_real_draft_errors_on_a_copy():
    """North Star's errors, fixed only through the fields the preview offers —
    on a temp copy, never the real draft."""
    src = bp.draft_path("north-star", REPO_BIBLES)
    if not src.exists():
        print("  (skipped: no north-star draft)")
        return
    root = _tmp_bibles(src.read_text(), "north-star")
    d = bp.preview("north-star", {}, root).to_dict()
    ids = [f["id"] for p in d["problems"] if p["fatal"] for f in p["fields"]]
    assert "genre" in ids and "cast:Juniper's father:name" in ids, ids
    bp.fill("north-star", "genre", "scifi", root)
    bp.fill("north-star", "cast:Juniper's father:name", "Abe Ward", root)
    bp.fill("north-star", "cast:Juniper's mother:name", "Rosa Ward", root)
    bp.fill("north-star", "cast:Captain:gender", "male", root)
    d = bp.preview("north-star", {}, root).to_dict()
    assert not d["blocked"], [p for p in d["problems"] if p["fatal"]]
    assert d["series"] == "North Star" and d["genre"] == "scifi"
    cap = next(c for c in d["cast"] if c["name"] == "Captain")
    assert cap["gender"] == "male" and not cap["inferred"]
    text = (root / "north-star" / "north-star.draft.md").read_text()
    assert "- **Abe Ward**:" in text and "  - Gender: male" in text


def _git_sandbox(text: str) -> tuple[Path, Path]:
    """A throwaway repo holding one draft, cloned from a throwaway bare remote."""
    import subprocess
    base = Path(tempfile.mkdtemp(prefix="mw-apply-"))
    remote, work = base / "remote.git", base / "work"
    run = lambda *a, cwd=base: subprocess.run(a, cwd=cwd, check=True,
                                              capture_output=True, text=True)
    run("git", "init", "-q", "--bare", str(remote))
    run("git", "clone", "-q", str(remote), str(work))
    for k, v in (("user.name", "Test"), ("user.email", "t@example.invalid")):
        run("git", "config", k, v, cwd=work)
    (work / "bibles" / "salt").mkdir(parents=True)
    (work / "bibles" / "salt" / "salt.draft.md").write_text(text)
    (work / "other.txt").write_text("untouched\n")
    run("git", "add", "-A", cwd=work)
    run("git", "commit", "-q", "-m", "init", cwd=work)
    run("git", "push", "-q", "-u", "origin", "HEAD", cwd=work)
    return work, remote


def test_apply_commits_and_pushes_only_touched_files():
    import os
    import subprocess
    work, remote = _git_sandbox(DRAFT)
    here = os.getcwd()
    os.chdir(work)
    try:
        bp.fill("salt", "genre", "fantasy", "bibles")
        Path("other.txt").write_text("edited, must not be committed\n")
        plan = bp.apply_plan("salt", {}, "bibles")
        assert not plan["blocked"] and plan["changed"]
        assert [f["action"] for f in plan["files"]] == ["create", "create", "commit"]
        assert plan["git"]["upstream"] and plan["git"]["ahead"] == 0
        out = bp.apply("salt", {}, author="t", bibles_dir="bibles")
        g = out["git"]
        assert out["written"] and g["committed"] and g["pushed"], out
        assert sorted(g["files"]) == ["bibles/salt/salt.changelog.md",
                                      "bibles/salt/salt.draft.md", "bibles/salt/salt.md"]
        shown = subprocess.run(["git", "--git-dir", str(remote), "show", "--stat",
                                "--format=%s", "HEAD"], capture_output=True, text=True).stdout
        assert "bible: promote salt" in shown and "other.txt" not in shown, shown
        assert "other.txt" in subprocess.run(["git", "status", "--porcelain"],
                                             capture_output=True, text=True).stdout
        # Applying again: nothing to write, nothing to commit.
        again = bp.apply("salt", {}, author="t", bibles_dir="bibles")
        assert not again["written"] and not again["git"]["committed"]
    finally:
        os.chdir(here)


def test_apply_push_refused_keeps_commit():
    import os
    import subprocess
    work, remote = _git_sandbox(DRAFT.replace("Working title: Salt Roads",
                                              "Genre: fantasy\nWorking title: Salt Roads"))
    # Someone else pushes first, so ours is not a fast-forward.
    other = work.parent / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    (other / "x.txt").write_text("x")
    for a in (["add", "x.txt"], ["-c", "user.name=O", "-c", "user.email=o@x.invalid",
                                 "commit", "-q", "-m", "theirs"], ["push", "-q"]):
        subprocess.run(["git", *a], cwd=other, check=True, capture_output=True)
    here = os.getcwd()
    os.chdir(work)
    try:
        out = bp.apply("salt", {}, author="t", bibles_dir="bibles")
        g = out["git"]
        assert g["committed"] and not g["pushed"] and g.get("push_error"), g
        local = subprocess.run(["git", "log", "-1", "--format=%s"],
                               capture_output=True, text=True).stdout
        assert local.startswith("bible: promote salt")
    finally:
        os.chdir(here)


def test_push_waiting_pushes_only_existing_commits():
    import os
    import subprocess
    work, remote = _git_sandbox(DRAFT)
    run = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True, text=True)
    here = os.getcwd()
    os.chdir(work)
    try:
        for n in (1, 2):
            Path(f"note{n}.txt").write_text(str(n))
            run("add", f"note{n}.txt")
            run("commit", "-q", "-m", f"waiting {n}")
        Path("other.txt").write_text("tracked, changed, never committed here\n")
        head = run("rev-parse", "HEAD").stdout.strip()
        plan = bp.apply_plan("salt", {}, "bibles")
        g = plan["git"]
        assert g["ahead"] == 2 and [w["subject"] for w in g["waiting"]] == ["waiting 2", "waiting 1"]
        assert g["uncommitted"] == ["other.txt"], g["uncommitted"]
        out = bp.push_waiting()
        assert out["pushed"] and out["count"] == 2 and out["git"]["ahead"] == 0, out
        assert run("rev-parse", "HEAD").stdout.strip() == head, "push made a commit"
        on_remote = subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "HEAD"],
                                   capture_output=True, text=True).stdout.strip()
        assert on_remote == head
        assert "other.txt" in run("status", "--porcelain").stdout
        assert bp.push_waiting()["count"] == 0
    finally:
        os.chdir(here)


def test_push_waiting_refused():
    import os
    import subprocess
    work, remote = _git_sandbox(DRAFT)
    other = work.parent / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    (other / "x.txt").write_text("x")
    for a in (["add", "x.txt"], ["-c", "user.name=O", "-c", "user.email=o@x.invalid",
                                 "commit", "-q", "-m", "theirs"], ["push", "-q"]):
        subprocess.run(["git", *a], cwd=other, check=True, capture_output=True)
    here = os.getcwd()
    os.chdir(work)
    try:
        Path("mine.txt").write_text("m")
        subprocess.run(["git", "add", "mine.txt"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "mine"], check=True)
        out = bp.push_waiting()
        assert not out["pushed"] and out.get("push_error") and out["git"]["ahead"] == 1, out
    finally:
        os.chdir(here)


def test_queue_rows_carry_series():
    """The series switcher filters on `bible` and `arc`; both must be there."""
    import json
    from ..bundle import Bundle
    from ..review import server
    stories = Path(tempfile.mkdtemp(prefix="mw-queue-")) / "stories"
    for arc, slug, bible in (("salt", "the-flood-road", "salt"),
                             ("standalone", "a-one-off", None)):
        b = Bundle.create(stories, slug, arc=arc if bible else None)
        outline = {"title": slug.replace("-", " ").title(), "genre": "fantasy",
                   "chapters": []}
        if bible:
            outline |= {"bible": bible, "series": "Salt Roads"}
        (b.root / "outline.json").write_text(json.dumps(outline))
    q = server.queue({"stories_dir": str(stories), "bibles_dir": "bibles"})
    rows = {r["slug"]: r for g in q["groups"].values() for r in g}
    assert q["total"] == 2 and set(rows) == {"the-flood-road", "a-one-off"}, rows.keys()
    salt = rows["the-flood-road"]
    assert (salt["arc"], salt["bible"], salt["series"]) == ("salt", "salt", "Salt Roads")
    one = rows["a-one-off"]
    assert one["bible"] is None and one["arc"] != "salt"


def test_server_endpoints():
    """preview (a runner job) -> fill -> apply, over HTTP, against a temp dir."""
    import json
    import os
    import threading
    import time
    import urllib.request
    from http.server import ThreadingHTTPServer

    from ..review import server

    root = _tmp_bibles()
    old_runs = os.environ.get("MW_RUNS_DIR")
    os.environ["MW_RUNS_DIR"] = str(root.parent / "runs")
    cfg = {"bibles_dir": str(root), "stories_dir": str(root.parent / "stories"),
           "_config_path": "config.yaml"}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server._handler(cfg))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(
            base + path, data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def wait():
        for _ in range(120):
            j = call("/api/bibles/salt/job")
            if j["state"] not in ("running", "none"):
                return j
            time.sleep(0.5)
        raise AssertionError("preview job never finished")

    try:
        menu = call("/api/bibles")
        assert menu["drafts"] == ["salt"], menu
        started = call("/api/bibles/salt/preview", {})
        assert started.get("ok"), started
        j = wait()
        pv = j.get("preview")
        assert pv, j
        assert pv["blocked"] and pv["problems"][0]["fields"][0]["id"] == "genre"
        assert "error" in call("/api/bibles/salt/apply", {"commit": False})

        assert call("/api/bibles/salt/fill", {"field": "genre", "value": "fantasy"})["ok"]
        j = wait()
        assert j["preview"]["blocked"] is False, j["preview"]["problems"]
        assert "Genre: fantasy" in (root / "salt" / "salt.draft.md").read_text()

        out = call("/api/bibles/salt/apply", {"commit": False})
        assert out.get("written"), out
        assert call("/api/bibles/salt/apply", {"commit": False})["written"] is False
        assert "salt" in [b["name"] for b in call("/api/bibles")["bibles"]]
        assert "error" in call("/api/bibles/..%2Fetc/preview", {})
    finally:
        httpd.shutdown()
        if old_runs is None:
            os.environ.pop("MW_RUNS_DIR", None)
        else:
            os.environ["MW_RUNS_DIR"] = old_runs


def main() -> int:
    failed = 0
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
