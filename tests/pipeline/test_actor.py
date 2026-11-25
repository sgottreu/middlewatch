"""actor.py: casting, planning, recording and estimates, with TTS mocked."""
import json
import subprocess

import pytest

from story_pipeline import calibration
from story_pipeline.agents import actor
from story_pipeline.bundle import BREAK, Bundle
from tests.conftest import has_ffmpeg
from tests.pipeline.conftest import FakeClip, FakeProvider, make_mini


@pytest.fixture
def no_encode(monkeypatch):
    """Replace ffmpeg encoding with a file write so tests stay fast."""
    calls = []

    def fake(pcm, rate, out):
        calls.append((len(pcm), rate, out))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp3")
    monkeypatch.setattr(actor, "_encode", fake)
    fake.calls = calls
    return fake


def set_segments(b, n, segs):
    b.chapter_json(n).write_text(json.dumps({"n": n, "title": "t", "segments": segs}))


# --- _split ------------------------------------------------------------------

def test_split_short_text_unchanged():
    assert actor._split("Hello there.", 100) == ["Hello there."]


def test_split_at_sentence_boundaries_within_limit():
    text = "Aaaa aaaa. Bbbb bbbb. Cccc cccc. Dddd dddd."
    chunks = actor._split(text, 25)
    assert chunks == ["Aaaa aaaa. Bbbb bbbb.", "Cccc cccc. Dddd dddd."]
    assert all(len(c) <= 25 for c in chunks)
    assert " ".join(chunks) == text


def test_split_keeps_oversize_sentence_whole():
    s = "x" * 50 + "."
    assert actor._split(s + " Short.", 20) == [s, "Short."]


def test_split_breaks_after_closing_quote():
    text = "“Stop!” Then she ran. Far away."
    chunks = actor._split(text, 14)
    assert chunks[0] == "“Stop!”"


# --- solo_voice / announcement ----------------------------------------------

def test_solo_voice(cfg):
    assert actor.solo_voice(cfg, {"narrator": "N"}) == "N"           # default mode is solo
    cfg["casting"]["solo_voice"] = "S"
    assert actor.solo_voice(cfg, {"narrator": "N"}) == "S"
    cfg["casting"]["mode"] = "cast"
    assert actor.solo_voice(cfg, {"narrator": "N"}) is None


def test_announcement(cfg, mini):
    assert actor.announcement(cfg, mini, 1) == "Chapter 1. T1."
    cfg["casting"]["chapter_announcement"] = None
    assert actor.announcement(cfg, mini, 1) is None
    cfg["casting"]["chapter_announcement"] = "{title}"
    assert actor.announcement(cfg, mini, 2) == "T2"
    cfg["casting"]["chapter_announcement"] = "  "
    assert actor.announcement(cfg, mini, 2) is None
    cfg["casting"]["chapter_announcement"] = "Ch {n}. {title}."
    assert actor.announcement(cfg, mini, 99) == "Ch 99. ."          # unknown chapter: empty title


# --- _plan -------------------------------------------------------------------

def seg(sp, text, **kw):
    return {"speaker": sp, "text": text, **kw}


def test_plan_solo_merges_runs_and_links_neighbours():
    ch = {"segments": [seg("narrator", "A."), seg("Ann", "B."), seg("narrator", "C.")]}
    plan = actor._plan(ch, 1000, solo="Reader")
    assert plan == [{"speaker": "Reader", "text": "A. B. C.", "previous": None, "following": None}]


def test_plan_solo_splits_at_limit():
    ch = {"segments": [seg("narrator", "Aaaa aaaa."), seg("narrator", "Bbbb bbbb.")]}
    plan = actor._plan(ch, 12, solo="R")
    assert [u["text"] for u in plan] == ["Aaaa aaaa.", "Bbbb bbbb."]
    assert plan[0]["following"] == "Bbbb bbbb." and plan[1]["previous"] == "Aaaa aaaa."


def test_plan_cast_keeps_speaker_switches_and_merges_same_speaker():
    ch = {"segments": [seg("narrator", "A."), seg("narrator", "B."), seg("Ann", "C."), seg("narrator", "D.")]}
    plan = actor._plan(ch, 1000)
    assert [(u["speaker"], u["text"]) for u in plan] == [
        ("narrator", "A. B."), ("Ann", "C."), ("narrator", "D.")]


