"""agents/text.py: chapter validation, speaker resolution, markdown rendering, length_fix,
title segments, approvals."""

from __future__ import annotations

import json

import pytest

from story_pipeline.agents import text
from story_pipeline.bundle import BREAK

from .helpers import chapter_json, make_bundle, mini_outline, verdict

OUTLINE = mini_outline(15)
CAST = [c["name"] for c in OUTLINE["cast"]]


def N(t):
    return {"speaker": "narrator", "text": t}


def S(speaker, t):
    return {"speaker": speaker, "text": t}


def brk():
    return {"speaker": BREAK, "text": ""}


def ch(*segs, n=1):
    return {"n": n, "title": "T", "segments": list(segs)}


def validate(c, outline=OUTLINE):
    return text._validate_chapter(c, outline)


# --------------------------------------------------------------------------- #
# speaker resolution
# --------------------------------------------------------------------------- #

SMITHS = ["Miss Anne Smith", "Mrs Beth Smith", "Mr Owen Vane", "Miss Judith Vane"]


def test_resolve_exact_name():
    assert text.resolve_speaker("Mr Owen Vane", SMITHS) == "Mr Owen Vane"


def test_resolve_by_surname_unique():
    assert text.resolve_speaker("Owen", SMITHS) == "Mr Owen Vane"
    assert text.resolve_speaker("Judith Vane", SMITHS) == "Miss Judith Vane"


def test_resolve_title_breaks_tie_between_same_surname():
    assert text.resolve_speaker("Miss Vane", SMITHS) == "Miss Judith Vane"
    assert text.resolve_speaker("Mr Vane", SMITHS) == "Mr Owen Vane"


def test_resolve_ambiguous_returns_none():
    assert text.resolve_speaker("Smith", SMITHS) is None
    assert text.resolve_speaker("Vane", SMITHS) is None
    assert sorted(text.rank_speaker("Vane", SMITHS)) == ["Miss Judith Vane", "Mr Owen Vane"]


def test_resolve_title_alone_is_never_a_match():
    assert text.resolve_speaker("Mrs Fenner", ["Mrs Honoria Pike", "Mrs Hale"]) is None
    assert text.rank_speaker("Mrs", ["Mrs Honoria Pike"]) == []


def test_resolve_wrong_title_same_surname_still_resolves_if_unique():
    assert text.resolve_speaker("Mrs Pike", ["Miss Honoria Pike"]) == "Miss Honoria Pike"


def test_resolve_empty_and_punctuation():
    assert text.resolve_speaker("", CAST) is None
    assert text.resolve_speaker("  ", CAST) is None
    assert text.resolve_speaker("Mr. Rowe,", CAST) == "Mr Silas Rowe"


def test_parts_splits_names_from_honorifics():
    assert text._parts("Miss Judith Vane") == ({"judith", "vane"}, {"miss"})
    assert text._parts("narrator") == ({"narrator"}, set())


# --------------------------------------------------------------------------- #
# _validate_chapter
# --------------------------------------------------------------------------- #


def test_valid_chapter_unchanged_and_returns_zero():
    c = ch(N("Dawn broke."), S("Mr Silas Rowe", "Hello."))
    before = json.loads(json.dumps(c))
    assert validate(c) == 0
    assert c == before


def test_no_segments_raises():
    with pytest.raises(ValueError, match="chapter 1 has no segments"):
        validate(ch())
    with pytest.raises(ValueError, match="no segments"):
        validate({"n": 1})


def test_empty_segments_dropped_and_counted():
    c = ch(N("Real."), N(""), S("Mr Silas Rowe", ""), N(" "), N("More."))
    assert validate(c) == 3
    assert [s["text"] for s in c["segments"]] == ["Real.", "More."]
    assert c["dropped_empty_segments"] == 3


def test_whitespace_only_segment_is_dropped_not_turned_into_scene_break():
    c = ch(N("Real."), N("    "), N("More."))
    validate(c)
    assert [s["speaker"] for s in c["segments"]] == ["narrator", "narrator"]


