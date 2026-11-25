"""casting_api.py: the casting page's functions, on a scratch arc."""
from __future__ import annotations

import json
import sys

import pytest

from story_pipeline import casting, casting_apply
from story_pipeline.review import casting_api, runner

ARC = "amberlight"
STORY = "the-cracked-beam"
V_SAMARA = "SAMARAxxxxxxxxxxxxxx"[:20]
V_EMILIA = "EMILIAxxxxxxxxxxxxxx"[:20]
V_OTHER = "OTHERxxxxxxxxxxxxxxx"[:20]
assert all(len(v) == 20 for v in (V_SAMARA, V_EMILIA, V_OTHER))


@pytest.fixture
def arc(real_story, real_bibles):
    return ARC


def audition(cfg, voice_id, name, speaker="narrator", model="eleven_multilingual_v2",
             verdict=None, story=STORY, file=None, line="A line."):
    """Record one take for a voice (and its audio file) under the arc."""
    stories = casting_api._stories(cfg)
    rec = casting.load(ARC, stories)
    casting.note_voice(rec, voice_id, name)
    # the record stores the path relative to _casting/, as check_voices writes it
    file = file or f"audio/{story}/{speaker.replace(' ', '_')}-{voice_id}-{model}.mp3"
    p = casting.casting_dir(ARC, stories) / file
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"ID3fakeaudio")
    casting.add_try(rec, voice_id, story=story, speaker=speaker, model=model,
                    line=line, file=file)
    if verdict:
        casting.set_verdict(rec, speaker, voice_id, verdict, None)
    casting.save(rec, stories)
    return file


# --- helpers ---------------------------------------------------------------


def test_stories_dir_defaults_and_follows_cfg(tmp_path):
    assert str(casting_api._stories({})) == "stories"
    assert casting_api._stories({"stories_dir": str(tmp_path)}) == tmp_path


def test_key_is_underscore_casting_arc():
    assert casting_api._key("amberlight") == "_casting-amberlight"


def test_arc_validation(cfg, arc):
    assert casting_api._arc(cfg, ARC) == ARC
    with pytest.raises(ValueError, match="no arc called 'nope'"):
        casting_api._arc(cfg, "nope")


def test_voice_ids_accepts_strings_and_dicts():
    cfg = {"elevenlabs": {"voice_library": {"A": "idA", "B": {"voice_id": "idB"}}}}
    assert casting_api._voice_ids(cfg) == {"A": "idA", "B": "idB"}
    assert casting_api._voice_ids({}) == {}
    assert casting_api._voice_ids({"elevenlabs": None}) == {}


# --- index -----------------------------------------------------------------


def test_index_empty_without_arcs(cfg):
    assert casting_api.index(cfg) == {"arcs": []}


def test_index_counts(cfg, arc):
    assert casting_api.index(cfg) == {"arcs": [
        {"arc": ARC, "stories": 1, "candidates": 0, "auditioned": 0, "cast": 0}]}
    casting.set_candidate(ARC, "narrator", V_OTHER, stories=casting_api._stories(cfg))
    casting.set_candidate(ARC, "Mr Silas Rowe", V_EMILIA, stories=casting_api._stories(cfg))
    audition(cfg, V_SAMARA, "Samara X", verdict="cast")
    audition(cfg, V_EMILIA, "Emilia")
    (row,) = casting_api.index(cfg)["arcs"]
    assert (row["candidates"], row["auditioned"], row["cast"]) == (2, 2, 1)


def test_index_ignores_hidden_underscore_dirs(cfg, arc, repo_root):
    (repo_root / "stories" / "_scratch" / "x").mkdir(parents=True)
    (repo_root / "stories" / "_scratch" / "x" / "manifest.json").write_text("{}")
    assert [a["arc"] for a in casting_api.index(cfg)["arcs"]] == [ARC]


# --- view / estimate -------------------------------------------------------


def test_view_unknown_arc(cfg, arc):
    with pytest.raises(ValueError, match="no arc called"):
        casting_api.view(cfg, "zzz")