def test_plan_announcement_leads_and_merges_into_first_narrator_run():
    ch = {"_announcement": "Chapter 1.", "segments": [seg("narrator", "Text.")]}
    assert [u["text"] for u in actor._plan(ch, 1000)] == ["Chapter 1. Text."]
    ch = {"_announcement": "Chapter 1.", "segments": [seg("Ann", "Hi.")]}
    plan = actor._plan(ch, 1000)
    assert [(u["speaker"], u["text"]) for u in plan] == [("narrator", "Chapter 1."), ("Ann", "Hi.")]


def test_plan_break_is_own_unit_and_not_a_neighbour():
    ch = {"segments": [seg("narrator", "A."), seg(BREAK, ""), seg("narrator", "B.")]}
    plan = actor._plan(ch, 1000, solo="R")
    assert [u["speaker"] for u in plan] == ["R", BREAK, "R"]
    assert plan[0]["following"] == "B." and plan[2]["previous"] == "A."
    assert "previous" not in plan[1]


def test_plan_tag_prefixed():
    ch = {"segments": [seg("narrator", " Hi. ", tag=" [softly] ")]}
    assert actor._plan(ch, 1000)[0]["text"] == "[softly] Hi."


# --- cast_voices -------------------------------------------------------------

def cast_story(repo_root, speakers, *, genders=None, voice_override=None):
    b = make_mini(repo_root / "stories", "cast1", 1)
    o = b.outline()
    o["cast"] = [{"name": n, "age": 30, "role": "supporting", "gender": (genders or {}).get(n, "female"),
                  **({"voice": voice_override[n]} if voice_override and n in voice_override else {})}
                 for n in speakers]
    b.outline_path.write_text(json.dumps(o))
    segs = [seg("narrator", "x.")]
    for i, n in enumerate(speakers):
        segs += [seg(n, "line.")] * (len(speakers) - i + 1)       # earlier names speak more
    set_segments(b, 1, segs)
    return b


def test_cast_returns_pinned_voices(story, cfg):
    p = FakeProvider()
    v = actor.cast_voices(cfg, story, p)
    assert v == story.pinned("voices") and v["narrator"] == "Samara X"
    assert p.calls == []


def test_cast_solo_pins_narrator_only(mini, cfg):
    p = FakeProvider()
    v = actor.cast_voices(cfg, mini, p)
    assert v == {"narrator": "Charlotte"}
    assert mini.pinned("voices") == v and mini.pinned("tts_provider") == "fake"


def test_cast_solo_uses_genre_voice_and_solo_voice(mini, cfg):
    mini.pin("genre_voices", {"narrator": "Daniel"})
    assert actor.cast_voices(cfg, mini, FakeProvider()) == {"narrator": "Daniel"}


def test_cast_solo_voice_setting_wins(mini, cfg):
    cfg["casting"]["solo_voice"] = "Brian"
    assert actor.cast_voices(cfg, mini, FakeProvider()) == {"narrator": "Brian"}


def test_cast_solo_unresolvable_voice(mini, cfg):
    p = FakeProvider(voices={"Other": "id"})
    with pytest.raises(ValueError, match="casting the narrator: .*Charlotte"):
        actor.cast_voices(cfg, mini, p)
    assert mini.pinned("voices") is None


def test_cast_solo_voice_not_in_account(mini, cfg):
    p = FakeProvider(voices={"Charlotte": "idC"}, listed=["someone-else"])
    with pytest.raises(ValueError, match="not in your ElevenLabs account"):
        actor.cast_voices(cfg, mini, p)
    assert mini.pinned("voices") is None


def test_cast_solo_listing_failure_is_not_fatal(mini, cfg):
    p = FakeProvider()
    p.list_voices = lambda: (_ for _ in ()).throw(RuntimeError("down"))
    assert actor.cast_voices(cfg, mini, p) == {"narrator": "Charlotte"}


def test_cast_solo_voice_present_in_account(mini, cfg):
    p = FakeProvider(voices={"Charlotte": "idC"}, listed=["idC"])
    assert actor.cast_voices(cfg, mini, p) == {"narrator": "Charlotte"}


def test_cast_mode_assigns_by_gender_and_frequency(repo_root, cfg):
    cfg["casting"]["mode"] = "cast"
    cfg["casting"]["max_named_voices"] = 2
    b = cast_story(repo_root, ["Ann", "Bea", "Cat", "Dan"], genders={"Dan": "male"})
    v = actor.cast_voices(cfg, b, FakeProvider())
    # Ann, Bea get the top two female voices; Cat is over the cap -> narrator; Dan is male (4th)
    assert v["narrator"] == "Charlotte"
    assert v["Ann"] == "Alice" and v["Bea"] == "Lily"
    assert v["Cat"] == "Charlotte" and v["Dan"] == "Charlotte"
    assert b.pinned("voices") == v


