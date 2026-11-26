"""A bible narrowing its genre: `subgenres`, `exclude_tropes`, `series_tropes`.

Genre.for_series builds the view; build_outline uses it for the menu, the
default subgenre and the outline check; bibles.validate reports mistakes;
bible promote carries the draft's lines and Series Tropes section across.
"""

from __future__ import annotations

import copy
import json

import pytest

from story_pipeline import bible_promote as bp
from story_pipeline import bibles, genres
from story_pipeline.agents import text

from .helpers import cfg, mini_outline


def raw(**over):
    o = mini_outline()
    o.pop("genre")
    o.update(over)
    return o


def write_bible(root, meta_lines: str, name="parish"):
    p = root / "bibles" / name / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nseries: Parish\ngenre: regency\ncanon:\n  - A fact.\n"
                 + meta_lines + "---\n\n# Parish\n\nA parish.\n")
    return name


# --------------------------------------------------------------------------- #
# Genre.for_series
# --------------------------------------------------------------------------- #

def test_for_series_without_settings_is_the_same_genre():
    g = genres.load("fantasy")
    assert g.for_series(None) is g and g.for_series({"canon": ["x"]}) is g


def test_for_series_narrows_subgenres_in_bible_order():
    g = genres.load("fantasy").for_series({"subgenres": ["slice_of_life", "mystery"]})
    assert g.subgenres == ["slice_of_life", "mystery"]
    assert g.subgenre_note("mystery") == genres.load("fantasy").subgenre_note("mystery")
    with pytest.raises(ValueError, match="unknown subgenre 'romance'"):
        g.validate_subgenre("romance")


def test_for_series_excludes_and_adds_tropes():
    base = genres.load("fantasy")
    g = base.for_series({
        "exclude_tropes": ["the HEDGE-WITCH", "Magical debt"],
        "series_tropes": [{"name": "The map is wrong", "note": "A trusted chart is out of date."}],
    })
    names = [t["name"] for t in g.tropes]
    assert "The hedge-witch" not in names and "Magical debt" not in names
    assert names[-1] == "The map is wrong"
    assert len(names) == len(base.tropes) - 1
    assert "The map is wrong" in g.trope_menu()
    assert len(base.tropes) == 15                     # the loaded genre is untouched


def test_for_series_ignores_unknown_subgenres_rather_than_emptying():
    g = genres.load("fantasy").for_series({"subgenres": ["nonsense"]})
    assert g.subgenres == genres.load("fantasy").subgenres


# --------------------------------------------------------------------------- #
# build_outline
# --------------------------------------------------------------------------- #

def test_bible_subgenres_replace_the_genre_default(scripted, repo_root):
    name = write_bible(repo_root, "subgenres: [gothic, slice_of_life]\n")
    o = raw()
    del o["subgenre"]
    scripted.json_queue.append(o)
    out = text.build_outline(cfg(), bible_name=name)
    assert out["subgenre"] == "gothic"                # not regency's first, romance
    system = scripted.json_calls[0]["system"]
    assert genres.load("regency").subgenre_note("gothic") in system


def test_default_subgenre_rotates_to_least_used(repo_root):
    name = write_bible(repo_root, "subgenres: [gothic, slice_of_life]\n")
    bible = bibles.load(name, cfg()["bibles_dir"])
    g = genres.load("regency").for_series(bible.meta)
    past = type("History", (), {"subgenres": {}})()
    assert text._default_subgenre(g, bible, past) == "gothic"          # tie: bible order
    past.subgenres = {"gothic": 2, "slice_of_life": 1}
    assert text._default_subgenre(g, bible, past) == "slice_of_life"
    assert text._default_subgenre(genres.load("regency"), None, past) == "romance"


def test_explicit_subgenre_outside_the_series_fails_before_paying(scripted, repo_root):
    name = write_bible(repo_root, "subgenres: [gothic]\n")
    with pytest.raises(ValueError, match="unknown subgenre 'romance'"):
        text.build_outline(cfg(), subgenre="romance", bible_name=name)
    assert scripted.json_calls == []


def test_excluded_trope_is_off_the_menu_and_rejected(scripted, repo_root):
    name = write_bible(repo_root, "exclude_tropes: [Enemies of manner]\n")
    c = cfg()
    c["trope_sample"] = 0                             # whole menu
    scripted.json_queue.append(raw())                 # declares Enemies of manner
    with pytest.raises(ValueError, match="tropes not in the regency list"):
        text.build_outline(c, bible_name=name)
    assert "**Enemies of manner**" not in scripted.json_calls[0]["system"]