def test_view_shape_and_defaults(cfg, arc, runs_dir):
    v = casting_api.view(cfg, ARC)
    assert v["arc"] == ARC and v["story"] == STORY and v["stories"] == [STORY]
    assert set(v) >= {"speakers", "pool", "estimate", "job", "updated"}
    names = [s["speaker"] for s in v["speakers"]]
    assert names[0] == "narrator" and "Mr Silas Rowe" in names
    assert v["job"]["state"] == "none"
    est = v["estimate"]
    assert est["takes"] == sum(x["takes"] for x in est["per_speaker"].values())
    assert est["chars"] == sum(x["chars"] for x in est["per_speaker"].values())


def test_view_unknown_story_falls_back_to_first(cfg, arc, runs_dir):
    assert casting_api.view(cfg, ARC, "no-such-story")["story"] == STORY


def test_view_attaches_bible_profile_for_bible_member(cfg, arc, runs_dir):
    v = casting_api.view(cfg, ARC, STORY)
    by = {s["speaker"]: s for s in v["speakers"]}
    silas = by["Mr Silas Rowe"]["bible"]
    assert silas["source"] == "bible"
    assert set(silas) <= {"source", *casting_api._PROFILE}
    assert by["narrator"]["bible"] is None


def test_view_falls_back_to_story_cast_without_bible(cfg, arc, runs_dir, repo_root):
    import shutil
    shutil.rmtree(repo_root / "bibles")
    (repo_root / "bibles").mkdir()
    v = casting_api.view(cfg, ARC, STORY)
    by = {s["speaker"]: s for s in v["speakers"]}
    silas = by["Mr Silas Rowe"]["bible"]
    assert silas is not None and silas["source"] == "story"


def test_view_tolerates_missing_outline(cfg, arc, runs_dir, repo_root):
    (repo_root / "stories" / ARC / STORY / "outline.json").unlink()
    v = casting_api.view(cfg, ARC, STORY)
    assert all("bible" in s for s in v["speakers"])


def test_view_job_reflects_runner_state(cfg, arc, runs_dir):
    d = runs_dir / f"_casting-{ARC}" / "20260101T000000-audition"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({
        "job_id": d.name, "slug": f"_casting-{ARC}", "stage": "audition",
        "status": "done", "exit_code": 0, "pid": None, "started": 1, "finished": 2}))
    (d / "output.log").write_text("")
    assert casting_api.view(cfg, ARC)["job"]["state"] == "done"


def _estimate_cfg(cfg):
    cfg = json.loads(json.dumps(cfg))
    cfg["elevenlabs"]["voice_library"] = {"Samara X": V_SAMARA}
    cfg["elevenlabs"]["usd_per_1k_credits"] = 0.5
    cfg["elevenlabs"]["model"] = "eleven_multilingual_v2"
    return cfg


def test_estimate_counts_missing_baseline_and_v4_takes_for_current_voice(
        cfg, arc, runs_dir):
    cfg = _estimate_cfg(cfg)
    audition(cfg, V_SAMARA, "Samara X")  # baseline model take exists, v4 does not
    v = casting_api.view(cfg, ARC, STORY)
    narr = next(s for s in v["speakers"] if s["speaker"] == "narrator")
    est = v["estimate"]["per_speaker"]["narrator"]
    assert est["takes"] == 1  # v4 only
    assert est["chars"] == len(narr["line"])
    assert est["usd"] == round(len(narr["line"]) / 1000 * 0.5, 2)
    assert v["estimate"]["baseline_model"] == "eleven_multilingual_v2"


def test_estimate_zero_once_both_models_exist(cfg, arc, runs_dir):
    cfg = _estimate_cfg(cfg)
    audition(cfg, V_SAMARA, "Samara X")
    audition(cfg, V_SAMARA, "Samara X", model=casting_api.V4)
    v = casting_api.view(cfg, ARC, STORY)
    assert v["estimate"]["per_speaker"]["narrator"]["takes"] == 0


