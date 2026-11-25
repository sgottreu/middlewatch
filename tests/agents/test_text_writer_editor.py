"""agents/text.py writer/editor side: write_chapter, review_chapter, draft_and_edit,
run_text_stages, redraft_chapter, estimates."""

from __future__ import annotations

import copy
import json

import pytest

from story_pipeline import llm
from story_pipeline.agents import text
from story_pipeline.bundle import Bundle

from .helpers import cfg, chapter_json, make_bundle, mini_outline, verdict

LONG = " ".join(["word"] * 700)           # 700 words > 450 * 1.2


def q_json(scripted, *items):
    scripted.json_queue.extend(copy.deepcopy(list(items)))


@pytest.fixture
def bundle(repo_root):
    return make_bundle(repo_root)


def fast_cfg(passes=2):
    c = cfg()
    c["max_edit_passes"] = passes
    return c


# --------------------------------------------------------------------------- #
# internal helpers
# --------------------------------------------------------------------------- #


def test_length_of_prefers_pin_then_outline_then_default(repo_root):
    b = make_bundle(repo_root, minutes=30)
    assert text._length_of(b)["minutes"] == 30
    b.write_manifest({**b.manifest(), "pinned": {}})
    assert text._length_of(b)["minutes"] == 15                       # outline has no length_min
    b.outline_path.write_text(json.dumps(b.outline() | {"length_min": 30}))
    assert text._length_of(b)["minutes"] == 30


def test_genre_of_and_bible_of(bundle):
    assert text._genre_of(bundle).name == "regency"
    assert text._bible_of(bundle, cfg()) is None


def test_context_has_outline_and_only_prior_chapters(bundle):
    for n in (1, 2, 4):
        bundle.chapter_json(n).write_text(json.dumps(chapter_json(n, text=f"UNIQUE-{n}")))
    ctx = text._context(bundle, 3)
    assert ctx.startswith("# Outline")
    assert "# Chapters so far" in ctx
    assert "UNIQUE-1" in ctx and "UNIQUE-2" in ctx and "UNIQUE-4" not in ctx
    assert "## Chapter 1" in ctx


def test_context_first_chapter_has_no_chapters_so_far(bundle):
    assert "Chapters so far" not in text._context(bundle, 1)


# --------------------------------------------------------------------------- #
# write_chapter
# --------------------------------------------------------------------------- #


def test_write_chapter_writes_json_md_and_returns_chapter(scripted, bundle):
    q_json(scripted, chapter_json(1) | {"n": 99})
    ch = text.write_chapter(cfg(), bundle, 1)
    assert ch["n"] == 1                                    # forced to the requested number
    assert bundle.chapter(1) == ch
    assert "## Chapter 1. Chapter title 1" in bundle.chapter_md(1).read_text()
    assert not bundle.rejected_path(1).exists()
    assert bundle.manifest()["spend"] == {"anthropic_input_tokens": 100,
                                          "anthropic_output_tokens": 50}


def test_write_chapter_prompt_assembly(scripted, bundle):
    q_json(scripted, chapter_json(2))
    text.write_chapter(cfg(), bundle, 2)
    call = scripted.json_calls[0]
    assert call["model"] == cfg()["models"]["writer"] and call["max_tokens"] == 8000
    assert call["system"].startswith(text.SYSTEM)
    assert "{" + "target_words" + "}" not in call["system"]
    # band is target +/- tolerance, stated in the prompt
    assert "360" in call["system"] and "540" in call["system"]
    user = call["user"]
    assert "# Write chapter 2: Chapter title 2" in user
    tail = user.split("Beats to cover:")[1]
    assert "- Beat 2.0" in tail and "- Beat 2.2" in tail and "Beat 3.0" not in tail
    assert "This is a revision" not in user


def test_write_chapter_band_follows_tolerance_and_length_pin(scripted, repo_root):
    b = make_bundle(repo_root, outline=mini_outline(30), minutes=30)
    c = cfg()
    c["length_tolerance"] = 0.5
    q_json(scripted, chapter_json(1))
    text.write_chapter(c, b, 1)
    sysm = scripted.json_calls[0]["system"]
    assert "280" in sysm and "840" in sysm                 # 560 * 0.5 / 1.5
    assert "4,500 words total" in sysm


