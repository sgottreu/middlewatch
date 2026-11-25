"""Bundle: paths, lifecycle, manifest, stage/approval state, hashing, staleness."""
import json

import pytest

from story_pipeline import bundle as bm
from story_pipeline.bundle import (BREAK, STAGES, Bundle, bundle_roots, ear_key,
                                   find_bundle, real_answer, slugify)
from tests.pipeline.conftest import make_mini


def test_stages_order():
    assert STAGES == ["ideate", "write", "edit", "record", "design", "direct",
                      "approve", "publish"]


# --- lifecycle ---------------------------------------------------------------

def test_create_makes_dirs_and_manifest(repo_root):
    b = Bundle.create(repo_root / "stories", "foo")
    assert b.root == repo_root / "stories" / "standalone" / "foo"
    for sub in Bundle.SUBDIRS:
        assert (b.root / sub).is_dir()
    m = b.manifest()
    assert m["pipeline"] == "story" and m["slug"] == "foo" and m["spend"] == {}
    assert m["stages"] == {s: "pending" for s in STAGES}
    assert m["created"]


def test_create_with_arc(repo_root):
    b = Bundle.create(repo_root / "stories", "foo", "amberlight")
    assert b.root.parent.name == "amberlight"


def test_create_idempotent_keeps_manifest(repo_root):
    b = Bundle.create(repo_root / "stories", "foo")
    b.pin("k", 1)
    b2 = Bundle.create(repo_root / "stories", "foo")
    assert b2.pinned("k") == 1


def test_create_rejects_slug_in_other_arc(repo_root):
    Bundle.create(repo_root / "stories", "foo", "a")
    with pytest.raises(ValueError, match="unique across arcs"):
        Bundle.create(repo_root / "stories", "foo", "b")


def test_open_missing_manifest(repo_root):
    with pytest.raises(FileNotFoundError, match="no manifest"):
        Bundle.open(repo_root / "stories" / "x" / "y")


def test_open_restores_missing_subdirs(mini):
    import shutil
    shutil.rmtree(mini.root / "images")
    shutil.rmtree(mini.root / "video")
    b = Bundle.open(mini.root)
    assert (b.root / "images" / "cast").is_dir() and (b.root / "video").is_dir()


def test_bundle_roots_sorted_across_arcs(repo_root):
    Bundle.create(repo_root / "stories", "b", "z")
    Bundle.create(repo_root / "stories", "a", "y")
    (repo_root / "stories" / "q" / "nomanifest").mkdir(parents=True)
    roots = bundle_roots(repo_root / "stories")
    assert [r.name for r in roots] == ["a", "b"]
    assert bundle_roots(repo_root / "nothing") == []


def test_find_bundle(repo_root):
    b = Bundle.create(repo_root / "stories", "foo", "a")
    assert find_bundle(repo_root / "stories", "foo") == b.root


@pytest.mark.parametrize("bad", ["../..", "", "Foo", "-x", "a/b", None])
def test_find_bundle_rejects_bad_slug(repo_root, bad):
    with pytest.raises(ValueError, match="not a story slug"):
        find_bundle(repo_root / "stories", bad)


def test_find_bundle_missing_and_ambiguous(repo_root):
    with pytest.raises(FileNotFoundError):
        find_bundle(repo_root / "stories", "nope")
    # create() forbids duplicates, so fake one by hand
    for arc in ("a", "b"):
        d = repo_root / "stories" / arc / "dup"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="several arcs"):
        find_bundle(repo_root / "stories", "dup")


def test_slugify():
    assert slugify("The Cracked Beam!") == "the-cracked-beam"
    assert slugify("  --  ") == "untitled"
    assert len(slugify("x" * 100)) == 60


def test_real_answer():
    assert real_answer("omit") == ""
    assert real_answer("No conflict.") == ""
    assert real_answer(None) == "" and real_answer(5) == ""
    assert real_answer("  a real conflict ") == "a real conflict"
    assert real_answer("none of this works") == "none of this works"


def test_ear_key():
    assert ear_key("Mrs Honoria Pike") == "hon"
    assert ear_key("Mrs Judith Vane") == "jud"
    assert ear_key("Dr. Smith") == "smi"
    assert ear_key("Mr") == "mr"        # only an honorific: falls back to the name
    assert ear_key("") == ""


# --- paths -------------------------------------------------------------------

def test_paths(mini):
    r = mini.root
    assert mini.manifest_path == r / "manifest.json"
    assert mini.chapter_json(3) == r / "chapters" / "03.json"
    assert mini.chapter_md(3) == r / "chapters" / "03.md"
    assert mini.review_path(3) == r / "reviews" / "03.json"
    assert mini.draft_path(3, 2) == r / "drafts" / "03.2.json"
    assert mini.draft_review_path(3, 2) == r / "drafts" / "03.2.review.json"
    assert mini.rejected_path(3) == r / "drafts" / "03.rejected.json"
    assert mini.audio_path(3) == r / "audio" / "03.mp3"
    assert mini.timeline_path(3) == r / "audio" / "03.timeline.json"
    assert mini.scenes_path == r / "images" / "scenes.json"
    assert mini.scene_image(2, 1) == r / "images" / "02-01.jpg"
    assert mini.srt_path == r / "video" / "subtitles.srt"
    assert mini.video_path == r / "video" / "final.mp4"
    assert mini.narration_path == r / "video" / "narration.mp3"
    assert mini.story_md_path == r / "story.md"


