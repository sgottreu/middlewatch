"""agents/text.py: length table, prompt assembly, config-derived helpers, outline validation."""

from __future__ import annotations

import copy

import pytest

from story_pipeline import bibles, config, genres
from story_pipeline.agents import text

from .helpers import cfg, mini_outline

REGENCY = genres.load("regency")


# --------------------------------------------------------------------------- #
# LENGTHS / length_spec
# --------------------------------------------------------------------------- #


def test_wpm_is_150():
    assert text.WPM == 150


def test_lengths_only_15_and_30_exist():
    assert sorted(text.LENGTHS) == [15, 30]
    assert text.DEFAULT_LENGTH == 15


def test_length_15_values():
    s = text.LENGTHS[15]
    assert (s["words"], s["chapters"], s["chapter_words"]) == (2250, 5, 450)
    assert (s["chapters_min"], s["chapters_max"]) == (4, 6)
    assert (s["cast_min"], s["cast_max"]) == (3, 6)
    assert (s["beats_min"], s["beats_max"]) == (3, 4)
    assert s["scenes_per_chapter"] == 3


def test_length_30_values():
    s = text.LENGTHS[30]
    assert (s["words"], s["chapters"], s["chapter_words"]) == (4500, 8, 560)
    assert (s["chapters_min"], s["chapters_max"]) == (7, 9)
    assert (s["cast_min"], s["cast_max"]) == (4, 7)
    assert (s["beats_min"], s["beats_max"]) == (4, 5)
    assert s["scenes_per_chapter"] == 4


@pytest.mark.parametrize("m", [15, 30])
def test_each_length_is_internally_consistent(m):
    s = text.LENGTHS[m]
    assert s["words"] == m * text.WPM * 1.0
    assert s["chapters_min"] <= s["chapters"] <= s["chapters_max"]
    assert s["cast_min"] <= s["cast_max"] and s["beats_min"] <= s["beats_max"]
    assert abs(s["chapters"] * s["chapter_words"] - s["words"]) <= 0.05 * s["words"]


def test_length_spec_adds_minutes_and_copies():
    s = text.length_spec(30)
    assert s["minutes"] == 30 and s["words"] == 4500
    s["words"] = 1
    assert text.LENGTHS[30]["words"] == 4500        # table not mutated


@pytest.mark.parametrize("empty", [None, 0])
def test_length_spec_default(empty):
    assert text.length_spec(empty)["minutes"] == 15


def test_length_spec_accepts_numeric_string():
    assert text.length_spec("30")["minutes"] == 30


@pytest.mark.parametrize("bad", [45, 60, 10, 1, -15])
def test_length_spec_rejects_unknown_with_available_list(bad):
    with pytest.raises(ValueError, match=r"no length spec for .* available: 15, 30"):
        text.length_spec(bad)


def test_length_spec_non_numeric_raises():
    with pytest.raises(ValueError):
        text.length_spec("abc")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def test_spell_known_and_fallback():
    assert text._spell(3) == "three" and text._spell(9) == "nine" and text._spell(10) == "ten"
    assert text._spell(12) == "12"


def test_structure_block_15_and_30():
    b15 = text._structure_block(text.length_spec(15))
    assert "Four to six chapters, about 450 words each, 2,250 words total" in b15
    assert "roughly 15 minutes read aloud" in b15
    assert "Three to six named characters. More than six" in b15
    b30 = text._structure_block(text.length_spec(30))
    assert "Seven to nine chapters, about 560 words each, 4,500 words total" in b30
    assert "Four to seven named characters" in b30


def test_tolerance_default_and_override():
    assert text._tolerance({}) == 0.20
    assert text._tolerance({"length_tolerance": "0.5"}) == 0.5


def test_ceiling_from_cfg_and_fallbacks():
    assert text._ceiling({"max_tokens": {"writer": 123}}, "writer") == 123
    assert text._ceiling({}, "ideator") == config.DEFAULTS["max_tokens"]["ideator"]
    assert text._ceiling({"max_tokens": {}}, "editor") == 16000
    assert text._ceiling({}, "no-such-agent") == 8000


def test_scenes_per_chapter_derived_or_overridden():
    spec = text.length_spec(30)
    assert text._scenes_per_chapter({"gemini": {"scenes_per_chapter": None}}, spec) == 4
    assert text._scenes_per_chapter({}, text.length_spec(15)) == 3
    assert text._scenes_per_chapter({"gemini": {"scenes_per_chapter": 7}}, spec) == 7
    assert text._scenes_per_chapter({"gemini": {"scenes_per_chapter": 0}}, spec) == 0


