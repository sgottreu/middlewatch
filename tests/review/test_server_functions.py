"""server.py's module-level functions, called directly (HTTP routes are in
test_server_http.py)."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from story_pipeline import release
from story_pipeline.agents import text as textagent
from story_pipeline.bundle import Bundle
from story_pipeline.review import runner, server

SLUG = "the-cracked-beam"


def manifest_path(root):
    return root / "manifest.json"


def edit_manifest(root, fn):
    m = json.loads(manifest_path(root).read_text())
    fn(m)
    manifest_path(root).write_text(json.dumps(m, indent=2))


def clone(repo_root, slug, mutate=None):
    src = repo_root / "stories" / "amberlight" / SLUG
    dst = repo_root / "stories" / "amberlight" / slug
    shutil.copytree(src, dst)
    if mutate:
        edit_manifest(dst, mutate)
    return dst


# --- pure helpers ----------------------------------------------------------


def test_spend_sums_only_usd_keys_and_rounds():
    out = server._spend({"spend": {"a_usd": 1.005, "b_usd": 2.0, "elevenlabs_credits": 500}})
    assert out["usd"] == 3.0 or out["usd"] == 3.01
    assert out["raw"]["elevenlabs_credits"] == 500
    assert server._spend({}) == {"usd": 0, "raw": {}}


def test_srt_to_vtt_header_commas_bom_and_crlf():
    srt = "﻿1\r\n00:00:01,500 --> 00:00:03,250\r\nHello, world\r\n"
    vtt = server.srt_to_vtt(srt)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:01.500 --> 00:00:03.250" in vtt
    assert "Hello, world" in vtt          # commas in text survive
    assert "\r" not in vtt and "﻿" not in vtt


def test_srt_to_vtt_empty():
    assert server.srt_to_vtt("") == "WEBVTT\n\n"


def test_outline_flags_minor_in_cast():
    flags = server._outline_flags(
        {"cast": [{"name": "Tom", "age": 12, "role": "page"},
                  {"name": "Ann", "age": 30}, {"name": "Odd", "age": "ten"}]}, {})
    (f,) = flags
    assert f["level"] == "danger" and "Tom (12, page)" in f["text"]
    assert "Ann" not in f["text"] and "Odd" not in f["text"]


def test_outline_flags_minor_without_role_uses_question_mark():
    (f,) = server._outline_flags({"cast": [{"name": "Tom", "age": 17}]}, {})
    assert "Tom (17, ?)" in f["text"]


def test_outline_flags_conflicts_ignore_non_answers():
    flags = server._outline_flags({"bible_conflict": "null", "notes_conflict": None}, {})
    assert flags == []
    flags = server._outline_flags(
        {"bible_conflict": "Notes say blond; bible says dark.",
         "notes_conflict": "Wanted a minor in a romance."}, {})
    assert [f["text"] for f in flags] == ["Notes contradicted the bible",
                                          "Notes conflicted with the boundaries"]
    assert all(f["level"] == "warn" for f in flags)


def test_outline_flags_thin_beats_threshold():
    thin = {"chapters": [{"beats": ["a"], "target_words": 151}]}
    ok = {"chapters": [{"beats": ["a"], "target_words": 150}]}
    (f,) = server._outline_flags(thin, {})
    assert f["level"] == "warn" and f["text"] == "151 words per beat"
    assert server._outline_flags(ok, {}) == []
    assert server._outline_flags({"chapters": [{"target_words": 9999}]}, {}) == []


def test_compose_feedback_keep_everything_with_note():
    spec = textagent.length_spec(30)
    out = server.compose_feedback([k for k, _ in server.KEEPABLE], "Make it warmer.", spec)
    assert out.startswith("Keep these exactly as they are")
    assert "Replace these wholesale" not in out
    assert out.endswith("Make it warmer.")


def test_compose_feedback_replace_chapters_mentions_beat_density():
    spec = textagent.length_spec(30)
    out = server.compose_feedback(["logline"], "", spec)
    assert f"{spec['beats_min']} to {spec['beats_max']} concrete beats" in out
    assert "Invent a genuinely new cast" in out
    assert "The chapters are being kept" not in out


def test_compose_feedback_cast_replaced_chapters_kept_adds_rewrite_instruction():
    spec = textagent.length_spec(15)
    keep = [k for k, _ in server.KEEPABLE if k != "cast"]
    out = server.compose_feedback(keep, "", spec)
    assert "The chapters are being kept, so rewrite any beat" in out
    assert "concrete beats each" not in out


def test_compose_feedback_nothing_kept_lists_all_labels():
    out = server.compose_feedback([], "  ", textagent.length_spec(15))
    assert "Keep these exactly" not in out
    for _, label in server.KEEPABLE:
        assert label in out


def test_keepable_keys_unique():
    keys = [k for k, _ in server.KEEPABLE]
    assert len(keys) == len(set(keys)) == 7


# --- bible_menu ------------------------------------------------------------


def test_bible_menu_lists_ready_and_drafts(cfg, real_bibles, sample_draft):
    menu = server.bible_menu(cfg)
    assert [b["name"] for b in menu["bibles"]] == ["amberlight"]
    b = menu["bibles"][0]
    assert b["problems"] == [] and b["genre"] == "regency"
    assert menu["drafts"] == [sample_draft]
    assert menu["lengths"] == sorted(textagent.LENGTHS)
    assert menu["default_length"] == cfg["length_min"]


def test_bible_menu_without_bibles_dir(cfg, repo_root):
    shutil.rmtree(repo_root / "bibles")
    menu = server.bible_menu(cfg)
    assert menu["bibles"] == [] and menu["drafts"] == []


def test_bible_menu_unparseable_bible_listed_with_problem(cfg, real_bibles):
    (real_bibles / "amberlight" / "amberlight.md").write_text("---\n: : not yaml [\n---\n")
    (b,) = server.bible_menu(cfg)["bibles"]
    assert b["name"] == "amberlight" and b["genre"] == "" and b["problems"]


def test_bible_menu_promoted_draft_not_listed_as_draft(cfg, real_bibles):
    names = server.bible_menu(cfg)["drafts"]
    assert "amberlight" not in names  # has no draft anyway
    (real_bibles / "amberlight" / "amberlight.draft.md").write_text("# x\n")
    assert "amberlight" not in server.bible_menu(cfg)["drafts"]


# --- new_story -------------------------------------------------------------


def test_new_story_rejects_unknown_bible(cfg, real_bibles):
    with pytest.raises(ValueError, match="no usable bible 'nope'"):
        server.new_story(cfg, "nope", None, "")


def test_new_story_rejects_bible_with_errors(cfg, real_bibles, monkeypatch):
    monkeypatch.setattr(server, "bible_menu", lambda c: {"bibles": [
        {"name": "amberlight", "problems": ["cast 'x': missing gender"]}]})
    with pytest.raises(ValueError, match="has errors: cast 'x': missing gender"):
        server.new_story(cfg, "amberlight", None, "")


@pytest.mark.parametrize("length", [7, "45", 60])
def test_new_story_rejects_unsupported_length(cfg, real_bibles, monkeypatch, length):
    monkeypatch.setattr(textagent, "ideate", lambda *a, **k: pytest.fail("paid call made"))
    with pytest.raises(ValueError, match="length must be one of"):
        server.new_story(cfg, "amberlight", length, "")


def test_new_story_passes_arguments_to_ideator(cfg, real_bibles, real_story, monkeypatch):
    seen = {}

    def fake(cfg_, stories, genre, sub, notes, bible, length_min=None):
        seen.update(stories=stories, genre=genre, sub=sub, notes=notes, bible=bible,
                    length=length_min)
        return Bundle.open(real_story)
    monkeypatch.setattr(textagent, "ideate", fake)
    out = server.new_story(cfg, "amberlight", "15", "  a ghost story  ")
    assert out == {"slug": SLUG, "title": "The Cracked Beam"}
    assert seen == {"stories": "stories", "genre": None, "sub": None,
                    "notes": "a ghost story", "bible": "amberlight", "length": 15}


def test_new_story_blank_notes_become_none_and_length_none(cfg, real_bibles, real_story,
                                                         monkeypatch):
    seen = {}

    def fake(cfg_, stories, genre, sub, notes, bible, length_min=None):
        seen.update(notes=notes, length=length_min)
        return Bundle.open(real_story)
    monkeypatch.setattr(textagent, "ideate", fake)
    server.new_story(cfg, "amberlight", None, "   ")
    assert seen == {"notes": None, "length": None}


# --- queue -----------------------------------------------------------------


def test_queue_empty(cfg):
    assert server.queue(cfg) == {"total": 0, "groups": {
        k: [] for k in ("outlines", "approved", "chapters", "failed", "stale", "video",
                        "publish", "done", "published")}}


def test_queue_real_story_waits_for_publish(cfg, real_story):
    q = server.queue(cfg)
    assert q["total"] == 1
    assert [r["slug"] for r in q["groups"]["publish"]] == [SLUG]
    assert sum(len(v) for v in q["groups"].values()) == 1


def test_story_row_fields(cfg, real_story):
    row = server.queue(cfg)["groups"]["publish"][0]
    assert row["arc"] == "amberlight" and row["bible"] == "amberlight"
    assert row["length"] == 30 and row["chapters"] == 8
    assert row["beats"] > 0 and row["words"] > 0
    assert row["per_beat"] == round(row["words"] / row["beats"])
    assert row["published"] in (None, {}) or isinstance(row["published"], dict)
    assert set(row) >= {"stages", "tropes", "logline", "unpassed", "stale", "spend",
                        "flags", "series", "genre", "subgenre"}


@pytest.mark.parametrize("name,mutate,group", [
    ("s-outline", lambda m: m["stages"].update(ideate="awaiting_review"), "outlines"),
    ("s-approved", lambda m: m["stages"].update(write="pending"), "approved"),
    ("s-failed", lambda m: m.setdefault("stage_info", {}).setdefault("edit", {}).update(unpassed=[2]), "failed"),
    ("s-chapters", lambda m: m["chapter_approvals"].pop("1"), "chapters"),
    ("s-video", lambda m: m["stages"].update(approve="awaiting_review"), "video"),
    ("s-done", lambda m: m["stages"].update(approve="pending", direct="pending"), "done"),
    ("s-published", lambda m: m["stages"].update(publish="done"), "published"),
])
def test_queue_groups(cfg, real_story, repo_root, name, mutate, group):
    clone(repo_root, name, mutate)
    groups = server.queue(cfg)["groups"]
    assert [r["slug"] for r in groups[group]] == [name]
    assert [r["slug"] for r in groups["publish"]] == [SLUG]


def test_queue_stale_narration_group(cfg, real_story, repo_root, monkeypatch):
    clone(repo_root, "s-stale")
    monkeypatch.setattr(Bundle, "narration_stale", lambda self, n: self.root.name == "s-stale")
    groups = server.queue(cfg)["groups"]
    assert [r["slug"] for r in groups["stale"]] == ["s-stale"]
    assert groups["stale"][0]["stale"] == list(range(1, 9))


def test_queue_edit_stage_reports_truth_of_chapter_approvals(cfg, real_story, repo_root):
    clone(repo_root, "s-lapsed", lambda m: m["chapter_approvals"].update({"3": "stale-hash"}))
    row = server.queue(cfg)["groups"]["chapters"][0]
    assert row["stages"]["edit"] == "awaiting_review"


def test_queue_skips_unreadable_bundles(cfg, real_story, repo_root):
    bad = clone(repo_root, "s-bad")
    (bad / "outline.json").write_text("{not json")
    q = server.queue(cfg)
    assert q["total"] == 1 and q["groups"]["publish"][0]["slug"] == SLUG


# --- story_detail ----------------------------------------------------------


def test_story_detail_shape(cfg, real_story):
    d = server.story_detail(cfg, SLUG)
    assert d["slug"] == SLUG and d["outline"]["title"] == "The Cracked Beam"
    assert len(d["chapter_detail"]) == 8
    ch = d["chapter_detail"][0]
    assert ch["n"] == 1 and ch["words"] > 0 and ch["markdown"] and ch["segments"]
    assert ch["approved"] is True and ch["approval_lapsed"] is False
    assert ch["redraft_estimate"] is not None
    assert d["story_md"]
    assert d["job"] == {"state": "none"}
    assert d["assets"]["audio"] == 0 and isinstance(d["assets"]["images"], int)
    assert set(d["impact"]) == {"stories", "tropes", "leader"}
    assert {"write_estimate", "write_estimate_restart"} <= set(d)


def test_story_detail_review_summary_when_review_file_present(cfg, real_story):
    b = Bundle.open(real_story)
    b.review_path(1).parent.mkdir(parents=True, exist_ok=True)
    b.review_path(1).write_text(json.dumps({"pass": False, "score": 4, "fixes": ["x"]}))
    ch = server.story_detail(cfg, SLUG)["chapter_detail"][0]
    assert ch["review"]["pass"] is False and ch["review"]["score"] == 4
    assert ch["review"]["fixes"] == ["x"] and ch["review"]["exhausted"] is False


def test_story_detail_corrupt_review_is_ignored(cfg, real_story):
    b = Bundle.open(real_story)
    b.review_path(1).parent.mkdir(parents=True, exist_ok=True)
    b.review_path(1).write_text("{oops")
    ch = server.story_detail(cfg, SLUG)["chapter_detail"][0]
    assert ch["review"] is None


def test_story_detail_unknown_slug(cfg, repo_root):
    with pytest.raises(FileNotFoundError):
        server.story_detail(cfg, "nope")


@pytest.mark.parametrize("slug", ["../..", "a/b", "", ".."])
def test_story_detail_refuses_non_slugs(cfg, real_story, slug):
    with pytest.raises(ValueError, match="not a story slug"):
        server.story_detail(cfg, slug)


# --- approve ---------------------------------------------------------------


def test_approve_outline(cfg, real_story):
    edit_manifest(real_story, lambda m: m["stages"].update(ideate="awaiting_review"))
    assert server.approve(cfg, SLUG, "outline") == {"ok": True, "slug": SLUG, "what": "outline"}
    assert Bundle.open(real_story).stage_status("ideate") == "approved"


def test_approve_unknown_what_falls_through_to_outline(cfg, real_story):
    edit_manifest(real_story, lambda m: m["stages"].update(ideate="awaiting_review"))
    out = server.approve(cfg, SLUG, "whatever")
    assert out["what"] == "whatever"
    assert Bundle.open(real_story).stage_status("ideate") == "approved"


def test_approve_all_chapters(cfg, real_story):
    edit_manifest(real_story, lambda m: m.update(chapter_approvals={}))
    out = server.approve(cfg, SLUG, "chapters")
    assert out["all_approved"] is True and out["written"] == 8
    assert out["approved"] == list(range(1, 9))


def test_approve_all_chapters_refused_when_unwritten(cfg, real_story):
    (real_story / "chapters" / "08.json").unlink()
    with pytest.raises(ValueError, match="no drafted chapters yet \\(1 missing\\)"):
        server.approve(cfg, SLUG, "chapters")


def test_approve_and_undo_single_chapters(cfg, real_story):
    out = server.approve(cfg, SLUG, "chapters", chapters=[2], undo=True)
    assert 2 not in out["approved"] and out["all_approved"] is False
    assert out["written"] == 8
    out = server.approve(cfg, SLUG, "chapters", chapters=["2"])
    assert 2 in out["approved"] and out["all_approved"] is True


def test_approve_single_chapter_unwritten_refused(cfg, real_story):
    with pytest.raises(ValueError, match=r"chapter\(s\) \[99\] have not been written"):
        server.approve(cfg, SLUG, "chapters", chapters=[99])


def test_approve_video_and_undo(cfg, real_story):
    out = server.approve(cfg, SLUG, "video", undo=True)
    assert out["approve"] == "awaiting_review"
    out = server.approve(cfg, SLUG, "video")
    assert out["approve"] == "approved"


def test_approve_video_requires_rendered_video(cfg, real_story):
    edit_manifest(real_story, lambda m: m["stages"].update(direct="pending"))
    with pytest.raises(ValueError, match="no rendered video to approve"):
        server.approve(cfg, SLUG, "video")


def test_approve_video_undo_refused_when_published(cfg, real_story):
    server.publish(cfg, SLUG, "dQw4w9WgXcQ")
    with pytest.raises(ValueError, match="already published"):
        server.approve(cfg, SLUG, "video", undo=True)


# --- publish ---------------------------------------------------------------


def test_publish_and_undo(cfg, real_story):
    out = server.publish(cfg, SLUG, "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                         "2026-11-01")
    assert out["published"] == {"url": "https://youtu.be/dQw4w9WgXcQ",
                                "video_id": "dQw4w9WgXcQ", "published": "2026-11-01"}
    assert [r["slug"] for r in server.queue(cfg)["groups"]["published"]] == [SLUG]
    undone = server.publish(cfg, SLUG, "", undo=True)
    assert undone["published"] == {}
    assert Bundle.open(real_story).stage_status("publish") == "pending"


def test_publish_defaults_date_to_today(cfg, real_story):
    from datetime import date
    out = server.publish(cfg, SLUG, "dQw4w9WgXcQ")
    assert out["published"]["published"] == date.today().isoformat()


def test_publish_requires_video_approval(cfg, real_story):
    server.approve(cfg, SLUG, "video", undo=True)
    with pytest.raises(ValueError, match="approve the video before publishing"):
        server.publish(cfg, SLUG, "dQw4w9WgXcQ")


@pytest.mark.parametrize("url", ["", "https://example.com/watch?v=dQw4w9WgXcQ", "short"])
def test_publish_rejects_non_youtube_urls(cfg, real_story, url):
    with pytest.raises(ValueError, match="not a YouTube video URL"):
        server.publish(cfg, SLUG, url)


def test_publish_rejects_bad_date(cfg, real_story):
    with pytest.raises(ValueError):
        server.publish(cfg, SLUG, "dQw4w9WgXcQ", "not-a-date")
    assert Bundle.open(real_story).stage_status("publish") != "done"


# --- reject ----------------------------------------------------------------


def test_reject_deletes_only_that_bundle(cfg, real_story, repo_root):
    other = clone(repo_root, "keep-me")
    assert server.reject(cfg, SLUG) == {"ok": True, "slug": SLUG}
    assert not real_story.exists() and other.exists()


def test_reject_unknown_and_traversal_delete_nothing(cfg, real_story):
    with pytest.raises(FileNotFoundError):
        server.reject(cfg, "ghost")
    for bad in ("../..", "..", "../amberlight/the-cracked-beam"):
        with pytest.raises(ValueError, match="not a story slug"):
            server.reject(cfg, bad)
    assert real_story.exists()


# --- save_chapter ----------------------------------------------------------


def test_save_chapter_rewrites_text_and_reports_lapsed_approval(cfg, real_story):
    b = Bundle.open(real_story)
    segs = [dict(s) for s in b.chapter(1)["segments"]]
    segs[0]["text"] = segs[0]["text"] + " An extra sentence."
    segs.append({"speaker": "narrator", "text": "   "})  # empty: dropped
    out = server.save_chapter(cfg, SLUG, "1", segs)
    assert out["ok"] is True and out["n"] == 1
    assert out["segments"][0]["text"].endswith("An extra sentence.")
    assert out["dropped_empty_segments"] == 1 or len(out["segments"]) == len(segs) - 1
    assert "An extra sentence." in out["markdown"]
    assert out["approval_lapsed"] is True
    assert Bundle.open(real_story).chapter_approved(1) is False
    assert out["words"] == Bundle.open(real_story).chapter_words(1)


def test_save_chapter_unwritten(cfg, real_story):
    with pytest.raises(ValueError, match="chapter 99 has not been written yet"):
        server.save_chapter(cfg, SLUG, 99, [])


def test_save_chapter_validator_rejects_and_leaves_file_alone(cfg, real_story):
    before = (real_story / "chapters" / "01.json").read_text()
    with pytest.raises(Exception):
        server.save_chapter(cfg, SLUG, 1, [])
    assert (real_story / "chapters" / "01.json").read_text() == before


# --- critique --------------------------------------------------------------


def test_critique_runs_editor_and_reports(cfg, real_story, monkeypatch):
    verdicts = {1: {"pass": True, "score": 9, "fixes": [], "continuity": []},
                2: {"pass": False, "score": 3, "fixes": ["a", "b"], "continuity": ["c"]}}
    calls = []

    def fake(cfg_, b, n):
        calls.append(n)
        return verdicts[n]
    monkeypatch.setattr(textagent, "review_chapter", fake)
    out = server.critique(cfg, SLUG, [1, "2"])
    assert calls == [1, 2] and out["ok"] is True
    assert out["failed"] == [2]
    r2 = out["results"][1]
    assert isinstance(r2["was_pass"], bool)  # the real story ships reviews
    assert {k: v for k, v in r2.items() if k != "was_pass"} == {
        "n": 2, "pass": False, "score": 3, "findings": 3}


def test_critique_default_reviews_every_written_chapter(cfg, real_story, monkeypatch):
    monkeypatch.setattr(textagent, "review_chapter",
                        lambda c, b, n: {"pass": True, "score": 8})
    out = server.critique(cfg, SLUG)
    assert [r["n"] for r in out["results"]] == list(range(1, 9)) and out["failed"] == []


def test_critique_records_previous_verdict(cfg, real_story, monkeypatch):
    b = Bundle.open(real_story)
    b.review_path(1).parent.mkdir(parents=True, exist_ok=True)
    b.review_path(1).write_text(json.dumps({"pass": True}))
    monkeypatch.setattr(textagent, "review_chapter",
                        lambda c, b, n: {"pass": False, "score": 2})
    (r,) = server.critique(cfg, SLUG, [1])["results"]
    assert r["was_pass"] is True and r["pass"] is False


def test_critique_unwritten_chapter_refused_before_any_paid_call(cfg, real_story, monkeypatch):
    monkeypatch.setattr(textagent, "review_chapter",
                        lambda *a: pytest.fail("paid call made"))
    with pytest.raises(ValueError, match=r"chapter\(s\) \[42\] have not been written"):
        server.critique(cfg, SLUG, [1, 42])


# --- revise / ask ----------------------------------------------------------


def test_revise_nothing_to_revise_refused_without_paid_call(cfg, real_story, monkeypatch):
    monkeypatch.setattr(textagent, "revise_outline", lambda *a: pytest.fail("paid call made"))
    keep_all = [k for k, _ in server.KEEPABLE]
    with pytest.raises(ValueError, match="Nothing to revise"):
        server.revise(cfg, SLUG, keep_all, "   ")


def test_revise_calls_ideator_with_composed_feedback(cfg, real_story, monkeypatch):
    seen = {}

    def fake(cfg_, b, feedback):
        seen["feedback"] = feedback
        return {"title": "New", "chapters": [{"beats": [1, 2], "target_words": 300},
                                             {"beats": [1], "target_words": 100}],
                "revisions": [{}, {}]}
    monkeypatch.setattr(textagent, "revise_outline", fake)
    out = server.revise(cfg, SLUG, ["title"], "Add a storm.")
    assert out == {"ok": True, "slug": SLUG, "title": "New", "chapters": 2, "beats": 3,
                   "per_beat": round(400 / 3), "revision": 2, "feedback": seen["feedback"]}
    assert "Add a storm." in seen["feedback"] and "the title" in seen["feedback"]


def test_revise_with_no_keep_list_replaces_everything(cfg, real_story, monkeypatch):
    seen = {}
    monkeypatch.setattr(textagent, "revise_outline",
                        lambda c, b, f: seen.setdefault("f", f) and {"chapters": []})
    out = server.revise(cfg, SLUG, None, "")
    assert out["per_beat"] == 0 and out["beats"] == 0
    assert "Keep these exactly" not in seen["f"]


def test_ask_returns_answer_and_logs_question(cfg, real_story, real_bibles, monkeypatch):
    monkeypatch.setattr(textagent.llm, "complete",
                        lambda model, system, user, **kw: ("  Because of the beam.  ", {"in": 1}))
    out = server.ask(cfg, SLUG, "Why the beam?")
    assert out["ok"] is True and out["slug"] == SLUG
    assert out["answer"] == "Because of the beam." and out["read"] is None
    log = (real_story / "questions.md").read_text()
    assert "**Q:** Why the beam?" in log and "Because of the beam." in log


def test_ask_empty_question(cfg, real_story, real_bibles, monkeypatch):
    monkeypatch.setattr(textagent.llm, "complete", lambda *a, **k: pytest.fail("paid"))
    with pytest.raises(ValueError, match="Write the question"):
        server.ask(cfg, SLUG, "   ")


# --- stage starts (runner.start mocked) -------------------------------------


@pytest.fixture
def stage_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "start",
                        lambda cfg, slug, stage, **kw: calls.append((slug, stage, kw)) or {})
    return calls


def test_write_start_requires_approved_outline(cfg, real_story, stage_calls):
    edit_manifest(real_story, lambda m: m["stages"].update(ideate="awaiting_review"))
    with pytest.raises(ValueError, match="has not been approved yet"):
        server.write_start(cfg, SLUG)
    assert stage_calls == []


def test_write_start_passes_restart(cfg, real_story, stage_calls):
    out = server.write_start(cfg, SLUG, restart=True)
    assert stage_calls == [(SLUG, "write", {"restart": True})]
    assert out["ok"] is True and out["slug"] == SLUG and out["state"] == "none"


def test_redraft_start_validations_and_call(cfg, real_story, stage_calls):
    with pytest.raises(ValueError, match="note is empty"):
        server.redraft_start(cfg, SLUG, 1, "   ")
    with pytest.raises(ValueError, match="chapter 99 has not been written"):
        server.redraft_start(cfg, SLUG, 99, "fix it")
    assert stage_calls == []
    server.redraft_start(cfg, SLUG, "2", "  tighter  ")
    assert stage_calls == [(SLUG, "redraft", {"chapter": 2, "note": "tighter"})]


def test_record_start_gate_and_force(cfg, real_story, stage_calls):
    server.approve(cfg, SLUG, "chapters", chapters=[1, 2], undo=True)
    with pytest.raises(ValueError, match="6 of 8 chapters are approved. Not yet: 1, 2"):
        server.record_start(cfg, SLUG)
    assert stage_calls == []
    server.record_start(cfg, SLUG, force=True)
    assert stage_calls == [(SLUG, "record", {"force": True})]


def test_record_start_when_all_approved(cfg, real_story, stage_calls):
    server.record_start(cfg, SLUG)
    assert stage_calls == [(SLUG, "record", {"force": False})]


def test_design_start_needs_narration_timelines(cfg, real_story, stage_calls):
    with pytest.raises(ValueError, match="have no narration yet"):
        server.design_start(cfg, SLUG)
    for n in range(1, 9):
        p = Bundle.open(real_story).timeline_path(n)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    server.design_start(cfg, SLUG, replan=True)
    assert stage_calls == [(SLUG, "design", {"replan": True})]


def test_direct_start_gates(cfg, real_story, stage_calls):
    edit_manifest(real_story, lambda m: m["stages"].update(design="running"))
    with pytest.raises(ValueError, match="design is not finished"):
        server.direct_start(cfg, SLUG)
    edit_manifest(real_story, lambda m: m["stages"].update(design="done"))
    b = Bundle.open(real_story)
    b.video_path.parent.mkdir(parents=True, exist_ok=True)
    b.video_path.write_bytes(b"x")
    with pytest.raises(ValueError, match="final.mp4 already exists"):
        server.direct_start(cfg, SLUG)
    assert stage_calls == []
    server.direct_start(cfg, SLUG, force=True)
    assert stage_calls == [(SLUG, "direct", {"force": True})]


# --- job_view --------------------------------------------------------------


def job(runs_dir, slug, stage, status="done", marks=(), exit_code=0, **over):
    d = runs_dir / slug / f"20260101T000000-{stage}"
    d.mkdir(parents=True)
    meta = {"job_id": d.name, "slug": slug, "stage": stage, "kind": stage,
            "status": status, "started": 1.0, "finished": 2.0,
            "exit_code": exit_code, "pid": None, **over}
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "output.log").write_text(
        "".join(runner.MARKER + json.dumps(m) + "\n" for m in marks))


def test_job_view_none(cfg, runs_dir):
    assert server.job_view(cfg, SLUG) == {"state": "none"}


def test_job_view_write_done_reads_state_off_disk(cfg, real_story, runs_dir):
    job(runs_dir, SLUG, "write", marks=[
        {"event": "chapter", "n": 1, "ok": True, "score": 9, "total": 8},
        {"event": "chapter", "n": 2, "ok": False, "score": 4, "exhausted": True}])
    j = server.job_view(cfg, SLUG)
    assert j["done"] == 2 and j["total"] == 8
    assert j["chapters"] == [{"n": 1, "pass": True, "score": 9, "exhausted": False},
                             {"n": 2, "pass": False, "score": 4, "exhausted": True}]
    assert j["words"] > 0 and j["chapters_written"] == 8 and j["target_minutes"] == 30
    assert j["minutes"] == round(j["words"] / textagent.WPM, 1)
    assert "progress" not in j


def test_job_view_write_total_falls_back_to_outline(cfg, real_story, runs_dir):
    job(runs_dir, SLUG, "write", status="running", exit_code=None, pid=__import__("os").getpid(),
        marks=[{"event": "chapter", "n": 1, "ok": True}])
    j = server.job_view(cfg, SLUG)
    assert j["state"] == "running" and j["total"] == 8 and j["can_cancel"] is True


def test_job_view_write_failed_reports_kept_chapters(cfg, real_story, runs_dir):
    job(runs_dir, SLUG, "write", status="failed", exit_code=1)
    assert server.job_view(cfg, SLUG)["kept"] == 8


def test_job_view_redraft(cfg, real_story, runs_dir):
    job(runs_dir, SLUG, "redraft", chapter=2, marks=[
        {"event": "pass", "attempt": 1, "ok": False, "score": 3, "words": 900},
        {"event": "pass", "attempt": 2, "ok": True, "score": 8, "words": 800}])
    j = server.job_view(cfg, SLUG)
    assert [p["attempt"] for p in j["passes"]] == [1, 2]
    assert j["max_passes"] == cfg.get("max_edit_passes", 2)
    assert j["n"] == 2 and j["words"] > 0 and j["later"] == [3, 4, 5, 6, 7, 8]
    assert j["target"] > 0 and j["approval_lapsed"] in (True, False)


def test_job_view_direct(cfg, real_story, runs_dir):
    job(runs_dir, SLUG, "direct", marks=[
        {"event": "clips", "done": 3, "total": 10}])
    j = server.job_view(cfg, SLUG)
    assert (j["done"], j["total"], j["phase"], j["video"]) == (3, 10, "clips", "final.mp4")


def test_job_view_direct_without_markers(cfg, real_story, runs_dir):
    job(runs_dir, SLUG, "direct", status="running", exit_code=None,
        pid=__import__("os").getpid())
    j = server.job_view(cfg, SLUG)
    assert (j["done"], j["total"], j["phase"]) == (0, None, "start")


def test_job_view_record_counts_audio_on_disk(cfg, real_story, runs_dir):
    b = Bundle.open(real_story)
    for n in (1, 2, 3):
        b.audio_path(n).parent.mkdir(parents=True, exist_ok=True)
        b.audio_path(n).write_bytes(b"x")
    job(runs_dir, SLUG, "record")
    j = server.job_view(cfg, SLUG)
    assert (j["done"], j["total"]) == (3, 8) and "spend" in j


def test_job_view_design_counts_images_on_disk(cfg, real_story, runs_dir):
    b = Bundle.open(real_story)
    cast = [m["name"] for m in b.outline()["cast"]]
    for name in cast[:2]:
        b.cast_image(name).parent.mkdir(parents=True, exist_ok=True)
        b.cast_image(name).write_bytes(b"x")
    job(runs_dir, SLUG, "design", status="running", exit_code=None,
        pid=__import__("os").getpid())
    j = server.job_view(cfg, SLUG)
    per = server._scenes_per(cfg, b)
    assert j["done"] == 2 and j["total"] == len(cast) + 8 * per


def test_job_view_survives_unreadable_bundle(cfg, runs_dir):
    job(runs_dir, "ghost-story", "write")
    j = server.job_view(cfg, "ghost-story")
    assert j["state"] == "done" and j["kind"] == "write"


def test_stage_counts_swallow_errors(cfg, repo_root):
    assert server._stage_counts(cfg, "ghost", "record") == {}


def test_job_log_delegates_to_runner(cfg, runs_dir):
    job(runs_dir, SLUG, "write")
    (runs_dir / SLUG / "20260101T000000-write" / "output.log").write_text("hello\n")
    assert server.job_log(cfg, SLUG)["text"] == "hello"
    assert server.job_log(cfg, SLUG, 6)["text"] == ""


# --- cancel_job / recover / _release_stage ----------------------------------


def test_cancel_job_kills_child_and_releases_running_stage(cfg, real_story, runs_dir):
    edit_manifest(real_story, lambda m: m["stages"].update(record="running"))
    runner.start_command(SLUG, "record", [sys.executable, "-c", "import time;time.sleep(30)"])
    out = server.cancel_job(cfg, SLUG)
    assert out["ok"] is True and out["state"] == "cancelled" and out["slug"] == SLUG
    assert Bundle.open(real_story).stage_status("record") == "pending"


def test_cancel_job_nothing_running(cfg, real_story, runs_dir):
    with pytest.raises(ValueError, match="nothing is running"):
        server.cancel_job(cfg, SLUG)


@pytest.mark.parametrize("stage,released", [
    ("write", True), ("record", True), ("design", True), ("direct", True),
    ("redraft", False), ("approve", False), ("bogus", False)])
def test_release_stage_only_for_releasable_stages(cfg, real_story, stage, released):
    edit_manifest(real_story, lambda m: m["stages"].update({stage: "running"}))
    server._release_stage(cfg, SLUG, stage)
    now = json.loads(manifest_path(real_story).read_text())["stages"][stage]
    assert now == ("pending" if released else "running")


def test_release_stage_leaves_non_running_stage_alone(cfg, real_story):
    server._release_stage(cfg, SLUG, "record")
    assert Bundle.open(real_story).stage_status("record") == "done"


def test_release_stage_for_missing_bundle_is_silent(cfg, repo_root):
    server._release_stage(cfg, "ghost", "write")
    server._release_stage(cfg, "", "")


def test_release_stage_also_rewrites_summary(cfg, real_story):
    edit_manifest(real_story, lambda m: m["stages"].update(design="running"))
    (real_story / "story.md").write_text("STALE")
    server._release_stage(cfg, SLUG, "design")
    assert (real_story / "story.md").read_text() != "STALE"


def test_recover_settles_dead_jobs_and_resets_stuck_stages(cfg, real_story, runs_dir):
    edit_manifest(real_story, lambda m: m["stages"].update(record="running"))
    dead = __import__("subprocess").Popen([sys.executable, "-c", "pass"])
    dead.wait()
    job(runs_dir, SLUG, "record", status="running", exit_code=None, pid=dead.pid)
    settled = server.recover(cfg)
    assert [(m["slug"], m["stage"], m["status"]) for m in settled] == [
        (SLUG, "record", "failed")]
    assert Bundle.open(real_story).stage_status("record") == "pending"
    assert server.recover(cfg) == []


def test_recover_leaves_live_jobs_and_their_stages_alone(cfg, real_story, runs_dir):
    edit_manifest(real_story, lambda m: m["stages"].update(record="running"))
    job(runs_dir, SLUG, "record", status="running", exit_code=None,
        pid=__import__("os").getpid())
    assert server.recover(cfg) == []
    assert Bundle.open(real_story).stage_status("record") == "running"


def test_recover_does_not_release_non_releasable_stage(cfg, real_story, runs_dir):
    edit_manifest(real_story, lambda m: m["stages"].update(approve="running"))
    dead = __import__("subprocess").Popen([sys.executable, "-c", "pass"])
    dead.wait()
    job(runs_dir, SLUG, "redraft", status="running", exit_code=None, pid=dead.pid)
    assert len(server.recover(cfg)) == 1
    assert json.loads(manifest_path(real_story).read_text())["stages"]["approve"] == "running"


def test_recover_with_nothing(cfg, runs_dir):
    assert server.recover(cfg) == []


# --- estimates -------------------------------------------------------------


def test_estimates_shape_and_arithmetic(cfg, real_story):
    e = server.estimates(cfg, SLUG)
    assert e["slug"] == SLUG
    assert {"billed_units", "usd", "runtime_min"} <= set(e["record"])
    b = Bundle.open(real_story)
    per = server._scenes_per(cfg, b)
    cast = len(b.outline()["cast"])
    assert e["design"]["per_chapter"] == per
    assert e["design"]["images"] == cast + 8 * per        # nothing drawn yet
    assert e["design"]["usd"] == round(e["design"]["images"] * cfg["gemini"]["usd_per_image"], 2)
    assert "direct" in e


def test_estimates_design_drops_as_images_appear(cfg, real_story):
    before = server.estimates(cfg, SLUG)["design"]["images"]
    b = Bundle.open(real_story)
    b.scene_image(1, 0).parent.mkdir(parents=True, exist_ok=True)
    b.scene_image(1, 0).write_bytes(b"x")
    assert server.estimates(cfg, SLUG)["design"]["images"] == before - 1


def test_estimates_unknown_slug(cfg, repo_root):
    with pytest.raises(FileNotFoundError):
        server.estimates(cfg, "ghost")


# --- package / video helpers ------------------------------------------------


def test_package_files_lists_audio_and_images_only(cfg, real_story):
    b = Bundle.open(real_story)
    b.audio_path(1).parent.mkdir(parents=True, exist_ok=True)
    b.audio_path(1).write_bytes(b"a")
    (b.root / "audio" / ".DS_Store").write_bytes(b"junk")
    root, files = server.package_files(cfg, SLUG)
    names = {str(p.relative_to(root)) for p in files}
    assert "audio/01.mp3" in names and "audio/.DS_Store" not in names
    assert all(n.startswith(("audio/", "images/")) for n in names)


def test_package_files_empty_bundle_refused(cfg, real_story):
    shutil.rmtree(real_story / "images", ignore_errors=True)
    with pytest.raises(ValueError, match="has no audio or images yet"):
        server.package_files(cfg, SLUG)


def test_video_file_and_subtitles_missing(cfg, real_story):
    with pytest.raises(FileNotFoundError, match="no rendered video yet"):
        server.video_file(cfg, SLUG)
    with pytest.raises(FileNotFoundError, match="no subtitles yet"):
        server.subtitles_vtt(cfg, SLUG)


def test_video_file_and_subtitles_present(cfg, real_story):
    b = Bundle.open(real_story)
    b.video_path.parent.mkdir(parents=True, exist_ok=True)
    b.video_path.write_bytes(b"mp4")
    b.srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    assert server.video_file(cfg, SLUG).resolve() == b.video_path.resolve()
    assert server.subtitles_vtt(cfg, SLUG).startswith(b"WEBVTT")


@pytest.mark.parametrize("fn", [server.video_file, server.subtitles_vtt, server.package_files])
def test_video_helpers_refuse_traversal(cfg, real_story, fn):
    with pytest.raises(ValueError, match="not a story slug"):
        fn(cfg, "../..")


# --- serve() ---------------------------------------------------------------


def test_serve_prints_banner_and_recovers(cfg, real_story, runs_dir, monkeypatch, capsys):
    started = {}

    class FakeServer:
        def __init__(self, addr, handler):
            started["addr"] = addr

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            started["closed"] = True
    monkeypatch.setattr(server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(server.webbrowser, "open", lambda url: pytest.fail("opened"))
    dead = __import__("subprocess").Popen([sys.executable, "-c", "pass"])
    dead.wait()
    job(runs_dir, SLUG, "record", status="running", exit_code=None, pid=dead.pid)
    server.serve(cfg, port=9999, open_browser=False)
    out = capsys.readouterr().out
    assert started == {"addr": ("127.0.0.1", 9999), "closed": True}
    assert "Review UI on http://127.0.0.1:9999/" in out
    assert "1 job(s) did not survive the last restart" in out
    assert "0 waiting on you, 1 stories in total." in out
    assert "Stopped." in out
