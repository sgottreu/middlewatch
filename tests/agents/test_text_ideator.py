"""agents/text.py ideator side: build_outline, ideate, revise_outline, ask_ideator,
finished_stories, _apply_bible_casting, _check_bible."""

from __future__ import annotations

import copy
import json

import pytest

from story_pipeline import bibles, genres, llm
from story_pipeline.agents import text
from story_pipeline.bundle import Bundle
from tests.conftest import REPO_ROOT

from .helpers import cfg, chapter_json, make_bundle, mini_outline

USAGE_KEYS = {"input_tokens", "output_tokens", "stop_reason"}


def raw(minutes=15, **over):
    """What the ideator model returns: an outline without the pipeline's own fields."""
    o = mini_outline(minutes)
    o.pop("genre")
    o.update(over)
    return o


def real_raw():
    o = json.loads((REPO_ROOT / "stories/amberlight/the-cracked-beam/outline.json").read_text())
    for k in ("slug", "series", "bible", "length_min", "genre"):
        o.pop(k, None)
    return o


def queue(scripted, outline):
    scripted.json_queue.append(copy.deepcopy(outline))


# --------------------------------------------------------------------------- #
# build_outline: standalone genre
# --------------------------------------------------------------------------- #


def test_build_outline_happy_path_fills_pipeline_fields(scripted):
    queue(scripted, raw())
    o = text.build_outline(cfg(), "regency", "gothic")
    assert o["genre"] == "regency" and o["length_min"] == 15
    assert o["subgenre"] == "slice_of_life"          # model's own value wins over the requested one
    assert "bible" not in o and "series" not in o and "notes" not in o
    assert [c["n"] for c in o["chapters"]] == [1, 2, 3, 4, 5]


def test_build_outline_subgenre_defaults_to_first_when_model_omits(scripted):
    queue(scripted, raw(subgenre=None) | {})          # key present but None
    o = raw()
    del o["subgenre"]
    scripted.json_queue[:] = [o]
    out = text.build_outline(cfg(), "regency")
    assert out["subgenre"] == genres.load("regency").subgenres[0]


def test_build_outline_calls_ideator_model_with_config_ceiling(scripted):
    queue(scripted, raw())
    c = cfg()
    c["models"]["ideator"] = "my-ideator"
    c["max_tokens"]["ideator"] = 12345
    text.build_outline(c, "regency")
    call = scripted.json_calls[0]
    assert call["model"] == "my-ideator" and call["max_tokens"] == 12345
    assert call["user"] == "Produce one outline. Return the JSON object."


def test_build_outline_system_prompt_carries_subgenre_length_and_tropes(scripted):
    queue(scripted, raw())
    text.build_outline(cfg(), "regency", "gothic", seed=1)
    system = scripted.json_calls[0]["system"]
    assert genres.load("regency").subgenre_note("gothic") in system
    assert "2,250 words total" in system
    assert "{" + "trope_menu" + "}" not in system
    assert system.count("\n- **") >= 8                 # trope_sample=8 menu present


def test_build_outline_trope_seed_is_deterministic(scripted):
    for _ in range(2):
        queue(scripted, raw())
        text.build_outline(cfg(), "regency", seed=42)
    a, b = (c["system"] for c in scripted.json_calls)
    assert a == b


def test_build_outline_notes_in_prompt_and_outline(scripted):
    queue(scripted, raw())
    o = text.build_outline(cfg(), "regency", notes="Set it at a mill.")
    assert o["notes"] == "Set it at a mill."
    assert "Notes from the person commissioning this" in scripted.json_calls[0]["system"]
    assert "Set it at a mill." in scripted.json_calls[0]["system"]


def test_build_outline_without_notes_has_no_notes_block(scripted):
    queue(scripted, raw())
    text.build_outline(cfg(), "regency")
    assert "Notes from the person commissioning" not in scripted.json_calls[0]["system"]


def test_build_outline_length_arg_beats_config(scripted):
    queue(scripted, raw(30))
    c = cfg()
    c["length_min"] = 15
    o = text.build_outline(c, "regency", length_min=30)
    assert o["length_min"] == 30
    assert "4,500 words total" in scripted.json_calls[0]["system"]


