"""casting_apply.py: YAML line editing, plan(), apply() and the git commit step."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from story_pipeline import bibles, casting, casting_apply as ca, changelog
from story_pipeline import config as cfgmod

M = "M" * 20
E = "E" * 20
Z = "Z" * 20


# --------------------------------------------------------------------------- #
# line editing helpers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("s,out", [
    ("Alice", "Alice"), ("Samara X", "Samara X"), ("Mrs O'Hara", "Mrs O'Hara"),
    ("Jean-Luc", "Jean-Luc"), ("a.b_c", "a.b_c"),
    ("AXdMgz6evoL7OPd7eU12", "AXdMgz6evoL7OPd7eU12"),
    ("", '""'), ("a: b", '"a: b"'), ("#tag", '"#tag"'), ("- x", '"- x"'),
    (" lead", '" lead"'), ("Zoë", '"Zo\\u00eb"'), ("a#b", '"a#b"'),
])
def test_q(s, out):
    assert ca._q(s) == out


def test_block_and_find_key():
    lines = ["a:", "  b: 1", "", "  # note", "  c: 2", "d: 3", "  e: 4"]
    assert ca._block(lines, 0) == 5
    assert ca._block(lines, 5) == 7
    assert ca._find_key(lines, "d") == 5
    assert ca._find_key(lines, "b") is None                 # indent 0 only
    assert ca._find_key(lines, "b", indent=2) == 1
    assert ca._find_key(lines, "c", 0, 3, indent=2) is None  # outside range
    assert ca._find_key(["voices:"], "voices") == 0          # key with no value
    assert ca._find_key(["voicesx: 1"], "voices") is None


def test_set_value_preserves_comment_and_spacing():
    assert ca._set_value("  mode: solo", "cast") == "  mode: cast"
    assert ca._set_value("  mode: solo   # keep me", "cast") == "  mode: cast   # keep me"
    assert ca._set_value("  mode:solo #c", "cast") == "  mode: cast #c"
    assert ca._set_value("voice:", "X") == "voice: X"
    assert ca._set_value("  - name: Pike", "Z") == "  - name: Z"
    with pytest.raises(ca.ApplyError):
        ca._set_value("no colon here", "x")


CONFIG = """# my config
casting:
  mode: solo        # solo | cast
  other: 1
elevenlabs:
  api: x
  model: eleven_v3
other:
  mode: untouched
"""


def test_set_child_replace_and_add():
    out = ca.set_child(CONFIG, "casting", "mode", "cast")
    assert "  mode: cast        # solo | cast" in out
    assert "other:\n  mode: untouched" in out                # sibling section untouched
    out = ca.set_child(CONFIG, "casting", "max_named_voices", "3")
    assert out.split("\n")[2] == "  max_named_voices: 3"      # inserted right under parent
    assert yaml.safe_load(out)["casting"]["max_named_voices"] == 3
    assert out.startswith("# my config")


def test_set_child_missing_parent():
    with pytest.raises(ca.ApplyError, match="no `nothing:` section"):
        ca.set_child(CONFIG, "nothing", "x", "1")


def test_set_child_does_not_cross_into_next_section():
    text = "casting:\n  a: 1\nother:\n  mode: keep\n"
    out = ca.set_child(text, "casting", "mode", "cast")
    assert yaml.safe_load(out) == {"casting": {"mode": "cast", "a": 1}, "other": {"mode": "keep"}}


VOICES = """# the library
voice_library:
  Charlotte: aaaaaaaaaaaaaaaaaaaa
  Alice: bbbbbbbbbbbbbbbbbbbb

  # trailing comment block
other: 1
"""


def test_add_library():
    assert ca.add_library(VOICES, {}) == VOICES
    out = ca.add_library(VOICES, {"Samara X": E, "Quote: Me": Z})
    data = yaml.safe_load(out)
    assert data["voice_library"]["Samara X"] == E and data["voice_library"]["Quote: Me"] == Z
    assert data["voice_library"]["Alice"] == "bbbbbbbbbbbbbbbbbbbb" and data["other"] == 1
    # appended inside the block, before trailing blanks/comments are re-closed
    assert out.index("Samara X") < out.index("other: 1")
    assert out.startswith("# the library")


def test_add_library_without_section():
    with pytest.raises(ca.ApplyError, match="no `voice_library:`"):
        ca.add_library("other: 1\n", {"A": E})


def test_add_library_at_end_of_file():
    out = ca.add_library("voice_library:\n  A: aaaaaaaaaaaaaaaaaaaa", {"B": E})
    assert yaml.safe_load(out)["voice_library"] == {"A": "aaaaaaaaaaaaaaaaaaaa", "B": E}


BIBLE = """---
series: "Test"   # display name
genre: regency
# keep this comment
canon:
  - "A fact."