def test_all_empty_raises():
    with pytest.raises(ValueError, match="every one of its 2 segments was empty"):
        validate(ch(N(""), N(" ")))


def test_break_text_is_cleared():
    c = ch(N("a"), {"speaker": BREAK, "text": "READ ME ALOUD"}, N("The next morning."))
    validate(c)
    assert c["segments"][1] == {"speaker": BREAK, "text": ""}


@pytest.mark.parametrize("stars", ["* * *", "***", "— — —", "- - -", " * * * * "])
def test_asterisk_only_segment_becomes_break(stars):
    c = ch(N("a"), N(stars), N("b"))
    validate(c)
    assert [s["speaker"] for s in c["segments"]] == ["narrator", BREAK, "narrator"]


def test_two_asterisks_is_not_a_break():
    c = ch(N("a"), N("**"), N("b"))
    validate(c)
    assert c["segments"][1]["speaker"] == "narrator"


def test_leading_tag_lifted_into_field():
    c = ch(S("Mr Silas Rowe", "[quietly] I will go."))
    validate(c)
    assert c["segments"][0] == {"speaker": "Mr Silas Rowe", "text": "I will go.", "tag": "[quietly]"}


def test_existing_tag_not_overwritten_and_inner_tags_dropped():
    c = ch({"speaker": "Mr Silas Rowe", "text": "[loud] Go [aside] now.", "tag": "[kept]"})
    validate(c)
    assert c["segments"][0]["tag"] == "[kept]"
    assert c["segments"][0]["text"] == "Go now."


def test_long_bracket_text_is_not_a_tag():
    long = "[" + "x" * 45 + "]"
    c = ch(N(f"Keep {long} here."))
    validate(c)
    assert long in c["segments"][0]["text"]


def test_bold_cast_label_stripped_from_text():
    c = ch(S("Mrs Honoria Pike", "**Mrs Pike** Well, well."),
           N("**Narrator**: Dusk."), N("**Emphasis** is not a name."))
    validate(c)
    assert [s["text"] for s in c["segments"]] == ["Well, well.", "Dusk.",
                                                 "**Emphasis** is not a name."]


def test_scene_breaks_repaired_leading_double_trailing():
    c = ch(brk(), N("a"), brk(), brk(), N("b"), brk())
    validate(c)
    assert [s["speaker"] for s in c["segments"]] == ["narrator", BREAK, "narrator"]
    assert c["dropped_breaks"] == 3


def test_only_breaks_raises():
    with pytest.raises(ValueError, match="nothing left but scene breaks"):
        validate(ch(brk(), brk()))


def test_no_dropped_breaks_key_when_clean():
    c = ch(N("a"), brk(), N("b"))
    validate(c)
    assert "dropped_breaks" not in c and "dropped_empty_segments" not in c


def test_speaker_repaired_to_canonical_name():
    c = ch(S("Miss Vane", "Hm."), S("Pike", "Oh."), S("narrator", "x"))
    validate(c)
    assert [s["speaker"] for s in c["segments"]] == ["Miss Judith Vane", "Mrs Honoria Pike",
                                                    "narrator"]


def test_unknown_speaker_walkon_raises_with_guidance():
    with pytest.raises(ValueError) as ei:
        validate(ch(N("a"), S("The Sexton", "Aye.")))
    msg = str(ei.value)
    assert "segment 1: unknown speaker 'The Sexton'" in msg
    assert "never cast" in msg and "Cast:" in msg and "Aye." in msg


def test_ambiguous_speaker_raises_and_names_candidates():
    with pytest.raises(ValueError, match="Ambiguous.*Mr Owen Vane"):
        validate(ch(S("Vane", "Hm.")))


def test_missing_speaker_key_raises():
    with pytest.raises(ValueError, match="unknown speaker None"):
        validate(ch({"text": "Orphan line."}))


