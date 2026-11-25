"""python3 -m ambience_pipeline.cli: new, approve, sound, status, audition."""
from __future__ import annotations

import json

import pytest

from ambience_pipeline import bundle as ab
from ambience_pipeline import cli, mix
from ambience_pipeline.bundle import AmbienceBundle
from tests.conftest import has_ffmpeg

def run(capsys, *argv):
    rc = cli.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out + out.err


def only_bundle(repo_root):
    roots = ab.bundle_roots(repo_root / "ambience")
    assert len(roots) == 1
    return AmbienceBundle.open(roots[0])


def test_new_from_a_scene(repo_root, capsys):
    rc, out = run(capsys, "new", "--starter", "--scene", "Waves on a shingle beach at night, a fire on the sand",
                  "--hours", "3", "--seed", "9")
    assert rc == 0 and "created ambience/waves-on-a-shingle-beach-at\n" in out
    b = only_bundle(repo_root)
    m = b.manifest()
    assert m["pinned"]["hours"] == 3 and m["pinned"]["seed"] == 9
    assert m["source"] == {"scene": "Waves on a shingle beach at night, a fire on the sand"}
    c = b.concept()
    assert ab.validate_concept(c) == [] and c["starter"] is True
    kinds = {L["recipe"]["type"]: L["role"] for L in c["layers"]}
    assert kinds == {"ocean": "bed", "fire": "texture"}
    assert b.stage_status("concept") == "awaiting_review"
    assert b.brief_path.is_file() and b.view_path.is_file()


def test_starter_always_has_a_bed():
    for text in ("", "a quiet study", "rain wind sea fire storm"):
        c = {"layers": cli.starter_layers(text)}
        assert ab.validate_concept(c) == [], (text, c)


def test_new_with_a_bible_pins_series_and_style(repo_root, real_bibles, capsys):
    rc, out = run(capsys, "new", "--starter", "--scene", "Rain on the library window", "--bible", "amberlight")
    assert rc == 0, out
    b = only_bundle(repo_root)
    assert b.pinned("bible") == "amberlight"
    assert b.pinned("series") == "Middle Watch: Amberlight"
    assert "## Bible: amberlight" in b.brief_path.read_text()


def test_new_with_a_missing_bible_fails_for_a_scene(repo_root, capsys):
    rc, out = run(capsys, "new", "--starter", "--scene", "rain", "--bible", "nope")
    assert rc == 1 and "would not load" in out
    assert not ab.bundle_roots(repo_root / "ambience")


def test_new_from_a_story(repo_root, fake_story, capsys):
    rc, out = run(capsys, "new", "--starter", "--from-story", str(fake_story.relative_to(repo_root)))
    assert rc == 0 and "skipping bible 'no-such-bible'" in out
    b = only_bundle(repo_root)
    assert b.slug == "the-salt-house-ambience"
    assert b.pinned("source_story") == "stories/standalone/the-salt-house"
    assert b.pinned("art_style") == "muted watercolour, grey light"
    brief = b.brief_path.read_text()
    for want in ("A lighthouse cottage on a storm coast", "A keeper hears the sea",
                 "Waves breaking under a lighthouse", "**genre:** gothic", "muted watercolour"):
        assert want in brief
    # The reciprocal link.
    assert json.loads((fake_story / "manifest.json").read_text())["ambience"] == [b.slug]
    assert ab.validate_concept(b.concept()) == []


def test_new_from_the_real_story_reads_its_bible(repo_root, real_story, real_bibles, capsys):
    rc, out = run(capsys, "new", "--starter", "--from-story", "stories/amberlight/the-cracked-beam")
    assert rc == 0, out
    b = only_bundle(repo_root)
    assert b.pinned("bible") == "amberlight" and "## Shots from the story" in b.brief_path.read_text()


def test_new_needs_a_real_story(repo_root, capsys):
    rc, out = run(capsys, "new", "--starter", "--from-story", "stories/nothing")
    assert rc == 1 and "not a story bundle" in out


def _scene(capsys, repo_root):
    run(capsys, "new", "--starter", "--scene", "rain on a window", "--seed", "3")
    return only_bundle(repo_root)


