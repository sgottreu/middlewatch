"""bibles.py: layout, parsing, the Bible model, sections and prompt_block."""

from __future__ import annotations

import pytest

from story_pipeline import bibles
from story_pipeline.bibles import Bible

from .conftest import base_meta, write_bible


# --------------------------------------------------------------------------- #
# path_for / load / available
# --------------------------------------------------------------------------- #

def test_path_for_nested_when_nothing_exists(repo_root):
    assert bibles.path_for("x") == bibles.Path("bibles") / "x" / "x.md"


def test_path_for_flat_fallback_and_nested_wins(repo_root, mk):
    mk("flat", nested=False)
    assert bibles.path_for("flat") == bibles.Path("bibles/flat.md")
    mk("both", nested=False)
    mk("both", nested=True)
    assert bibles.path_for("both") == bibles.Path("bibles/both/both.md")


def test_path_for_custom_dir(tmp_path):
    p = bibles.path_for("x", tmp_path / "elsewhere")
    assert p == tmp_path / "elsewhere" / "x" / "x.md"


def test_load_nested(mk):
    mk("demo")
    b = bibles.load("demo")
    assert b.name == "demo" and b.series == "Test Parish" and b.genre == "regency"
    assert b.guide.startswith("# Test")
    assert b.path == bibles.Path("bibles/demo/demo.md")
    assert b.cast_dir == bibles.Path("bibles/demo/demo.cast")


def test_load_flat_fallback_keeps_cast_beside(mk):
    mk("old", nested=False)
    b = bibles.load("old")
    assert b.path == bibles.Path("bibles/old.md")
    assert b.cast_dir == bibles.Path("bibles/old.cast")


def test_load_missing_lists_available(mk):
    mk("one")
    mk("two", nested=False)
    with pytest.raises(ValueError) as e:
        bibles.load("nope")
    msg = str(e.value)
    assert "no bible 'nope'" in msg and "available: one, two" in msg


def test_load_missing_none_found(repo_root):
    with pytest.raises(ValueError, match="none found"):
        bibles.load("nope")


def test_load_explicit_dir(tmp_path):
    d = tmp_path / "shelf"
    (d / "z").mkdir(parents=True)
    (d / "z" / "z.md").write_text("---\nseries: Z\n---\nbody")
    assert bibles.load("z", d).series == "Z"


def test_available_rules(repo_root, mk):
    assert bibles.available() == []
    mk("nested-b")
    mk("flat-a", nested=False)
    mk("both", nested=True)
    mk("both", nested=False)
    # excluded: stray dir, draft-only dir, changelog and draft files in flat layout
    (repo_root / "bibles" / "stray").mkdir()
    (repo_root / "bibles" / "stray" / "other.md").write_text("x")
    (repo_root / "bibles" / "draftonly").mkdir()
    (repo_root / "bibles" / "draftonly" / "draftonly.draft.md").write_text("x")
    (repo_root / "bibles" / "flat-a.changelog.md").write_text("x")
    (repo_root / "bibles" / "flat-a.draft.md").write_text("x")
    (repo_root / "bibles" / "readme.txt").write_text("x")
    assert bibles.available() == ["both", "flat-a", "nested-b"]


def test_available_missing_dir(tmp_path):
    assert bibles.available(tmp_path / "nowhere") == []


def test_available_excludes_draft_md_even_beside_bible(mk):
    path = mk("demo")
    (path.parent / "demo.draft.md").write_text("# draft\n")
    assert bibles.available() == ["demo"]


# --------------------------------------------------------------------------- #
# _parse
# --------------------------------------------------------------------------- #