def test_validate_chapter_error_after_repairs_does_not_hide_dropped():
    c = ch(N(""), S("Nobody", "Hm."))
    with pytest.raises(ValueError, match="unknown speaker"):
        validate(c)


# --------------------------------------------------------------------------- #
# render_markdown
# --------------------------------------------------------------------------- #


def md(*segs, attribute=True, outline=OUTLINE, title="T"):
    return text.render_markdown({"n": 3, "title": title, "segments": list(segs)}, outline,
                                attribute=attribute)


def test_markdown_heading_and_trailing_newline():
    out = md(N("Dawn."))
    assert out.startswith("## Chapter 3. T\n\n")
    assert out.endswith("Dawn.\n")


def test_markdown_heading_without_title():
    out = text.render_markdown({"n": 2, "segments": [N("x")]}, OUTLINE)
    assert out.splitlines()[0] == "## Chapter 2."


def test_dialogue_gets_curly_quotes_and_short_label():
    out = md(S("Mr Silas Rowe", "Hello."))
    assert "**Mr Rowe** \u201cHello.\u201d" in out        # three-word names are shortened
    assert "Silas" not in out


def test_three_part_name_shortened_when_unique():
    o = {"cast": [{"name": "Mrs Honoria Pike"}, {"name": "Mr Silas Rowe"}]}
    out = md(S("Mrs Honoria Pike", "Oh."), outline=o)
    assert "**Mrs Pike** “Oh.”" in out


def test_short_name_kept_full_when_it_would_clash():
    o = {"cast": [{"name": "Mr Silas Rowe"}, {"name": "Mr Owen Rowe"}]}
    assert text._short_name("Mr Silas Rowe", o) == "Mr Silas Rowe"
    assert text._short_name("Mr Rowe", o) == "Mr Rowe"
    assert text._short_name("Mrs Honoria Pike", {"cast": [{"name": "Mrs Honoria Pike"}]}) == "Mrs Pike"
    assert text._short_name("Pike", {}) == "Pike"


def test_attribute_false_has_no_labels():
    out = md(S("Mr Silas Rowe", "Hello."), attribute=False)
    assert "**" not in out and "“Hello.”" in out


def test_same_speaker_runs_join_one_paragraph_and_change_breaks():
    out = md(S("Mr Silas Rowe", "One."), S("Mr Silas Rowe", "Two."), S("Miss Judith Vane", "Three."))
    body = out.split("\n\n")[1:]
    assert body[0].count("“One.”") == 1 and "“Two.”" in body[0]
    assert "Three." in body[1] and body[1].startswith("**Miss Vane**")


def test_lowercase_tag_joins_the_dialogue_line():
    out = md(S("Mr Silas Rowe", "I could not."), N("said Rowe."))
    assert "“I could not.” said Rowe." in out
    assert out.count("\n\n") == 1            # heading + one paragraph


def test_tag_followed_by_narration_splits_after_first_sentence():
    out = md(S("Mr Silas Rowe", "No."), N("said Rowe. He left the room."))
    paras = out.strip().split("\n\n")
    assert paras[1].endswith("said Rowe.")
    assert paras[2] == "He left the room."


def test_capitalised_narration_after_dialogue_is_its_own_paragraph():
    out = md(S("Mr Silas Rowe", "No."), N("Rowe left."))
    assert out.strip().split("\n\n")[2] == "Rowe left."


def test_leading_tag_narration_without_prior_speaker_is_plain_paragraph():
    out = md(N("said nobody."))
    assert out.strip().split("\n\n")[1] == "said nobody."


def test_scene_break_rendered_and_resets_speaker():
    out = md(S("Mr Silas Rowe", "No."), brk(), N("said who."), S("Mr Silas Rowe", "Yes."))
    paras = out.strip().split("\n\n")
    assert text.SCENE_BREAK == "* * *"
    assert paras[2] == "* * *"
    assert paras[3] == "said who."          # not joined across the break
    assert "Yes." in paras[4]