def test_build_outline_length_from_config_when_arg_none(scripted):
    queue(scripted, raw(30))
    c = cfg()
    c["length_min"] = 30
    assert text.build_outline(c, "regency")["length_min"] == 30


def test_build_outline_feedback_with_previous_builds_revision_ask(scripted):
    prev = text.build_outline.__globals__["json"].loads(json.dumps(mini_outline()))
    queue(scripted, raw())
    text.build_outline(cfg(), "regency", feedback="Make it funnier.", previous=prev)
    ask = scripted.json_calls[0]["user"]
    assert "Make it funnier." in ask and json.dumps(prev, indent=2) in ask
    assert "same title, cast names" in ask


def test_build_outline_feedback_without_previous_is_plain_ask(scripted):
    queue(scripted, raw())
    text.build_outline(cfg(), "regency", feedback="x")
    assert scripted.json_calls[0]["user"].startswith("Produce one outline.")


@pytest.mark.parametrize("kwargs,match", [
    (dict(genre_name=None), "need a --genre"),
    (dict(genre_name="western"), "unknown genre 'western'"),
    (dict(genre_name="regency", subgenre="zombie"), "unknown subgenre 'zombie'"),
    (dict(genre_name="regency", length_min=45), "no length spec for 45"),
    (dict(genre_name="regency", bible_name="nope"), "no bible 'nope'"),
])
def test_build_outline_input_errors_fail_before_any_model_call(scripted, kwargs, match):
    with pytest.raises(ValueError, match=match):
        text.build_outline(cfg(), **kwargs)
    assert scripted.json_calls == []


@pytest.mark.parametrize("mutate,match", [
    (lambda o: o.pop("title"), "outline missing 'title'"),
    (lambda o: o.__setitem__("tropes", ["Enemies of manner"]), "want 2 or 3"),
    (lambda o: o.__setitem__("chapters", o["chapters"][:3]), "3 chapters; a 15-minute"),
])
def test_build_outline_rejects_invalid_model_output(scripted, mutate, match):
    o = raw()
    mutate(o)
    queue(scripted, o)
    with pytest.raises(ValueError, match=match):
        text.build_outline(cfg(), "regency")


def test_build_outline_model_errors_propagate(scripted):
    scripted.json_queue.append(llm.TruncatedReply("too long"))
    with pytest.raises(llm.TruncatedReply):
        text.build_outline(cfg(), "regency")
    scripted.json_queue.append(ValueError("no JSON object in model reply"))
    with pytest.raises(ValueError, match="no JSON"):
        text.build_outline(cfg(), "regency")
    scripted.json_queue.append(llm.ConfigurationError("bad key"))
    with pytest.raises(llm.ConfigurationError):
        text.build_outline(cfg(), "regency")


# --------------------------------------------------------------------------- #
# build_outline: with a bible
# --------------------------------------------------------------------------- #


def test_build_outline_bible_supplies_genre_and_series(scripted, real_bibles):
    queue(scripted, real_raw())
    o = text.build_outline(cfg(), bible_name="amberlight", length_min=30)
    assert o["genre"] == "regency" and o["bible"] == "amberlight"
    assert o["series"] == "Middle Watch: Amberlight"
    assert "Series bible: Middle Watch: Amberlight" in scripted.json_calls[0]["system"]


def test_build_outline_bible_genre_conflict(scripted, real_bibles):
    with pytest.raises(ValueError, match="is a regency series, but --genre said fantasy"):
        text.build_outline(cfg(), "fantasy", bible_name="amberlight")
    assert scripted.json_calls == []


def test_build_outline_bible_matching_genre_ok(scripted, real_bibles):
    queue(scripted, real_raw())
    assert text.build_outline(cfg(), "regency", bible_name="amberlight", length_min=30)["bible"]


def test_build_outline_forces_recurring_identity_from_bible(scripted, real_bibles):
    o = real_raw()
    rowe = next(c for c in o["cast"] if c["name"] == "Mr Silas Rowe")
    rowe.update(age=99, appearance="paraphrased", dress="paraphrased", voice="Nobody",
                gender="female", station="drifted")
    queue(scripted, o)
    out = text.build_outline(cfg(), bible_name="amberlight", length_min=30)
    canon = bibles.load("amberlight", "bibles").member("Mr Silas Rowe")
    got = next(c for c in out["cast"] if c["name"] == "Mr Silas Rowe")
    for f in ("appearance", "dress", "voice", "gender", "station"):
        assert got[f] == canon[f]
    assert got["age"] == 34 and got["recurring"] is True