def test_cast_image_prefers_jpg_falls_back_to_png(mini):
    jpg = mini.root / "images" / "cast" / "ann-lee.jpg"
    assert mini.cast_image("Ann Lee") == jpg          # nothing on disk: the jpg path
    png = jpg.with_suffix(".png")
    png.write_bytes(b"p")
    assert mini.cast_image("Ann Lee") == png
    jpg.write_bytes(b"j")
    assert mini.cast_image("Ann Lee") == jpg


def test_scene_image_png_fallback(mini):
    p = mini.root / "images" / "01-00.png"
    p.write_bytes(b"p")
    assert mini.scene_image(1, 0) == p


def test_drafts_lists_attempts_ignoring_reviews_and_rejected(mini):
    d = mini.root / "drafts"
    for name in ("01.0.json", "01.2.json", "01.1.json", "01.1.review.json",
                 "01.rejected.json", "02.0.json"):
        (d / name).write_text("{}")
    assert mini.drafts(1) == [0, 1, 2]
    assert mini.drafts(2) == [0]
    assert mini.drafts(5) == []


def test_drafts_no_dir(mini):
    import shutil
    shutil.rmtree(mini.root / "drafts")
    assert mini.drafts(1) == []


# --- manifest ----------------------------------------------------------------

def test_set_stage_and_stage_info(mini):
    mini.set_stage("write", "done")
    assert mini.manifest()["stages"]["write"] == "done"
    assert "stage_info" not in mini.manifest()
    mini.set_stage("publish", "done", url="u", video_id="v")
    assert mini.manifest()["stage_info"]["publish"] == {"url": "u", "video_id": "v"}
    assert mini.publish_info() == {"url": "u", "video_id": "v"}


def test_set_stage_unknown_stage_is_stored(mini):
    mini.set_stage("weird", "x")
    assert mini.stage_status("weird") == "x"
    assert mini.stages()["weird"] == "x"
    assert mini.stage_status("never") == "pending"


def test_manifest_written_with_indent_and_newline(mini):
    text = mini.manifest_path.read_text()
    assert text.endswith("}\n") and '\n  "slug"' in text


def test_stages_fill_missing_keys_and_derive_approve(mini):
    m = mini.manifest()
    del m["stages"]["approve"], m["stages"]["publish"]
    m["stages"]["direct"] = "done"
    mini.write_manifest(m)
    s = mini.stages()
    assert s["approve"] == "awaiting_review" and s["publish"] == "pending"
    mini.set_stage("approve", "approved")
    assert mini.stages()["approve"] == "approved"


def test_stages_approve_stays_pending_until_direct_done(mini):
    assert mini.stages()["approve"] == "pending"


def test_publish_info_empty(mini):
    assert mini.publish_info() == {}


def test_add_spend_accumulates_and_rounds(mini):
    mini.add_spend("gemini_images", 1)
    mini.add_spend("gemini_images", 2)
    mini.add_spend("x", 0.123456)
    assert mini.manifest()["spend"] == {"gemini_images": 3, "x": 0.1235}


def test_pin_and_pinned(mini):
    assert mini.pinned("a") is None and mini.pinned("a", 7) == 7
    mini.pin("a", {"k": 1})
    assert mini.pinned("a") == {"k": 1}


def test_ledger_story_reads_manifest(mini):
    mini.pin("title", "Mini")
    mini.pin("genre", "regency")
    d = mini.ledger_story()
    assert d == {"pipeline": "story", "slug": "mini", "title": "Mini", "series": "",
                 "genre": "regency", "flavor": "", "arc_slug": "", "volume": "",
                 "episode": ""}


def test_ledger_story_real(story):
    d = story.ledger_story()
    assert d["pipeline"] == "story" and d["slug"] == "the-cracked-beam"
    assert d["series"] == "Middle Watch: Amberlight" and d["flavor"] == "slice_of_life"


# --- chapters / hashing ------------------------------------------------------

def test_outline_chapter_numbers_words(mini):
    assert mini.chapter_numbers() == [1, 2]
    assert mini.chapter_words(1) == 6
    assert mini.word_count() == 12
    mini.chapter_json(2).unlink()
    assert mini.chapter_words(2) is None and mini.word_count() == 6


def test_chapter_hash_stable_and_sensitive(mini):
    h = mini.chapter_hash(1)
    assert len(h) == 16 and h == mini.chapter_hash(1)
    assert mini.chapter_hash(9) is None
    segs = mini.chapter(1)["segments"]
    segs[0]["text"] += "!"
    mini.chapter_json(1).write_text(json.dumps({"segments": segs}))
    assert mini.chapter_hash(1) != h