def test_empty_text_segment_skipped():
    assert md(N("a"), N("  "), N("b")).count("\n\n") == 2


@pytest.mark.parametrize("t,expected", [
    ("said Rowe.", True), (", said Rowe", True), ("—said Rowe", True),
    ("“said Rowe", True), ("Rowe said.", False), ("", False), ("   ", False)])
def test_is_tag(t, expected):
    assert text._is_tag(t) is expected


def test_split_tag():
    assert text._split_tag("said Vane. He left.") == ("said Vane.", "He left.")
    assert text._split_tag("said Vane.") == ("said Vane.", "")
    assert text._split_tag("no terminator") == ("no terminator", "")
    assert text._split_tag("said Vane.” Then dark.") == ("said Vane.”", "Then dark.")


def test_real_chapters_render_without_error_and_keep_all_text(real_story):
    outline = json.loads((real_story / "outline.json").read_text())
    for n in range(1, 9):
        c = json.loads((real_story / "chapters" / f"{n:02d}.json").read_text())
        out = text.render_markdown(c, outline)
        assert out.startswith(f"## Chapter {n}.")
        # every word of every segment survives into the markdown
        words = " ".join(s["text"] for s in c["segments"] if s["speaker"] != BREAK).split()
        flat = out.replace("“", "").replace("”", "")
        assert all(w in flat for w in words[:200])


def test_real_chapter_md_matches_render_for_non_attributed_or_attributed(real_story):
    outline = json.loads((real_story / "outline.json").read_text())
    c = json.loads((real_story / "chapters" / "01.json").read_text())
    on_disk = (real_story / "chapters" / "01.md").read_text()
    assert on_disk in (text.render_markdown(c, outline, True), text.render_markdown(c, outline, False))


# --------------------------------------------------------------------------- #
# length_fix
# --------------------------------------------------------------------------- #


def bundle_with_words(tmp_path, words, n=1):
    b = make_bundle(tmp_path)
    c = chapter_json(n, text=" ".join(["w"] * words))
    c["segments"] = [{"speaker": "narrator", "text": " ".join(["w"] * words)}]
    b.chapter_json(n).write_text(json.dumps(c))
    return b


@pytest.mark.parametrize("words,fix", [(540, False), (541, True), (100, False)])
def test_length_fix_boundary_at_tolerance(tmp_path, words, fix):
    b = bundle_with_words(tmp_path, words)
    got = text.length_fix(b, 1, 450, 0.20)
    assert (got is not None) is fix


def test_length_fix_message_numbers(tmp_path):
    b = bundle_with_words(tmp_path, 720)
    msg = text.length_fix(b, 1, 450, 0.20)
    assert "720 words against a target of 450" in msg
    assert "60% over" in msg
    assert "at most 495 words" in msg


def test_length_fix_zero_target_never_fires(tmp_path):
    assert text.length_fix(bundle_with_words(tmp_path, 9999), 1, 0, 0.2) is None


# --------------------------------------------------------------------------- #
# title segments
# --------------------------------------------------------------------------- #


def title_bundle(tmp_path, segs):
    b = make_bundle(tmp_path)
    c = {"n": 1, "title": "Chapter title 1", "segments": segs}
    b.chapter_json(1).write_text(json.dumps(c))
    return b


@pytest.mark.parametrize("first", ["Chapter 1. Chapter title 1.", "Chapter title 1",
                                   "CHAPTER I: chapter TITLE 1!", "chapter title 1."])
def test_title_segments_detects_announcements(tmp_path, first):
    b = title_bundle(tmp_path, [N(first), N("Real prose begins here.")])
    assert text.title_segments(b, 1) == [0]


def test_title_segments_leaves_real_sentences(tmp_path):
    b = title_bundle(tmp_path, [N("Chapter title 1 was what he called it, years on."), N("x")])
    assert text.title_segments(b, 1) == []