def test_story_year():
    assert text.story_year({"setting": "Amberlight parish, late autumn 1816"}) == 1816
    assert text.story_year({"setting": "no year here"}) is None
    assert text.story_year({}) is None
    assert text.story_year({"setting": None}) is None
    assert text.story_year({"setting": "in 2100 and 1816"}) == 1816   # regex wants 1xxx


# --------------------------------------------------------------------------- #
# _render
# --------------------------------------------------------------------------- #

import re  # noqa: E402

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")

IDEATOR_EXTRA = dict(
    subgenre="romance", subgenre_note="note", trope_menu="- **T** — n",
    notes_block="", history_block="", chapters_min="4", chapters_max="6",
    chapters_target="5", cast_min="3", cast_max="6", total_words="2250",
    chapter_words="450", minutes="15", beats_min="3", beats_max="4",
)
WRITER_EXTRA = dict(n="2", title="T", subgenre="romance", tropes="a, b",
                    target_words="450", length_floor="360", length_ceiling="540")
EDITOR_EXTRA = dict(n="2", target_words="450", tropes="a, b")


@pytest.mark.parametrize("name,extra", [("ideator", IDEATOR_EXTRA), ("writer", WRITER_EXTRA),
                                        ("editor", EDITOR_EXTRA)])
@pytest.mark.parametrize("genre_name", genres.available())
def test_render_leaves_no_placeholder_for_every_agent_and_genre(name, extra, genre_name):
    g = genres.load(genre_name)
    out = text._render(name, g, None, spec=text.length_spec(15), **extra)
    left = PLACEHOLDER.findall(out)
    assert left == [], f"{name}/{genre_name} left {left}"


def test_render_missing_extra_leaves_placeholder_visible():
    out = text._render("writer", REGENCY, None, spec=text.length_spec(15), n="1")
    assert "{target_words}" in out          # caller's responsibility; not silently blanked


def test_render_structure_follows_spec():
    o15 = text._render("editor", REGENCY, None, spec=text.length_spec(15), **EDITOR_EXTRA)
    o30 = text._render("editor", REGENCY, None, spec=text.length_spec(30), **EDITOR_EXTRA)
    assert "2,250 words total" in o15 and "4,500 words total" not in o15
    assert "4,500 words total" in o30


def test_render_defaults_to_15_minute_structure():
    out = text._render("editor", REGENCY, None, **EDITOR_EXTRA)
    assert "2,250 words total" in out


def test_render_includes_genre_guide_and_shared_layers():
    out = text._render("writer", REGENCY, None, **WRITER_EXTRA)
    assert REGENCY.guide[:80] in out
    assert config.prompt_text("boundaries").strip()[:60] in out
    assert config.prompt_text("ai-tells").strip()[:60] in out


def test_render_ideator_does_not_receive_ai_tells_placeholder_only_if_unused():
    # whatever the ideator prompt says about {ai_tells}, nothing is left unresolved
    out = text._render("ideator", REGENCY, None, **IDEATOR_EXTRA)
    assert "{ai_tells}" not in out


def test_render_extra_values_substituted_verbatim():
    out = text._render("writer", REGENCY, None, **{**WRITER_EXTRA, "title": "Mine {not_a_key}"})
    assert "Mine {not_a_key}" in out


def test_render_raises_if_structure_survives(monkeypatch):
    real = text.prompt_text

    def fake(name):
        return "{boundaries}" if name == "writer" else real(name)
    monkeypatch.setattr(text, "prompt_text", lambda n: ("X {house} {structure}" if n == "writer"
                                                        else ("no placeholder" if n == "house"
                                                              else real(n))))
    # {structure} directly in the writer prompt is substituted (it is a field)
    assert "{structure}" not in text._render("writer", REGENCY, None, **WRITER_EXTRA)
    # but one introduced by a later substitution (via genre guide) survives
    g = genres.Genre(name="x", meta={}, guide="guide {structure}")
    monkeypatch.setattr(text, "prompt_text", lambda n: "{genre_guide}" if n == "writer" else real(n))
    with pytest.raises(RuntimeError, match="survived substitution"):
        text._render("writer", g, None, **WRITER_EXTRA)