def test_build_outline_validates_after_casting(scripted, real_bibles):
    o = real_raw()
    o["tropes"] = ["Enemies of manner", "Not a trope"]
    queue(scripted, o)
    with pytest.raises(ValueError, match="tropes not in"):
        text.build_outline(cfg(), bible_name="amberlight", length_min=30)


def test_build_outline_history_block_reaches_ideator(scripted, real_bibles, real_story):
    queue(scripted, real_raw())
    text.build_outline(cfg(), bible_name="amberlight", length_min=30)
    system = scripted.json_calls[0]["system"]
    assert "What this series has already done" in system
    assert "1 approved story so far" in system


def test_build_outline_history_without_bible_sees_every_approved_story(scripted, real_story):
    # scan(bible=None) applies no filter: a bible-less run is shown all approved stories
    queue(scripted, raw())
    text.build_outline(cfg(), "regency")
    assert "1 approved story so far" in scripted.json_calls[0]["system"]


def test_check_bible_refuses_bible_with_fatal_errors(scripted, repo_root):
    (repo_root / "bibles" / "broken.md").write_text("---\nseries: Broken\n---\nbody\n")
    with pytest.raises(ValueError, match=r"bible 'broken' has \d+ error\(s\)"):
        text.build_outline(cfg(), "regency", bible_name="broken")
    assert scripted.json_calls == []


# --------------------------------------------------------------------------- #
# _apply_bible_casting
# --------------------------------------------------------------------------- #


def fake_bible(anchor=1817):
    return bibles.Bible(
        name="x", path=None, guide="",
        meta={"anchor_year": anchor, "recurring_cast": [
            {"name": "Mrs Honoria Pike", "age": 58, "gender": "female",
             "station": "widow", "appearance": "stout", "dress": "plum", "voice": "Meera"}]})


def cast_of(age=1, station=None):
    m = {"name": "Mrs Honoria Pike", "age": age}
    if station:
        m["station"] = station
    return {"setting": "", "cast": [m, {"name": "New Person", "age": 40}]}


@pytest.mark.parametrize("setting,age", [("spring 1817", 58), ("spring 1816", 57),
                                         ("spring 1820", 61), ("no year", 58)])
def test_casting_shifts_age_by_anchor_difference(setting, age):
    o = cast_of()
    o["setting"] = setting
    text._apply_bible_casting(o, fake_bible())
    assert o["cast"][0]["age"] == age


def test_casting_station_pinned_up_to_anchor_year_and_ideator_wins_after():
    o = cast_of(station="remarried")
    o["setting"] = "1817"
    text._apply_bible_casting(o, fake_bible())
    assert o["cast"][0]["station"] == "widow"
    o = cast_of(station="remarried")
    o["setting"] = "1819"
    text._apply_bible_casting(o, fake_bible())
    assert o["cast"][0]["station"] == "remarried"
    o = cast_of()                       # later year but ideator gave none -> canon fills in
    o["setting"] = "1819"
    text._apply_bible_casting(o, fake_bible())
    assert o["cast"][0]["station"] == "widow"


def test_casting_leaves_invented_characters_alone_and_marks_recurring():
    o = cast_of()
    text._apply_bible_casting(o, fake_bible())
    assert o["cast"][0]["recurring"] is True
    assert o["cast"][1] == {"name": "New Person", "age": 40}


def test_casting_keeps_alias_name_but_takes_canon_fields():
    bible = fake_bible()
    bible.meta["recurring_cast"][0]["aliases"] = ["Mrs Honoria Fenn"]
    o = {"setting": "1817", "cast": [{"name": "Mrs Honoria Pike", "appearance": "x"}]}
    text._apply_bible_casting(o, bible)
    assert o["cast"][0]["appearance"] == "stout"


# --------------------------------------------------------------------------- #
# ideate
# --------------------------------------------------------------------------- #


