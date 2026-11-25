"""bibles.validate, Problem, fatal and scaffold."""

from __future__ import annotations

import copy

import pytest

from story_pipeline import bibles

from .conftest import base_meta, write_bible

LIB = {"elevenlabs": {"voice_library": {"Meera": "x" * 20, "Charlotte": "y" * 20}}}


def run(repo_root, meta=None, body="# T\n\nBody.\n", cfg=None, raw=None):
    path = write_bible(repo_root, "demo", raw if raw is not None else meta, body)
    return bibles.validate(path, cfg)


def wheres(problems, fatal=None):
    return [p.where for p in problems if fatal is None or p.fatal == fatal]


def one(problems, where, contains=""):
    hits = [p for p in problems if p.where == where and contains in p.message]
    assert hits, [str(p) for p in problems]
    return hits[0]


def cast_with(**member):
    m = {"name": "Mrs Honoria Pike", "gender": "female", "appearance": "a", "dress": "b"}
    m.update(member)
    return base_meta(recurring_cast=[m])


# --------------------------------------------------------------------------- #

def test_baseline_is_clean(repo_root):
    assert run(repo_root) == []


def test_no_frontmatter(repo_root):
    path = repo_root / "bibles" / "x.md"
    path.write_text("# nothing\n")
    out = bibles.validate(path)
    assert len(out) == 1 and out[0].fatal and out[0].where == "x.md"
    assert "has no frontmatter block" in out[0].message
    assert "`---` frontmatter block" in out[0].fix


def test_bad_yaml(repo_root):
    out = run(repo_root, raw="series: [oops\n")
    assert len(out) == 1 and out[0].fatal and "not valid YAML" in out[0].message


def test_unclosed_frontmatter_is_reported_as_error(repo_root):
    path = repo_root / "bibles" / "x.md"
    path.write_text("---\nseries: S\n")
    out = bibles.validate(path)
    assert len(out) == 1 and out[0].fatal


def test_genre_missing_and_unknown(repo_root):
    p = one(run(repo_root, base_meta(genre=None)), "genre", "not declared")
    assert p.fatal and "regency" in p.fix
    p = one(run(repo_root, base_meta(genre="western")), "genre", "'western' is not installed")
    assert p.fatal


def test_series_missing_warns(repo_root):
    p = one(run(repo_root, base_meta(series=None)), "series")
    assert not p.fatal and "'demo'" in p.message


def test_empty_body_warns(repo_root):
    p = one(run(repo_root, body="\n"), "prose")
    assert not p.fatal


def test_canon_checks(repo_root):
    assert one(run(repo_root, base_meta(canon="a string")), "canon").fatal
    out = run(repo_root, base_meta(canon=["ok", "", 5, None]))
    assert wheres(out, True) == ["canon[1]", "canon[2]", "canon[3]"]
    out = run(repo_root, base_meta(canon=["Replace this with something a story could contradict."]))
    p = one(out, "canon[0]")
    assert not p.fatal and "placeholder" in p.message
    assert run(repo_root, base_meta(canon=None)) == []


def test_anchor_year(repo_root):
    assert one(run(repo_root, base_meta(anchor_year="1817")), "anchor_year").fatal
    assert run(repo_root, base_meta(anchor_year=1817)) == []


def test_minor_names(repo_root):
    assert one(run(repo_root, base_meta(minor_names="Tom")), "minor_names").fatal
    out = run(repo_root, base_meta(minor_names=["Tom", " ", 3]))
    assert wheres(out, True) == ["minor_names[1]", "minor_names[2]"]
    assert run(repo_root, base_meta(minor_names=["Tom Dyer"])) == []


def test_cast_not_a_list_and_member_not_mapping(repo_root):
    assert one(run(repo_root, base_meta(recurring_cast="x")), "recurring_cast", "must be a list").fatal
    out = run(repo_root, base_meta(recurring_cast=["just a string"]))
    assert one(out, "recurring_cast[0]", "mapping").fatal


def test_cast_member_without_name(repo_root):
    out = run(repo_root, base_meta(recurring_cast=[{"age": 5}, {"name": "  "}]))
    assert wheres(out, True) == ["recurring_cast[0]", "recurring_cast[1]"]


def test_cast_empty_list_and_none_ok(repo_root):
    assert run(repo_root, base_meta(recurring_cast=[])) == []
    assert run(repo_root, base_meta(recurring_cast=None)) == []


def test_aliases(repo_root):
    assert one(run(repo_root, cast_with(aliases="Honoria")), "cast 'Mrs Honoria Pike'", "aliases").fatal
    assert one(run(repo_root, cast_with(aliases=["ok", ""])), "cast 'Mrs Honoria Pike'", "aliases").fatal
    assert run(repo_root, cast_with(aliases=["Honoria"])) == []