def test_write_chapter_context_includes_earlier_chapters(scripted, bundle):
    q_json(scripted, chapter_json(1, text="FIRST-CHAPTER-PROSE"), chapter_json(2))
    text.write_chapter(cfg(), bundle, 1)
    text.write_chapter(cfg(), bundle, 2)
    assert "FIRST-CHAPTER-PROSE" in scripted.json_calls[1]["user"]
    assert "FIRST-CHAPTER-PROSE" not in scripted.json_calls[0]["user"]


def test_write_chapter_repairs_speaker_before_saving(scripted, bundle):
    c = chapter_json(1)
    c["segments"][1]["speaker"] = "Rowe"
    q_json(scripted, c)
    text.write_chapter(cfg(), bundle, 1)
    assert bundle.chapter(1)["segments"][1]["speaker"] == "Mr Silas Rowe"


def test_write_chapter_revision_with_fixes(scripted, bundle):
    bundle.chapter_json(1).write_text(json.dumps(chapter_json(1, text="OLD-DRAFT-TEXT")))
    q_json(scripted, chapter_json(1, text="new"))
    text.write_chapter(cfg(), bundle, 1, fixes=["Cut the adverbs.", "Fix the ending."])
    user = scripted.json_calls[0]["user"]
    assert "# This is a revision" in user and "OLD-DRAFT-TEXT" in user
    assert "The editor requires these fixes. Apply all of them and change nothing" in user
    assert "- Cut the adverbs.\n- Fix the ending." in user
    assert "note from the person" not in user
    assert bundle.chapter(1)["segments"][0]["text"] == "new"


def test_write_chapter_note_only_and_note_plus_fixes(scripted, bundle):
    bundle.chapter_json(1).write_text(json.dumps(chapter_json(1)))
    q_json(scripted, chapter_json(1), chapter_json(1))
    text.write_chapter(cfg(), bundle, 1, note="  Make Rowe kinder.  ")
    user = scripted.json_calls[0]["user"]
    assert "A note from the person commissioning this story" in user
    assert user.rstrip().endswith("Make Rowe kinder.")
    assert "editor requires" not in user
    text.write_chapter(cfg(), bundle, 1, fixes=["F1"], note="N1")
    both = scripted.json_calls[1]["user"]
    assert "without undoing the change the note above asks for" in both
    assert both.index("N1") < both.index("F1")             # note ranked above fixes


def test_write_chapter_invalid_keeps_rejected_draft_and_no_chapter(scripted, bundle):
    bad = chapter_json(1)
    bad["segments"].append({"speaker": "The Sexton", "text": "Aye."})
    q_json(scripted, bad)
    with pytest.raises(ValueError, match="unknown speaker 'The Sexton'"):
        text.write_chapter(cfg(), bundle, 1)
    assert not bundle.chapter_json(1).exists() and not bundle.chapter_md(1).exists()
    kept = json.loads(bundle.rejected_path(1).read_text())
    assert kept["segments"][-1]["speaker"] == "The Sexton"
    assert bundle.drafts(1) == []                            # rejected is not a numbered attempt


def test_write_chapter_success_clears_stale_rejected_file(scripted, bundle):
    bundle.rejected_path(1).write_text("{}")
    q_json(scripted, chapter_json(1))
    text.write_chapter(cfg(), bundle, 1)
    assert not bundle.rejected_path(1).exists()


def test_write_chapter_no_segments_raises_and_keeps_draft(scripted, bundle):
    q_json(scripted, {"title": "T", "segments": []})
    with pytest.raises(ValueError, match="no segments"):
        text.write_chapter(cfg(), bundle, 1)
    assert bundle.rejected_path(1).exists()


@pytest.mark.parametrize("exc", [llm.TruncatedReply("cut"), ValueError("no JSON object"),
                                 llm.ConfigurationError("key")])
def test_write_chapter_model_errors_write_nothing(scripted, bundle, exc):
    scripted.json_queue.append(exc)
    with pytest.raises(type(exc)):
        text.write_chapter(cfg(), bundle, 1)
    assert not bundle.chapter_json(1).exists() and not bundle.rejected_path(1).exists()


def test_write_chapter_unknown_chapter_number_fails_before_model_call(scripted, bundle):
    with pytest.raises((StopIteration, ValueError, KeyError)):
        text.write_chapter(cfg(), bundle, 42)
    assert scripted.json_calls == []