def test_ideate_creates_bundle_with_outline_and_pins(scripted, repo_root):
    queue(scripted, raw())
    b = text.ideate(cfg(), "stories", "regency", "slice_of_life")
    assert b.root.resolve() == (repo_root / "stories" / "standalone" / "the-cracked-beam").resolve()
    o = b.outline()
    assert o["slug"] == "the-cracked-beam" and o["genre"] == "regency"
    g = genres.load("regency")
    m = b.manifest()
    p = m["pinned"]
    assert p["art_style"] == g.art_style
    assert (p["genre"], p["flavor"], p["title"]) == ("regency", "slice_of_life", "The Cracked Beam")
    assert p["length_min"] == 15 and p["scenes_per_chapter"] == 3
    assert p["genre_voices"] == g.voices()
    assert "series" not in p and "bible" not in p
    assert m["stages"]["ideate"] == "awaiting_review"
    assert m["pipeline"] == "story"


def test_ideate_ledger_story_labels(scripted):
    queue(scripted, raw())
    b = text.ideate(cfg(), "stories", "regency")
    ls = b.ledger_story()
    assert ls["pipeline"] == "story" and ls["slug"] == "the-cracked-beam"
    assert (ls["title"], ls["genre"], ls["flavor"]) == ("The Cracked Beam", "regency", "slice_of_life")


def test_ideate_scenes_per_chapter_config_override(scripted):
    queue(scripted, raw())
    c = cfg()
    c["gemini"]["scenes_per_chapter"] = 6
    assert text.ideate(c, "stories", "regency").pinned("scenes_per_chapter") == 6


def test_ideate_30_minute_pins_length_and_scenes(scripted):
    queue(scripted, raw(30))
    b = text.ideate(cfg(), "stories", "regency", length_min=30)
    assert b.pinned("length_min") == 30 and b.pinned("scenes_per_chapter") == 4


def test_ideate_with_bible_uses_arc_folder_series_and_voice_merge(scripted, real_bibles):
    queue(scripted, real_raw())
    b = text.ideate(cfg(), "stories", bible_name="amberlight", length_min=30)
    assert b.root.parent.name == "amberlight"
    p = b.manifest()["pinned"]
    assert p["bible"] == "amberlight" and p["series"] == "Middle Watch: Amberlight"
    bible = bibles.load("amberlight", "bibles")
    assert p["art_style"] == (bible.meta.get("art_style") or genres.load("regency").art_style)
    for k, v in (bible.meta.get("voices") or {}).items():
        assert p["genre_voices"][k] == v
    assert b.outline()["bible"] == "amberlight"


def test_ideate_slug_must_be_unique_across_arcs(scripted, real_bibles):
    queue(scripted, raw())
    text.ideate(cfg(), "stories", "regency")
    queue(scripted, real_raw() | {"title": "The Cracked Beam"})
    with pytest.raises(ValueError, match="already exists"):
        text.ideate(cfg(), "stories", bible_name="amberlight", length_min=30)


def test_ideate_invalid_outline_creates_nothing(scripted, repo_root):
    o = raw()
    del o["cast"]
    queue(scripted, o)
    with pytest.raises(ValueError):
        text.ideate(cfg(), "stories", "regency")
    assert list((repo_root / "stories").glob("*/*")) == []


def test_ideate_records_spend_in_manifest_and_ledger(scripted, repo_root):
    import csv
    queue(scripted, raw())
    c = cfg()
    b = text.ideate(c, "stories", "regency")
    assert b.manifest()["spend"] == {"anthropic_input_tokens": 100,
                                     "anthropic_output_tokens": 50}
    rows = list(csv.DictReader(open(repo_root / "ledger" / "costs.csv")))
    assert {r["unit_type"] for r in rows} == {"input_tokens", "output_tokens"}
    assert all(r["pipeline"] == "story" and r["stage"] == "ideate"
               and r["model"] == c["models"]["ideator"]
               and r["story_slug"] == b.root.name and r["genre"] == "regency"
               for r in rows)


def test_ledger_failure_does_not_lose_the_outline(scripted, repo_root, capsys):
    # An unpriced model must not cost the paid outline: manifest counts stay,
    # the ledger miss is a warning.
    queue(scripted, raw())
    c = cfg()
    c["models"]["ideator"] = "model-with-no-rate"
    b = text.ideate(c, "stories", "regency")
    assert b.outline_path.exists()
    assert b.manifest()["spend"]["anthropic_output_tokens"] == 50
    assert "cost not written to the ledger" in capsys.readouterr().err