def test_alias_collisions(repo_root):
    meta = base_meta(recurring_cast=[
        {"name": "Alice Brown", "gender": "female", "appearance": "a", "dress": "b",
         "aliases": ["Zed Quill"]},
        {"name": "Bob Quill", "gender": "male", "appearance": "a", "dress": "b",
         "aliases": ["zed quill"]}])
    out = run(repo_root, meta)
    assert any("alias 'Zed Quill' is also" in p.message and p.fatal for p in out)
    meta = base_meta(recurring_cast=[
        {"name": "Alice Brown", "gender": "female", "appearance": "a", "dress": "b"},
        {"name": "Bob Quill", "gender": "male", "appearance": "a", "dress": "b",
         "aliases": ["alice brown"]}])
    out = run(repo_root, meta)
    assert one(out, "cast 'Bob Quill'", "alias 'alice brown' is also Alice Brown").fatal


def test_age(repo_root):
    assert one(run(repo_root, cast_with(age="58")), "cast 'Mrs Honoria Pike'", "age").fatal
    assert run(repo_root, cast_with(age=58)) == []
    assert run(repo_root, cast_with(age=None)) == []


def test_gender(repo_root):
    meta = cast_with()
    del meta["recurring_cast"][0]["gender"]
    p = one(run(repo_root, meta), "cast 'Mrs Honoria Pike'", "no `gender`")
    assert not p.fatal and "female pool" in p.fix
    assert one(run(repo_root, cast_with(gender="woman")), "cast 'Mrs Honoria Pike'", "'woman'").fatal
    for g in ("female", "male", "neutral"):
        assert run(repo_root, cast_with(gender=g)) == []


@pytest.mark.parametrize("field", ["appearance", "dress"])
def test_appearance_and_dress_warn_when_blank(repo_root, field):
    for val in ("", "   ", None):
        out = run(repo_root, cast_with(**{field: val}))
        p = one(out, "cast 'Mrs Honoria Pike'", f"no `{field}`")
        assert not p.fatal


def test_voice_checked_only_against_a_library(repo_root):
    assert run(repo_root, cast_with(voice="Nonexistent")) == []          # no library: no check
    assert run(repo_root, cast_with(voice="Meera"), cfg=LIB) == []
    assert run(repo_root, cast_with(voice="AXdMgz6evoL7OPd7eU12"), cfg=LIB) == []   # raw id
    p = one(run(repo_root, cast_with(voice="Nonexistent"), cfg=LIB),
            "cast 'Mrs Honoria Pike'", "not in voices.yaml")
    assert p.fatal
    # 19 chars is not an id
    assert run(repo_root, cast_with(voice="AXdMgz6evoL7OPd7eU1"), cfg=LIB)


@pytest.mark.parametrize("field", ["role", "region", "station"])
def test_text_fields(repo_root, field):
    assert one(run(repo_root, cast_with(**{field: 5})), "cast 'Mrs Honoria Pike'",
               f"`{field}` must be text").fatal
    assert run(repo_root, cast_with(**{field: "text"})) == []


def test_portrait_field_warns_when_file_absent(repo_root):
    p = one(run(repo_root, cast_with(portrait="face.png")), "cast 'Mrs Honoria Pike'", "not found")
    assert not p.fatal
    cast = repo_root / "bibles" / "demo" / "demo.cast"
    cast.mkdir()
    (cast / "face.png").write_bytes(b"x")
    assert run(repo_root, cast_with(portrait="face.png")) == []


def test_duplicate_names_case_insensitive(repo_root):
    meta = base_meta(recurring_cast=[
        {"name": "Mrs Honoria Pike", "gender": "female", "appearance": "a", "dress": "b"},
        {"name": "mrs honoria pike", "gender": "female", "appearance": "a", "dress": "b"}])
    out = run(repo_root, meta)
    assert one(out, "recurring_cast", "duplicate name 'mrs honoria pike'").fatal


def test_names_too_similar_by_ear(repo_root):
    meta = base_meta(recurring_cast=[
        {"name": "Mr Hal Venn", "gender": "male", "appearance": "a", "dress": "b"},
        {"name": "Hallam Shaw", "gender": "male", "appearance": "a", "dress": "b"}])
    out = run(repo_root, meta)
    p = one(out, "recurring_cast", "too similar")
    assert p.fatal and "Mr Hal Venn" in p.message and "Hallam Shaw" in p.message


def test_distinct_names_pass_ear_check(repo_root):
    assert run(repo_root) == []


def test_regions_and_factions(repo_root):
    assert one(run(repo_root, base_meta(regions="x")), "regions").fatal
    assert one(run(repo_root, base_meta(factions={"a": 1})), "factions").fatal
    out = run(repo_root, base_meta(regions=[{"name": "A"}, {"summary": "no name"}, "str", {"name": " "}]))
    assert wheres(out, True) == ["regions[1]", "regions[2]", "regions[3]"]
    assert run(repo_root, base_meta(regions=[{"name": "A"}], factions=[{"name": "F"}])) == []


