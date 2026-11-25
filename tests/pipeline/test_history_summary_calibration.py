"""history.py, summary.py, calibration.py against synthetic and real bundles."""
import json

import pytest

from story_pipeline import calibration, history, summary
from story_pipeline.agents import text
from story_pipeline.bundle import STAGES, Bundle
from story_pipeline.history import History, avoid_block, scan
from tests.pipeline.conftest import make_mini
from tests.pipeline.test_bundle import _narrate


# --- history -----------------------------------------------------------------

def _story(stories, slug, arc, *, ideate="approved", tropes=(), subgenre="s", cast=(), bible="amber", title=None):
    b = Bundle.create(stories, slug, arc)
    b.set_stage("ideate", ideate)
    b.outline_path.write_text(json.dumps({
        "title": title or slug, "bible": bible, "tropes": list(tropes),
        "subgenre": subgenre, "cast": list(cast), "chapters": []}))
    return b


def test_scan_missing_dir():
    h = scan("nowhere")
    assert h.stories == 0 and h.titles == []


def test_scan_counts_only_approved(repo_root):
    st = repo_root / "stories"
    cast = [{"name": "Ann", "role": "protagonist", "recurring": True},
            {"name": "Zed", "role": "supporting"}, {"role": "supporting"}]
    _story(st, "a", "x", tropes=["Slow Burn", "Rivals"], cast=cast)
    _story(st, "b", "x", tropes=["slow burn"], cast=cast, ideate="done")
    _story(st, "c", "x", tropes=["slow burn"], ideate="awaiting_review")
    _story(st, "d", "x", tropes=["slow burn"], ideate="pending")
    h = scan(st)
    assert h.stories == 2
    assert h.tropes["slow burn"] == 2 and h.tropes["rivals"] == 1
    assert h.cast["Ann"] == 2 and h.protagonists["Ann"] == 2
    assert h.invented_cast == {"Zed": 2}
    assert h.subgenres["s"] == 2 and h.titles == ["a", "b"]


def test_scan_bible_filter(repo_root):
    st = repo_root / "stories"
    _story(st, "a", "x", bible="one")
    _story(st, "b", "x", bible="two")
    assert scan(st, bible="one").stories == 1
    assert scan(st, bible="none").stories == 0
    assert scan(st).stories == 2


def test_scan_skips_broken_bundles(repo_root):
    st = repo_root / "stories"
    _story(st, "ok", "x")
    Bundle.create(st, "nooutline", "x").set_stage("ideate", "approved")
    bad = Bundle.create(st, "badjson", "x")
    bad.set_stage("ideate", "approved")
    bad.outline_path.write_text("{not json")
    (st / "x" / "badmanifest").mkdir()
    (st / "x" / "badmanifest" / "manifest.json").write_text("{")
    assert scan(st).stories == 1


def test_scan_real_story(story, repo_root):
    h = scan("stories")
    assert h.stories == 1 and h.titles == ["The Cracked Beam"]
    assert scan("stories", bible="amberlight").stories == 1
    assert scan("stories", bible="other").stories == 0
    assert sum(h.tropes.values()) == len(story.outline()["tropes"])


def test_weights():
    h = History()
    h.tropes["a"] = 3
    assert h.weight("A") == 0.25 and h.weight("unused") == 1.0
    assert h.trope_weights(["a", "b"]) == [0.25, 1.0]
    h.tropes["b"] = 2
    assert h.overused() == [("a", 3), ("b", 2)]
    assert h.overused(3) == [("a", 3)]


def test_avoid_block_empty_and_filled():
    assert avoid_block(History()) == ""
    h = History(stories=1, titles=["T1"])
    out = avoid_block(h)
    assert "1 approved story so far" in out and "- T1" in out
    assert "Has led" not in out and "Invented" not in out
    h = History(stories=3, titles=[f"T{i}" for i in range(20)])
    h.protagonists.update({"Ann": 2, "Bea": 1})
    h.invented_cast.update({"Zed": 2, "Yan": 1})
    h.subgenres.update({"cozy": 2, "dark": 1})
    out = avoid_block(h, limit=3)
    assert "3 approved stories" in out
    assert "- T19" in out and "- T16" not in out
    assert "Ann (2x)" in out and "Bea" not in out
    assert "Zed (2x)" in out and "Yan" not in out
    assert "cozy (2x)" in out and "dark" not in out


# --- summary -----------------------------------------------------------------

def test_mmss_and_drift():
    assert summary._mmss(61_400) == "1:01"
    assert summary._mmss(0) == "0:00"
    assert summary._drift(100, 0) == ""
    assert summary._drift(101, 100) == "on target"
    assert summary._drift(120, 100) == "+20%"
    assert summary._drift(80, 100) == "-20%"


def test_render_fresh_bundle_marks_pending(mini):
    out = summary.render(mini)
    assert out.startswith("# Mini\n\nregency / slice\n")
    assert "> ll" in out
    assert "| Target (15 min) | 2,250 | 15:00 |" in out
    assert "| Planned | 200 | 1:20 |" in out
    assert "*not yet written*" not in out        # chapters are written in the mini fixture
    assert "| Written | 12 | 0:05 |" in out
    assert "*not yet recorded*" in out
    assert "Measured pace" not in out
    assert "- **Ann Lee**, 30 — protagonist" in out
    assert "- Slug: `mini`" in out and "- Tropes: a, b" in out
    for s in STAGES:
        assert f"| {s} | pending |" in out
    assert "Spend" not in out and "Published" not in out
    assert out.endswith("this file.*\n")