def test_sound_waits_for_approval_and_dry_run_renders_nothing(repo_root, capsys):
    b = _scene(capsys, repo_root)
    with pytest.raises(SystemExit, match="not approved"):
        cli.main(["sound", str(b.root)])
    rc, out = run(capsys, "approve", str(b.root))
    assert rc == 0 and b.concept_approved()
    rc, out = run(capsys, "sound", str(b.root), "--dry-run")
    assert rc == 0 and "dry run" in out and "procedural, rendered locally, free" in out
    assert not list((b.root / "audio").iterdir())
    # An edit after approval blocks the render until it is approved again.
    c = b.concept()
    c["layers"][0]["gain_db"] = -21.0
    assert b.write_concept(c) is True
    with pytest.raises(SystemExit, match="awaiting_review, not approved"):
        cli.main(["sound", str(b.root)])


def test_approve_reports_problems(repo_root, capsys):
    b = _scene(capsys, repo_root)
    c = b.concept()
    c["layers"][0]["role"] = "nope"
    b.write_concept(c)
    rc, out = run(capsys, "approve", str(b.root))
    assert rc == 1 and "role must be one of" in out


def test_status(repo_root, capsys):
    b = _scene(capsys, repo_root)
    rc, out = run(capsys, "status", str(b.root))
    assert rc == 0 and "concept  awaiting_review" in out and "rain" in out


@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_preview_with_progress_markers(repo_root, capsys, monkeypatch):
    monkeypatch.setenv("MW_PROGRESS", "1")
    b = _scene(capsys, repo_root)
    run(capsys, "approve", str(b.root))
    rc, out = run(capsys, "sound", str(b.root), "--seconds", "3")
    assert rc == 0, out
    assert b.preview_path.is_file() and not b.mix_path.exists()
    assert b.stage_status("sound") == "pending"       # a preview is not the stage
    marks = [json.loads(l[len("::progress "):]) for l in out.splitlines()
             if l.startswith("::progress ")]
    events = [m["event"] for m in marks]
    assert events[0] == "plan" and "block" in events and events[-1] == "done"
    assert any(m["event"] == "artifact" and m["path"].endswith("preview.wav") for m in marks)
    assert b.manifest()["stage_info"]["preview"]["lufs"] is not None


@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_full_sound_marks_the_stage_done(repo_root, capsys, monkeypatch):
    monkeypatch.setitem(ab.HOURS, 1, dict(ab.HOURS[1], seconds=4))
    b = _scene(capsys, repo_root)
    run(capsys, "approve", str(b.root))
    rc, out = run(capsys, "sound", str(b.root))
    assert rc == 0, out
    assert b.mix_path.is_file() and b.stage_status("sound") == "done"
    info = b.stage_info("sound")
    assert mix.LUFS_WINDOW[0] <= info["lufs"] <= mix.LUFS_WINDOW[1]
    assert b.manifest()["spend"]["procedural_seconds"] == 4.0
    assert not list((b.root / "audio").glob("*.partial.wav"))


@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_gate_failure_keeps_the_file_and_fails_the_stage(repo_root, capsys, monkeypatch):
    monkeypatch.setitem(ab.HOURS, 1, dict(ab.HOURS[1], seconds=3))
    monkeypatch.setattr(mix, "TARGET_LUFS", -40.0)
    b = _scene(capsys, repo_root)
    run(capsys, "approve", str(b.root))
    rc, out = run(capsys, "sound", str(b.root))
    assert rc == 2 and "loudness gate failed" in out
    assert b.rejected_path(b.mix_path).is_file() and not b.mix_path.exists()
    assert b.stage_status("sound") == "failed"


def test_audition(repo_root, capsys):
    out_wav = repo_root / "aud" / "rain.wav"
    rc, out = run(capsys, "audition", "rain", "-p", "surface=roof", "--seconds", "1",
                  "--out", str(out_wav))
    assert rc == 0 and out_wav.is_file() and "surface=roof" in out
    with pytest.raises(SystemExit, match="no recipe"):
        cli.main(["audition", "thunder", "--out", "x.wav"])
    rc, out = run(capsys, "audition", "rain", "-p", "colour=1", "--seconds", "1",
                  "--out", str(out_wav))
    assert rc == 1 and "no parameter" in out