def test_series_trope_is_offered_and_accepted(scripted, repo_root):
    name = write_bible(repo_root, "series_tropes:\n  - name: The leaking roof\n"
                                  "    note: Damp in the vestry.\n")
    c = cfg()
    c["trope_sample"] = 0
    scripted.json_queue.append(raw(tropes=["The leaking roof", "The meddling matchmaker"]))
    out = text.build_outline(c, bible_name=name)
    assert out["tropes"][0] == "The leaking roof"
    assert "**The leaking roof** — Damp in the vestry." in scripted.json_calls[0]["system"]


def test_revision_drops_a_retired_subgenre_and_asks_to_replace_retired_tropes(scripted, repo_root):
    name = write_bible(repo_root, "subgenres: [gothic]\nexclude_tropes: [Enemies of manner]\n")
    prev = mini_outline() | {"bible": name, "subgenre": "romance"}
    scripted.json_queue.append(raw(subgenre="romance",
                                   tropes=["The meddling matchmaker", "Second chance"]))
    out = text.build_outline(cfg(), "regency", "romance", bible_name=name,
                             feedback="Tighter.", previous=copy.deepcopy(prev))
    ask = scripted.json_calls[0]["user"]
    assert "no longer available for this series" in ask and "Enemies of manner" in ask
    assert "The subgenre is now gothic, not romance" in ask
    assert out["subgenre"] == "gothic"                # model's stale value overridden


# --------------------------------------------------------------------------- #
# bibles.validate
# --------------------------------------------------------------------------- #

def _problems(root, lines):
    name = write_bible(root, lines)
    return {(p.fatal, p.where, p.message)
            for p in bibles.validate(bibles.path_for(name, "bibles"))}


def test_validate_series_genre_fields(repo_root):
    found = _problems(repo_root, "subgenres: [gothic, space_opera]\n"
                                 "exclude_tropes: [Enemies of manner, Not a trope]\n"
                                 "series_tropes:\n  - name: A\n    note: x\n"
                                 "  - name: a\n  - note: nameless\n")
    msgs = {m for _, _, m in found}
    assert (True, "subgenres", "'space_opera' is not a regency subgenre") in found
    assert any("'Not a trope' is not a regency trope" in m for m in msgs)
    assert not any("Enemies of manner" in m for m in msgs)
    assert (True, "series_tropes[1]", "'a' is listed twice") in found
    assert (True, "series_tropes[2]", "needs a `name`") in found
    assert (False, "series_tropes[1]", "'a' has no note") in found


def test_validate_rejects_non_lists(repo_root):
    found = _problems(repo_root, "subgenres: gothic\n")
    assert (True, "subgenres", "must be a list") in found


# --------------------------------------------------------------------------- #
# bible promote
# --------------------------------------------------------------------------- #

DRAFT = (
    "# Salt Roads (Working Draft)\n\n"
    "Genre: fantasy\nSeries: Salt Roads\n"
    "Subgenres: mystery, slice_of_life\n"
    "Exclude tropes: The hedge-witch, Magical debt\n\n"
    "## Premise\n\nSalt towns.\n\n"
    "## Story Engine\n\nSomeone goes after a question about the road.\n\n"
    "## Not This Series\n\n- No spellcasters.\n\n"
    "## Canon\n\n- The road floods.\n\n"
    "## Series Tropes\n\n- **The map is wrong:** a trusted chart is out of date.\n"
    "- **Contract vs honest record:** the paying client wants the record shaded.\n\n"
    "## Recurring Cast\n\n### Ada Penrose\n\n- Gender: female\n\nWarden.\n"
)


def test_promote_carries_series_genre_fields(repo_root):
    p = repo_root / "bibles/salt/salt.draft.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(DRAFT)
    pv = bp.build("salt")
    (repo_root / "bibles/salt/salt.md").write_text(pv.text)
    b = bibles.load("salt")
    assert b.meta["subgenres"] == ["mystery", "slice_of_life"]
    assert b.meta["exclude_tropes"] == ["The hedge-witch", "Magical debt"]
    assert b.meta["series_tropes"] == [
        {"name": "The map is wrong", "note": "a trusted chart is out of date."},
        {"name": "Contract vs honest record", "note": "the paying client wants the record shaded."}]
    assert "## Series Tropes" not in b.guide          # menu only, not prose
    assert "## Story Engine" in b.guide and "## Not This Series" in b.guide
    assert "## Story Engine" in b.prompt_block()      # reaches every agent
    assert not bibles.fatal(bibles.validate(b.path))
    assert {"title": "Series Tropes", "as": "series_tropes", "count": 2} in pv.kept