def test_cast_mode_male_voice_from_male_pool(repo_root, cfg):
    cfg["casting"]["mode"] = "cast"
    b = cast_story(repo_root, ["Dan"], genders={"Dan": "male"})
    assert actor.cast_voices(cfg, b, FakeProvider())["Dan"] == "Daniel"


def test_cast_mode_pool_exhausted_falls_to_narrator_not_wrong_gender(repo_root, cfg):
    cfg["casting"]["mode"] = "cast"
    cfg["casting"]["max_named_voices"] = 5
    b = cast_story(repo_root, ["A1", "A2", "A3", "A4"])
    v = actor.cast_voices(cfg, b, FakeProvider())
    assert [v[n] for n in ("A1", "A2", "A3")] == ["Alice", "Lily", "Matilda"]
    assert v["A4"] == "Charlotte"


def test_cast_mode_override_honoured_and_not_counted(repo_root, cfg):
    cfg["casting"]["mode"] = "cast"
    cfg["casting"]["max_named_voices"] = 1
    b = cast_story(repo_root, ["Ann", "Bea"], voice_override={"Bea": "George"})
    v = actor.cast_voices(cfg, b, FakeProvider())
    assert v["Bea"] == "George" and v["Ann"] == "Alice"


def test_cast_mode_flat_pool(repo_root, cfg):
    cfg["casting"]["mode"] = "cast"
    cfg["casting"]["character_voices"] = ["Alice", "Lily"]
    b = cast_story(repo_root, ["Ann", "Bea"])
    v = actor.cast_voices(cfg, b, FakeProvider())
    assert (v["Ann"], v["Bea"]) == ("Alice", "Lily")


def test_cast_mode_unresolvable_voice_names_speaker(repo_root, cfg):
    cfg["casting"]["mode"] = "cast"
    b = cast_story(repo_root, ["Ann"])
    p = FakeProvider(voices={"Charlotte": "c"})
    with pytest.raises(ValueError, match="casting Ann: "):
        actor.cast_voices(cfg, b, p)
    assert b.pinned("voices") is None


def test_cast_mode_voice_missing_from_account(repo_root, cfg):
    cfg["casting"]["mode"] = "cast"
    b = cast_story(repo_root, ["Ann"])
    p = FakeProvider(voices={"Charlotte": "c", "Alice": "a"}, listed=["c"])
    with pytest.raises(ValueError, match=r"not in your ElevenLabs account:\n  Ann: Alice \(a\)"):
        actor.cast_voices(cfg, b, p)


def test_cast_mode_ignores_breaks_and_uses_genre_voices(repo_root, cfg):
    cfg["casting"]["mode"] = "cast"
    b = cast_story(repo_root, ["Ann"])
    segs = b.chapter(1)["segments"] + [seg(BREAK, "")]
    set_segments(b, 1, segs)
    b.pin("genre_voices", {"narrator": "Daniel"})
    v = actor.cast_voices(cfg, b, FakeProvider())
    assert v["narrator"] == "Daniel" and BREAK not in v


# --- record_chapter ----------------------------------------------------------

def test_record_chapter_solo_timeline(mini, cfg, no_encode):
    cfg["casting"]["chapter_announcement"] = None
    p = FakeProvider()
    t = actor.record_chapter(cfg, mini, 1, {"narrator": "Charlotte"}, p)
    assert len(p.calls) == 1                       # solo: all segments merged into one run
    assert p.calls[0]["text"] == "One two three four. Five six."
    assert p.calls[0]["voice"] == "Charlotte"
    assert t["n"] == 1 and t["chapter_hash"] == mini.chapter_hash(1)
    assert t["announcement"] is None and t["provider"] == "fake"
    assert t["voices"] == {"narrator": "Charlotte"} and t["billed_units"] == 10 and t["unit"] == "credits"
    assert t["duration_ms"] == 100
    assert t["sentences"] == [{"start_ms": 0, "end_ms": 100, "text": p.calls[0]["text"],
                               "speaker": "Charlotte", "voice": "Charlotte"}]
    assert json.loads(mini.timeline_path(1).read_text()) == t
    assert mini.audio_path(1).exists()
    assert mini.manifest()["spend"]["fake_credits"] == 10