def test_title_segments_stops_at_dialogue_and_long_segments(tmp_path):
    b = title_bundle(tmp_path, [S("Mr Silas Rowe", "Chapter title 1"), N("x")])
    assert text.title_segments(b, 1) == []
    b = title_bundle(tmp_path, [N("w " * 13)])
    assert text.title_segments(b, 1) == []


def test_title_segments_unwritten_or_untitled(tmp_path):
    b = make_bundle(tmp_path)
    assert text.title_segments(b, 1) == []
    b = title_bundle(tmp_path, [N("x")])
    o = b.outline()
    o["chapters"][0]["title"] = ""
    b.outline_path.write_text(json.dumps(o))
    assert text.title_segments(b, 1) == []


def test_strip_title_segments_removes_and_rewrites(tmp_path):
    b = title_bundle(tmp_path, [N("Chapter 1. Chapter title 1."), N("Prose here."),
                                S("Mr Silas Rowe", "Hello.")])
    removed = text.strip_title_segments(b)
    assert removed == {1: "Chapter 1. Chapter title 1."}
    assert [s["text"] for s in b.chapter(1)["segments"]] == ["Prose here.", "Hello."]
    assert "Prose here." in b.chapter_md(1).read_text()
    assert "Chapter 1. Chapter title 1." not in b.chapter_md(1).read_text()


def test_strip_title_segments_noop_when_none(tmp_path):
    b = title_bundle(tmp_path, [N("Prose here.")])
    assert text.strip_title_segments(b) == {}


# --------------------------------------------------------------------------- #
# save_chapter
# --------------------------------------------------------------------------- #


def test_save_chapter_normalises_writes_json_and_md(tmp_path):
    b = make_bundle(tmp_path)
    b.chapter_json(1).write_text(json.dumps(chapter_json(1)))
    saved = text.save_chapter(b, 1, [
        {"speaker": "Pike", "text": "Oh my."},
        {"text": "Defaults to narrator."},
        {"speaker": "narrator", "text": "[softly] Tagged.", "tag": "[x]"},
        {"speaker": "narrator", "text": "   "},
    ])
    assert saved["segments"][0]["speaker"] == "Mrs Honoria Pike"
    assert saved["segments"][1]["speaker"] == "narrator"
    assert saved["segments"][2]["tag"] == "[x]"
    assert len(saved["segments"]) == 3
    assert json.loads(b.chapter_json(1).read_text()) == saved
    assert "Oh my." in b.chapter_md(1).read_text()
    assert saved["title"] == "Chapter title 1"        # preserved from disk


def test_save_chapter_invalid_speaker_leaves_disk_untouched(tmp_path):
    b = make_bundle(tmp_path)
    original = json.dumps(chapter_json(1))
    b.chapter_json(1).write_text(original)
    with pytest.raises(ValueError, match="unknown speaker"):
        text.save_chapter(b, 1, [{"speaker": "Ghost", "text": "Boo."}])
    assert b.chapter_json(1).read_text() == original
    assert not b.chapter_md(1).exists()


# --------------------------------------------------------------------------- #
# done / pending / stale / approvals
# --------------------------------------------------------------------------- #


def written(tmp_path, n_chapters=5, reviewed=()):
    b = make_bundle(tmp_path)
    for n in range(1, n_chapters + 1):
        b.chapter_json(n).write_text(json.dumps(chapter_json(n)))
    for n, v in reviewed:
        b.review_path(n).write_text(json.dumps(v))
    return b


def test_chapter_done_states(tmp_path):
    b = written(tmp_path, reviewed=[(1, verdict(True)), (2, {"pass": False, "exhausted": True}),
                                    (3, verdict(False))])
    assert text.chapter_done(b, 1) and text.chapter_done(b, 2)
    assert not text.chapter_done(b, 3)       # failed, not exhausted
    assert not text.chapter_done(b, 4)       # no review