def test_approve_sets_ideate_approved(scripted):
    queue(scripted, raw())
    b = text.ideate(cfg(), "stories", "regency")
    text.approve(b)
    assert b.stage_status("ideate") == "approved"


# --------------------------------------------------------------------------- #
# revise_outline
# --------------------------------------------------------------------------- #


@pytest.fixture
def ideated(scripted):
    queue(scripted, raw())
    return text.ideate(cfg(), "stories", "regency")


def test_revise_outline_keeps_slug_logs_revision_and_snapshots(scripted, ideated):
    before = ideated.outline()
    new = raw(title="A Different Title", logline="New.")
    queue(scripted, new)
    out = text.revise_outline(cfg(), ideated, "Rename it.")
    assert out["title"] == "A Different Title"
    assert out["slug"] == "the-cracked-beam"              # bundle identity unchanged
    assert out["revisions"] == [{"feedback": "Rename it.", "previous_title": "The Cracked Beam"}]
    assert json.loads((ideated.root / "drafts" / "outline.1.json").read_text()) == before
    assert ideated.outline() == out
    assert ideated.stage_status("ideate") == "awaiting_review"
    ask = scripted.json_calls[-1]["user"]
    assert "Rename it." in ask and '"The Cracked Beam"' in ask


def test_revise_outline_twice_accumulates(scripted, ideated):
    queue(scripted, raw(title="Second"))
    text.revise_outline(cfg(), ideated, "one")
    queue(scripted, raw(title="Third"))
    out = text.revise_outline(cfg(), ideated, "two")
    assert [r["feedback"] for r in out["revisions"]] == ["one", "two"]
    assert out["revisions"][1]["previous_title"] == "Second"
    assert (ideated.root / "drafts" / "outline.2.json").exists()


def test_revise_outline_held_to_pinned_length(scripted, repo_root):
    b = make_bundle(repo_root, minutes=30)
    queue(scripted, raw(30))
    out = text.revise_outline(cfg(), b, "tweak")
    assert out["length_min"] == 30
    assert "4,500 words total" in scripted.json_calls[0]["system"]
    # a 15-minute-shaped answer to a 30-minute story is refused
    queue(scripted, raw(15))
    with pytest.raises(ValueError, match="30-minute story wants 7-9"):
        text.revise_outline(cfg(), b, "tweak")


def test_revise_outline_failure_leaves_outline_untouched(scripted, ideated):
    before = ideated.outline_path.read_text()
    bad = raw()
    del bad["title"]
    queue(scripted, bad)
    with pytest.raises(ValueError):
        text.revise_outline(cfg(), ideated, "x")
    assert ideated.outline_path.read_text() == before
    assert not (ideated.root / "drafts" / "outline.1.json").exists()


def test_revise_outline_carries_notes_forward(scripted, repo_root):
    b = make_bundle(repo_root, outline=mini_outline() | {"notes": "Mill setting."})
    queue(scripted, raw())
    out = text.revise_outline(cfg(), b, "tweak")
    assert out["notes"] == "Mill setting."
    assert "Mill setting." in scripted.json_calls[0]["system"]


# --------------------------------------------------------------------------- #
# ask_ideator / finished_stories
# --------------------------------------------------------------------------- #


def test_ask_ideator_empty_question_raises(scripted, ideated):
    for q in ("", "   ", None):
        with pytest.raises(ValueError, match="Write the question"):
            text.ask_ideator(cfg(), ideated, q)
    assert scripted.text_calls == []


def test_ask_ideator_returns_answer_usage_and_logs(scripted, ideated):
    scripted.text_queue.append("  Because the beam is cracked.  \n")
    res = text.ask_ideator(cfg(), ideated, "  Why a beam? ")
    assert res["answer"] == "Because the beam is cracked."
    assert set(res["usage"]) == USAGE_KEYS and res["read"] is None
    call = scripted.text_calls[0]
    assert call["max_tokens"] == 16000
    assert '"title": "The Cracked Beam"' in call["user"] and call["user"].endswith("Why a beam?")
    assert "{" + "genre_guide" + "}" not in call["system"]
    log = (ideated.root / "questions.md").read_text()
    assert "**Q:** Why a beam?" in log and "Because the beam is cracked." in log
    assert "*Read:" not in log


