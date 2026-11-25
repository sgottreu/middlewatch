"""casting.py: the per-arc casting record. Everything under the tmp root's stories/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from story_pipeline import casting

ID_A = "AAAAAAAAAAAAAAAAAAAA"
ID_B = "BBBBBBBBBBBBBBBBBBBB"
ID_C = "CCCCCCCCCCCCCCCCCCCC"


def make_story(arc="arc", slug="one", voices=None, chapters=None, extra=None):
    d = Path("stories") / arc / slug
    (d / "chapters").mkdir(parents=True)
    manifest = {"title": slug, "pinned": {"voices": voices or {"narrator": "Charlotte", "Mrs Hale": "Alice"}}}
    manifest.update(extra or {})
    (d / "manifest.json").write_text(json.dumps(manifest))
    chapters = chapters if chapters is not None else {"01": [
        ("narrator", "It was a dull morning."), ("Mrs Hale", "Good day to you."),
        ("Mrs Hale", "   "), (None, "Orphan line.")]}
    for n, segs in chapters.items():
        (d / "chapters" / f"{n}.json").write_text(json.dumps(
            {"segments": [{"speaker": s, "text": t} for s, t in segs]}))
    return d


def rec(**voices):
    return {"arc": "arc", "updated": None, "voices": voices, "unavailable": []}


def voice(name, tries=(), verdicts=None, labels=None):
    return {"name": name, "labels": labels or {}, "first_tried": "t",
            "tries": [{"story": "one", "speaker": s, "model": "m", "line": "l",
                       "file": f"{s}.mp3", "date": "d"} for s in tries],
            "verdicts": verdicts or {}}


# --- paths and persistence ----------------------------------------------------

def test_casting_dir_and_arc_of(tmp_path):
    assert casting.casting_dir("a") == Path("stories/a/_casting")
    assert casting.casting_dir("a", tmp_path) == tmp_path / "a" / "_casting"
    d = tmp_path / "myarc" / "story-slug"
    d.mkdir(parents=True)
    assert casting.arc_of(d) == "myarc"
    assert casting.arc_of(str(d)) == "myarc"


def test_now_is_iso_utc():
    s = casting.now()
    assert s.endswith("+00:00") and "." not in s


def test_load_default_and_save_roundtrip():
    r = casting.load("arc")
    assert r == {"arc": "arc", "updated": None, "voices": {}, "unavailable": []}
    casting.note_voice(r, ID_A, "Alice")
    path = casting.save(r)
    assert path == Path("stories/arc/_casting/auditions.json")
    assert r["updated"]                                   # stamped on save
    assert (path.parent / "CASTING.md").read_text().startswith("# Casting — arc")
    assert path.read_text().endswith("\n")
    assert casting.load("arc") == r


def test_save_keeps_unicode_unescaped():
    r = rec()
    r["unavailable"].append({"speaker": "Zoë", "pick": "x", "note": "café", "date": "d"})
    casting.save(r)
    assert "café" in Path("stories/arc/_casting/auditions.json").read_text()


# --- find_cast / story_lines / pick_line --------------------------------------

def test_find_cast_nested_and_missing():
    assert casting.find_cast({"voices": {"narrator": "C", "x": "y"}}) == {"narrator": "C", "x": "y"}
    assert casting.find_cast({"a": {"b": [{"voices": {"narrator": "C"}}]}}) == {"narrator": "C"}
    assert casting.find_cast({"voices": {"x": "y"}}) is None
    assert casting.find_cast([1, "x", {"voices": {"narrator": "C"}}]) == {"narrator": "C"}
    assert casting.find_cast("str") is None and casting.find_cast({}) is None


def test_story_lines():
    d = make_story()
    cast, lines = casting.story_lines(d)
    assert cast == {"narrator": "Charlotte", "Mrs Hale": "Alice"}
    assert lines == {"narrator": ["It was a dull morning."],
                     "Mrs Hale": ["Good day to you."], None: ["Orphan line."]}


def test_story_lines_sorted_chapters_and_blank_segments():
    d = make_story(chapters={"02": [("a", "second")], "01": [("a", "first")]})
    _, lines = casting.story_lines(d)
    assert lines["a"] == ["first", "second"]


def test_story_lines_no_voices_map():
    d = make_story(voices={"x": "y"})
    with pytest.raises(ValueError, match="No voices map"):
        casting.story_lines(d)


def test_pick_line():
    assert casting.pick_line([]) is None
    assert casting.pick_line(["short", "x" * 240, "y" * 10]) == "x" * 240
    long = ("Word " * 30).strip() + ". " + ("Another sentence here. " * 20)
    out = casting.pick_line([long], target=240, cap=400)
    assert len(out) <= 400 and out.endswith(".")
    nosent = "word " * 200
    out = casting.pick_line([nosent], cap=100)
    assert out.endswith("…") and len(out) <= 101
    short_sent = "Hi. " + "x" * 500
    assert casting.pick_line([short_sent], cap=400).endswith("…")   # cut <= 80 -> word cut


# --- listing arcs and stories --------------------------------------------------

def test_arc_stories_and_arcs():
    assert casting.arcs(Path("stories")) == []
    make_story("b", "s2")
    make_story("b", "s1")
    make_story("a", "x")
    make_story("c", "empty", chapters={})            # no chapters: not listed as story
    (Path("stories") / "_hidden" / "s").mkdir(parents=True)
    (Path("stories") / "_hidden" / "s" / "manifest.json").write_text("{}")
    (Path("stories") / ".dot").mkdir()
    (Path("stories") / "loose.txt").write_text("x")
    assert [p.name for p in casting.arc_stories("b")] == ["s1", "s2"]
    assert casting.arc_stories("c") == [] and casting.arc_stories("missing") == []
    assert casting.arcs() == ["a", "b", "c"]


# --- candidates ------------------------------------------------------------------

def test_candidates_roundtrip_and_set_candidate():
    assert casting.load_candidates("arc") == {}
    data = casting.set_candidate("arc", "Mrs Hale", ID_A, name=" Elizabeth ", note=" warm ")
    assert data == {"Mrs Hale": [{"id": ID_A, "name": "Elizabeth", "note": "warm"}]}
    assert casting.load_candidates("arc") == data
    casting.set_candidate("arc", "Mrs Hale", ID_A, name="Renamed")      # replaces, not duplicates
    assert [p["name"] for p in casting.load_candidates("arc")["Mrs Hale"]] == ["Renamed"]
    casting.set_candidate("arc", "Mrs Hale", ID_B)                      # name defaults to id
    assert casting.load_candidates("arc")["Mrs Hale"][1]["name"] == ID_B
    casting.set_candidate("arc", "Mrs Hale", ID_A, remove=True)
    casting.set_candidate("arc", "Mrs Hale", ID_B, remove=True)
    assert casting.load_candidates("arc") == {}                        # empty speaker dropped


def test_set_candidate_validates_id():
    for bad in ("", "short", "AAAAAAAAAAAAAAAAAAA!", " ", None, ID_A + "A"):
        with pytest.raises(ValueError, match="20 letters and digits"):
            casting.set_candidate("arc", "x", bad)
    assert casting.set_candidate("arc", "x", f"  {ID_A}  ")["x"][0]["id"] == ID_A


def test_set_candidate_remove_drops_legacy_string_picks_by_id():
    casting.save_candidates("arc", {"x": [ID_A, "Alice", {"id": ID_B}]})
    data = casting.set_candidate("arc", "x", ID_A, remove=True)
    assert data["x"] == ["Alice", {"id": ID_B}]


def test_pick_helpers():
    assert casting._pick_id({"id": ID_A}) == ID_A
    assert casting._pick_id(ID_A) == ID_A
    assert casting._pick_id("Alice") is None
    assert casting._pick_name({"name": "A", "id": ID_A}) == "A"
    assert casting._pick_name({"id": ID_A}) == ID_A
    assert casting._pick_name("Alice") == "Alice"


def test_labels_of():
    assert casting.labels_of({"labels": {"gender": "female", "age": "old", "x": "y"}}) == \
        {"gender": "female", "age": "old"}
    assert casting.labels_of({"gender": "male", "accent": "british"}) == \
        {"gender": "male", "accent": "british"}
    assert casting.labels_of({"labels": {"gender": "female"}, "gender": "male"}) == {"gender": "female"}
    assert casting.labels_of({}) == {}


# --- the record --------------------------------------------------------------------

def test_note_voice_creates_and_updates():
    r = rec()
    v = casting.note_voice(r, ID_A, "Alice", {"gender": "female", "age": ""}, source="library")
    assert v["name"] == "Alice" and v["labels"] == {"gender": "female"}
    assert v["sources"] == ["library"] and v["tries"] == [] and v["verdicts"] == {}
    first = v["first_tried"]
    casting.note_voice(r, ID_A, "", {"accent": "uk"}, source="library")      # blank name keeps
    casting.note_voice(r, ID_A, "Alice 2", None, source="account")
    assert v["name"] == "Alice 2" and v["labels"] == {"gender": "female", "accent": "uk"}
    assert v["sources"] == ["library", "account"] and v["first_tried"] == first


def test_add_try_updates_same_take():
    r = rec()
    casting.note_voice(r, ID_A, "Alice")
    kw = dict(story="s", speaker="Mrs Hale", model="m1", line="one", file="f1")
    casting.add_try(r, ID_A, **kw)
    casting.add_try(r, ID_A, **{**kw, "line": "two", "file": "f2"})
    assert len(r["voices"][ID_A]["tries"]) == 1
    assert r["voices"][ID_A]["tries"][0]["line"] == "two"
    casting.add_try(r, ID_A, **{**kw, "model": "m2"})
    casting.add_try(r, ID_A, **{**kw, "speaker": "Other"})
    casting.add_try(r, ID_A, **{**kw, "story": "s2"})
    assert len(r["voices"][ID_A]["tries"]) == 4


def test_add_unavailable_updates():
    r = rec()
    casting.add_unavailable(r, speaker="A", pick="P", note="gone")
    casting.add_unavailable(r, speaker="A", pick="P", note="still gone")
    casting.add_unavailable(r, speaker="A", pick="Q", note="n")
    assert [(u["pick"], u["note"]) for u in r["unavailable"]] == [("P", "still gone"), ("Q", "n")]


def test_find_exact_name_prefix_and_ambiguity(capsys):
    r = rec(**{ID_A: voice("Alice - Young"), ID_B: voice("Alicia"), ID_C: voice("Bob")})
    assert casting.find(r, ID_A)[0] == ID_A                       # id
    assert casting.find(r, "alice")[0] == ID_A                    # before " - "
    assert casting.find(r, "ALICIA")[0] == ID_B                   # full name, case-insens
    assert casting.find(r, "bo")[0] == ID_C                       # unique prefix
    with pytest.raises(ValueError) as e:
        casting.find(r, "ali")                                    # two prefix hits
    assert "matches 2 auditioned voices" in str(e.value)
    with pytest.raises(ValueError) as e:
        casting.find(r, "zzz")
    assert "matches 0" in str(e.value) and "none" in str(e.value)


def test_set_verdict_and_recast():
    r = rec(**{ID_A: voice("Alice", ["Mrs Hale"]), ID_B: voice("Bella", ["Mrs Hale"])})
    assert casting.set_verdict(r, "Mrs Hale", "Alice", "cast", "lovely") == ID_A
    assert r["voices"][ID_A]["verdicts"]["Mrs Hale"]["verdict"] == "cast"
    assert r["voices"][ID_A]["verdicts"]["Mrs Hale"]["note"] == "lovely"
    casting.set_verdict(r, "Mrs Hale", "Bella", "cast", None)
    prev = r["voices"][ID_A]["verdicts"]["Mrs Hale"]
    assert prev["verdict"] == "maybe" and prev["note"] == "lovely (recast)"
    assert r["voices"][ID_B]["verdicts"]["Mrs Hale"]["verdict"] == "cast"
    assert list(casting.cast_map(r)) == ["Mrs Hale"]
    assert casting.cast_map(r)["Mrs Hale"][0] == ID_B


def test_recast_with_no_prior_note():
    r = rec(**{ID_A: voice("Alice", ["x"]), ID_B: voice("Bella", ["x"])})
    casting.set_verdict(r, "x", "Alice", "cast", None)
    casting.set_verdict(r, "x", "Bella", "cast", None)
    assert r["voices"][ID_A]["verdicts"]["x"]["note"] == " (recast)"


def test_cast_only_demotes_same_speaker_and_non_cast_untouched():
    r = rec(**{ID_A: voice("Alice", ["x", "y"]), ID_B: voice("Bella", ["x", "y"])})
    casting.set_verdict(r, "x", "Alice", "cast", None)
    casting.set_verdict(r, "y", "Alice", "maybe", None)
    casting.set_verdict(r, "x", "Bella", "no", None)
    assert r["voices"][ID_A]["verdicts"]["x"]["verdict"] == "cast"
    casting.set_verdict(r, "y", "Bella", "cast", None)
    assert r["voices"][ID_A]["verdicts"]["y"]["verdict"] == "maybe"   # not "(recast)": was maybe
    assert r["voices"][ID_A]["verdicts"]["y"].get("note") is None
    assert r["voices"][ID_A]["verdicts"]["x"]["verdict"] == "cast"


def test_clear_verdict():
    r = rec(**{ID_A: voice("Alice", ["x"], {"x": {"verdict": "no", "note": None, "date": "d"}})})
    assert casting.clear_verdict(r, "x", "Alice") == ID_A
    assert r["voices"][ID_A]["verdicts"] == {}
    casting.clear_verdict(r, "x", "Alice")        # idempotent


def test_pool_excludes_cast():
    r = rec(**{ID_A: voice("Alice", ["x"], {"x": {"verdict": "cast", "note": None, "date": "d"}}),
               ID_B: voice("Bella", ["x"], {"x": {"verdict": "maybe", "note": None, "date": "d"}}),
               ID_C: voice("Cara")})
    assert [vid for vid, _ in casting.pool(r)] == [ID_B, ID_C]


# --- render --------------------------------------------------------------------------

def test_render_empty():
    out = casting.render(rec())
    assert "Nobody cast yet." in out and "Empty." in out and "Couldn't audition" not in out
    assert "casting verdict arc" in out


def test_render_full():
    r = rec(**{
        ID_A: voice("Alice", ["Mrs Hale"], {"Mrs Hale": {"verdict": "cast", "note": "a|b", "date": "d"}},
                    {"gender": "female", "accent": "british_rp"}),
        ID_B: voice("Bella", ["Mrs Hale", "Tom"], {"Tom": {"verdict": "maybe", "note": None, "date": "d"}})})
    r["unavailable"].append({"speaker": "Tom", "pick": "Gone", "note": "x|y", "date": "d"})
    r["updated"] = "2026-01-01T00:00:00+00:00"
    out = casting.render(r)
    assert "Updated 2026-01-01T00:00:00+00:00." in out
    assert f"| Mrs Hale | Alice | `{ID_A}` |" in out
    assert "### Mrs Hale" in out and "### Tom" in out
    assert "| a/b |" in out                                   # pipe escaped in notes
    assert "| Bella |" in out and "Mrs Hale, Tom (maybe)" in out
    assert "## Couldn't audition" in out and "| Tom | Gone | x/y |" in out
    assert "female, british rp" in casting._labels(r["voices"][ID_A])
    assert casting._labels({"labels": {}}) == "—"


def test_speakers_order_unique():
    r = rec(**{ID_A: voice("A", ["x", "y"]), ID_B: voice("B", ["y", "z"])})
    assert casting._speakers(r) == ["x", "y", "z"]


# --- arc_view ---------------------------------------------------------------------------

def _arc():
    make_story("arc", "one", voices={"narrator": "Charlotte", "Mrs Hale": "Alice"},
               chapters={"01": [("narrator", "N line."), ("Mrs Hale", "Hello there.")]})
    make_story("arc", "two", voices={"narrator": "Charlotte", "Tom": "George"},
               chapters={"01": [("narrator", "N2."), ("Tom", "Hi.")]})
    r = casting.load("arc")
    casting.note_voice(r, ID_A, "Alice - warm")
    casting.add_try(r, ID_A, story="one", speaker="Mrs Hale", model="m", line="l", file="a.mp3")
    casting.note_voice(r, ID_B, "Bella")
    casting.add_try(r, ID_B, story="one", speaker="Mrs Hale", model="m", line="l", file="b.mp3")
    casting.note_voice(r, ID_C, "Cara")
    casting.set_verdict(r, "Mrs Hale", "Bella", "cast", "best")
    casting.add_unavailable(r, speaker="Mrs Hale", pick="Dora (DDDD)", note="retired")
    casting.save(r)
    casting.save_candidates("arc", {"Mrs Hale": [{"id": ID_A, "name": "Alice"}, "Ghost",
                                                {"id": ID_C, "name": "Cara", "note": "try"}],
                                    "Extra": [ID_B], "_note": ["ignored"]})


def test_arc_view_structure():
    _arc()
    v = casting.arc_view("arc")
    assert v["arc"] == "arc" and v["stories"] == ["one", "two"] and v["story"] == "one"
    assert [s["speaker"] for s in v["speakers"]][:2] == ["narrator", "Mrs Hale"]
    assert "Extra" in [s["speaker"] for s in v["speakers"]]
    assert "_note" not in [s["speaker"] for s in v["speakers"]]
    hale = next(s for s in v["speakers"] if s["speaker"] == "Mrs Hale")
    assert hale["current"] == "Alice" and hale["line"] == "Hello there."
    assert hale["in_stories"] == ["one"]
    assert hale["cast"] == {"id": ID_B, "name": "Bella"}
    roles = {e["id"]: e["role"] for e in hale["entries"] if e["id"]}
    assert roles[ID_A] == "current" and roles[ID_C] == "candidate" and roles[ID_B] == "other"
    cur = hale["entries"][0]
    assert cur["auditioned"] and cur["takes"][0]["url"] == "/casting-audio/arc/a.mp3"
    ghost = next(e for e in hale["entries"] if e["name"] == "Ghost")
    assert ghost["id"] is None and not ghost["auditioned"]
    cara = next(e for e in hale["entries"] if e["id"] == ID_C)
    assert cara["note"] == "try" and cara["takes"] == []
    bella = next(e for e in hale["entries"] if e["id"] == ID_B)
    assert bella["verdict"] == "cast" and bella["verdict_note"] == "best"
    assert [p["id"] for p in v["pool"]] == [ID_A, ID_C]           # Bella is cast
    assert v["pool"][0]["sample"] == "/casting-audio/arc/a.mp3" and v["pool"][1]["sample"] is None
    assert v["updated"]


def test_arc_view_other_story_and_unknown_story_falls_back():
    _arc()
    v = casting.arc_view("arc", "two")
    assert v["story"] == "two"
    assert [s["speaker"] for s in v["speakers"]][:2] == ["narrator", "Tom"]
    assert casting.arc_view("arc", "nope")["story"] == "one"


def test_arc_view_marks_unavailable_pick():
    _arc()
    r = casting.load("arc")
    casting.add_unavailable(r, speaker="Mrs Hale", pick="Ghost (x)", note="gone")
    casting.save(r)
    hale = next(s for s in casting.arc_view("arc")["speakers"] if s["speaker"] == "Mrs Hale")
    ghost = next(e for e in hale["entries"] if e["name"] == "Ghost")
    assert ghost["unavailable"] == "gone"


def test_arc_view_empty_arc():
    v = casting.arc_view("nothing")
    assert v["story"] is None and v["stories"] == [] and v["speakers"] == [] and v["pool"] == []


def test_arc_view_skips_stories_without_voice_map():
    _arc()
    make_story("arc", "three", voices={"x": "y"})
    v = casting.arc_view("arc")
    assert "three" in v["stories"]
    hale = next(s for s in v["speakers"] if s["speaker"] == "Mrs Hale")
    assert hale["in_stories"] == ["one"]


# --- CLI ----------------------------------------------------------------------------------

def _record_with_voices():
    r = casting.load("arc")
    casting.note_voice(r, ID_A, "Alice", {"gender": "female", "accent": "british"})
    casting.add_try(r, ID_A, story="one", speaker="Mrs Hale", model="m", line="l", file="a.mp3")
    casting.note_voice(r, ID_B, "Bob", {"gender": "male", "accent": "american"})
    casting.add_try(r, ID_B, story="one", speaker="Tom", model="m", line="l", file="b.mp3")
    casting.save(r)


def test_cli_show(capsys):
    casting.main(["show", "arc"])           # works with no auditions
    out = capsys.readouterr().out
    assert "# Casting — arc" in out and "Nobody cast yet." in out
    assert Path("stories/arc/_casting/CASTING.md").exists()


def test_cli_requires_auditions_for_other_commands():
    for argv in (["verdict", "arc", "x", "y", "cast"], ["pool", "arc"]):
        with pytest.raises(SystemExit) as e:
            casting.main(argv)
        assert "No auditions recorded for arc" in str(e.value)


def test_cli_verdict(capsys):
    _record_with_voices()
    casting.main(["verdict", "arc", "Mrs Hale", "Alice", "cast", "--note", "yes"])
    out = capsys.readouterr().out
    assert f"Mrs Hale: Alice ({ID_A}) -> cast." in out
    assert "Not applied to the bible" in out and "hasn't been auditioned" not in out
    r = casting.load("arc")
    assert r["voices"][ID_A]["verdicts"]["Mrs Hale"]["note"] == "yes"
    assert "Mrs Hale | Alice" in Path("stories/arc/_casting/CASTING.md").read_text()


def test_cli_verdict_not_auditioned_note_and_no_apply_hint(capsys):
    _record_with_voices()
    casting.main(["verdict", "arc", "Tom", "Alice", "no"])
    out = capsys.readouterr().out
    assert "hasn't been auditioned as Tom (heard as: Mrs Hale). Recorded anyway." in out
    assert "Not applied" not in out


def test_cli_verdict_rejects_bad_verdict(capsys):
    _record_with_voices()
    with pytest.raises(SystemExit):
        casting.main(["verdict", "arc", "x", "Alice", "love"])


def test_cli_pool_filters(capsys):
    _record_with_voices()
    casting.main(["pool", "arc"])
    out = capsys.readouterr().out
    assert "Alice" in out and "Bob" in out
    casting.main(["pool", "arc", "--gender", "FEMALE"])
    out = capsys.readouterr().out
    assert "Alice" in out and "Bob" not in out
    casting.main(["pool", "arc", "--accent", "amer"])
    out = capsys.readouterr().out
    assert "Bob" in out and "Alice" not in out
    casting.main(["pool", "arc", "--gender", "female", "--accent", "amer"])
    assert capsys.readouterr().out == ""


def test_cli_stories_dir_option(tmp_path, capsys):
    casting.main(["--stories", str(tmp_path), "show", "z"])
    assert (tmp_path / "z" / "_casting" / "CASTING.md").exists()


def test_cli_verdict_unknown_voice_exits_with_message():
    _record_with_voices()
    with pytest.raises(SystemExit) as e:
        casting.main(["verdict", "arc", "Mrs Hale", "zzz", "cast"])
    assert "matches 0 auditioned voices" in str(e.value)