def test_write_chapter_with_real_story_and_bible(scripted, real_story, real_bibles):
    b = Bundle.open(real_story)
    original = json.loads((real_story / "chapters" / "02.json").read_text())
    q_json(scripted, original)
    ch = text.write_chapter(cfg(), b, 2)
    assert ch["segments"] == original["segments"]            # a real, accepted chapter is a fixed point
    sysm = scripted.json_calls[0]["system"]
    assert "Series bible: Middle Watch: Amberlight" in sysm
    assert "448" in sysm and "672" in sysm                   # 30-minute pin: 560 +/- 20%
    assert "Story ideas" not in sysm
    assert "## Chapter 1" in scripted.json_calls[0]["user"]  # context carries chapter 1
    assert b.chapter_md(2).read_text() == text.render_markdown(ch, b.outline())


# --------------------------------------------------------------------------- #
# review_chapter
# --------------------------------------------------------------------------- #


@pytest.fixture
def written(bundle):
    bundle.chapter_json(1).write_text(json.dumps(chapter_json(1)))
    return bundle


def test_review_chapter_stores_verdict_with_lint_and_hash(scripted, written):
    q_json(scripted, verdict(True, score=8))
    r = text.review_chapter(cfg(), written, 1)
    assert r["n"] == 1 and r["pass"] is True and r["score"] == 8
    assert r["lint"]["total"] == 0 and r["lint"]["errors"] == 0
    assert r["chapter_hash"] == written.chapter_hash(1)
    assert json.loads(written.review_path(1).read_text()) == r


def test_review_chapter_prompt_assembly(scripted, written):
    q_json(scripted, verdict(True))
    text.review_chapter(cfg(), written, 1)
    call = scripted.json_calls[0]
    assert call["model"] == cfg()["models"]["editor"] and call["max_tokens"] == 16000
    assert call["system"].startswith(text.SYSTEM)
    assert "{" + "tropes" + "}" not in call["system"]
    assert "Enemies of manner, The meddling matchmaker" in call["system"]
    user = call["user"]
    assert "# Beats chapter 1 was required to cover" in user and "- Beat 1.0" in user
    assert "# The draft" in user and "I will go up myself." in user
    assert user.endswith("Judge it.")


def test_lint_error_overrides_passing_verdict(scripted, bundle):
    c = chapter_json(1, text="The room was a vibrant tapestry of grey.")
    bundle.chapter_json(1).write_text(json.dumps(c))
    q_json(scripted, verdict(True))
    r = text.review_chapter(cfg(), bundle, 1)
    assert r["pass"] is False
    assert r["lint"]["errors"] >= 1 and r["lint"]["by_rule"]["ai_vocab"] == 2


def test_lint_warnings_alone_do_not_fail(scripted, bundle):
    c = chapter_json(1, text="He stopped — and looked back.")
    bundle.chapter_json(1).write_text(json.dumps(c))
    q_json(scripted, verdict(True))
    r = text.review_chapter(cfg(), bundle, 1)
    assert r["pass"] is True and r["lint"]["total"] >= 1 and r["lint"]["errors"] == 0


def test_boundary_breach_forces_fail(scripted, written):
    q_json(scripted, verdict(True) | {"boundary_breaches": ["something"]})
    assert text.review_chapter(cfg(), written, 1)["pass"] is False


def test_editor_fail_stays_fail_when_lint_clean(scripted, written):
    q_json(scripted, verdict(False, fixes=["x"]))
    assert text.review_chapter(cfg(), written, 1)["pass"] is False


def test_review_chapter_missing_chapter_file_raises(scripted, bundle):
    q_json(scripted, verdict(True))
    with pytest.raises(FileNotFoundError):
        text.review_chapter(cfg(), bundle, 1)


def test_review_chapter_model_error_writes_no_review(scripted, written):
    scripted.json_queue.append(llm.TruncatedReply("cut"))
    with pytest.raises(llm.TruncatedReply):
        text.review_chapter(cfg(), written, 1)
    assert not written.review_path(1).exists()