def test_assets(repo_root):
    assert one(run(repo_root, base_meta(assets=["x"])), "assets").fatal
    out = run(repo_root, base_meta(assets={"map": "", "layout": 3}))
    assert wheres(out, True) == ["assets.map", "assets.layout"]
    p = one(run(repo_root, base_meta(assets={"map": "demo.map.jpg"})), "assets.map", "not found")
    assert not p.fatal
    d = repo_root / "bibles" / "demo"
    (d / "demo.map.jpg").write_bytes(b"x")
    assert run(repo_root, base_meta(assets={"map": "demo.map.jpg"})) == []


def test_art_style(repo_root):
    assert one(run(repo_root, base_meta(art_style="  ")), "art_style", "empty").fatal
    assert run(repo_root, base_meta(art_style="Watercolour.")) == []


def test_art_style_explicit_null_is_an_error(repo_root):
    out = run(repo_root, raw="series: S\ngenre: regency\nart_style:\n")
    assert one(out, "art_style", "empty").fatal


def test_voices_block(repo_root):
    assert one(run(repo_root, base_meta(voices="Charlotte")), "voices").fatal
    assert run(repo_root, base_meta(voices={"narrator": "Nonexistent"})) == []   # no library
    assert run(repo_root, base_meta(voices={"narrator": "Charlotte"}), cfg=LIB) == []
    p = one(run(repo_root, base_meta(voices={"narrator": "Nonexistent"}), cfg=LIB),
            "voices.narrator", "not in voices.yaml")
    assert p.fatal


def test_orphan_portraits(repo_root):
    path = write_bible(repo_root, "demo", base_meta())
    cast = path.parent / "demo.cast"
    cast.mkdir()
    for n in ("mrs-honoria-pike.jpg", "mr-silas-rowe.png", "stranger.jpg", "old.png", "note.txt"):
        (cast / n).write_bytes(b"x")
    out = bibles.validate(path)
    assert sorted(p.where for p in out) == ["demo.cast/old.png", "demo.cast/stranger.jpg"]
    assert not any(p.fatal for p in out)


def test_orphan_check_honours_portrait_field(repo_root):
    path = write_bible(repo_root, "demo", cast_with(portrait="custom.jpg"))
    cast = path.parent / "demo.cast"
    cast.mkdir()
    (cast / "custom.jpg").write_bytes(b"x")
    assert bibles.validate(path) == []


def test_problem_str_and_fatal():
    a = bibles.Problem(True, "w", "boom", "do this")
    b = bibles.Problem(False, "w2", "hm")
    assert str(a) == "  ERROR  w: boom\n         → do this"
    assert str(b) == "  warn   w2: hm"
    assert bibles.fatal([a, b]) == [a]
    assert bibles.fatal([]) == []


def test_validate_does_not_mutate_cfg(repo_root):
    cfg = copy.deepcopy(LIB)
    run(repo_root, cast_with(voice="Meera"), cfg=cfg)
    assert cfg == LIB


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #

def test_scaffold_creates_nested_and_validates(repo_root):
    path = bibles.scaffold("my-series", "fantasy", None, "bibles")
    assert path == bibles.Path("bibles/my-series/my-series.md") and path.exists()
    b = bibles.load("my-series")
    assert b.series == "My Series" and b.genre == "fantasy"
    out = bibles.validate(path)
    assert not bibles.fatal(out)
    assert any(p.where == "canon[0]" for p in out)        # placeholder still there


def test_scaffold_example_is_clean_of_errors(repo_root):
    path = bibles.scaffold("ex", "regency", "Example Parish", "bibles", example=True)
    b = bibles.load("ex")
    assert b.series == "Example Parish"
    assert [c["name"] for c in b.recurring_cast] == ["Mrs Honoria Pike", "Mr Silas Rowe"]
    assert bibles.validate(path) == []


def test_scaffold_refuses_existing_nested_and_flat(repo_root):
    bibles.scaffold("a", "fantasy", None, "bibles")
    with pytest.raises(ValueError, match="already exists"):
        bibles.scaffold("a", "fantasy", None, "bibles")
    write_bible(repo_root, "b", nested=False)
    with pytest.raises(ValueError, match="already exists"):
        bibles.scaffold("b", "fantasy", None, "bibles")


def test_scaffold_genre_and_series_substituted(repo_root):
    path = bibles.scaffold("x", "scifi", "Salt Roads", "bibles")
    text = path.read_text()
    assert "genre: scifi" in text and "# Salt Roads" in text and 'series: "Salt Roads"' in text


def test_scaffold_series_with_quote_is_valid_yaml(repo_root):
    path = bibles.scaffold("x", "scifi", 'The "Salt" Roads', "bibles")
    assert bibles.load("x").series == 'The "Salt" Roads'