def test_render_bible_story_ideas_reach_only_the_ideator(repo_root):
    (repo_root / "bibles" / "seriesx.md").write_text(
        "---\nseries: Series X\ngenre: regency\nanchor_year: 1817\n---\n"
        "# Guide\n\nThe parish of X.\n\n## Story ideas\n\nUNIQUE-SEED-123 a rivalry.\n\n"
        "## Open questions\n\nWORKING-ONLY-456\n")
    bible = bibles.load("seriesx", "bibles")
    ideator = text._render("ideator", REGENCY, bible, **IDEATOR_EXTRA)
    writer = text._render("writer", REGENCY, bible, **WRITER_EXTRA)
    editor = text._render("editor", REGENCY, bible, **EDITOR_EXTRA)
    assert "UNIQUE-SEED-123" in ideator
    assert "UNIQUE-SEED-123" not in writer and "UNIQUE-SEED-123" not in editor
    for out in (ideator, writer, editor):
        assert "WORKING-ONLY-456" not in out
        assert "The parish of X." in out and "Series bible: Series X" in out


def test_render_genre_label_falls_back_to_name():
    g = genres.Genre(name="x", meta={}, guide="G")
    out = text._render("writer", g, None, **WRITER_EXTRA)
    assert PLACEHOLDER.findall(out) == []


# --------------------------------------------------------------------------- #
# _validate_outline
# --------------------------------------------------------------------------- #


def valid(minutes=15):
    return mini_outline(minutes)


def check(o, minutes=15):
    text._validate_outline(o, REGENCY, text.length_spec(minutes))


def test_valid_15_and_30_outlines_pass():
    check(valid(15), 15)
    check(valid(30), 30)


def test_real_story_outline_passes_validation(real_story):
    import json
    o = json.loads((real_story / "outline.json").read_text())
    text._validate_outline(o, REGENCY, text.length_spec(30))


def test_spec_defaults_from_outline_length_min():
    o = valid(30)
    o["length_min"] = 30
    text._validate_outline(o, REGENCY)               # spec derived


def test_spec_defaults_to_15_without_length_min():
    text._validate_outline(valid(15), REGENCY)
    with pytest.raises(ValueError, match="chapters"):
        text._validate_outline(valid(30), REGENCY)    # 8 chapters is too many for 15


@pytest.mark.parametrize("key", ["title", "genre", "cast", "chapters", "setting", "tropes"])
def test_missing_required_key(key):
    o = valid()
    del o[key]
    with pytest.raises(ValueError, match=f"outline missing '{key}'"):
        check(o)