def test_review_chapter_real_story(scripted, real_story, real_bibles):
    b = Bundle.open(real_story)
    stored = json.loads((real_story / "reviews" / "03.json").read_text())
    model_part = {k: v for k, v in stored.items() if k not in ("lint", "chapter_hash")}
    q_json(scripted, model_part)
    r = text.review_chapter(cfg(), b, 3)
    assert r["pass"] == stored["pass"]
    assert r["chapter_hash"] == b.chapter_hash(3) == stored["chapter_hash"]
    assert r["lint"]["errors"] == stored["lint"]["errors"] == 0
    assert "Series bible" in scripted.json_calls[0]["system"]


# --------------------------------------------------------------------------- #
# draft_and_edit
# --------------------------------------------------------------------------- #


def test_draft_and_edit_passes_first_time(scripted, bundle, capsys):
    q_json(scripted, chapter_json(1), verdict(True))
    seen = []
    r = text.draft_and_edit(fast_cfg(), bundle, 1, on_pass=lambda a, rv: seen.append((a, rv["pass"])))
    assert r["pass"] is True and seen == [(0, True)]
    assert bundle.drafts(1) == [0]
    assert bundle.draft_review_path(1, 0).exists()
    assert len(scripted.json_calls) == 2
    out = capsys.readouterr().out
    assert "chapter 1: drafting..." in out and "chapter 1: passed" in out


def test_draft_and_edit_verbose_false_is_silent(scripted, bundle, capsys):
    q_json(scripted, chapter_json(1), verdict(True))
    text.draft_and_edit(fast_cfg(), bundle, 1, verbose=False)
    assert capsys.readouterr().out == ""


def test_draft_and_edit_revision_loop_feeds_editor_fixes_back(scripted, bundle):
    q_json(scripted,
           chapter_json(1, text="draft one"), verdict(False, fixes=["Cut the adverbs."]),
           chapter_json(1, text="draft two"), verdict(True))
    r = text.draft_and_edit(fast_cfg(), bundle, 1, verbose=False)
    assert r["pass"] is True
    second_write = scripted.json_calls[2]["user"]
    assert "This is a revision" in second_write and "draft one" in second_write
    assert "- Cut the adverbs." in second_write
    assert bundle.drafts(1) == [0, 1]
    assert json.loads(bundle.draft_path(1, 0).read_text())["segments"][0]["text"] == "draft one"
    assert bundle.chapter(1)["segments"][0]["text"] == "draft two"
    assert json.loads(bundle.draft_review_path(1, 0).read_text())["pass"] is False
    assert json.loads(bundle.review_path(1).read_text())["pass"] is True


def test_draft_and_edit_lint_errors_become_fixes_even_if_editor_passed(scripted, bundle):
    q_json(scripted,
           chapter_json(1, text="A vibrant tapestry of grey rooms."), verdict(True),
           chapter_json(1, text="Plain grey rooms."), verdict(True))
    r = text.draft_and_edit(fast_cfg(), bundle, 1, verbose=False)
    assert r["pass"] is True and r["lint"]["errors"] == 0
    fixes_section = scripted.json_calls[2]["user"].split("The editor requires these fixes")[1]
    assert "vibrant" in fixes_section and "AI-signature word" in fixes_section


def test_draft_and_edit_exhaustion_keeps_last_draft_and_marks_review(scripted, bundle):
    q_json(scripted, *[x for i in range(2) for x in (chapter_json(1, text=f"d{i}"),
                                                      verdict(False, fixes=["still bad"]))])
    r = text.draft_and_edit(fast_cfg(passes=1), bundle, 1, verbose=False)
    assert r["exhausted"] is True and r["pass"] is False
    assert json.loads(bundle.review_path(1).read_text())["exhausted"] is True
    assert bundle.chapter(1)["segments"][0]["text"] == "d1"
    assert bundle.drafts(1) == [0, 1] and len(scripted.json_calls) == 4
    assert text.chapter_done(bundle, 1)


def test_draft_and_edit_zero_revisions_allowed(scripted, bundle):
    q_json(scripted, chapter_json(1), verdict(False, fixes=["x"]))
    r = text.draft_and_edit(fast_cfg(passes=0), bundle, 1, verbose=False)
    assert r["exhausted"] is True and len(scripted.json_calls) == 2


def test_draft_and_edit_fail_without_fixes_stops_early(scripted, bundle):
    q_json(scripted, chapter_json(1), verdict(False, fixes=[]))
    r = text.draft_and_edit(fast_cfg(passes=3), bundle, 1, verbose=False)
    assert r["exhausted"] is True
    assert len(scripted.json_calls) == 2                    # no pointless second write