def test_ask_ideator_appends_each_question(scripted, ideated):
    for q in ("one?", "two?"):
        scripted.text_queue.append("a")
        text.ask_ideator(cfg(), ideated, q)
    log = (ideated.root / "questions.md").read_text()
    assert log.count("**Q:**") == 2


def test_ask_ideator_changes_nothing_else(scripted, ideated):
    before_outline, before_manifest = ideated.outline_path.read_text(), ideated.manifest()
    scripted.text_queue.append("a")
    text.ask_ideator(cfg(), ideated, "q?")
    assert ideated.outline_path.read_text() == before_outline
    after = ideated.manifest()
    # Only the spend moves: asking is a paid call and is now recorded.
    assert after["spend"]["anthropic_output_tokens"] == before_manifest["spend"]["anthropic_output_tokens"] + 50
    after.pop("spend"), before_manifest.pop("spend")
    assert after == before_manifest


def _finished(root, slug, title, bible=None, complete=True):
    b = Bundle.create(root / "stories", slug, arc=bible)
    o = mini_outline() | {"title": title, "slug": slug}
    if bible:
        o["bible"] = bible
    b.outline_path.write_text(json.dumps(o))
    n_written = 5 if complete else 3
    for n in range(1, n_written + 1):
        c = chapter_json(n, text=f"{title} chapter {n} prose.")
        b.chapter_json(n).write_text(json.dumps(c))
        b.chapter_md(n).write_text(text.render_markdown(c, o))
    return b


def test_finished_stories_filters_by_series_and_completeness(repo_root):
    me = _finished(repo_root, "me", "Mine")
    _finished(repo_root, "done", "Done One")
    _finished(repo_root, "partial", "Partial", complete=False)
    _finished(repo_root, "other", "Other Series", bible="amberlight")
    (me.root.parent / "no-manifest").mkdir()
    bad = _finished(repo_root, "badoutline", "Bad")
    bad.outline_path.write_text("{")
    found = text.finished_stories(me)
    assert [t for t, _ in found] == ["Done One"]
    title, body = found[0]
    assert body.startswith("# Done One\n\nSetting: ") and "Done One chapter 3 prose." in body


def test_ask_ideator_read_stories_includes_text_and_logs_read(scripted, repo_root):
    me = _finished(repo_root, "me", "Mine")
    _finished(repo_root, "done", "Done One")
    scripted.text_queue.append("It matches.")
    res = text.ask_ideator(cfg(), me, "Continuity?", read_stories=True)
    assert res["read"] == ["Done One"]
    system = scripted.text_calls[0]["system"]
    assert "Finished stories in this series" in system and "Done One chapter 1 prose." in system
    assert "*Read: Done One*" in (me.root / "questions.md").read_text()


def test_ask_ideator_read_stories_without_any_found(scripted, repo_root):
    me = _finished(repo_root, "me", "Mine")
    scripted.text_queue.append("ok")
    res = text.ask_ideator(cfg(), me, "q?", read_stories=True)
    assert res["read"] == []
    assert "Finished stories in this series" not in scripted.text_calls[0]["system"]


def test_ask_ideator_with_bible_includes_ideas_for_ideator(scripted, repo_root):
    (repo_root / "bibles" / "seriesx.md").write_text(
        "---\nseries: Series X\ngenre: regency\n---\nGuide.\n\n## Story ideas\n\nSEED-777\n")
    o = mini_outline() | {"bible": "seriesx"}
    b = make_bundle(repo_root, outline=o)
    scripted.text_queue.append("fine")
    text.ask_ideator(cfg(), b, "q?")
    assert "SEED-777" in scripted.text_calls[0]["system"]


def test_ask_ideator_propagates_model_errors(scripted, ideated):
    scripted.text_queue.append(llm.TruncatedReply("cut"))
    with pytest.raises(llm.TruncatedReply):
        text.ask_ideator(cfg(), ideated, "q?")
    assert not (ideated.root / "questions.md").exists()