def test_chapter_hash_ignores_title_and_empty_tag(mini):
    h = mini.chapter_hash(1)
    c = mini.chapter(1)
    c["title"] = "Different"
    c["segments"][0]["tag"] = ""
    mini.chapter_json(1).write_text(json.dumps(c))
    assert mini.chapter_hash(1) == h


def test_chapter_hash_includes_tag(mini):
    h = mini.chapter_hash(1)
    c = mini.chapter(1)
    c["segments"][0]["tag"] = "[quietly]"
    mini.chapter_json(1).write_text(json.dumps(c))
    assert mini.chapter_hash(1) != h


def test_chapter_hash_break_segment_counts(mini):
    h = mini.chapter_hash(1)
    c = mini.chapter(1)
    c["segments"].insert(1, {"speaker": BREAK, "text": ""})
    mini.chapter_json(1).write_text(json.dumps(c))
    assert mini.chapter_hash(1) != h


def test_chapter_hash_real_matches_recorded_approvals(story):
    """The hash algorithm must keep matching approvals already on disk."""
    approvals = story.chapter_approvals()
    assert len(approvals) == 8
    for n in story.chapter_numbers():
        assert story.chapter_hash(n) == approvals[str(n)]
    assert story.all_chapters_approved()
    assert story.approved_chapters() == list(range(1, 9))


# --- per-chapter approval ----------------------------------------------------

def test_approval_roundtrip_and_lapse(mini):
    assert not mini.chapter_approved(1) and mini.chapter_approvals() == {}
    mini.approve_chapter(1)
    assert mini.chapter_approved(1) and mini.approved_chapters() == [1]
    assert not mini.all_chapters_approved()
    mini.approve_chapter(2)
    assert mini.all_chapters_approved()
    c = mini.chapter(1)
    c["segments"][0]["text"] = "changed"
    mini.chapter_json(1).write_text(json.dumps(c))
    assert not mini.chapter_approved(1)
    assert "1" in mini.chapter_approvals()           # recorded, but lapsed
    assert not mini.all_chapters_approved()
    mini.unapprove_chapter(1)
    assert "1" not in mini.chapter_approvals()
    mini.unapprove_chapter(1)                          # no-op, no raise


def test_all_chapters_approved_false_when_nothing_written(mini):
    mini.chapter_json(1).unlink()
    mini.chapter_json(2).unlink()
    assert not mini.all_chapters_approved()


def test_all_chapters_approved_ignores_unwritten(mini):
    mini.approve_chapter(1)
    mini.chapter_json(2).unlink()
    assert mini.all_chapters_approved()


# --- narration staleness / duration -----------------------------------------

def _narrate(b, n, **extra):
    b.audio_path(n).write_bytes(b"x")
    t = {"n": n, "chapter_hash": b.chapter_hash(n), "duration_ms": 1000, "sentences": []}
    t.update(extra)
    b.timeline_path(n).write_text(json.dumps(t))


def test_narration_stale_no_audio(mini):
    assert not mini.narration_stale(1)
    mini.audio_path(1).write_bytes(b"x")
    assert not mini.narration_stale(1)       # timeline missing too


def test_narration_stale_hash(mini):
    _narrate(mini, 1)
    assert not mini.narration_stale(1)
    c = mini.chapter(1)
    c["segments"][0]["text"] = "new words"
    mini.chapter_json(1).write_text(json.dumps(c))
    assert mini.narration_stale(1)


def test_narration_stale_legacy_timeline_without_hash_trusted(mini):
    _narrate(mini, 1, chapter_hash=None)
    assert not mini.narration_stale(1, "anything", {"narrator": "X"})


def test_narration_stale_announcement(mini):
    _narrate(mini, 1, announcement="Chapter 1. T1.")
    assert not mini.narration_stale(1)                       # not asked
    assert not mini.narration_stale(1, "Chapter 1. T1.")
    assert mini.narration_stale(1, "Chapter One.")
    assert mini.narration_stale(1, None) is False            # None = "not checking"


def test_narration_stale_legacy_without_announcement_key(mini):
    _narrate(mini, 1)
    assert mini.narration_stale(1, "Chapter 1. T1.")         # None != text


def test_narration_stale_voices(mini):
    _narrate(mini, 1, voices={"narrator": "A"})
    assert not mini.narration_stale(1, voices={"narrator": "A"})
    assert mini.narration_stale(1, voices={"narrator": "B"})
    assert not mini.narration_stale(1, voices=None)


def test_narration_stale_voices_missing_in_timeline_trusted(mini):
    _narrate(mini, 1)
    assert not mini.narration_stale(1, voices={"narrator": "B"})


def test_duration_ms(mini):
    assert mini.duration_ms() is None
    _narrate(mini, 1, duration_ms=1500)
    assert mini.duration_ms() is None                          # chapter 2 missing
    _narrate(mini, 2, duration_ms=2500)
    assert mini.duration_ms() == 4000


def test_duration_real(story):
    assert story.duration_ms() == sum(story.timeline(n)["duration_ms"] for n in range(1, 9))
    assert story.timeline(1)["n"] == 1