def test_draft_and_edit_overlong_chapter_sent_back_to_trim(scripted, bundle):
    q_json(scripted,
           chapter_json(1, text=LONG), verdict(True),
           chapter_json(1, text="Short enough now."), verdict(True))
    r = text.draft_and_edit(fast_cfg(), bundle, 1, verbose=False)
    assert r["pass"] is True
    first_review = json.loads(bundle.draft_review_path(1, 0).read_text())
    assert first_review["pass"] is False
    assert first_review["length_over"] == 707 and first_review["length_target"] == 450
    second_write = scripted.json_calls[2]["user"]
    assert "LENGTH: this chapter is 707 words against a target of 450" in second_write
    fixes = second_write.split("The editor requires these fixes")[1]
    assert fixes.split("\n- ")[1].startswith("LENGTH")      # trim instruction comes first


def test_draft_and_edit_overlong_on_last_pass_is_recorded_but_kept(scripted, bundle):
    q_json(scripted, chapter_json(1, text=LONG), verdict(True))
    r = text.draft_and_edit(fast_cfg(passes=0), bundle, 1, verbose=False)
    assert r["pass"] is True and r["length_over"] == bundle.chapter_words(1) >= 700
    assert json.loads(bundle.review_path(1).read_text())["length_over"] == r["length_over"]
    assert len(scripted.json_calls) == 2


def test_draft_and_edit_respects_tolerance_config(scripted, bundle):
    c = fast_cfg()
    c["length_tolerance"] = 5.0                            # absurdly loose
    q_json(scripted, chapter_json(1, text=LONG), verdict(True))
    r = text.draft_and_edit(c, bundle, 1, verbose=False)
    assert "length_over" not in r and r["pass"] is True


def test_draft_and_edit_note_redrafts_existing_chapter_and_appends_drafts(scripted, bundle):
    bundle.chapter_json(1).write_text(json.dumps(chapter_json(1, text="ORIGINAL")))
    bundle.draft_path(1, 0).write_text("{}")
    bundle.draft_path(1, 1).write_text("{}")
    q_json(scripted,
           chapter_json(1, text="v1"), verdict(False, fixes=["fix me"]),
           chapter_json(1, text="v2"), verdict(True))
    r = text.draft_and_edit(fast_cfg(), bundle, 1, verbose=False, note="  Warmer.  ")
    assert r["writer_note"] == "Warmer." and r["pass"] is True
    assert bundle.drafts(1) == [0, 1, 2, 3]                 # original history preserved
    for call in (scripted.json_calls[0], scripted.json_calls[2]):
        assert "Warmer." in call["user"]                     # note rides on every pass
    assert "without undoing the change" in scripted.json_calls[2]["user"]
    assert json.loads(bundle.review_path(1).read_text())["writer_note"] == "Warmer."
    assert json.loads(bundle.draft_path(1, 0).read_text()) == {}


def test_draft_and_edit_verbose_labels(scripted, bundle, capsys):
    bundle.chapter_json(1).write_text(json.dumps(chapter_json(1)))
    q_json(scripted, chapter_json(1), verdict(False, fixes=["f"]), chapter_json(1), verdict(True))
    text.draft_and_edit(fast_cfg(), bundle, 1, note="n")
    out = capsys.readouterr().out
    assert "redrafting from your note" in out and "revision 1" in out
    assert "1 fix(es) required" in out


def test_draft_and_edit_propagates_invalid_chapter_and_keeps_rejected(scripted, bundle):
    bad = chapter_json(1)
    bad["segments"][1]["speaker"] = "Stranger"
    q_json(scripted, bad)
    with pytest.raises(ValueError, match="unknown speaker"):
        text.draft_and_edit(fast_cfg(), bundle, 1, verbose=False)
    assert bundle.rejected_path(1).exists() and not bundle.review_path(1).exists()


# --------------------------------------------------------------------------- #
# run_text_stages
# --------------------------------------------------------------------------- #


def script_all(scripted, chapters=(1, 2, 3, 4, 5), ok=True):
    for n in chapters:
        q_json(scripted, chapter_json(n), verdict(ok, fixes=[] if ok else []))