def test_record_chapter_solo_announcement_is_its_own_call_followed_by_gap(mini, cfg, no_encode):
    """Pins current behaviour: the announcement unit's speaker is 'narrator', not the
    solo voice, so it is never merged into the first run and a gap_ms pause follows it."""
    p = FakeProvider()
    t = actor.record_chapter(cfg, mini, 1, {"narrator": "Charlotte"}, p)
    assert [c["text"] for c in p.calls] == ["Chapter 1. T1.", "One two three four. Five six."]
    assert [c["voice"] for c in p.calls] == ["Charlotte", "Charlotte"]
    assert p.calls[1]["previous"] == "Chapter 1. T1."
    assert t["announcement"] == "Chapter 1. T1."
    assert t["sentences"][1]["start_ms"] == 100 + 400 and t["duration_ms"] == 600


def test_record_chapter_cast_gaps_between_voice_changes(mini, cfg, no_encode):
    cfg["casting"]["mode"] = "cast"
    cfg["casting"]["gap_ms"] = 400
    cfg["casting"]["chapter_announcement"] = None
    p = FakeProvider()
    voices = {"narrator": "Charlotte", "Ann Lee": "Alice"}
    t = actor.record_chapter(cfg, mini, 1, voices, p)
    assert [c["voice"] for c in p.calls] == ["Charlotte", "Alice"]
    assert [c["text"] for c in p.calls] == ["One two three four.", "Five six."]
    # each FakeClip is 100 ms; the second starts after clip(100)+gap(400)
    s = t["sentences"]
    assert s[1]["start_ms"] == 500 and t["duration_ms"] == 600
    # first cue extended toward the next by at most gap_ms without overrunning it
    assert s[0]["end_ms"] == 500
    # gap bytes are in the audio: 2 clips of 4800 bytes + 400ms of silence at 24k mono 16-bit
    assert no_encode.calls[0][0] == 4800 * 2 + int(24000 * 2 * 0.4)


def test_record_chapter_unknown_speaker_falls_back_to_narrator(mini, cfg, no_encode):
    cfg["casting"]["mode"] = "cast"
    cfg["casting"]["chapter_announcement"] = None
    p = FakeProvider()
    actor.record_chapter(cfg, mini, 1, {"narrator": "Charlotte"}, p)
    assert [c["voice"] for c in p.calls] == ["Charlotte", "Charlotte"]


def test_record_chapter_break_inserts_silence_no_synthesis(mini, cfg, no_encode):
    cfg["casting"]["chapter_announcement"] = None
    cfg["casting"]["break_ms"] = 2000
    set_segments(mini, 1, [seg("narrator", "A."), seg(BREAK, ""), seg("narrator", "B.")])
    p = FakeProvider()
    t = actor.record_chapter(cfg, mini, 1, {"narrator": "N"}, p)
    assert [c["text"] for c in p.calls] == ["A.", "B."]
    assert t["sentences"][1]["start_ms"] == 100 + 2000
    assert t["duration_ms"] == 100 + 2000 + 100
    assert t["billed_units"] == 20


def test_record_chapter_long_run_splits_without_gap(mini, cfg, no_encode):
    cfg["casting"]["chapter_announcement"] = None
    p = FakeProvider()
    p.max_chars = 12
    set_segments(mini, 1, [seg("narrator", "Aaaa aaaa."), seg("narrator", "Bbbb bbbb.")])
    t = actor.record_chapter(cfg, mini, 1, {"narrator": "N"}, p)
    assert len(p.calls) == 2 and t["sentences"][1]["start_ms"] == 100   # same voice: no gap


# --- record_all --------------------------------------------------------------

@pytest.fixture
def rec(monkeypatch, cfg, no_encode):
    prov = FakeProvider()
    monkeypatch.setattr(actor, "_require_ffmpeg", lambda: None)
    monkeypatch.setattr(actor.tts, "build", lambda c: prov)
    return prov


def test_record_all_requires_approval(mini, cfg, rec):
    with pytest.raises(ValueError, match=r"chapters \[1, 2\] are not approved"):
        actor.record_all(cfg, mini)
    assert rec.calls == [] and mini.stage_status("record") == "pending"


def test_record_all_force_runs_and_sets_stage(mini, cfg, rec, capsys):
    actor.record_all(cfg, mini, force=True)
    assert mini.stage_status("record") == "done"
    assert all(mini.audio_path(n).exists() and mini.timeline_path(n).exists() for n in (1, 2))
    assert len(rec.calls) == 4                      # announcement + body, per chapter
    out = capsys.readouterr().out
    assert "provider: fake, solo narration by Charlotte" in out and "chapter 1: synthesizing" in out
    assert "assumed" in out                                  # calibration report printed