def test_estimate_candidate_pending_and_retry(cfg, arc, runs_dir):
    cfg = _estimate_cfg(cfg)
    stories = casting_api._stories(cfg)
    casting.set_candidate(ARC, "narrator", V_OTHER, name="Other", stories=stories)
    casting.set_candidate(ARC, "narrator", V_EMILIA, name="Emilia", stories=stories)
    rec = casting.load(ARC, stories)
    casting.add_unavailable(rec, speaker="narrator", pick=V_EMILIA, note="not in My Voices")
    casting.save(rec, stories)
    v = casting_api.view(cfg, ARC, STORY)
    narr = next(s for s in v["speakers"] if s["speaker"] == "narrator")
    cands = {e["id"]: e for e in narr["entries"] if e["role"] == "candidate"}
    assert cands[V_OTHER]["pending"] is True and cands[V_EMILIA]["pending"] is True
    est = v["estimate"]["per_speaker"]["narrator"]
    # the current voice (in the library, two models to cover) + both candidates
    assert est["takes"] == 2 + 2
    assert est["retry"] == 1      # only the unavailable one counts as a retry
    assert v["estimate"]["retry"] == 1 and v["estimate"]["takes"] >= 2


def test_estimate_skips_speakers_without_lines(cfg, arc, runs_dir):
    cfg = _estimate_cfg(cfg)
    casting.set_candidate(ARC, "Ghost Speaker", V_OTHER, stories=casting_api._stories(cfg))
    v = casting_api.view(cfg, ARC, STORY)
    ghost = next(s for s in v["speakers"] if s["speaker"] == "Ghost Speaker")
    assert ghost["line"] is None
    assert "Ghost Speaker" not in v["estimate"]["per_speaker"]


def test_estimate_without_rate_is_free(cfg, arc, runs_dir):
    cfg = _estimate_cfg(cfg)
    cfg["elevenlabs"]["usd_per_1k_credits"] = None
    audition(cfg, V_SAMARA, "Samara X")
    est = casting_api.view(cfg, ARC, STORY)["estimate"]
    assert est["takes"] >= 1 and est["usd"] == 0


# --- job / log -------------------------------------------------------------


def _job_dir(runs_dir, **over):
    d = runs_dir / f"_casting-{ARC}" / "20260101T000000-audition"
    d.mkdir(parents=True)
    meta = {"job_id": d.name, "slug": f"_casting-{ARC}", "stage": "audition",
            "status": "running", "exit_code": None, "pid": None, "started": 1,
            "finished": None, **over}
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def _marks(*ms):
    return "".join(runner.MARKER + json.dumps(m) + "\n" for m in ms)


def test_job_progress_counts_takes_and_failures(cfg, runs_dir):
    d = _job_dir(runs_dir, status="done", exit_code=0)
    (d / "output.log").write_text(_marks(
        {"event": "plan", "total": 5},
        {"event": "start", "speaker": "A"},
        {"event": "take", "speaker": "A", "ok": True},
        {"event": "take", "speaker": "B", "ok": False},
        {"event": "take", "speaker": "B", "ok": True}))
    j = casting_api.job(cfg, ARC)
    assert (j["total"], j["done"], j["failed"]) == (5, 3, 1)
    assert j["speaker"] == "B" and "progress" not in j


def test_job_speaker_falls_back_to_first_asked(cfg, runs_dir):
    d = _job_dir(runs_dir, speakers=["Zed", "Yan"])
    (d / "output.log").write_text(_marks({"event": "plan", "total": 2}))
    assert casting_api.job(cfg, ARC)["speaker"] == "Zed"


def test_job_speaker_none_for_everyone_run_before_first_take(cfg, runs_dir):
    d = _job_dir(runs_dir, speakers=[])
    (d / "output.log").write_text("")
    assert casting_api.job(cfg, ARC)["speaker"] is None


def test_job_with_no_runs(cfg, runs_dir):
    j = casting_api.job(cfg, ARC)
    assert j["state"] == "none" and j["total"] is None and j["done"] == 0


def test_log_passthrough(cfg, runs_dir):
    d = _job_dir(runs_dir, status="done", exit_code=0)
    (d / "output.log").write_text("a\nb\n")
    assert casting_api.log(cfg, ARC)["text"] == "a\nb"
    assert casting_api.log(cfg, ARC, 2)["text"] == "b"