def test_parse_splits_front_and_body(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("---\nseries: S\ngenre: fantasy\n---\n\n\n# S\n\ntext\n\n")
    b = bibles._parse(p)
    assert b.name == "s" and b.meta == {"series": "S", "genre": "fantasy"}
    assert b.guide == "# S\n\ntext"


def test_parse_horizontal_rule_in_body_survives(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("---\nseries: S\n---\n\nabove\n\n---\n\nbelow\n")
    assert "---" in bibles._parse(p).guide
    assert bibles._parse(p).guide.endswith("below")


def test_parse_empty_frontmatter_is_empty_meta(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("---\n---\nbody")
    assert bibles._parse(p).meta == {}


def test_parse_no_frontmatter_error(tmp_path):
    p = tmp_path / "s.draft.md"
    p.write_text("# Just a draft\n")
    with pytest.raises(ValueError) as e:
        bibles._parse(p)
    assert str(e.value) == f"{p} has no frontmatter block"


def test_parse_leading_blank_line_counts_as_no_frontmatter(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("\n---\nseries: S\n---\nbody")
    with pytest.raises(ValueError, match="has no frontmatter block"):
        bibles._parse(p)


def test_parse_unclosed_frontmatter_raises_value_error(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("---\nseries: S\nbody never closes\n")
    with pytest.raises(ValueError):
        bibles._parse(p)


def test_parse_bad_yaml_propagates_yaml_error(tmp_path):
    import yaml
    p = tmp_path / "s.md"
    p.write_text("---\nseries: [unclosed\n---\nbody")
    with pytest.raises(yaml.YAMLError):
        bibles._parse(p)


def test_parse_triple_dash_inside_a_value(tmp_path):
    p = tmp_path / "s.md"
    p.write_text('---\nseries: S\ncanon:\n  - "wait --- what"\n---\n\nbody\n')
    assert bibles._parse(p).meta["canon"] == ["wait --- what"]


# --------------------------------------------------------------------------- #
# Bible fields
# --------------------------------------------------------------------------- #

def _b(meta=None, guide="g", name="demo", path="bibles/demo/demo.md"):
    from pathlib import Path
    return Bible(name=name, meta=meta or {}, guide=guide, path=Path(path))


def test_series_defaults_to_name():
    assert _b().series == "demo"
    assert _b({"series": "Proper"}).series == "Proper"


def test_genre_required():
    assert _b({"genre": "fantasy"}).genre == "fantasy"
    with pytest.raises(ValueError, match="does not declare a genre"):
        _b().genre


def test_list_properties_default_empty():
    b = _b()
    assert b.recurring_cast == [] and b.canon == [] and b.minor_names == []
    assert _b({"minor_names": None}).minor_names == []
    assert _b({"minor_names": ["A"], "canon": ["c"]}).minor_names == ["A"]
    assert _b({"canon": ["c"]}).canon == ["c"]


def test_anchor_year_only_ints():
    assert _b({"anchor_year": 1817}).anchor_year == 1817
    assert _b({"anchor_year": "1817"}).anchor_year is None
    assert _b().anchor_year is None


def test_aged():
    b = _b({"anchor_year": 1817})
    assert b.aged({"age": 30}, 1820) == 33
    assert b.aged({"age": 30}, 1815) == 28
    assert b.aged({"age": 30}, 1817) == 30
    assert b.aged({"age": 30}, None) == 30
    assert b.aged({}, 1820) is None
    assert b.aged({"age": "forty"}, 1820) == "forty"
    assert _b().aged({"age": 30}, 1820) == 30      # no anchor year: unchanged


def test_member_lookup_case_insensitive_and_alias():
    b = _b({"recurring_cast": [
        {"name": "Miss Judith Vane", "aliases": ["Mrs Judith Rowe"]},
        {"name": "Mr Silas Rowe"}]})
    assert b.member("miss judith vane")["name"] == "Miss Judith Vane"
    assert b.member("MRS JUDITH ROWE")["name"] == "Miss Judith Vane"
    assert b.member("Judith Vane") is None          # no fuzzy joins
    assert b.member("nobody") is None


def test_member_name_beats_alias():
    b = _b({"recurring_cast": [
        {"name": "A", "aliases": ["B"]}, {"name": "B"}]})
    assert b.member("B")["name"] == "B"


def test_member_alias_none_tolerated():
    b = _b({"recurring_cast": [{"name": "A", "aliases": None}]})
    assert b.member("x") is None


# --------------------------------------------------------------------------- #
# portraits
# --------------------------------------------------------------------------- #

def _with_cast(mk, cast):
    path = mk("demo", base_meta(recurring_cast=cast))
    return bibles.load("demo"), path


def test_portrait_unknown_member(mk):
    b, _ = _with_cast(mk, [{"name": "Mrs Honoria Pike"}])
    assert b.portrait("Nobody") is None


def test_portrait_by_slug_jpg_then_png_and_alias(mk):
    b, path = _with_cast(mk, [{"name": "Mrs Honoria Pike", "aliases": ["Honoria"]}])
    assert b.portrait("Mrs Honoria Pike") is None
    b.cast_dir.mkdir()
    (b.cast_dir / "mrs-honoria-pike.png").write_bytes(b"png")
    assert b.portrait("Mrs Honoria Pike").name == "mrs-honoria-pike.png"
    (b.cast_dir / "mrs-honoria-pike.jpg").write_bytes(b"jpg")
    assert b.portrait("Mrs Honoria Pike").name == "mrs-honoria-pike.jpg"
    assert b.portrait("honoria").name == "mrs-honoria-pike.jpg"   # alias, same face


def test_portrait_explicit_filename(mk):
    b, _ = _with_cast(mk, [{"name": "Mrs Honoria Pike", "portrait": "face.png"}])
    assert b.portrait("Mrs Honoria Pike") is None       # named but absent: no slug fallback
    b.cast_dir.mkdir()
    (b.cast_dir / "face.png").write_bytes(b"x")
    (b.cast_dir / "mrs-honoria-pike.jpg").write_bytes(b"y")
    assert b.portrait("Mrs Honoria Pike").name == "face.png"


def test_save_portrait_unknown_name_writes_nothing(mk, tmp_path):
    b, _ = _with_cast(mk, [{"name": "Mrs Honoria Pike"}])
    img = tmp_path / "g.png"
    img.write_bytes(b"i")
    assert b.save_portrait("Nobody", img) is None
    assert not b.cast_dir.exists()


def test_save_portrait_writes_once(mk, tmp_path):
    b, _ = _with_cast(mk, [{"name": "Mrs Honoria Pike"}])
    img = tmp_path / "gen.png"
    img.write_bytes(b"first")
    target = b.save_portrait("Mrs Honoria Pike", img)
    assert target == b.cast_dir / "mrs-honoria-pike.png"
    assert target.read_bytes() == b"first"
    img.write_bytes(b"second")
    assert b.save_portrait("Mrs Honoria Pike", img) is None     # no overwrite, no log-worthy write
    assert target.read_bytes() == b"first"


def test_save_portrait_explicit_name_and_alias(mk, tmp_path):
    b, _ = _with_cast(mk, [{"name": "Mrs Honoria Pike", "aliases": ["Honoria"],
                            "portrait": "pike.jpg"}])
    img = tmp_path / "gen.jpg"
    img.write_bytes(b"x")
    assert b.save_portrait("Honoria", img) == b.cast_dir / "pike.jpg"


def test_save_portrait_suffixless_defaults_to_jpg(mk, tmp_path):
    b, _ = _with_cast(mk, [{"name": "Mrs Honoria Pike"}])
    img = tmp_path / "gen"
    img.write_bytes(b"x")
    assert b.save_portrait("Mrs Honoria Pike", img).name == "mrs-honoria-pike.jpg"


def test_save_portrait_other_suffix_is_found_again(mk, tmp_path):
    b, _ = _with_cast(mk, [{"name": "Mrs Honoria Pike"}])
    img = tmp_path / "gen.jpeg"
    img.write_bytes(b"x")
    assert b.save_portrait("Mrs Honoria Pike", img) is not None
    assert b.save_portrait("Mrs Honoria Pike", img) is None


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("title,key", [
    ("Regions (draft, to refine)", "regions"),
    ("The Regions", "regions"),
    ("Open Questions", "open questions"),
    ("What must stay consistent!", "what must stay consistent"),
    ("  Story   Ideas ", "story ideas"),
    ("Minor Names Register", "minor names register"),
])
def test_heading_key(title, key):
    assert bibles.heading_key(title) == key


TEXT = """preamble

## One

a
### Sub

b

```
## not a heading
```

## Two ##

c
"""


def test_split_sections():
    secs = bibles.split_sections(TEXT)
    assert [t for t, _ in secs] == [None, "One", "Two"]
    assert secs[0][1].strip() == "preamble"
    one = secs[1][1]
    assert "### Sub" in one and "## not a heading" in one
    assert "\n".join(c for _, c in secs) == TEXT        # nothing lost


def test_strip_sections():
    out = bibles.strip_sections(TEXT, ("one",))
    assert "## One" not in out and "## Two" in out and "preamble" in out
    assert "\n\n\n" not in out
    assert bibles.strip_sections(TEXT, ()) == TEXT.strip()


def test_section_body():
    assert bibles.section_body(TEXT, "two") == "c"
    assert "### Sub" in bibles.section_body(TEXT, "one")
    assert bibles.section_body(TEXT, "three") is None
    assert bibles.section_body("## Empty", "empty") == ""
    assert bibles.section_body("## A\n\n## B\n\nx", "a") == ""


def test_section_constants():
    assert bibles.WORKING_ONLY == ("open questions", "minor names register", "changelog")
    assert bibles.IDEATOR_ONLY == ("story ideas",)


# --------------------------------------------------------------------------- #
# prompt_block
# --------------------------------------------------------------------------- #

GUIDE = """# Parish

## Premise

A parish.

## Open Questions

- SECRET-OQ

## Minor Names Register

- SECRET-MN

## Changelog

- SECRET-CL

## Story Ideas

- SEED-ONE

## What recurs

The green.
"""


def test_prompt_block_header_and_guide():
    b = _b({"series": "Test Parish"}, guide="# Parish\n\n## Premise\n\nA parish.")
    out = b.prompt_block()
    assert out.startswith("# Series bible: Test Parish\n\n# Parish")
    assert "## Established facts" not in out
    assert "## Recurring cast" not in out
    assert "## Names already used" not in out


def test_prompt_block_filters_working_only_for_everyone():
    b = _b(guide=GUIDE)
    for ideator in (False, True):
        out = b.prompt_block(ideator=ideator)
        for secret in ("SECRET-OQ", "SECRET-MN", "SECRET-CL"):
            assert secret not in out, (ideator, secret)
        assert "## Premise" in out and "## What recurs" in out


def test_prompt_block_story_ideas_ideator_only():
    b = _b(guide=GUIDE)
    assert "SEED-ONE" not in b.prompt_block()
    assert "SEED-ONE" not in b.prompt_block(ideator=False)
    out = b.prompt_block(ideator=True)
    assert "SEED-ONE" in out and out.count("## Story ideas") == 1
    assert "## Story Ideas" not in out         # the original heading is replaced by the framed one


def test_prompt_block_ideator_without_seeds_has_no_section():
    b = _b(guide="## Premise\n\nx\n\n## Story Ideas\n\n")
    assert "Story ideas" not in b.prompt_block(ideator=True)


def test_story_ideas_property():
    assert _b(guide=GUIDE).story_ideas == "- SEED-ONE"
    assert _b(guide="## Premise\n\nx").story_ideas == ""


def test_prompt_block_hand_added_working_section_still_stripped_with_decoration():
    b = _b(guide="## Premise\n\nx\n\n## Open Questions (for me)\n\nSECRET\n\n## The Changelog\n\nSECRET2")
    out = b.prompt_block(ideator=True)
    assert "SECRET" not in out


def test_prompt_block_subheading_with_working_name_is_not_stripped():
    # Only `##` sections are filtered; a `###` stays inside its parent.
    b = _b(guide="## Setting\n\n### Open Questions\n\nvisible")
    assert "visible" in b.prompt_block()


def test_prompt_block_ignores_headings_in_code_fence():
    b = _b(guide="## Premise\n\n```\n## Open Questions\n```\n\nstill here")
    assert "still here" in b.prompt_block()


def test_prompt_block_canon():
    out = _b({"canon": ["Fact one.", "Fact two."]}).prompt_block()
    assert "## Established facts" in out
    assert "- Fact one.\n- Fact two." in out


def test_prompt_block_cast_fields_routing():
    cast = [{"name": "Mrs Honoria Pike", "age": 58, "gender": "female",
             "station": "widow", "role": "the news service", "region": "Amberlight",
             "appearance": "APPEARANCE-TEXT", "dress": "DRESS-TEXT",
             "voice": "Meera", "portrait": "p.jpg",
             "aliases": ["Honoria", "Mrs P"], "note": "Knows everything."},
            {"name": "Mr Silas Rowe"}]
    out = _b({"recurring_cast": cast}).prompt_block()
    assert ("- **Mrs Honoria Pike**, age 58, widow, the news service, "
            "usually found in Amberlight") in out
    assert "  - Also appears as: **Honoria**, **Mrs P**" in out
    assert "  - Knows everything." in out
    assert "- **Mr Silas Rowe**\n" in out + "\n"
    # image-only and pipeline-only fields never reach the text agents
    for hidden in ("APPEARANCE-TEXT", "DRESS-TEXT", "Meera", "p.jpg", "female"):
        assert hidden not in out


def test_prompt_block_age_zero_and_missing_omitted():
    out = _b({"recurring_cast": [{"name": "Baby", "age": 0}]}).prompt_block()
    assert "- **Baby**" in out and "age 0" not in out


def test_prompt_block_anchor_year_sentence_only_when_set():
    cast = [{"name": "A B"}]
    assert "Ages are as of" not in _b({"recurring_cast": cast}).prompt_block()
    out = _b({"recurring_cast": cast, "anchor_year": 1817}).prompt_block()
    assert "Ages are as of 1817" in out


def test_prompt_block_minor_names():
    out = _b({"minor_names": ["Mrs Copp, landlady", "Tom Dyer"]}).prompt_block()
    assert "## Names already used" in out
    assert "- Mrs Copp, landlady\n- Tom Dyer" in out


def test_prompt_block_section_order():
    b = _b({"canon": ["c"], "recurring_cast": [{"name": "A B"}],
            "minor_names": ["M"]}, guide=GUIDE)
    out = b.prompt_block(ideator=True)
    order = [out.index(h) for h in ("# Series bible", "## Premise", "## Established facts",
                                    "## Recurring cast", "## Names already used",
                                    "## Story ideas")]
    assert order == sorted(order)


def test_prompt_block_does_not_mutate_guide():
    b = _b(guide=GUIDE)
    b.prompt_block(ideator=True)
    assert b.guide == GUIDE