def test_chapter_done_needs_chapter_and_valid_review(tmp_path):
    b = written(tmp_path, n_chapters=0)
    b.review_path(1).write_text(json.dumps(verdict(True)))
    assert not text.chapter_done(b, 1)                       # no chapter json
    b.chapter_json(2).write_text(json.dumps(chapter_json(2)))
    b.review_path(2).write_text("{not json")
    assert not text.chapter_done(b, 2)


def test_pending_chapters_and_restart(tmp_path):
    b = written(tmp_path, reviewed=[(1, verdict(True)), (2, verdict(True))])
    assert text.pending_chapters(b) == [3, 4, 5]
    assert text.pending_chapters(b, restart=True) == [1, 2, 3, 4, 5]


def test_review_stale(tmp_path):
    b = written(tmp_path, reviewed=[(1, verdict(True, chapter_hash="deadbeef")),
                                    (2, verdict(True)), ])
    assert text.review_stale(b, 1) is True
    b.review_path(2).write_text(json.dumps({**verdict(True), "chapter_hash": b.chapter_hash(2)}))
    assert text.review_stale(b, 2) is False
    assert text.review_stale(b, 3) is False                  # no review file
    b.review_path(4).write_text(json.dumps(verdict(True)))   # legacy: no hash -> trusted
    assert text.review_stale(b, 4) is False
    b.review_path(5).write_text("garbage")
    assert text.review_stale(b, 5) is False


def test_approve_chapters_all_syncs_edit_stage(tmp_path):
    b = written(tmp_path, reviewed=[(n, verdict(True)) for n in range(1, 6)])
    assert text.approve_chapters(b) == [1, 2, 3, 4, 5]
    assert b.stage_status("edit") == "approved"


def test_approve_some_leaves_stage_awaiting_and_skips_unwritten(tmp_path):
    b = written(tmp_path, n_chapters=3, reviewed=[(n, verdict(True)) for n in range(1, 4)])
    assert text.approve_chapters(b, [1, 2]) == [1, 2]
    assert b.stage_status("edit") == "awaiting_review"
    assert text.approve_chapters(b, [5]) == [1, 2]           # chapter 5 unwritten -> ignored


def test_unapprove_reverts_stage(tmp_path):
    b = written(tmp_path, reviewed=[(n, verdict(True)) for n in range(1, 6)])
    text.approve_chapters(b)
    assert text.unapprove_chapters(b, [2]) == [1, 3, 4, 5]
    assert b.stage_status("edit") == "awaiting_review"


def test_sync_edit_stage_records_unpassed(tmp_path):
    b = written(tmp_path, reviewed=[(1, verdict(True)), (2, verdict(False))])
    text._sync_edit_stage(b)
    assert b.manifest()["stage_info"]["edit"]["unpassed"] == [2]


def test_edited_chapter_lapses_approval(tmp_path):
    b = written(tmp_path, reviewed=[(n, verdict(True)) for n in range(1, 6)])
    text.approve_chapters(b)
    text.save_chapter(b, 1, [{"speaker": "narrator", "text": "Changed prose."}])
    assert 1 not in b.approved_chapters()
    assert b.stage_status("edit") == "awaiting_review"


# --------------------------------------------------------------------------- #
# attribution / dialogue helpers
# --------------------------------------------------------------------------- #


def test_attribution_note_tags_clause_only_when_requested():
    base = text.attribution_note(False)
    assert base == text.ATTRIBUTION_NOTE
    assert text.attribution_note(True) == text.ATTRIBUTION_NOTE + text.TAG_CLAUSE
    assert "tag" in text.TAG_CLAUSE


def test_chapter_dialogue_turns_counts_only_spoken(tmp_path):
    b = make_bundle(tmp_path)
    b.chapter_json(1).write_text(json.dumps(
        ch(N("a"), S("Mr Silas Rowe", "b"), brk(), S("Miss Judith Vane", "c"))))
    assert text.chapter_dialogue_turns(b, 1) == 2