def test_record_all_skips_existing_audio(mini, cfg, rec, capsys):
    mini.approve_chapter(1)
    mini.approve_chapter(2)
    actor.record_all(cfg, mini)
    n_first = len(rec.calls)
    actor.record_all(cfg, mini)
    assert len(rec.calls) == n_first
    assert "audio exists, skipping" in capsys.readouterr().out


def test_record_all_rerecords_changed_chapter(mini, cfg, rec, capsys):
    actor.record_all(cfg, mini, force=True)
    set_segments(mini, 1, [seg("narrator", "Entirely new words.")])
    rec.calls.clear()
    actor.record_all(cfg, mini, force=True)
    assert [c["text"] for c in rec.calls] == ["Chapter 1. T1.", "Entirely new words."]
    assert "the text changed since it was narrated, re-recording" in capsys.readouterr().out


def test_record_all_rerecords_when_casting_changes(mini, cfg, rec, capsys):
    actor.record_all(cfg, mini, force=True)
    mini.pin("voices", {"narrator": "Daniel"})
    rec.calls.clear()
    actor.record_all(cfg, mini, force=True)
    assert len(rec.calls) == 4 and {c["voice"] for c in rec.calls} == {"Daniel"}
    assert "the casting changed" in capsys.readouterr().out


def test_record_all_rerecords_when_announcement_changes(mini, cfg, rec):
    actor.record_all(cfg, mini, force=True)
    cfg["casting"]["chapter_announcement"] = "Part {n}."
    rec.calls.clear()
    actor.record_all(cfg, mini, force=True)
    assert len(rec.calls) == 4 and rec.calls[0]["text"] == "Part 1."


def test_record_all_quiet(mini, cfg, rec, capsys):
    actor.record_all(cfg, mini, verbose=False, force=True)
    assert capsys.readouterr().out == ""


def test_record_all_real_story_nothing_to_record(story, cfg, rec):
    """Every chapter of the real bundle is approved, narrated and current."""
    assert not any(story.narration_stale(n, actor.announcement(cfg, story, n),
                                         story.pinned("voices")) for n in story.chapter_numbers())
    actor.record_all(cfg, story)
    assert rec.calls == [] and story.stage_status("record") == "done"


# --- estimate / ffmpeg --------------------------------------------------------

def test_estimate_elevenlabs(mini, cfg):
    e = actor.estimate(cfg, mini)
    chars = sum(len(s["text"]) for n in (1, 2) for s in mini.chapter(n)["segments"])
    assert e == {"billed_units": chars, "unit": "credits", "engine": "eleven_multilingual_v2",
                 "usd": round(chars / 1000 * 0.22, 2), "words": 12, "runtime_min": 0.1, "chars": chars}


def test_estimate_polly_and_skips_unwritten(mini, cfg):
    cfg["tts_provider"] = "polly"
    mini.chapter_json(2).unlink()
    e = actor.estimate(cfg, mini)
    assert e["unit"] == "characters" and e["engine"] == "generative" and e["words"] == 6
    assert e["billed_units"] == e["chars"] == len("One two three four.Five six.")


def test_estimate_real_story(story, cfg):
    e = actor.estimate(cfg, story)
    assert e["words"] == story.word_count() and e["unit"] == "credits"
    assert e["billed_units"] == e["chars"]
    assert e["runtime_min"] == round(e["words"] / 150, 1)


def test_require_ffmpeg_missing(monkeypatch):
    def boom(*a, **k): raise FileNotFoundError
    monkeypatch.setattr(actor.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="ffmpeg is not on PATH"):
        actor._require_ffmpeg()


def test_require_ffmpeg_nonzero(monkeypatch):
    def boom(*a, **k): raise subprocess.CalledProcessError(1, "ffmpeg")
    monkeypatch.setattr(actor.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="ffmpeg is not on PATH"):
        actor._require_ffmpeg()


def test_encode_command(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(actor.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd, **kw) or None)
    out = tmp_path / "sub" / "a.mp3"
    actor._encode(b"\x00\x00", 24000, out)
    assert out.parent.is_dir()
    assert seen["cmd"][:2] == ["ffmpeg", "-y"] and "s16le" in seen["cmd"] and "24000" in seen["cmd"]
    assert seen["cmd"][-1] == str(out) and seen["input"] == b"\x00\x00"
    assert seen["check"] is True


@pytest.mark.slow
@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_encode_real_mp3(tmp_path):
    out = tmp_path / "a.mp3"
    actor._encode(b"\x00\x00" * 24000, 24000, out)       # 1 s of silence
    assert out.stat().st_size > 500
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert 0.9 < float(probe.stdout) < 1.3
