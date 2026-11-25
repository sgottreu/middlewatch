"""story_pipeline.genres"""

from __future__ import annotations

import random

import pytest

from story_pipeline import genres

NAMES = genres.available()


def test_expected_genres_present():
    assert {"fantasy", "regency", "scifi"} <= set(NAMES)
    assert NAMES == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_every_genre_file_loads_with_required_parts(name):
    g = genres.load(name)
    assert g.name == name
    assert g.guide.strip()
    assert g.meta.get("label")
    assert g.subgenres, "needs at least one subgenre (build_outline defaults to the first)"
    assert g.art_style and g.art_style == g.art_style.strip()
    assert len(g.tropes) >= 8, "trope_sample defaults to 8"
    assert g.voices().get("narrator")
    assert set(g.voices()["character_voices"]) >= {"female", "male"}


@pytest.mark.parametrize("name", NAMES)
def test_every_trope_has_name_note_and_names_are_unique(name):
    tropes = genres.load(name).tropes
    assert all(t.get("name") and t.get("note") for t in tropes)
    lowered = [t["name"].lower() for t in tropes]
    assert len(lowered) == len(set(lowered))


@pytest.mark.parametrize("name", NAMES)
def test_subgenre_notes_non_empty(name):
    g = genres.load(name)
    assert all(g.subgenre_note(s).strip() for s in g.subgenres)


def test_unknown_genre_error_lists_available():
    with pytest.raises(ValueError) as ei:
        genres.load("western")
    assert "unknown genre 'western'" in str(ei.value)
    assert "regency" in str(ei.value)


def test_all_subgenres_maps_every_genre():
    m = genres.all_subgenres()
    assert set(m) == set(NAMES)
    assert m["regency"][0] == "romance"


def test_subgenre_note_unknown_is_empty():
    assert genres.load("regency").subgenre_note("nope") == ""


def test_validate_subgenre():
    g = genres.load("regency")
    assert g.validate_subgenre("gothic") == "gothic"
    assert g.validate_subgenre(None) is None
    assert g.validate_subgenre("") == ""
    with pytest.raises(ValueError, match="unknown subgenre 'zombie' for regency"):
        g.validate_subgenre("zombie")


def test_trope_menu_full_list_format():
    g = genres.load("regency")
    menu = g.trope_menu()
    lines = menu.splitlines()
    assert len(lines) == len(g.tropes)
    assert lines[0] == f"- **{g.tropes[0]['name']}** — {g.tropes[0]['note']}"


def test_trope_menu_sample_size_and_seed_determinism():
    g = genres.load("regency")
    a = g.trope_menu(sample=4, seed=1).splitlines()
    b = g.trope_menu(sample=4, seed=1).splitlines()
    assert a == b and len(a) == 4
    assert set(a) <= set(g.trope_menu().splitlines())
    assert any(g.trope_menu(sample=4, seed=s).splitlines() != a for s in range(2, 12))


def test_trope_menu_sample_ge_len_returns_all():
    g = genres.load("regency")
    assert len(g.trope_menu(sample=len(g.tropes)).splitlines()) == len(g.tropes)
    assert len(g.trope_menu(sample=999).splitlines()) == len(g.tropes)


def test_trope_menu_weights_bias_selection_but_never_exclude():
    g = genres.load("regency")
    heavy = g.tropes[0]["name"]
    weights = {t["name"].lower(): 1e-6 for t in g.tropes}
    weights[heavy.lower()] = 1000.0
    hits = sum(f"**{heavy}**" in g.trope_menu(sample=2, seed=s, weights=weights)
               for s in range(30))
    assert hits >= 28
    # a near-zero weight can still appear when the sample is large enough
    assert len(g.trope_menu(sample=len(g.tropes) - 1, seed=0, weights=weights).splitlines()) \
        == len(g.tropes) - 1


def test_weighted_sample_returns_k_distinct_items():
    items = [{"name": f"T{i}"} for i in range(10)]
    got = genres._weighted_sample(items, 5, {"t0": 5.0}, random.Random(3))
    assert len(got) == 5 and len({i["name"] for i in got}) == 5


def test_parse_requires_frontmatter(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("no frontmatter here")
    with pytest.raises(ValueError, match="no frontmatter"):
        genres._parse(p)


def test_parse_empty_frontmatter_ok(tmp_path):
    p = tmp_path / "mini.md"
    p.write_text("---\n---\nbody text\n")
    g = genres._parse(p)
    assert g.name == "mini" and g.meta == {} and g.guide == "body text"
    assert g.subgenres == [] and g.tropes == [] and g.voices() == {}


def test_art_style_missing_raises_keyerror(tmp_path):
    p = tmp_path / "mini.md"
    p.write_text("---\nlabel: X\n---\nbody")
    with pytest.raises(KeyError):
        genres._parse(p).art_style