# --- run / cancel ----------------------------------------------------------


@pytest.fixture
def script(repo_root):
    (repo_root / "check_voices.py").write_text("print('stub')\n")


def test_run_builds_audition_command(cfg, arc, started, script, runs_dir):
    out = casting_api.run(cfg, ARC, STORY, speakers=["A B", "C"], voices=["v1"],
                          force=True)
    assert out["ok"] is True and "job" in out
    (call,) = started
    assert call["key"] == "_casting-amberlight" and call["stage"] == "audition"
    argv = call["argv"]
    assert argv[:2] == [sys.executable, "check_voices.py"]
    assert argv[argv.index("--config") + 1] == "config.yaml"
    assert argv[argv.index("--story") + 1] == f"stories/{ARC}/{STORY}"
    for flag in ("--sample", "--add-missing", "--yes", "--force"):
        assert flag in argv
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--speaker"] == ["A B", "C"]
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--voice"] == ["v1"]
    assert call["extra"] == {"arc": ARC, "story": STORY,
                             "speakers": ["A B", "C"], "voices": ["v1"]}
    assert "already running for amberlight" in call["busy"]


def test_run_minimal_has_no_optional_flags(cfg, arc, started, script, runs_dir):
    casting_api.run(cfg, ARC, STORY)
    argv = started[0]["argv"]
    assert "--force" not in argv and "--speaker" not in argv and "--voice" not in argv
    assert started[0]["extra"]["speakers"] == []


def test_run_validations(cfg, arc, started, script):
    with pytest.raises(ValueError, match="no arc called"):
        casting_api.run(cfg, "zzz", STORY)
    with pytest.raises(ValueError, match="isn't a written story in amberlight"):
        casting_api.run(cfg, ARC, "ghost-story")
    assert started == []


def test_run_requires_script_in_cwd(cfg, arc, started):
    with pytest.raises(ValueError, match="check_voices.py isn't in the working directory"):
        casting_api.run(cfg, ARC, STORY)
    assert started == []