def test_run_text_stages_writes_every_chapter(scripted, bundle):
    script_all(scripted)
    calls = []
    reviews = text.run_text_stages(fast_cfg(), bundle, verbose=False,
                                   on_chapter=lambda n, t, r: calls.append((n, t, r["pass"])))
    assert sorted(reviews) == [1, 2, 3, 4, 5]
    assert calls == [(n, 5, True) for n in range(1, 6)]
    assert bundle.stage_status("write") == "done"
    assert bundle.stage_status("edit") == "awaiting_review"
    assert bundle.manifest()["stage_info"]["edit"]["unpassed"] == []
    assert all(bundle.chapter_md(n).exists() for n in range(1, 6))


def test_run_text_stages_resumes_finished_chapters(scripted, bundle, capsys):
    for n in (1, 2):
        bundle.chapter_json(n).write_text(json.dumps(chapter_json(n)))
        bundle.review_path(n).write_text(json.dumps(verdict(True)))
    script_all(scripted, (3, 4, 5))
    calls = []
    reviews = text.run_text_stages(fast_cfg(), bundle, on_chapter=lambda n, t, r: calls.append(n))
    assert len(scripted.json_calls) == 6                      # 3 chapters x (write + review)
    assert calls == [1, 2, 3, 4, 5] and reviews[1]["pass"] is True
    assert "resuming: chapters 1, 2 already written, 3 to go" in capsys.readouterr().out


def test_run_text_stages_resume_singular_wording(scripted, bundle, capsys):
    for n in (1, 2, 3, 4):
        bundle.chapter_json(n).write_text(json.dumps(chapter_json(n)))
        bundle.review_path(n).write_text(json.dumps(verdict(True)))
    script_all(scripted, (5,))
    text.run_text_stages(fast_cfg(), bundle)
    assert "chapters 1, 2, 3, 4 already written, 1 to go" in capsys.readouterr().out


def test_run_text_stages_restart_rewrites_everything(scripted, bundle):
    for n in range(1, 6):
        bundle.chapter_json(n).write_text(json.dumps(chapter_json(n)))
        bundle.review_path(n).write_text(json.dumps(verdict(True)))
    script_all(scripted)
    text.run_text_stages(fast_cfg(), bundle, verbose=False, restart=True)
    assert len(scripted.json_calls) == 10


def test_run_text_stages_records_unpassed_chapters(scripted, bundle):
    script_all(scripted, (1, 2, 3, 4, 5), ok=False)           # every chapter fails w/o fixes
    reviews = text.run_text_stages(fast_cfg(), bundle, verbose=False)
    assert all(r["exhausted"] for r in reviews.values())
    assert bundle.manifest()["stage_info"]["edit"]["unpassed"] == [1, 2, 3, 4, 5]
    assert bundle.stage_status("edit") == "awaiting_review"


def test_run_text_stages_error_leaves_write_running(scripted, bundle):
    q_json(scripted, chapter_json(1), verdict(True))
    scripted.json_queue.append(llm.TruncatedReply("cut"))
    with pytest.raises(llm.TruncatedReply):
        text.run_text_stages(fast_cfg(), bundle, verbose=False)
    assert bundle.stage_status("write") == "running"
    assert text.chapter_done(bundle, 1) and not text.chapter_done(bundle, 2)
    # and a rerun resumes at chapter 2
    script_all(scripted, (2, 3, 4, 5))
    text.run_text_stages(fast_cfg(), bundle, verbose=False)
    assert bundle.stage_status("write") == "done"


# --------------------------------------------------------------------------- #
# redraft_chapter
# --------------------------------------------------------------------------- #


def test_redraft_requires_note_and_written_chapter(scripted, written):
    for bad in ("", "   ", None):
        with pytest.raises(ValueError, match="cannot be empty"):
            text.redraft_chapter(cfg(), written, 1, bad)
    with pytest.raises(ValueError, match="chapter 3 has not been written yet"):
        text.redraft_chapter(cfg(), written, 3, "note")
    assert scripted.json_calls == []