recurring_cast:
  - name: Mrs Honoria Pike
    age: 58
    gender: female
    voice: Alice   # her voice
    note: Knows things.
  - name: "Mr Silas Rowe"
    age: 34
    gender: male
  - name: Miss Judith Vane
    voice: Lily
voices:
  narrator: Charlotte
---

# Test

Body mentioning Alice in prose.
"""


def test_front_splits_at_closing_marker():
    front, rest = ca._front(BIBLE)
    assert front.startswith("---\nseries") and front.endswith("\n")
    assert rest.startswith("---\n\n# Test")
    assert front + rest == BIBLE
    with pytest.raises(ca.ApplyError, match="no front matter"):
        ca._front("# no front\n")


def test_bible_set_voices_replace_add_and_narrator():
    out = ca.bible_set_voices(BIBLE, {"Mrs Honoria Pike": "Meera",
                                       "Mr Silas Rowe": "George",
                                       "Miss Judith Vane": "Samara X"}, "Elizabeth")
    meta = yaml.safe_load(out.split("---")[1])
    cast = {c["name"]: c for c in meta["recurring_cast"]}
    assert cast["Mrs Honoria Pike"]["voice"] == "Meera"
    assert cast["Mr Silas Rowe"]["voice"] == "George"        # added where missing (quoted name matched)
    assert cast["Miss Judith Vane"]["voice"] == "Samara X"
    assert meta["voices"]["narrator"] == "Elizabeth"
    assert "voice: Meera   # her voice" in out               # comment survives
    assert "# keep this comment" in out and 'series: "Test"   # display name' in out
    assert out.endswith("Body mentioning Alice in prose.\n")  # body untouched
    assert cast["Mrs Honoria Pike"]["note"] == "Knows things."
    assert cast["Mr Silas Rowe"]["age"] == 34 and cast["Mrs Honoria Pike"]["age"] == 58


def test_bible_set_voices_only_named_members():
    out = ca.bible_set_voices(BIBLE, {"Miss Judith Vane": "Meera"}, None)
    meta = yaml.safe_load(out.split("---")[1])
    cast = {c["name"]: c for c in meta["recurring_cast"]}
    assert cast["Mrs Honoria Pike"]["voice"] == "Alice" and "voice" not in cast["Mr Silas Rowe"]
    assert cast["Miss Judith Vane"]["voice"] == "Meera"
    assert meta["voices"]["narrator"] == "Charlotte"


def test_bible_set_voices_noop():
    assert ca.bible_set_voices(BIBLE, {}, None) == BIBLE


def test_bible_set_voices_unknown_name_ignored():
    assert ca.bible_set_voices(BIBLE, {"Nobody": "X"}, None) == BIBLE


def test_bible_set_voices_narrator_added_after_genre():
    text = BIBLE.replace("voices:\n  narrator: Charlotte\n", "")
    out = ca.bible_set_voices(text, {}, "Charlotte")
    assert yaml.safe_load(out.split("---")[1])["voices"] == {"narrator": "Charlotte"}
    assert out.index("genre: regency") < out.index("voices:") < out.index("# keep this comment")


def test_bible_set_voices_narrator_key_added_to_existing_voices_block():
    text = BIBLE.replace("  narrator: Charlotte\n", "  other: x\n")
    out = ca.bible_set_voices(text, {}, "Meera")
    assert yaml.safe_load(out.split("---")[1])["voices"] == {"narrator": "Meera", "other": "x"}


def test_bible_set_voices_without_cast_errors():
    text = "---\ngenre: regency\n---\nbody\n"
    with pytest.raises(ca.ApplyError, match="no recurring_cast"):
        ca.bible_set_voices(text, {"A": "B"}, None)
    assert "narrator: X" in ca.bible_set_voices(text, {}, "X")


def test_bible_set_voices_without_genre_puts_voices_first():
    text = "---\nseries: S\n---\nbody\n"
    out = ca.bible_set_voices(text, {}, "X")
    assert yaml.safe_load(out.split("---")[1]) == {"series": "S", "voices": {"narrator": "X"}}


def test_bible_set_voices_quotes_unsafe_values():
    out = ca.bible_set_voices(BIBLE, {"Mrs Honoria Pike": "A: B"}, None)
    meta = yaml.safe_load(out.split("---")[1])
    assert meta["recurring_cast"][0]["voice"] == "A: B"


def test_bible_set_voices_bible_has_no_front_matter():
    with pytest.raises(ca.ApplyError):
        ca.bible_set_voices("# nope", {}, "X")


# --------------------------------------------------------------------------- #
# library helpers
# --------------------------------------------------------------------------- #

def test_library_flattens_dict_entries():
    cfg = {"elevenlabs": {"voice_library": {"A": "id1", "B": {"voice_id": "id2"}}}}
    assert ca._library(cfg) == {"A": "id1", "B": "id2"}
    assert ca._library({}) == {} and ca._library({"elevenlabs": None}) == {}


def test_key_for():
    lib = {"Alice": "A" * 20}
    assert ca._key_for("A" * 20, "Whatever", lib, {}) == "Alice"            # existing id reused
    assert ca._key_for(Z, "Pending", lib, {"Pending": Z}) == "Pending"       # already queued
    assert ca._key_for(E, "Eliz@beth!", lib, {}) == "Elizbeth"           # unsafe characters dropped
    assert ca._key_for(E, "Elizabeth - Warm", lib, {}) == "Elizabeth"
    assert ca._key_for(Z, "Alice - Young", lib, {}) == "Alice ZZZZ"          # name taken, different id
    assert ca._key_for(Z, "Alice", lib, {"Alice ZZZZ": E}) == "Alice ZZZZ3"
    assert ca._key_for(Z, "!!!", lib, {}) == Z                               # nothing usable in name


def test_speakers_in(repo_root):
    assert ca._speakers_in(Path("nowhere")) == set()
    d = _story("s", {"narrator": "x", "Tom": "y"}, [("narrator", "a"), ("Tom", "b"), (None, "c")])
    assert ca._speakers_in(d) == {"narrator", "Tom"}


# --------------------------------------------------------------------------- #
# fixtures for plan / apply
# --------------------------------------------------------------------------- #

def _story(slug, voices, segs, arc="amb", bible="demo", outline=None, genre_narr="Charlotte",
           audio=False, title=None):
    d = Path("stories") / arc / slug
    (d / "chapters").mkdir(parents=True, exist_ok=True)
    manifest = {"title": title or slug, "bible": bible, "genre": "regency",
                "pinned": {"voices": voices, "genre_voices": {"narrator": genre_narr}}}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (d / "chapters" / "01.json").write_text(json.dumps(
        {"segments": [{"speaker": s, "text": t} for s, t in segs]}))
    if outline is not None:
        (d / "outline.json").write_text(json.dumps(outline, indent=2))
    if audio:
        (d / "audio").mkdir()
        (d / "audio" / "01.mp3").write_bytes(b"x")
    return d


@pytest.fixture
def world(repo_root, monkeypatch):
    """voices.yaml, config.yaml, a bible, two stories and a casting record in the tmp root."""
    monkeypatch.setenv("MW_RUNS_DIR", str(repo_root / "runs"))
    (repo_root / "voices.yaml").write_text(
        "# lib\nvoice_library:\n"
        f"  Charlotte: {'C' * 20}\n  Alice: {'A' * 20}\n  Meera: {M}\n")
    (repo_root / "config.yaml").write_text(
        "# per machine\ncasting:\n  mode: solo\nelevenlabs:\n  model: eleven_v3   # old\n")
    bpath = repo_root / "bibles" / "demo" / "demo.md"
    bpath.parent.mkdir(parents=True)
    bpath.write_text(
        "---\nseries: Demo\ngenre: regency\ncanon:\n  - \"A fact.\"\nrecurring_cast:\n"
        "  - name: Mrs Honoria Pike\n    age: 58\n    gender: female\n    voice: Alice\n"
        "    appearance: Short.\n    dress: Black.\n"
        "  - name: Mr Silas Rowe\n    age: 34\n    gender: male\n"
        "    appearance: Tall.\n    dress: Coat.\n"
        "voices:\n  narrator: Charlotte\n---\n\n# Demo\n\nShe was once cast as Alice in a test.\n")
    _story("one", {"narrator": "Charlotte", "Mrs Honoria Pike": "Alice"},
           [("narrator", "N."), ("Mrs Honoria Pike", "Hello."), ("Tom", "Hi.")],
           outline={"title": "One Title", "cast": [
               {"name": "Mrs Honoria Pike", "voice": "Alice"}, {"name": "Mr Silas Rowe"}]},
           audio=True)
    _story("two", {"narrator": "Charlotte"}, [("narrator", "N."), ("Mrs Honoria Pike", "Hello.")])
    r = casting.load("amb")
    casting.note_voice(r, M, "Meera - Calm")
    casting.add_try(r, M, story="one", speaker="narrator", model="m", line="l", file="m.mp3")
    casting.note_voice(r, E, "Elizabeth - Warm")
    casting.add_try(r, E, story="one", speaker="Mrs Honoria Pike", model="m", line="l", file="e.mp3")
    casting.set_verdict(r, "narrator", M, "cast", None)
    casting.set_verdict(r, "Mrs Honoria Pike", E, "cast", None)
    casting.save(r)
    cfg = cfgmod.load(repo_root / "config.yaml")
    cfg["author"] = "Tester"
    return cfg


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #

def test_plan_nobody_cast(repo_root):
    with pytest.raises(ca.ApplyError, match="Nobody is cast yet"):
        ca.plan({}, "amb")


def test_plan_contents(world):
    p = ca.plan(world, "amb")
    assert p["arc"] == "amb"
    assert p["cast"] == [
        {"speaker": "Mrs Honoria Pike", "voice": "Elizabeth", "id": E, "name": "Elizabeth - Warm"},
        {"speaker": "narrator", "voice": "Meera", "id": M, "name": "Meera - Calm"}]
    c = p["config"]
    assert c["library_add"] == {"Elizabeth": E}              # Meera already in the library
    assert c["mode"] == ["solo", "cast"]
    assert "max_named_voices" not in c                       # default 3 already covers 1 named voice
    assert c["model"] == ["eleven_v3", ca.V4]
    b = p["bible"]
    assert b["name"] == "demo" and b["path"] == str(Path("bibles/demo/demo.md"))
    assert b["changes"] == [{"character": "Mrs Honoria Pike", "from": "Alice", "to": "Elizabeth"}]
    assert b["narrator"] == {"from": "Charlotte", "to": "Meera"}
    assert any("still mentions Alice" in n for n in b["notes"])
    slugs = {s["slug"]: s for s in p["stories"]}
    one = slugs["one"]
    assert one["title"] == "one"
    assert one["outline"] == [{"character": "Mrs Honoria Pike", "from": "Alice", "to": "Elizabeth"}]
    assert one["pinned"] == [
        {"character": "Mrs Honoria Pike", "from": "Alice", "to": "Elizabeth"},
        {"character": "narrator", "from": "Charlotte", "to": "Meera"}]
    assert one["genre_narrator"] is True and one["rerecord"] is True
    assert one["uncast"] == ["Tom"]
    two = slugs["two"]
    assert two["rerecord"] is False                          # no audio yet
    assert [x["character"] for x in two["pinned"]] == ["Mrs Honoria Pike", "narrator"]
    assert p["busy"] == []
    assert any("config.yaml isn't in git" in w for w in p["warnings"])
    assert any("expire on 2026-12-31" in w for w in p["warnings"])   # regency pool uses Default voices


def test_plan_does_not_write(world, repo_root):
    before = {p: p.read_bytes() for p in repo_root.rglob("*") if p.is_file()}
    ca.plan(world, "amb")
    assert {p: p.read_bytes() for p in repo_root.rglob("*") if p.is_file()} == before


def test_plan_flags_off(world):
    p = ca.plan(world, "amb", use_v4=False, cast_mode=False)
    assert set(p["config"]) == {"library_add"}


def test_plan_no_model_change_when_already_v4_and_mode_cast(world):
    world["elevenlabs"]["model"] = ca.V4
    world["casting"] = {"mode": "cast", "max_named_voices": 5}
    p = ca.plan(world, "amb")
    assert set(p["config"]) == {"library_add"}


def test_plan_mode_default_is_solo(world):
    world["casting"] = {}
    assert ca.plan(world, "amb")["config"]["mode"] == ["solo", "cast"]


def test_plan_raises_max_named_voices_only_when_too_low(world):
    world["casting"] = {"mode": "cast", "max_named_voices": 0}
    assert ca.plan(world, "amb")["config"]["max_named_voices"] == [0, 1]    # narrator not counted
    world["casting"] = {"mode": "cast", "max_named_voices": 1}
    assert "max_named_voices" not in ca.plan(world, "amb")["config"]


def test_apply_writes_raised_max_named_voices(world):
    Path("config.yaml").write_text("casting:\n  mode: solo\n  max_named_voices: 0\nelevenlabs:\n  model: eleven_v3\n")
    world["casting"]["max_named_voices"] = 0
    ca.apply(world, "amb", commit=False)
    assert yaml.safe_load(Path("config.yaml").read_text())["casting"]["max_named_voices"] == 1


def test_plan_uses_existing_key_for_known_voice(world):
    r = casting.load("amb")
    r["voices"][E]["name"] = "Whoever"
    casting.save(r)
    world["elevenlabs"]["voice_library"]["Lizzie"] = E
    p = ca.plan(world, "amb")
    assert p["config"]["library_add"] == {}
    assert next(c for c in p["cast"] if c["id"] == E)["voice"] == "Lizzie"


def test_plan_already_applied_has_no_story_changes(world):
    p0 = ca.plan(world, "amb")
    r = casting.load("amb")
    # make everything already match
    world["elevenlabs"]["voice_library"]["Elizabeth"] = E
    for s in ("one", "two"):
        d = Path("stories/amb") / s
        m = json.loads((d / "manifest.json").read_text())
        m["pinned"]["voices"] = {"narrator": "Meera", "Mrs Honoria Pike": "Elizabeth"}
        m["pinned"]["genre_voices"] = {"narrator": "Meera"}
        (d / "manifest.json").write_text(json.dumps(m))
    o = Path("stories/amb/one/outline.json")
    o.write_text(json.dumps({"cast": [{"name": "Mrs Honoria Pike", "voice": "Elizabeth"}]}))
    p = ca.plan(world, "amb")
    assert [s["slug"] for s in p["stories"]] == ["one"]       # only "Tom" uncast remains
    assert p["stories"][0]["uncast"] == ["Tom"] and not p["stories"][0]["pinned"]
    assert p0["stories"] != p["stories"] and r


def test_plan_no_bible_named(world):
    for s in ("one", "two"):
        d = Path("stories/amb") / s
        m = json.loads((d / "manifest.json").read_text())
        del m["bible"]
        (d / "manifest.json").write_text(json.dumps(m))
    b = ca.plan(world, "amb")["bible"]
    assert b == {"name": None, "path": None, "changes": [], "narrator": None, "notes": []}


def test_plan_bible_from_pinned_and_majority(world):
    for s in ("one", "two"):
        d = Path("stories/amb") / s
        m = json.loads((d / "manifest.json").read_text())
        del m["bible"]
        m["pinned"]["bible"] = "demo"
        (d / "manifest.json").write_text(json.dumps(m))
    assert ca.plan(world, "amb")["bible"]["name"] == "demo"


def test_plan_missing_bible_raises_value_error(world):
    d = Path("stories/amb/one/manifest.json")
    m = json.loads(d.read_text())
    m["bible"] = "ghost"
    d.write_text(json.dumps(m))
    d2 = Path("stories/amb/two/manifest.json")
    m2 = json.loads(d2.read_text())
    m2["bible"] = "ghost"
    d2.write_text(json.dumps(m2))
    with pytest.raises(ValueError, match="no bible 'ghost'"):
        ca.plan(world, "amb")


def test_plan_narrator_falls_back_to_genre_default(world):
    bp = Path("bibles/demo/demo.md")
    bp.write_text(bp.read_text().replace("voices:\n  narrator: Charlotte\n", ""))
    b = ca.plan(world, "amb")["bible"]
    assert b["narrator"] == {"from": None, "to": "Meera"}
    # if the cast narrator IS the genre's default, there is nothing to change
    r = casting.load("amb")
    casting.note_voice(r, "H" * 20, "Charlotte")
    casting.set_verdict(r, "narrator", "H" * 20, "cast", None)
    casting.save(r)
    world["elevenlabs"]["voice_library"]["Charlotte"] = "H" * 20
    assert ca.plan(world, "amb")["bible"]["narrator"] is None


def test_plan_busy_uses_runner(world, monkeypatch):
    from story_pipeline.review import runner
    monkeypatch.setattr(runner, "latest", lambda key: {"status": "running", "stage": "tts"}
                        if key in ("_casting-amb", "two") else {"status": "done"})
    assert ca.plan(world, "amb")["busy"] == ["_casting-amb (tts)", "two (tts)"]


def test_plan_stories_dir_from_cfg(world, tmp_path):
    world["stories_dir"] = "stories"
    assert ca.plan(world, "amb")["arc"] == "amb"


def test_plan_story_title_from_outline(world):
    m = Path("stories/amb/one/manifest.json")
    d = json.loads(m.read_text())
    del d["title"]
    m.write_text(json.dumps(d))
    one = next(s for s in ca.plan(world, "amb")["stories"] if s["slug"] == "one")
    assert one["title"] == "One Title"


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #

def test_apply_nothing_to_change(world):
    # wire everything up so the plan is empty
    world["elevenlabs"]["voice_library"]["Elizabeth"] = E
    world["elevenlabs"]["model"] = ca.V4
    world["casting"] = {"mode": "cast", "max_named_voices": 5}
    bp = Path("bibles/demo/demo.md")
    bp.write_text(bp.read_text().replace("voice: Alice", "voice: Elizabeth")
                  .replace("narrator: Charlotte", "narrator: Meera"))
    for s in ("one", "two"):
        d = Path("stories/amb") / s
        m = json.loads((d / "manifest.json").read_text())
        m["pinned"]["voices"] = {"narrator": "Meera", "Mrs Honoria Pike": "Elizabeth"}
        m["pinned"]["genre_voices"] = {"narrator": "Meera"}
        (d / "manifest.json").write_text(json.dumps(m))
    Path("stories/amb/one/outline.json").write_text(json.dumps(
        {"cast": [{"name": "Mrs Honoria Pike", "voice": "Elizabeth"}]}))
    with pytest.raises(ca.ApplyError, match="Nothing to change"):
        ca.apply(world, "amb", commit=False)


def test_apply_busy_refuses_without_writing(world, monkeypatch):
    from story_pipeline.review import runner
    monkeypatch.setattr(runner, "latest", lambda k: {"status": "running", "stage": "x"})
    before = Path("config.yaml").read_text()
    with pytest.raises(ca.ApplyError, match="Wait for these to finish"):
        ca.apply(world, "amb", commit=False)
    assert Path("config.yaml").read_text() == before


def test_apply_writes_everything(world):
    res = ca.apply(world, "amb", commit=False)
    assert res["ok"] and res["rerecord"] == ["one"]
    # voices.yaml
    lib = yaml.safe_load(Path("voices.yaml").read_text())["voice_library"]
    assert lib["Elizabeth"] == E and lib["Meera"] == M
    assert Path("voices.yaml").read_text().startswith("# lib")
    # config.yaml edited in place, with a backup of the original
    cfg_text = Path("config.yaml").read_text()
    assert "mode: cast" in cfg_text and "model: eleven_v4   # old" in cfg_text
    assert cfg_text.startswith("# per machine")
    baks = list(Path(".").glob("config.yaml.*.bak"))
    assert len(baks) == 1 and "mode: solo" in baks[0].read_text()
    # bible
    b = bibles.load("demo")
    assert b.member("Mrs Honoria Pike")["voice"] == "Elizabeth"
    assert b.meta["voices"]["narrator"] == "Meera"
    assert b.member("Mr Silas Rowe").get("voice") is None
    assert "She was once cast as Alice" in b.guide              # prose is the user's to rewrite
    # outline and manifest
    o = json.loads(Path("stories/amb/one/outline.json").read_text())
    assert o["cast"][0]["voice"] == "Elizabeth" and "voice" not in o["cast"][1]
    m = json.loads(Path("stories/amb/one/manifest.json").read_text())
    assert m["pinned"]["voices"] == {"narrator": "Meera", "Mrs Honoria Pike": "Elizabeth"}
    assert m["pinned"]["genre_voices"]["narrator"] == "Meera"
    m2 = json.loads(Path("stories/amb/two/manifest.json").read_text())
    assert m2["pinned"]["voices"]["narrator"] == "Meera"
    # changelog
    (e,) = changelog.entries(Path("bibles/demo/demo.md"))
    assert e.kind == "voices" and e.author == "Tester"
    assert "Voices recast from the casting page (amb)" in e.summary
    assert f"narrator: Meera - Calm (`{M}`) as `Meera`" in e.summary
    assert "Narration model: eleven_v3 → eleven_v4" in e.summary
    # casting record
    rec = casting.load("amb")
    (applied,) = rec["applied"]
    assert applied["cast"]["narrator"] == {"id": M, "voice": "Meera"}
    assert applied["model"] == ca.V4 and applied["mode"] == "cast"
    # returned file list
    files = set(res["files"])
    assert {"voices.yaml", str(Path("bibles/demo/demo.md")),
            str(Path("stories/amb/one/outline.json")),
            str(Path("stories/amb/_casting/auditions.json"))} <= files
    assert "git" not in res


def test_apply_refreshes_cfg_in_place_and_keeps_private_keys(world):
    world["_config_path"] = "config.yaml"
    ca.apply(world, "amb", commit=False)
    assert world["_config_path"] == "config.yaml"
    assert world["casting"]["mode"] == "cast" and world["elevenlabs"]["model"] == ca.V4
    assert world["elevenlabs"]["voice_library"]["Elizabeth"] == E


def test_apply_second_run_is_nothing_to_change(world):
    ca.apply(world, "amb", commit=False)
    with pytest.raises(ca.ApplyError, match="Nothing to change|Nobody"):
        ca.apply(world, "amb", commit=False)


def test_apply_without_flags_leaves_config_alone(world):
    before = Path("config.yaml").read_text()
    ca.apply(world, "amb", use_v4=False, cast_mode=False, commit=False)
    assert Path("config.yaml").read_text() == before
    assert not list(Path(".").glob("config.yaml.*.bak"))
    assert yaml.safe_load(Path("voices.yaml").read_text())["voice_library"]["Elizabeth"] == E


def test_apply_missing_voices_file(world):
    Path("voices.yaml").unlink()
    with pytest.raises(ca.ApplyError, match="voices.yaml.*not found"):
        ca.apply(world, "amb", commit=False)


def test_apply_respects_custom_config_path(world, tmp_path):
    other = tmp_path / "elsewhere.yaml"
    other.write_text(Path("config.yaml").read_text())
    ca.apply(world, "amb", commit=False, config_path=str(other))
    assert "mode: cast" in other.read_text()
    assert "mode: solo" in Path("config.yaml").read_text()


def test_apply_config_without_casting_section_fails_before_writing(world):
    Path("config.yaml").write_text("elevenlabs:\n  model: eleven_v3\n")
    vbefore = Path("voices.yaml").read_text()
    with pytest.raises(ca.ApplyError, match="no `casting:` section"):
        ca.apply(world, "amb", commit=False)
    assert Path("voices.yaml").read_text() == vbefore            # nothing written yet
    assert bibles.load("demo").member("Mrs Honoria Pike")["voice"] == "Alice"


def test_apply_rolls_back_when_edited_bible_does_not_validate(world, monkeypatch):
    calls = {"n": 0}

    def fake_validate(path, cfg=None):
        calls["n"] += 1
        return [] if calls["n"] == 1 else [bibles.Problem(True, "cast 'X'", "broke it")]
    monkeypatch.setattr(ca.bibles, "validate", fake_validate)
    snap = {p: Path(p).read_text() for p in ("config.yaml", "voices.yaml", "bibles/demo/demo.md")}
    with pytest.raises(ca.ApplyError, match="didn't validate, so nothing was changed"):
        ca.apply(world, "amb", commit=False)
    for p, text in snap.items():
        assert Path(p).read_text() == text, p
    assert json.loads(Path("stories/amb/one/manifest.json").read_text())["pinned"]["voices"]["narrator"] == "Charlotte"
    assert not changelog.entries(Path("bibles/demo/demo.md"))


def test_apply_ignores_preexisting_bible_errors(world, monkeypatch):
    pre = bibles.Problem(True, "old", "already broken")
    monkeypatch.setattr(ca.bibles, "validate", lambda p, c=None: [pre])
    ca.apply(world, "amb", commit=False)
    assert bibles.load("demo").member("Mrs Honoria Pike")["voice"] == "Elizabeth"


def test_apply_pinned_only_story_without_outline(world):
    ca.apply(world, "amb", commit=False)
    assert not Path("stories/amb/two/outline.json").exists()


# --------------------------------------------------------------------------- #
# _commit / _git
# --------------------------------------------------------------------------- #

PLAN = {"cast": [{"speaker": "narrator", "name": "Meera - Calm", "id": M}]}


@pytest.fixture
def gitenv(monkeypatch, tmp_path):
    cfgfile = tmp_path / "gitconfig"
    cfgfile.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfgfile))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    return cfgfile


def _sh(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_commit_outside_git_checkout(repo_root, gitenv, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repo_root.parent))
    Path("f.txt").write_text("x")
    out = ca._commit([Path("f.txt")], "amb", PLAN, push=False)
    assert out["committed"] is False and "not a git checkout" in out["message"]


def test_commit_exactly_given_files_with_fallback_identity(repo_root, gitenv):
    _sh("git", "init", "-q")
    Path("a.txt").write_text("a")
    Path("b.txt").write_text("b")
    _sh("git", "add", "b.txt")                                  # staged but not ours
    out = ca._commit([Path("a.txt"), Path("missing.txt")], "amb", PLAN, push=False)
    assert out["committed"] and out["pushed"] is False and out["files"] == ["a.txt"]
    assert _sh("git", "show", "--name-only", "--format=%an|%s").strip().splitlines()[0] == \
        "Middle Watch review|casting: apply amb voices"
    assert "b.txt" not in _sh("git", "show", "--name-only", "--format=")
    assert "narrator: Meera - Calm (" + M + ")" in _sh("git", "log", "-1", "--format=%b")
    assert "b.txt" in _sh("git", "diff", "--cached", "--name-only")      # still staged, uncommitted


def test_commit_uses_configured_identity(repo_root, gitenv):
    _sh("git", "init", "-q")
    _sh("git", "config", "user.name", "Configured")
    _sh("git", "config", "user.email", "c@example.invalid")
    Path("a.txt").write_text("a")
    ca._commit([Path("a.txt")], "amb", PLAN, push=False)
    assert _sh("git", "log", "-1", "--format=%an").strip() == "Configured"


def test_commit_nothing_to_commit(repo_root, gitenv):
    _sh("git", "init", "-q")
    _sh("git", "config", "user.name", "T")
    _sh("git", "config", "user.email", "t@example.invalid")
    Path("a.txt").write_text("a")
    assert ca._commit([Path("a.txt")], "amb", PLAN, push=False)["committed"]
    again = ca._commit([Path("a.txt")], "amb", PLAN, push=False)
    assert again == {"committed": False, "message": "nothing to commit — already up to date"}


def test_commit_push_success_and_refusal(repo_root, gitenv, tmp_path):
    remote = tmp_path / "remote.git"
    _sh("git", "init", "-q", "--bare", str(remote))
    _sh("git", "init", "-q")
    _sh("git", "config", "user.name", "T")
    _sh("git", "config", "user.email", "t@example.invalid")
    _sh("git", "remote", "add", "origin", str(remote))
    Path("seed.txt").write_text("s")
    _sh("git", "add", "seed.txt")
    _sh("git", "commit", "-q", "-m", "seed")
    _sh("git", "push", "-q", "-u", "origin", "HEAD")
    Path("a.txt").write_text("a")
    out = ca._commit([Path("a.txt")], "amb", PLAN, push=True)
    assert out["committed"] and out["pushed"] is True and "push_error" not in out
    # someone else pushes first: ours is refused, the commit stays local
    other = tmp_path / "other"
    _sh("git", "clone", "-q", str(remote), str(other))
    (other / "x.txt").write_text("x")
    _sh("git", "add", "x.txt", cwd=other)
    _sh("git", "-c", "user.name=O", "-c", "user.email=o@example.invalid", "commit", "-q", "-m", "theirs", cwd=other)
    _sh("git", "push", "-q", cwd=other)
    Path("b.txt").write_text("b")
    out = ca._commit([Path("b.txt")], "amb", PLAN, push=True)
    assert out["committed"] and out["pushed"] is False and out["push_error"]
    assert _sh("git", "log", "-1", "--format=%s").startswith("casting: apply amb")


def test_git_helper_raises_apply_error(repo_root, gitenv, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repo_root.parent))
    with pytest.raises(ca.ApplyError):
        ca._git("rev-parse", "--is-inside-work-tree")
    assert ca._git("rev-parse", "--is-inside-work-tree", check=False).returncode != 0


def test_apply_with_commit_in_a_git_repo(world, gitenv):
    _sh("git", "init", "-q")
    _sh("git", "config", "user.name", "T")
    _sh("git", "config", "user.email", "t@example.invalid")
    Path("config.yaml").write_text(Path("config.yaml").read_text())
    res = ca.apply(world, "amb", push=False)
    g = res["git"]
    assert g["committed"] and g["pushed"] is False
    assert "config.yaml" not in g["files"] and "voices.yaml" in g["files"]
    assert not any(f.endswith(".bak") for f in g["files"])