def test_render_unwritten_and_spend_and_published(mini):
    mini.chapter_json(1).unlink()
    mini.chapter_json(2).unlink()
    mini.add_spend("gemini_images", 3)
    mini.set_stage("publish", "done", url="https://youtu.be/x", published="2026-01-01")
    out = summary.render(mini)
    assert "| Written | *not yet written* | |" in out
    assert "| gemini_images | 3.0 |" in out
    assert "Published 2026-01-01: https://youtu.be/x" in out
    assert "| 1 | T1 | 2 | 100 | — | — |" in out


def test_render_real_story_matches_bundle(story):
    out = summary.render(story)
    assert out.startswith("# The Cracked Beam\n")
    assert "regency / " in out and "· Middle Watch: Amberlight" in out
    assert "| Target (30 min) | 4,500 | 30:00 |" in out
    words = story.word_count()
    assert f"| Written | {words:,} |" in out
    ms = story.duration_ms()
    assert f"| **Narrated** | | **{summary._mmss(ms)}** |" in out
    assert "Measured pace" in out and "wpm** against an assumed 150" in out
    for n in story.chapter_numbers():
        assert f"| {n} | {story.outline()['chapters'][n-1]['title']} |" in out
    assert "| ideate | approved |" in out and "| approve | approved |" in out
    assert "| publish | pending |" in out
    assert "- Bible: `amberlight`" in out and "- Length: 30 min (pinned)" in out
    assert "- Images: 4/chapter, 32 total" in out
    assert "| elevenlabs_credits | 78275.0 |" in out
    assert "(recurring)" in out


def test_write_creates_story_md(mini):
    summary.write(mini)
    assert mini.story_md_path.read_text() == summary.render(mini)


# --- calibration -------------------------------------------------------------

def _timeline(b, n, dur, sentences):
    b.audio_path(n).write_bytes(b"x")
    b.timeline_path(n).write_text(json.dumps({"n": n, "duration_ms": dur, "sentences": sentences}))


def test_measure_none_without_audio(mini):
    assert calibration.measure(mini) is None


def test_measure_synthetic(mini):
    # 6 words per chapter, 12 total, over 8 seconds => 90 wpm effective
    _timeline(mini, 1, 4000, [{"start_ms": 0, "end_ms": 3000}])
    _timeline(mini, 2, 4000, [{"start_ms": 0, "end_ms": 2000}, {"start_ms": 2000, "end_ms": 3000}])
    m = calibration.measure(mini)
    assert m["words"] == 12 and m["duration_ms"] == 8000
    assert m["effective_wpm"] == 90.0
    assert m["spoken_wpm"] == round(12 / (6000 / 60000), 1) == 120.0
    assert m["gap_ms"] == 2000
    assert m["assumed_wpm"] == text.WPM == 150
    assert m["error_pct"] == round((150 / 90 - 1) * 100, 1)
    assert m["predicted_minutes"] == round(12 / 150, 1)
    assert m["target_minutes"] == 15            # from outline length_min
    assert [c["n"] for c in m["chapters"]] == [1, 2]
    assert m["chapters"][0]["wpm"] == round(6 / (4000 / 60000), 1)


def test_measure_pinned_length_wins_and_unknown_dial_is_none(mini):
    _timeline(mini, 1, 4000, [])
    _timeline(mini, 2, 4000, [])
    mini.pin("length_min", 30)
    assert calibration.measure(mini)["target_minutes"] == 30
    mini.pin("length_min", 45)
    m = calibration.measure(mini)
    assert m["target_minutes"] is None and m["spoken_wpm"] is None


def test_measure_real_story(story):
    m = calibration.measure(story)
    assert m["words"] == story.word_count()
    assert m["duration_ms"] == story.duration_ms()
    assert m["target_minutes"] == 30
    assert len(m["chapters"]) == 8
    assert m["effective_wpm"] == round(m["words"] / (m["duration_ms"] / 60000), 1)
    assert m["gap_ms"] >= 0
    rep = calibration.report(m)
    assert f"{m['words']:,} words narrated in {m['minutes']} min" in rep
    assert "against a 30-minute dial" in rep


def _m(**kw):
    base = {"words": 1000, "minutes": 6.7, "assumed_wpm": 150, "predicted_minutes": 6.7,
            "effective_wpm": 150.0, "spoken_wpm": None, "gap_ms": 0, "duration_ms": 400000,
            "error_pct": 0.0, "target_minutes": None}
    base.update(kw)
    return base


def test_report_within_five_percent():
    rep = calibration.report(_m(error_pct=3.0))
    assert "The assumption is 3% fast." in rep
    assert "Within 5% — leave WPM at 150." in rep
    assert "Set WPM" not in rep and "This story:" not in rep and "speaking" not in rep


def test_report_slow_and_table():
    rep = calibration.report(_m(error_pct=-20.0, effective_wpm=187.5))
    assert "The assumption is 20% slow." in rep
    assert "Set WPM = 188 in story_pipeline/agents/text.py." in rep
    assert "15min" in rep and "30min" in rep           # one row per LENGTHS entry
    assert f"{round(30 * 187.5):,}" in rep


def test_report_speaking_and_target_lines():
    rep = calibration.report(_m(spoken_wpm=170.0, gap_ms=40000, target_minutes=15, minutes=7.5))
    assert "speaking   170.0 wpm excluding 40s of gaps (10% of the runtime)" in rep
    assert "7.5 min against a 15-minute dial (-50%)" in rep