def test_redraft_chapter_runs_loop_and_resyncs_edit_stage(scripted, bundle):
    for n in range(1, 6):
        bundle.chapter_json(n).write_text(json.dumps(chapter_json(n)))
        bundle.review_path(n).write_text(json.dumps(verdict(True)))
    text.approve_chapters(bundle)
    assert bundle.stage_status("edit") == "approved"
    q_json(scripted, chapter_json(2, text="Rewritten."), verdict(True))
    seen = []
    r = text.redraft_chapter(fast_cfg(), bundle, 2, " Be kinder ", verbose=False,
                             on_pass=lambda a, rv: seen.append(a))
    assert r["writer_note"] == "Be kinder" and seen == [0]
    assert 2 not in bundle.approved_chapters()               # approval lapsed with the hash
    assert bundle.stage_status("edit") == "awaiting_review"
    assert bundle.chapter(2)["segments"][0]["text"] == "Rewritten."


# --------------------------------------------------------------------------- #
# estimates
# --------------------------------------------------------------------------- #


def test_anthropic_rates_reads_ledger_card(repo_root):
    rates = text._anthropic_rates()
    assert all({"input_tokens", "output_tokens"} <= set(v) for v in rates.values())
    assert cfg()["models"]["writer"] in rates and cfg()["models"]["editor"] in rates


def test_estimate_write_full_story(bundle):
    est = text.estimate_write(cfg(), bundle)
    assert est["known"] is True
    assert (est["chapters"], est["to_write"], est["already_written"]) == (5, 5, 0)
    assert est["low"] > 0 and abs(est["high"] - est["low"] * 2) <= 0.011
    assert est["writer"] == cfg()["models"]["writer"] and est["max_edit_passes"] == 2


def test_estimate_write_resume_is_cheaper_but_not_proportional(bundle):
    full = text.estimate_write(cfg(), bundle)["low"]
    for n in (1, 2):
        bundle.chapter_json(n).write_text(json.dumps(chapter_json(n)))
        bundle.review_path(n).write_text(json.dumps(verdict(True)))
    part = text.estimate_write(cfg(), bundle)
    assert (part["to_write"], part["already_written"]) == (3, 2)
    assert 0 < part["low"] < full
    assert part["low"] > full * 3 / 5 * 0.99                  # later chapters carry more context
    assert text.estimate_write(cfg(), bundle, restart=True)["low"] == full


def test_estimate_write_nothing_left_costs_zero(bundle):
    for n in range(1, 6):
        bundle.chapter_json(n).write_text(json.dumps(chapter_json(n)))
        bundle.review_path(n).write_text(json.dumps(verdict(True)))
    est = text.estimate_write(cfg(), bundle)
    assert est["known"] is True and est["low"] == est["high"] == 0.0 and est["to_write"] == 0


def test_estimate_write_no_chapters_unknown(bundle):
    bundle.outline_path.write_text(json.dumps(mini_outline() | {"chapters": []}))
    assert text.estimate_write(cfg(), bundle) == {"known": False}


def test_estimate_write_unpriced_model_is_unknown_but_keeps_counts(bundle):
    c = cfg()
    c["models"]["writer"] = "model-not-in-rates"
    est = text.estimate_write(c, bundle)
    assert est["known"] is False and est["chapters"] == 5 and est["to_write"] == 5


def test_estimate_write_missing_rates_file_is_unknown(bundle, repo_root):
    (repo_root / "ledger" / "rates.json").unlink()
    assert text.estimate_write(cfg(), bundle)["known"] is False


def test_estimate_redraft(bundle):
    assert text.estimate_redraft(cfg(), bundle, 1) == {"known": False}      # unwritten
    for n in range(1, 4):
        bundle.chapter_json(n).write_text(json.dumps(chapter_json(n, text=" ".join(["w"] * 300))))
    est = text.estimate_redraft(cfg(), bundle, 3)
    assert est["known"] is True and est["passes"] == 3
    assert est["low"] > 0 and abs(est["high"] - est["low"] * 3) <= 0.02
    assert text.estimate_redraft(cfg(), bundle, 3)["low"] > text.estimate_redraft(cfg(), bundle, 1)["low"]
    assert text.estimate_redraft(cfg(), bundle, 9) == {"known": False}      # not in outline


def test_estimate_redraft_unpriced_model(bundle):
    bundle.chapter_json(1).write_text(json.dumps(chapter_json(1)))
    c = cfg()
    c["models"]["editor"] = "nope"
    est = text.estimate_redraft(c, bundle, 1)
    assert est["known"] is False and est["passes"] == 3 and est["editor"] == "nope"