def with_chapters(n, words=None, beats=3):
    o = valid()
    total = words or 2250
    o["chapters"] = [{"title": f"c{i}", "beats": [f"b{j}" for j in range(beats)],
                      "target_words": total // n} for i in range(n)]
    return o


@pytest.mark.parametrize("n", [4, 5, 6])
def test_chapter_count_in_bounds_15(n):
    check(with_chapters(n))


@pytest.mark.parametrize("n", [3, 7, 0])
def test_chapter_count_out_of_bounds_15(n):
    with pytest.raises(ValueError, match=rf"{n} chapters; a 15-minute story wants 4-6"):
        check(with_chapters(n))


@pytest.mark.parametrize("n,ok", [(6, False), (7, True), (9, True), (10, False)])
def test_chapter_count_bounds_30(n, ok):
    o = valid(30)
    o["chapters"] = [{"title": "c", "beats": ["a", "b", "c", "d"], "target_words": 4500 // n}
                     for _ in range(n)]
    if ok:
        check(o, 30)
    else:
        with pytest.raises(ValueError, match="30-minute story wants 7-9"):
            check(o, 30)


def test_chapters_are_renumbered_and_target_defaulted():
    o = valid()
    for c in o["chapters"]:
        c["n"] = 99
        del c["target_words"]
    check(o)
    assert [c["n"] for c in o["chapters"]] == [1, 2, 3, 4, 5]
    assert {c["target_words"] for c in o["chapters"]} == {450}


def test_duplicate_cast_names():
    o = valid()
    o["cast"][3]["name"] = o["cast"][0]["name"]
    with pytest.raises(ValueError, match="duplicate cast names"):
        check(o)


@pytest.mark.parametrize("size,ok", [(2, False), (3, True), (6, True), (7, False)])
def test_cast_size_bounds_15(size, ok):
    names = ["Alma Reed", "Bram Cole", "Cora Dunn", "Dale Frost", "Edna Gale", "Finn Hart",
             "Gwen Ives"]
    o = valid()
    o["cast"] = [{"name": n, "role": "supporting", "age": 40} for n in names[:size]]
    if ok:
        check(o)
    else:
        with pytest.raises(ValueError, match=rf"{size} named characters.*3-6"):
            check(o)


def test_cast_size_bounds_30():
    o = valid(30)
    o["cast"] = o["cast"][:3]
    with pytest.raises(ValueError, match="4-7"):
        check(o, 30)


def test_ear_collision_rejected():
    o = valid()
    o["cast"][3]["name"] = "Mr Owen Vane"
    o["cast"][2]["name"] = "Mrs Owens Pike"           # "owe" vs "owe"
    with pytest.raises(ValueError, match="too similar to tell apart by ear"):
        check(o)


def test_ear_check_ignores_honorifics():
    o = valid()
    o["cast"] = [{"name": "Mr Aaron Bell", "role": "supporting", "age": 40},
                 {"name": "Mr Colin Dray", "role": "supporting", "age": 40},
                 {"name": "Mr Edwin Fox", "role": "supporting", "age": 40}]
    check(o)


@pytest.mark.parametrize("tropes", [[], ["Enemies of manner"], ["a", "b", "c", "d"]])
def test_trope_count_must_be_two_or_three(tropes):
    o = valid()
    o["tropes"] = tropes
    with pytest.raises(ValueError, match=rf"declares {len(tropes)} tropes; want 2 or 3"):
        check(o)


def test_three_tropes_accepted_and_case_insensitive():
    o = valid()
    o["tropes"] = ["ENEMIES OF MANNER", "the meddling matchmaker", "Second chance"]
    check(o)


def test_trope_not_in_genre_rejected():
    o = valid()
    o["tropes"] = ["Enemies of manner", "Chosen one"]
    with pytest.raises(ValueError, match=r"tropes not in the regency list: \['Chosen one'\]"):
        check(o)


def test_tropes_from_other_genre_rejected():
    other = next(g for g in genres.available() if g != "regency")
    t = genres.load(other).tropes[0]["name"]
    o = valid()
    o["tropes"] = ["Enemies of manner", t]
    with pytest.raises(ValueError, match="tropes not in"):
        check(o)


@pytest.mark.parametrize("role", sorted(text.ROMANTIC_ROLES))
@pytest.mark.parametrize("age", [17, 0, None, "30", 17.5])
def test_romantic_role_requires_integer_age_18_plus(role, age):
    o = valid()
    o["cast"][3]["role"] = role
    if age is None:
        del o["cast"][3]["age"]
    else:
        o["cast"][3]["age"] = age
    with pytest.raises(ValueError, match="romantic role.*18 or over"):
        check(o)


@pytest.mark.parametrize("role", sorted(text.ROMANTIC_ROLES))
def test_romantic_role_age_18_accepted(role):
    o = valid()
    o["cast"][3]["role"] = role
    o["cast"][3]["age"] = 18
    check(o)


def test_non_romantic_minor_not_checked_by_this_rule():
    o = valid()
    o["cast"][3].update(role="supporting", age=9)
    check(o)


def test_romantic_roles_constant():
    assert text.ROMANTIC_ROLES == {"protagonist", "love_interest"}


@pytest.mark.parametrize("beats", [None, [], "missing"])
def test_chapter_without_beats_rejected(beats):
    o = valid()
    if beats == "missing":
        del o["chapters"][2]["beats"]
    else:
        o["chapters"][2]["beats"] = beats
    with pytest.raises(ValueError, match="chapter 3 has no beats"):
        check(o)


def test_single_beat_chapter_rejected():
    o = valid()
    o["chapters"][0]["beats"] = ["only one"]
    with pytest.raises(ValueError, match=r"chapter 1 has 1 beat; .*at least 2.*3-4"):
        check(o)


def test_two_beats_accepted_even_below_prompt_minimum():
    o = valid()
    o["chapters"][0]["beats"] = ["a", "b"]
    check(o)


@pytest.mark.parametrize("total,ok", [(2587, True), (2588, False), (1913, True), (1912, False)])
def test_word_total_within_15_percent(total, ok):
    o = valid()
    base = total // 5
    for c in o["chapters"]:
        c["target_words"] = base
    o["chapters"][0]["target_words"] += total - base * 5
    if ok:
        check(o)
    else:
        with pytest.raises(ValueError, match="chapter targets sum to"):
            check(o)


def test_word_total_error_mentions_minutes_and_expected():
    o = valid()
    for c in o["chapters"]:
        c["target_words"] = 900
    with pytest.raises(ValueError, match=r"4,500 words \(~30 min\).*2,250"):
        check(o)


def test_validate_does_not_require_optional_keys():
    o = valid()
    for k in ("logline", "promise", "subgenre"):
        o.pop(k)
    check(o)