def test_run_refuses_story_without_written_chapters(cfg, arc, started, script, repo_root):
    d = repo_root / "stories" / ARC / "empty-one"
    d.mkdir()
    (d / "manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="isn't a written story"):
        casting_api.run(cfg, ARC, "empty-one")


def test_run_busy_while_a_job_is_live(cfg, arc, script, runs_dir):
    # real runner, harmless long-lived child standing in for an audition
    runner.start_command(f"_casting-{ARC}", "audition",
                         [sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        with pytest.raises(ValueError, match="Auditions are already running"):
            casting_api.run(cfg, ARC, STORY)
    finally:
        casting_api.cancel(cfg, ARC)


def test_cancel_stops_live_job_and_returns_job(cfg, runs_dir):
    runner.start_command(f"_casting-{ARC}", "audition",
                         [sys.executable, "-c", "import time;time.sleep(30)"])
    out = casting_api.cancel(cfg, ARC)
    assert out["ok"] is True and out["job"]["state"] == "cancelled"


def test_cancel_with_nothing_running(cfg, runs_dir):
    with pytest.raises(ValueError, match="nothing is running"):
        casting_api.cancel(cfg, ARC)


# --- verdict ---------------------------------------------------------------


def _record(cfg):
    return casting.load(ARC, casting_api._stories(cfg))


def test_verdict_set_note_stripped_and_persisted(cfg, arc):
    audition(cfg, V_SAMARA, "Samara X")
    assert casting_api.verdict(cfg, ARC, "narrator", V_SAMARA, "maybe", "  warm  ") == {"ok": True}
    v = _record(cfg)["voices"][V_SAMARA]["verdicts"]["narrator"]
    assert v["verdict"] == "maybe" and v["note"] == "warm"
    assert (casting.casting_dir(ARC, casting_api._stories(cfg)) / "CASTING.md").is_file()


def test_verdict_blank_note_is_none(cfg, arc):
    audition(cfg, V_SAMARA, "Samara X")
    casting_api.verdict(cfg, ARC, "narrator", V_SAMARA, "no", "   ")
    assert _record(cfg)["voices"][V_SAMARA]["verdicts"]["narrator"]["note"] is None


@pytest.mark.parametrize("clear", [None, "", "clear"])
def test_verdict_clear_variants(cfg, arc, clear):
    audition(cfg, V_SAMARA, "Samara X", verdict="cast")
    casting_api.verdict(cfg, ARC, "narrator", V_SAMARA, clear)
    assert "narrator" not in _record(cfg)["voices"][V_SAMARA]["verdicts"]


def test_verdict_cast_demotes_previous_cast_to_maybe(cfg, arc):
    audition(cfg, V_SAMARA, "Samara X", verdict="cast")
    audition(cfg, V_EMILIA, "Emilia")
    casting_api.verdict(cfg, ARC, "narrator", V_EMILIA, "cast")
    rec = _record(cfg)
    assert rec["voices"][V_EMILIA]["verdicts"]["narrator"]["verdict"] == "cast"
    old = rec["voices"][V_SAMARA]["verdicts"]["narrator"]
    assert old["verdict"] == "maybe" and "(recast)" in old["note"]


def test_verdict_invalid_value(cfg, arc):
    audition(cfg, V_SAMARA, "Samara X")
    with pytest.raises(ValueError, match="verdict must be one of cast, maybe, no, or clear"):
        casting_api.verdict(cfg, ARC, "narrator", V_SAMARA, "love")


def test_verdict_unknown_arc(cfg, arc):
    with pytest.raises(ValueError, match="no arc called"):
        casting_api.verdict(cfg, "zzz", "narrator", V_SAMARA, "cast")


def test_verdict_by_voice_name_resolves(cfg, arc):
    audition(cfg, V_SAMARA, "Samara X")
    casting_api.verdict(cfg, ARC, "narrator", "Samara X", "cast")
    assert _record(cfg)["voices"][V_SAMARA]["verdicts"]["narrator"]["verdict"] == "cast"


# --- candidate -------------------------------------------------------------


def test_candidate_add_then_remove(cfg, arc):
    stories = casting_api._stories(cfg)
    assert casting_api.candidate(cfg, ARC, "  narrator ", V_OTHER, " Other ", " warm ") == {"ok": True}
    data = casting.load_candidates(ARC, stories)
    assert data == {"narrator": [{"id": V_OTHER, "name": "Other", "note": "warm"}]}
    casting_api.candidate(cfg, ARC, "narrator", V_OTHER, remove=True)
    assert casting.load_candidates(ARC, stories) == {}


def test_candidate_readd_replaces_not_duplicates(cfg, arc):
    casting_api.candidate(cfg, ARC, "narrator", V_OTHER, "One")
    casting_api.candidate(cfg, ARC, "narrator", V_OTHER, "Two")
    picks = casting.load_candidates(ARC, casting_api._stories(cfg))["narrator"]
    assert [p["name"] for p in picks] == ["Two"]


@pytest.mark.parametrize("speaker", ["", "   ", None])
def test_candidate_requires_speaker(cfg, arc, speaker):
    with pytest.raises(ValueError, match="Which character"):
        casting_api.candidate(cfg, ARC, speaker, V_OTHER)


@pytest.mark.parametrize("vid", ["", "short", "has spaces in it 20ch", "x" * 21, "bad-chars-bad-chars!"])
def test_candidate_requires_20_char_alnum_id(cfg, arc, vid):
    with pytest.raises(ValueError, match="20 letters and digits"):
        casting_api.candidate(cfg, ARC, "narrator", vid)


def test_candidate_unknown_arc(cfg, arc):
    with pytest.raises(ValueError, match="no arc called"):
        casting_api.candidate(cfg, "zzz", "narrator", V_OTHER)


# --- audio_path ------------------------------------------------------------


def test_audio_path_serves_take(cfg, arc):
    f = audition(cfg, V_SAMARA, "Samara X")
    p = casting_api.audio_path(cfg, ARC, f)
    assert p.is_file() and p.suffix == ".mp3"


@pytest.mark.parametrize("rel", [
    "../auditions.json",
    "../../the-cracked-beam/manifest.json",
    "../../../../etc/passwd",
    "/etc/passwd",
    "missing.mp3",
    "",
    ".",
])
def test_audio_path_refuses_escapes_and_missing(cfg, arc, rel):
    audition(cfg, V_SAMARA, "Samara X")
    with pytest.raises(FileNotFoundError):
        casting_api.audio_path(cfg, ARC, rel)


def test_audio_path_refuses_non_mp3_inside_audio_dir(cfg, arc):
    audition(cfg, V_SAMARA, "Samara X")
    d = casting.casting_dir(ARC, casting_api._stories(cfg)) / "audio"
    (d / "notes.txt").write_text("x")
    with pytest.raises(FileNotFoundError):
        casting_api.audio_path(cfg, ARC, "notes.txt")


def test_audio_path_refuses_symlink_out_of_audio_dir(cfg, arc, tmp_path):
    audition(cfg, V_SAMARA, "Samara X")
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"x")
    d = casting.casting_dir(ARC, casting_api._stories(cfg)) / "audio"
    (d / "link.mp3").symlink_to(outside)
    with pytest.raises(FileNotFoundError):
        casting_api.audio_path(cfg, ARC, "link.mp3")


def test_audio_path_accepts_uppercase_extension(cfg, arc):
    f = audition(cfg, V_SAMARA, "Samara X", file=f"audio/{STORY}/UP.MP3")
    assert casting_api.audio_path(cfg, ARC, f).name == "UP.MP3"


def test_audio_path_unknown_arc_is_value_error(cfg, arc):
    with pytest.raises(ValueError):
        casting_api.audio_path(cfg, "zzz", "a.mp3")


# --- apply_plan / apply ----------------------------------------------------


def test_apply_plan_requires_somebody_cast(cfg, arc):
    with pytest.raises(casting_apply.ApplyError, match="Nobody is cast yet"):
        casting_api.apply_plan(cfg, ARC)


def test_apply_plan_real_with_one_cast(cfg, arc):
    audition(cfg, V_OTHER, "Brand New Voice", verdict="cast")
    plan = casting_api.apply_plan(cfg, ARC)
    assert plan["arc"] == ARC
    assert plan["cast"][0]["speaker"] == "narrator" and plan["cast"][0]["id"] == V_OTHER
    assert plan["config"]["library_add"]
    assert plan["config"]["model"] == ["eleven_multilingual_v2", casting_api.V4]
    assert plan["config"]["mode"] == ["solo", "cast"]


def test_apply_plan_flags_switch_off_changes(cfg, arc):
    audition(cfg, V_OTHER, "Brand New Voice", verdict="cast")
    plan = casting_api.apply_plan(cfg, ARC, use_v4=False, cast_mode=False)
    assert "model" not in plan["config"] and "mode" not in plan["config"]


def test_apply_plan_unknown_arc(cfg, arc):
    with pytest.raises(ValueError, match="no arc called"):
        casting_api.apply_plan(cfg, "zzz")


def test_apply_passes_flags_and_config_path(cfg, arc, monkeypatch):
    seen = {}

    def fake(cfg_, arc_, **kw):
        seen.update(arc=arc_, **kw)
        return {"ok": True}
    monkeypatch.setattr(casting_apply, "apply", fake)
    assert casting_api.apply(cfg, ARC) == {"ok": True}
    assert seen == {"arc": ARC, "use_v4": True, "cast_mode": True, "commit": True,
                    "push": True, "config_path": "config.yaml"}
    casting_api.apply({**cfg, "_config_path": "/x.yaml"}, ARC, use_v4=False,
                      cast_mode=False, commit=False, push=False)
    assert seen == {"arc": ARC, "use_v4": False, "cast_mode": False, "commit": False,
                    "push": False, "config_path": "/x.yaml"}


def test_apply_unknown_arc_never_reaches_applier(cfg, arc, monkeypatch):
    monkeypatch.setattr(casting_apply, "apply", lambda *a, **k: pytest.fail("called"))
    with pytest.raises(ValueError):
        casting_api.apply(cfg, "zzz")
