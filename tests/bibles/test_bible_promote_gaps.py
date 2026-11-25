"""bible_promote.py: what story_pipeline/tests/test_bible_promote.py does not cover
(parsing helpers, bullet-form casts, preserved fields, fill_draft edge cases,
git helpers outside a repository)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from story_pipeline import bible_promote as bp
from story_pipeline import bibles, changelog

DRAFT_HEAD = "# Salt Roads (Working Draft)\n\nGenre: fantasy\nWorking title: Salt Roads Status: early.\n"


def put_draft(root, text, name="salt"):
    p = root / "bibles" / name / f"{name}.draft.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["salt", "salt-roads", "a1", "x_y", "0abc"])
def test_check_name_ok(name):
    assert bp.check_name(name) == name


@pytest.mark.parametrize("name", ["", None, "Salt", "-x", "_x", "a b", "../x", "a/b", "a.b"])
def test_check_name_rejects(name):
    with pytest.raises(bp.PromoteError, match="is not a bible name"):
        bp.check_name(name)


def test_paths():
    assert bp.draft_path("s") == Path("bibles/s/s.draft.md")
    assert bp.draft_path("s", "x") == Path("x/s/s.draft.md")
    assert bp.bible_path("s") == Path("bibles/s/s.md")


def test_field_value():
    assert bp._field_value("age", "45") == 45
    assert bp._field_value("age", "**45**") == 45
    assert bp._field_value("age", "about forty") == "about forty"
    assert bp._field_value("age", "1234") == "1234"
    assert bp._field_value("gender", " Male ") == "male"
    assert bp._field_value("aliases", "Mrs A, Mrs B ,, ") == ["Mrs A", "Mrs B"]
    assert bp._field_value("station", "  *curate*  ") == "curate"


def test_paragraphs_and_bullets():
    assert bp._paragraphs(["a", "b", "", "", "c ", "  d"]) == "a b c d"
    assert bp._paragraphs([]) == ""
    lines = ["- one", "  continued", "", "* two", "text outside", "\t- nested?", "- three"]
    # an indented line, bullet or not, folds into the bullet above it
    assert bp._bullets(lines) == ["one continued", "two - nested?", "three"]
    assert bp._bullets(["no bullets here"]) == []


def test_split_names():
    assert bp._split_names("Garret Marsh (Glasswood mapper)") == (["Garret Marsh"], "Glasswood mapper")
    assert bp._split_names("The collectors: Tobias Finch and Tess Ridley") == \
        (["Tobias Finch", "Tess Ridley"], "collectors")
    assert bp._split_names("A, B & C") == (["A", "B", "C"], None)
    assert bp._split_names("Plain Name") == (["Plain Name"], None)
    assert bp._split_names("The pair: X and Y (twins)") == (["X", "Y"], "twins")   # paren role wins


def test_infer_gender():
    assert bp._infer_gender("Mrs Ada", "", sole=False) is None
    assert bp._infer_gender("Old Man Rowe", "", sole=False) == "male"
    assert bp._infer_gender("Ada Penrose", "Ada Penrose, a woman of forty.", sole=False) == "female"
    assert bp._infer_gender("Ada Penrose", "Ada was the warden's widow.", sole=False) == "female"
    assert bp._infer_gender("Ada", "Unrelated text about a man.", sole=False) is None
    assert bp._infer_gender("Ada", "Unrelated text about a man.", sole=True) == "male"
    assert bp._infer_gender("Ada", "nothing relevant at all", sole=True) is None
    # only 80 chars after the name count
    far = "Ada " + "x" * 90 + " woman"
    assert bp._infer_gender("Ada", far, sole=False) is None


def test_label_regexes():
    m = bp.CAST_LINE.match("- **Age (Tess Ridley):** 45")
    assert m["label"] == "Age" and m["who"] == "Tess Ridley" and m["value"] == "45"
    m = bp.CAST_LINE.match("Gender: Female")
    assert m["value"] == "Female"
    assert bp.CAST_LINE.match("  - role: warden")["value"] == "warden"
    assert bp.CAST_LINE.match("Genre: fantasy") is None
    assert bp.TOP_LINE.match("Anchor year: 1817")["value"] == "1817"
    assert bp.TOP_LINE.match("**Series:** X")["value"] == "X"
    assert bp.CAST_LINE.match("Agent: nope") is None


# --------------------------------------------------------------------------- #
# parse_draft
# --------------------------------------------------------------------------- #

def test_parse_draft_top_fields_title_and_working_title():
    d = bp.parse_draft(DRAFT_HEAD + "Anchor year: 1817\nSeries: Salt\n\n## Premise\n\nText.\n")
    assert d.title == "Salt Roads (Working Draft)"
    assert d.working_title == "Salt Roads"
    assert d.top == {"genre": "fantasy", "anchor_year": 1817, "series": "Salt"}
    assert [t for t, _, _ in d.sections] == ["Premise"]
    assert d.cast == [] and d.cast_intro == ""


def test_parse_draft_anchor_year_non_digit_stays_text():
    d = bp.parse_draft("# T\n\nAnchor year: circa 1817\n")
    assert d.top["anchor_year"] == "circa 1817"


def test_parse_draft_top_fields_after_first_h2_ignored():
    d = bp.parse_draft("# T\n\n## Premise\n\nGenre: fantasy\n")
    assert d.top == {}


def test_parse_draft_frontmatter():
    d = bp.parse_draft("---\ngenre: scifi\nseries: Fm\n---\n\n# T\n\n## A\n\nx\n")
    assert d.front == {"genre": "scifi", "series": "Fm"}
    assert d.title == "T" and [t for t, _, _ in d.sections] == ["A"]


def test_parse_draft_bad_frontmatter():
    with pytest.raises(bp.PromoteError, match="frontmatter does not parse"):
        bp.parse_draft("---\nseries: [oops\n---\n# T\n")
    with pytest.raises(bp.PromoteError, match="frontmatter does not parse"):
        bp.parse_draft("---\nnever closed\n")


def test_parse_draft_ignores_headings_in_fences():
    d = bp.parse_draft("# T\n\n## A\n\n```\n## B\n```\n\n## C\n\nx\n")
    assert [t for t, _, _ in d.sections] == ["A", "C"]


def test_parse_cast_heading_form():
    text = ("# T\n\n## Recurring Cast\n\nIntro line.\n\n"
            "### Ada Penrose (road warden)\n\n- Age: 44\n- Appearance: Tall, grey coat.\n\n"
            "A woman who keeps the tide tables.\n\n"
            "### Hal Venn\n\n- Gender: male\n\nDrives carts.\n")
    d = bp.parse_draft(text)
    ada, hal = d.cast
    assert ada.name == "Ada Penrose" and ada.kind == "heading" and not ada.shared
    assert ada.fields == {"age": 44, "appearance": "Tall, grey coat.", "role": "road warden",
                          "gender": "female"}
    assert ada.inferred == ["gender"] and ada.note == "A woman who keeps the tide tables."
    assert hal.fields["gender"] == "male" and hal.inferred == []
    assert d.cast_intro == "Intro line."
    assert ada.line < hal.line


def test_parse_cast_explicit_role_not_overwritten_by_heading():
    d = bp.parse_draft("# T\n\n## Cast\n\n### Ada Penrose (warden)\n\n- Role: tide keeper\n\nA woman.\n")
    assert d.cast[0].fields["role"] == "tide keeper"


def test_parse_cast_shared_heading_and_qualified_fields():
    text = ("# T\n\n## Recurring Cast\n\n### The carters: Hal Venn and Ottoline Shaw\n\n"
            "- Gender (Hal Venn): male\n- Gender (Ottoline Shaw): female\n- Station: carters\n\n"
            "They haul salt.\n")
    hal, ott = bp.parse_draft(text).cast
    assert hal.shared and ott.shared
    assert hal.fields == {"gender": "male", "station": "carters", "role": "carters"}
    assert ott.fields["gender"] == "female"
    assert hal.note == ott.note == "They haul salt."


def test_parse_cast_bullet_form():
    text = ("# T\n\n## Cast\n\nWho recurs.\n\n"
            "- **Ada Penrose**: a widow who keeps the tide tables.\n"
            "  - Age: 44\n  - Station: warden\n\n"
            "- **Hal Venn** — drives.\n  - Gender: male\n"
            "  more about Hal.\n\n"
            "Closing prose.\n")
    d = bp.parse_draft(text)
    ada, hal = d.cast
    assert ada.kind == "bullet" and ada.fields["age"] == 44 and ada.fields["station"] == "warden"
    assert ada.fields["gender"] == "female" and ada.inferred == ["gender"]
    assert ada.note == "a widow who keeps the tide tables."
    assert hal.note.startswith("drives.") and "more about Hal." in hal.note
    assert hal.fields["gender"] == "male"
    assert "Who recurs." in d.cast_intro and "Closing prose." in d.cast_intro


def test_parse_cast_field_line_with_empty_value_is_prose():
    d = bp.parse_draft("# T\n\n## Cast\n\n### Ada\n\n- Age:\n\nA woman.\n")
    assert "age" not in d.cast[0].fields and "Age:" in d.cast[0].note


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #

def test_build_missing_draft_and_bad_name(repo_root):
    with pytest.raises(bp.PromoteError, match="no draft at"):
        bp.build("salt")
    with pytest.raises(bp.PromoteError, match="is not a bible name"):
        bp.build("../x")


FULL = (DRAFT_HEAD + "Anchor year: 1817\n\n"
        "## Premise\n\nText.\n\n## Established Facts\n\n- A fact.\n- Another\n  wrapped.\n\n"
        "## Regions\n\n- **Brinehold:** the big town. Owns the pans.\n- **Flats**: mud; birds.\n\n"
        "## Empty\n\n## Recurring Cast\n\n### Ada Penrose\n\n- Gender: female\n\nWarden.\n\n"
        "## Story Ideas\n\n- seed\n")


def test_build_series_genre_anchor_sources(repo_root):
    pv = bp.build("salt") if put_draft(repo_root, FULL) else None
    assert pv.meta["series"] == "Salt Roads" and pv.meta["genre"] == "fantasy"
    assert pv.meta["anchor_year"] == 1817
    assert pv.meta["canon"] == ["A fact.", "Another wrapped."]
    assert pv.meta["regions"] == [{"name": "Brinehold", "summary": "the big town"},
                                  {"name": "Flats", "summary": "mud"}]   # first clause only
    kept = {k["title"]: k for k in pv.kept}
    assert kept["Established Facts"] == {"title": "Established Facts", "as": "canon", "count": 2}
    assert kept["Recurring Cast"]["as"] == "recurring_cast" and kept["Recurring Cast"]["count"] == 1
    assert kept["Story Ideas"]["as"] == "ideator only" and kept["Premise"]["as"] == "prose"
    assert {"title": "Empty", "reason": "empty"} in pv.dropped
    assert pv.dropped[0] == {"title": "(title and status lines)", "reason": "draft header"}
    assert "## Story Ideas" in pv.text and "## Premise" in pv.text
    assert "## Established Facts" not in pv.text           # became frontmatter
    assert "## Empty" not in pv.text
    assert pv.text.startswith("---\n# Generated by `bible promote` from salt.draft.md.")
    assert pv.current == "" and pv.changed and not pv.blocked


def test_build_series_precedence(repo_root):
    put_draft(repo_root, "# T\n\nSeries: Top\nWorking title: Work\n\n## A\n\nx\n")
    assert bp.build("salt").meta["series"] == "Top"
    put_draft(repo_root, "---\nseries: Front\n---\n# T\n\nWorking title: Work\n\n## A\n\nx\n")
    assert bp.build("salt").meta["series"] == "Front"
    put_draft(repo_root, "# T\n\nWorking title: Work\n\n## A\n\nx\n")
    assert bp.build("salt").meta["series"] == "Work"
    put_draft(repo_root, "## A\n\nx\n")
    assert bp.build("salt").meta["series"] == "Salt"                # from the name
    put_draft(repo_root, "## A\n\nx\n", "two-words")
    assert bp.build("two-words").meta["series"] == "Two Words"
    assert "genre" not in bp.build("two-words").meta


def test_build_series_and_genre_fall_back_to_existing_bible(repo_root):
    put_draft(repo_root, "## A\n\nx\n")
    (repo_root / "bibles/salt/salt.md").write_text(
        "---\nseries: Old Name\ngenre: scifi\n---\n\nold body\n")
    pv = bp.build("salt")
    assert pv.meta["series"] == "Old Name" and pv.meta["genre"] == "scifi"
    assert pv.current.startswith("---\nseries: Old Name")


def test_build_preserves_hand_written_fields(repo_root):
    put_draft(repo_root, DRAFT_HEAD + "\n## Recurring Cast\n\n### Ada Penrose\n\n"
              "- Gender: female\n\nWarden.\n\n### Hal Venn\n\n- Gender: male\n\nCarter.\n")
    (repo_root / "bibles/salt/salt.md").write_text(
        "---\nseries: S\ngenre: fantasy\nart_style: Ink.\nvoices:\n  narrator: Charlotte\n"
        "minor_names:\n  - Tom Dyer\nrecurring_cast:\n"
        "  - name: ada penrose\n    voice: Meera\n    portrait: ada.jpg\n"
        "  - name: Gone\n    voice: X\n---\n\nbody\n")
    pv = bp.build("salt")
    assert pv.meta["art_style"] == "Ink." and pv.meta["voices"] == {"narrator": "Charlotte"}
    assert pv.meta["minor_names"] == ["Tom Dyer"]
    ada, hal = pv.meta["recurring_cast"]
    assert ada["voice"] == "Meera" and ada["portrait"] == "ada.jpg"      # matched case-insensitively
    assert "voice" not in hal
    assert [c["name"] for c in pv.meta["recurring_cast"]] == ["Ada Penrose", "Hal Venn"]   # Gone dropped
    assert pv.preserved == {"voices": True, "art_style": True, "cast_voices": ["Ada Penrose"],
                            "minor_names": True}


def test_build_cast_field_order_and_unknown_fields_after(repo_root):
    put_draft(repo_root, DRAFT_HEAD + "\n## Cast\n\n### Ada Penrose\n\n- Station: warden\n"
              "- Age: 44\n- Gender: female\n\nNote text.\n")
    c = bp.build("salt").meta["recurring_cast"][0]
    assert list(c) == ["name", "age", "gender", "station", "note"]


def test_build_assets_detected_from_files_beside_draft(repo_root):
    d = put_draft(repo_root, DRAFT_HEAD + "\n## A\n\nx\n").parent
    assert "assets" not in bp.build("salt").meta
    (d / "salt.map.png").write_bytes(b"x")
    (d / "salt.layout.jpg").write_bytes(b"x")
    assert bp.build("salt").meta["assets"] == {"map": "salt.map.png", "layout": "salt.layout.jpg"}
    (d / "salt.map.jpg").write_bytes(b"x")                      # jpg preferred for map
    assert bp.build("salt").meta["assets"]["map"] == "salt.map.jpg"


def test_build_intro_of_cast_kept_in_body(repo_root):
    put_draft(repo_root, DRAFT_HEAD + "\n## Cast\n\nHow they work.\n\n### Ada\n\n- Gender: female\n\nx\n")
    pv = bp.build("salt")
    assert "## Recurring Cast\n\nHow they work." in pv.text


def test_build_working_only_sections_dropped(repo_root):
    put_draft(repo_root, DRAFT_HEAD + "\n## Open Questions\n\n- q\n\n## Changelog\n\n- c\n"
              "\n## Minor Names Register\n\n- m\n\n## Premise\n\nkeep\n")
    pv = bp.build("salt")
    assert [d["title"] for d in pv.dropped if d["reason"] == "working-only"] == \
        ["Open Questions", "Changelog", "Minor Names Register"]
    assert "- q" not in pv.text and "keep" in pv.text


def test_built_text_is_a_loadable_bible(repo_root):
    put_draft(repo_root, FULL)
    pv = bp.build("salt")
    target = repo_root / "bibles/salt/salt.md"
    target.write_text(pv.text)
    b = bibles.load("salt")
    assert b.genre == "fantasy" and b.anchor_year == 1817 and b.canon == ["A fact.", "Another wrapped."]
    assert b.member("Ada Penrose")["note"] == "Warden."
    assert b.story_ideas == "- seed"
    assert "## Premise" in b.guide and b.guide.startswith("# Salt Roads")


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #

def test_preview_diff_and_to_dict(repo_root):
    put_draft(repo_root, FULL)
    pv = bp.preview("salt")
    assert pv.diff().startswith("--- /dev/null\n+++ salt.md (promoted)")
    d = pv.to_dict()
    assert d["exists"] is False and d["changed"] is True and d["regions"] == ["Brinehold", "Flats"]
    assert d["cast"][0]["name"] == "Ada Penrose" and "note" not in d["cast"][0]
    assert d["cast"][0]["inferred"] == []
    bp.promote("salt", {}, author="t")
    pv = bp.preview("salt")
    assert pv.diff() == "" and not pv.changed and pv.to_dict()["exists"] is True
    Path("bibles/salt/salt.md").write_text(pv.text + "\nextra\n")
    assert bp.preview("salt").diff().startswith("--- salt.md (current)")


def test_preview_cfg_bibles_dir(repo_root, tmp_path):
    other = tmp_path / "shelf"
    (other / "salt").mkdir(parents=True)
    (other / "salt" / "salt.draft.md").write_text(FULL)
    assert bp.preview("salt", {"bibles_dir": str(other)}).draft == other / "salt/salt.draft.md"
    assert bp.preview("salt", {}, bibles_dir=other).bible == other / "salt/salt.md"


# --------------------------------------------------------------------------- #
# check / _fields_for
# --------------------------------------------------------------------------- #

def _problem(where, msg):
    return bibles.Problem(True, where, msg)


def test_fields_for_mapping():
    meta = {"genre": "x", "recurring_cast": [{"name": "Hal Venn"}, {"name": "Hal Shaw"}]}
    f = bp._fields_for(_problem("genre", "not declared"), meta)
    assert f[0]["id"] == "genre" and f[0]["type"] == "select" and "fantasy" in f[0]["options"]
    assert f[0]["value"] == "x"
    assert bp._fields_for(_problem("series", "m"), meta)[0]["id"] == "series"
    assert bp._fields_for(_problem("anchor_year", "m"), meta)[0]["type"] == "number"
    g = bp._fields_for(_problem("cast 'Ada'", "gender 'x' is not one of"), meta)[0]
    assert g["id"] == "cast:Ada:gender" and g["options"] == ["female", "male", "neutral"]
    assert bp._fields_for(_problem("cast 'Ada'", "no `appearance`"), meta)[0]["id"] == "cast:Ada:appearance"
    assert bp._fields_for(_problem("cast 'Ada'", "age 'x' is not a whole number"), meta)[0]["type"] == "number"
    assert bp._fields_for(_problem('cast "Ada\'s father"', "no `dress`"), meta)[0]["id"] == \
        "cast:Ada's father:dress"
    assert bp._fields_for(_problem("cast 'Ada'", "something unrelated"), meta) == []
    sim = bp._fields_for(_problem("recurring_cast", "names too similar to tell apart by ear: Hal Venn, Hal Shaw"), meta)
    assert [x["id"] for x in sim] == ["cast:Hal Venn:name", "cast:Hal Shaw:name"]
    dup = bp._fields_for(_problem("recurring_cast", "duplicate name 'hal venn'"), meta)
    assert [x["id"] for x in dup] == ["cast:Hal Venn:name"]
    assert bp._fields_for(_problem("canon", "must be a list"), meta) == []


def test_check_maps_fix_text_and_keeps_unmapped(repo_root):
    put_draft(repo_root, "# Salt\n\n## Premise\n\nx\n\n## Cast\n\n### Ada\n\n- Gender: female\n\nA woman.\n")
    pv = bp.build("salt")
    out = bp.check(pv)
    genre = next(p for p in out if p["where"] == "genre")
    assert genre["fatal"] and genre["fix"] == bp.DRAFT_FIX["genre"] and genre["fields"]
    assert any(p["where"] == "cast 'Ada'" and not p["fatal"] for p in out)     # missing appearance/dress warn
    for p in out:
        assert set(p) == {"fatal", "where", "message", "fix", "fields"}


def test_check_does_not_write_into_bibles_dir(repo_root):
    put_draft(repo_root, FULL)
    before = sorted(str(p) for p in (repo_root / "bibles").rglob("*"))
    bp.preview("salt")
    assert sorted(str(p) for p in (repo_root / "bibles").rglob("*")) == before


def test_check_sees_existing_cast_dir_and_assets(repo_root):
    d = put_draft(repo_root, DRAFT_HEAD + "\n## Cast\n\n### Ada Penrose\n\n- Gender: female\n"
                  "- Appearance: Tall.\n- Dress: Grey.\n\nA woman.\n").parent
    (d / "salt.cast").mkdir()
    (d / "salt.cast" / "stranger.jpg").write_bytes(b"x")
    (d / "salt.map.jpg").write_bytes(b"x")
    out = bp.check(bp.build("salt"))
    assert any(p["where"] == "salt.cast/stranger.jpg" for p in out)           # orphan seen via symlink
    assert not any(p["where"].startswith("assets.") for p in out)             # map found via symlink


# --------------------------------------------------------------------------- #
# summary / promote
# --------------------------------------------------------------------------- #

def test_summary_text(repo_root):
    put_draft(repo_root, FULL + "\n## Open Questions\n\n- q\n")
    pv = bp.build("salt")
    assert bp.summary(pv) == ("Promoted from salt.draft.md: 2 canon fact(s), 1 recurring cast. "
                              "Left in the draft: Open Questions.")
    pv.preserved["cast_voices"] = ["Ada Penrose"]
    assert bp.summary(pv).endswith("Kept voices for Ada Penrose.")


def test_promote_writes_bible_and_changelog_and_noop_second_time(repo_root):
    put_draft(repo_root, FULL)
    out = bp.promote("salt", {"author": "Cfg"})
    assert out["written"] and Path(out["bible"]).exists() and Path(out["changelog"]).exists()
    (e,) = changelog.entries(Path(out["bible"]))
    assert e.author == "Cfg" and e.kind == "promote" and e.sha == changelog.content_hash(Path(out["bible"]))
    assert Path(out["changelog"]).read_text().startswith("# Changelog — Salt Roads")
    assert bp.promote("salt", {})["message"] == "already up to date"


def test_promote_explicit_author_beats_config(repo_root):
    put_draft(repo_root, FULL)
    out = bp.promote("salt", {"author": "Cfg"}, author="Explicit")
    assert changelog.entries(Path(out["bible"]))[0].author == "Explicit"


def test_promote_error_lists_fatal_problems_and_writes_nothing(repo_root):
    put_draft(repo_root, "# Salt\n\n## Premise\n\nx\n")                 # no genre
    with pytest.raises(bp.PromoteError) as e:
        bp.promote("salt", {})
    assert "1 error(s)" in str(e.value) and "genre: not declared" in str(e.value)
    assert not (repo_root / "bibles/salt/salt.md").exists()
    assert not (repo_root / "bibles/salt/salt.changelog.md").exists()


def test_promote_creates_missing_bible_folder_only_when_writing(repo_root):
    put_draft(repo_root, FULL)
    assert bp.promote("salt", {})["written"]
    assert (repo_root / "bibles/salt").is_dir()


# --------------------------------------------------------------------------- #
# fill_draft
# --------------------------------------------------------------------------- #

def test_fill_empty_value_and_unknown_fields():
    with pytest.raises(bp.PromoteError, match="empty value"):
        bp.fill_draft("# T\n", "genre", "   ")
    with pytest.raises(bp.PromoteError, match="unknown field"):
        bp.fill_draft("# T\n", "bogus", "x")
    with pytest.raises(bp.PromoteError, match="no cast member 'Zed'"):
        bp.fill_draft("# T\n\n## Cast\n\n### Ada\n\nx\n", "cast:Zed:age", "4")
    with pytest.raises(bp.PromoteError, match="unknown cast field 'favourite'"):
        bp.fill_draft("# T\n\n## Cast\n\n### Ada\n\nx\n", "cast:Ada:favourite", "4")


def test_fill_collapses_whitespace_and_coerces_to_str():
    out = bp.fill_draft("# T\n\n## A\n\nx\n", "anchor_year", 1817)
    assert "Anchor year: 1817" in out
    out = bp.fill_draft("# T\n\n## A\n\nx\n", "series", "  Two\n  Lines  ")
    assert "Series: Two Lines" in out


def test_fill_top_field_positions():
    out = bp.fill_draft("# T\n\nSome intro.\n\n## A\n\nx\n", "genre", "fantasy")
    assert out == "# T\n\nGenre: fantasy\n\nSome intro.\n\n## A\n\nx\n"
    assert bp.parse_draft(out).top["genre"] == "fantasy"
    # directly under the title with a blank already following
    out = bp.fill_draft("# T\n\n## A\n\nx\n", "genre", "fantasy")
    assert bp.parse_draft(out).top == {"genre": "fantasy"}
    # replace an existing line instead of adding a second
    out = bp.fill_draft("# T\n\nGenre: scifi\n\n## A\n\nx\n", "genre", "fantasy")
    assert out.count("Genre:") == 1 and "Genre: fantasy" in out
    # no H1 at all
    out = bp.fill_draft("## A\n\nx\n", "genre", "fantasy")
    assert out.startswith("Genre: fantasy\n\n## A")
    # no sections at all
    out = bp.fill_draft("# T\n", "genre", "fantasy")
    assert bp.parse_draft(out).top["genre"] == "fantasy"


def test_fill_top_field_not_confused_by_same_label_in_a_section():
    text = "# T\n\n## A\n\nGenre: not a top field here\n"
    out = bp.fill_draft(text, "genre", "fantasy")
    assert out.count("Genre:") == 2 and bp.parse_draft(out).top["genre"] == "fantasy"


def test_fill_cast_heading_adds_after_existing_field_lines_and_replaces():
    text = "# T\n\n## Cast\n\n### Ada Penrose\n\n- Age: 44\n- Gender: female\n\nProse.\n\n### Hal\n\nMore.\n"
    out = bp.fill_draft(text, "cast:Ada Penrose:station", "warden")
    assert "- Gender: female\n- Station: warden\n" in out
    assert bp.parse_draft(out).cast[0].fields["station"] == "warden"
    out2 = bp.fill_draft(out, "cast:Ada Penrose:age", "45")
    assert out2.count("Age") == 1 and bp.parse_draft(out2).cast[0].fields["age"] == 45
    out3 = bp.fill_draft(text, "cast:Hal:gender", "male")
    hal = bp.parse_draft(out3).cast[1]
    assert hal.fields["gender"] == "male" and hal.inferred == [] and hal.note == "More."


def test_fill_cast_case_insensitive_who():
    out = bp.fill_draft("# T\n\n## Cast\n\n### Ada Penrose\n\nProse.\n", "cast:ada penrose:age", "3")
    assert bp.parse_draft(out).cast[0].fields["age"] == 3


def test_fill_cast_bullet_form_adds_and_replaces():
    text = "# T\n\n## Cast\n\n- **Ada**: a widow.\n  - Age: 44\n- **Hal**: a man.\n"
    out = bp.fill_draft(text, "cast:Ada:station", "warden")
    assert "  - Age: 44\n  - Station: warden\n- **Hal**" in out
    out = bp.fill_draft(out, "cast:Ada:age", "45")
    assert out.count("Age") == 1 and bp.parse_draft(out).cast[0].fields["age"] == 45
    out = bp.fill_draft(text, "cast:Hal:gender", "male")
    assert bp.parse_draft(out).cast[1].fields["gender"] == "male"


def test_fill_cast_name_rename_heading_and_bullet():
    out = bp.fill_draft("# T\n\n## Cast\n\n### Ada Penrose (warden)\n\nx\n", "cast:Ada Penrose:name", "Ada Pell")
    assert "### Ada Pell (warden)" in out
    out = bp.fill_draft("# T\n\n## Cast\n\n- **Ada**: a widow.\n", "cast:Ada:name", "Ada Pell")
    assert "- **Ada Pell**: a widow." in out


def test_fill_with_frontmatter_hits_the_right_lines():
    text = "---\nseries: Fm\n---\n\n# T\n\n## Cast\n\n### Ada\n\nProse.\n\n### Hal\n\nOther.\n"
    out = bp.fill_draft(text, "cast:Hal:age", "50")
    assert out.startswith("---\nseries: Fm\n---")
    d = bp.parse_draft(out)
    assert d.cast[1].fields["age"] == 50 and "age" not in d.cast[0].fields
    out = bp.fill_draft(text, "cast:Ada:name", "Ada Pell")
    assert "### Ada Pell" in out and "### Hal" in out


def test_fill_shared_heading_qualifies_label():
    text = "# T\n\n## Cast\n\n### The pair: Hal Venn and Ottoline Shaw\n\nThey haul salt.\n"
    out = bp.fill_draft(text, "cast:Ottoline Shaw:age", "14")
    assert "- Age (Ottoline Shaw): 14" in out
    d = bp.parse_draft(out)
    assert d.cast[1].fields["age"] == 14 and "age" not in d.cast[0].fields
    out = bp.fill_draft(out, "cast:Ottoline Shaw:age", "15")
    assert out.count("Age (Ottoline Shaw)") == 1


def test_fill_on_disk(repo_root):
    with pytest.raises(bp.PromoteError, match="no draft at"):
        bp.fill("salt", "genre", "fantasy")
    with pytest.raises(bp.PromoteError, match="is not a bible name"):
        bp.fill("Bad Name", "genre", "fantasy")
    p = put_draft(repo_root, "# T\n\n## A\n\nx\n")
    assert bp.fill("salt", "genre", "fantasy") == Path("bibles/salt/salt.draft.md")
    assert "Genre: fantasy" in p.read_text()
    before = p.read_text()
    with pytest.raises(bp.PromoteError):
        bp.fill("salt", "bogus", "x")
    assert p.read_text() == before


# --------------------------------------------------------------------------- #
# git helpers outside a checkout, apply_plan, apply
# --------------------------------------------------------------------------- #

@pytest.fixture
def no_git(repo_root, monkeypatch, tmp_path):
    cfg = tmp_path / "gitconfig"
    cfg.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repo_root.parent))


def test_git_state_outside_repo(no_git):
    st = bp.git_state([Path("x")])
    assert st["repo"] is False and st["branch"] is None and st["dirty"] == []


def test_commit_outside_repo(no_git):
    Path("a.txt").write_text("a")
    out = bp.commit([Path("a.txt")], "salt", "msg", push=False)
    assert out["committed"] is False and "not a git checkout" in out["message"]


def test_push_waiting_errors(no_git):
    with pytest.raises(bp.PromoteError, match="not a git checkout"):
        bp.push_waiting()


def test_push_waiting_no_upstream(no_git):
    subprocess.run(["git", "init", "-q"], check=True)
    with pytest.raises(bp.PromoteError, match="has no upstream"):
        bp.push_waiting()
    st = bp.git_state([])
    assert st["repo"] is True and st["upstream"] is None and st["ahead"] is None


def test_git_helper_raises_promote_error(no_git):
    with pytest.raises(bp.PromoteError):
        bp._git("rev-parse", "--is-inside-work-tree")


def test_apply_plan_actions_and_counts(repo_root, no_git):
    put_draft(repo_root, FULL)
    plan = bp.apply_plan("salt")
    assert plan["changed"] and not plan["blocked"] and plan["errors"] == []
    assert [f["action"] for f in plan["files"]] == ["create", "create", "commit"]
    assert plan["files"][0]["added"] > 5 and plan["files"][0]["removed"] == 0
    assert plan["git"]["repo"] is False and "Promoted from salt.draft.md" in plan["summary"]
    bp.promote("salt", {})
    plan = bp.apply_plan("salt")
    assert [f["action"] for f in plan["files"]] == ["unchanged", "unchanged", "commit"]
    d = Path("bibles/salt/salt.draft.md")
    d.write_text(d.read_text().replace("Text.", "New text."))
    plan = bp.apply_plan("salt")
    assert [f["action"] for f in plan["files"]] == ["update", "append", "commit"]
    assert plan["files"][0]["added"] >= 1 and plan["files"][0]["removed"] >= 1


def test_apply_plan_reports_blockers(repo_root, no_git):
    put_draft(repo_root, "# Salt\n\n## Premise\n\nx\n")
    plan = bp.apply_plan("salt")
    assert plan["blocked"] and [e["where"] for e in plan["errors"]] == ["genre"]


def test_apply_without_commit_and_with_commit_outside_repo(repo_root, no_git):
    put_draft(repo_root, FULL)
    out = bp.apply("salt", {}, do_commit=False)
    assert out["written"] and "git" not in out
    put_draft(repo_root, FULL.replace("Text.", "Newer text."))
    out = bp.apply("salt", {}, author="t")
    assert out["written"] and out["git"]["committed"] is False
    again = bp.apply("salt", {})
    assert again["written"] is False and "git" in again


def test_apply_reuses_summary_in_commit_message(repo_root, no_git):
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.name", "T"], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], check=True)
    put_draft(repo_root, FULL)
    out = bp.apply("salt", {}, author="t", push=False)
    assert out["git"]["committed"] and out["git"]["pushed"] is False
    msg = subprocess.run(["git", "log", "-1", "--format=%B"], capture_output=True, text=True).stdout
    assert msg.startswith("bible: promote salt") and "Promoted from salt.draft.md" in msg
    assert sorted(out["git"]["files"]) == ["bibles/salt/salt.changelog.md",
                                           "bibles/salt/salt.draft.md", "bibles/salt/salt.md"]
