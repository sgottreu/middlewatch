"""director.py: shot list, SRT, ffmpeg command construction, stage transitions.

Command-construction tests monkeypatch `_run`; the real tiny renders at the
bottom are marked slow + needs_ffmpeg.
"""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from story_pipeline import config as cfgmod
from story_pipeline.agents import director
from story_pipeline.bundle import Bundle
from tests.conftest import has_ffmpeg
from tests.pipeline.conftest import make_mini


@pytest.fixture
def runs(monkeypatch):
    calls = []
    monkeypatch.setattr(director, "_run", lambda args: calls.append(list(args)))
    return calls


def tl(b, n, dur, sentences=()):
    b.timeline_path(n).write_text(json.dumps({"n": n, "duration_ms": dur, "sentences": list(sentences)}))


def scenes(b, data):
    b.scenes_path.write_text(json.dumps(data))


def sent(s, e, text, speaker="narrator"):
    return {"start_ms": s, "end_ms": e, "text": text, "speaker": speaker}


# --- _run / _progress --------------------------------------------------------

def test_run_failure_includes_command_and_stderr_tail(monkeypatch):
    stderr = "\n".join(f"line{i}" for i in range(40))
    monkeypatch.setattr(director.subprocess, "run",
                        lambda a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr=stderr))
    with pytest.raises(RuntimeError, match="ffmpeg failed") as e:
        director._run(["ffmpeg", "-y", "-i", "in"])
    msg = str(e.value)
    assert "ffmpeg -y -i in" in msg and "line39" in msg and "line24" not in msg   # last 15 lines


def test_run_success_silent(monkeypatch):
    monkeypatch.setattr(director.subprocess, "run",
                        lambda a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""))
    director._run(["ffmpeg"])


def test_progress_marker_only_when_env_set(monkeypatch, capsys):
    director._progress(event="shot", done=1, total=2)
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("MW_PROGRESS", "1")
    director._progress(event="shot", done=1, total=2)
    line = capsys.readouterr().out.strip()
    assert line.startswith("::progress ") and json.loads(line[len("::progress "):]) == {
        "event": "shot", "done": 1, "total": 2}


# --- build_shot_list ---------------------------------------------------------

def test_shot_list_offsets_and_durations(mini):
    tl(mini, 1, 10_000)
    tl(mini, 2, 5_000)
    scenes(mini, {"1": {"shots": [{"at_ms": 6000, "image": "images/01-01.jpg"},
                                  {"at_ms": 2000, "image": "images/01-00.jpg"}]},
                  "2": {"shots": [{"at_ms": 0, "image": "images/02-00.jpg"}]}})
    shots = director.build_shot_list(mini)
    assert [s["chapter"] for s in shots] == [1, 1, 2]
    # sorted by at_ms; the first shot holds from the chapter start regardless of its at_ms
    assert shots[0]["image"] == mini.root / "images/01-00.jpg"
    assert (shots[0]["start_ms"], shots[0]["duration_ms"]) == (0, 6000)
    assert (shots[1]["start_ms"], shots[1]["duration_ms"]) == (6000, 4000)
    assert (shots[2]["start_ms"], shots[2]["duration_ms"]) == (10_000, 5000)


def test_shot_list_minimum_duration_1200(mini):
    tl(mini, 1, 10_000)
    tl(mini, 2, 500)
    scenes(mini, {"1": {"shots": [{"at_ms": 0, "image": "a.jpg"}, {"at_ms": 9_500, "image": "b.jpg"}]},
                  "2": {"shots": [{"at_ms": 0, "image": "c.jpg"}]}})
    shots = director.build_shot_list(mini)
    assert shots[0]["duration_ms"] == 9500 and shots[1]["duration_ms"] == 1200
    assert shots[2]["duration_ms"] == 1200 and shots[2]["start_ms"] == 10_000


def test_shot_list_missing_chapter_in_scenes(mini):
    tl(mini, 1, 1000)
    tl(mini, 2, 1000)
    scenes(mini, {"1": {"shots": [{"at_ms": 0, "image": "a.jpg"}]}})
    with pytest.raises(KeyError):
        director.build_shot_list(mini)


def test_shot_list_real_story(story):
    shots = director.build_shot_list(story)
    assert len(shots) == 32
    assert [s["chapter"] for s in shots] == [n for n in range(1, 9) for _ in range(4)]
    starts = [s["start_ms"] for s in shots]
    assert starts == sorted(starts) and starts[0] == 0
    for a, b in zip(shots, shots[1:]):
        assert b["start_ms"] >= a["start_ms"]
    # shots tile the narration: each chapter's shot durations add up to its audio
    for n in range(1, 9):
        total = sum(s["duration_ms"] for s in shots if s["chapter"] == n)
        assert total >= story.timeline(n)["duration_ms"]
        assert total - story.timeline(n)["duration_ms"] < 1200
    assert shots[0]["image"] == story.root / "images" / "01-00.jpg"


# --- write_srt ---------------------------------------------------------------

def test_write_srt_format_offsets_and_quotes(mini):
    tl(mini, 1, 4000, [sent(0, 1500, " Hello. "), sent(1500, 3_700_000 // 1000 * 1000 - 3_696_500 + 1500, "x")])
    tl(mini, 2, 2000, [sent(100, 900, "Hi there.", speaker="Ann Lee")])
    path = director.write_srt(mini)
    assert path == mini.srt_path
    lines = path.read_text().split("\n")
    assert lines[:4] == ["1", "00:00:00,000 --> 00:00:01,500", "Hello.", ""]
    assert lines[4] == "2"
    # chapter two is offset by chapter one's duration; dialogue wrapped in curly quotes
    assert "3" in lines and "00:00:04,100 --> 00:00:04,900" in lines
    assert "“Hi there.”" in lines


def test_write_srt_timestamp_rollover(mini):
    tl(mini, 1, 4_000_000, [sent(3_723_004, 3_725_999, "Late.")])
    tl(mini, 2, 10, [])
    assert "01:02:03,004 --> 01:02:05,999" in director.write_srt(mini).read_text()


def test_write_srt_real_story(story):
    path = director.write_srt(story)
    text = path.read_text()
    n_sent = sum(len(story.timeline(n)["sentences"]) for n in story.chapter_numbers())
    blocks = [b for b in text.strip().split("\n\n") if b.strip()]
    assert len(blocks) == n_sent
    assert blocks[0].split("\n")[0] == "1" and blocks[-1].split("\n")[0] == str(n_sent)
    first = story.timeline(1)["sentences"][0]
    assert blocks[0].split("\n")[2] == first["text"].strip()
    dialogue = sum(1 for n in story.chapter_numbers()
                   for s in story.timeline(n)["sentences"] if s["speaker"] != "narrator")
    assert text.count("“") == dialogue


# --- concat_audio ------------------------------------------------------------

def test_concat_audio_command_and_listing(mini, runs, monkeypatch):
    seen = {}
    orig = director._run

    def capture(args):
        listing = Path(args[args.index("-i") + 1])
        seen["listing"] = listing.read_text()
        runs.append(args)
    monkeypatch.setattr(director, "_run", capture)
    out = director.concat_audio(mini)
    assert out == mini.narration_path
    a = runs[0]
    assert a[:7] == ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i"]
    assert a[-5:] == ["-c:a", "libmp3lame", "-b:a", "128k", str(out)]
    assert seen["listing"] == "".join(f"file '{mini.audio_path(n).resolve()}'\n" for n in (1, 2))
    assert not Path(a[7]).exists()          # temp listing cleaned up


# --- _X264 / _ken_burns ------------------------------------------------------

def test_x264_pins_pixel_format():
    a = director._X264("slow")
    assert a == ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
                 "-profile:v", "high"]


def vcfg(**kw):
    c = copy.deepcopy(cfgmod.load())
    c["video"].update(width=64, height=36, fps=10, **kw)
    return c


def shot(path, ms=2000):
    return {"image": Path(path), "start_ms": 0, "duration_ms": ms, "chapter": 1}


@pytest.mark.parametrize("mode,preset,interp", [(True, "medium", "cubic"), ("lite", "veryfast", "linear")])
def test_ken_burns_commands(runs, tmp_path, mode, preset, interp):
    cfg = vcfg(ken_burns=mode)
    out = tmp_path / "clip.mp4"
    director._ken_burns(shot("img.jpg", 2000), cfg, out, extra_ms=500, index=0)
    fit, render = runs
    assert fit[:4] == ["ffmpeg", "-y", "-i", "img.jpg"]
    assert "scale=64:36:force_original_aspect_ratio=increase,crop=64:36" in fit
    assert fit[-1] == str(out.with_suffix(".png")) and "-frames:v" in fit
    assert "-loop" in render and render[render.index("-framerate") + 1] == "10"
    assert render[render.index("-t") + 1] == "2.500"                 # duration + extra
    vf = render[render.index("-vf") + 1]
    assert f"interpolation={interp}" in vf and "perspective=" in vf and vf.endswith("format=yuv420p")
    assert render[render.index("-preset") + 1] == preset
    assert render[-1] == str(out)


def test_ken_burns_direction_alternates(runs, tmp_path):
    cfg = vcfg(ken_burns=True)
    director._ken_burns(shot("i.jpg"), cfg, tmp_path / "a.mp4", 0, index=0)
    director._ken_burns(shot("i.jpg"), cfg, tmp_path / "b.mp4", 0, index=1)
    vf_even = runs[1][runs[1].index("-vf") + 1]
    vf_odd = runs[3][runs[3].index("-vf") + 1]
    assert "max(1.14-" in vf_even and "min(1.001+" in vf_odd


def test_ken_burns_off_is_plain_scale(runs, tmp_path):
    director._ken_burns(shot("i.jpg"), vcfg(ken_burns=False), tmp_path / "a.mp4", 0, 0)
    vf = runs[1][runs[1].index("-vf") + 1]
    assert vf == "scale=out_range=tv,format=yuv420p"
    assert runs[1][runs[1].index("-preset") + 1] == "medium"


def test_ken_burns_removes_temp_png(monkeypatch, tmp_path):
    out = tmp_path / "c.mp4"
    png = out.with_suffix(".png")

    def fake(args):
        if args[-1] == str(png):
            png.write_bytes(b"x")
    monkeypatch.setattr(director, "_run", fake)
    director._ken_burns(shot("i.jpg"), vcfg(ken_burns=True), out, 0, 0)
    assert not png.exists()


def test_ken_burns_minimum_two_frames(runs, tmp_path):
    director._ken_burns({"image": Path("i.jpg"), "duration_ms": 10}, vcfg(ken_burns=True),
                        tmp_path / "a.mp4", 0, 0)
    assert "perspective" in runs[1][runs[1].index("-vf") + 1]          # no ZeroDivision


# --- _stitch -----------------------------------------------------------------

def test_stitch_single_clip_is_moved(tmp_path, runs):
    clip, out = tmp_path / "c.mp4", tmp_path / "o.mp4"
    clip.write_bytes(b"v")
    director._stitch([clip], [shot("i")], 800, out)
    assert out.read_bytes() == b"v" and not clip.exists() and runs == []


def test_stitch_concat_when_no_fade(tmp_path, runs, monkeypatch):
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    listings = []
    monkeypatch.setattr(director, "_run",
                        lambda a: (listings.append(Path(a[a.index("-i") + 1]).read_text()), runs.append(a)))
    director._stitch(clips, [shot("a"), shot("b")], 0, tmp_path / "o.mp4")
    a = runs[0]
    assert a[:7] == ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i"] and a[-3:-1] == ["-c", "copy"]
    assert listings[0] == "".join(f"file '{c.resolve()}'\n" for c in clips)


def test_stitch_xfade_chain_offsets(tmp_path, runs):
    clips = [tmp_path / f"{i}.mp4" for i in range(3)]
    shots = [shot("a", 3000), shot("b", 2000), shot("c", 4000)]
    director._stitch(clips, shots, 500, tmp_path / "o.mp4")
    a = runs[0]
    assert [a[i + 1] for i, x in enumerate(a) if x == "-i"] == [str(c) for c in clips]
    graph = a[a.index("-filter_complex") + 1]
    assert graph == ("[0:v][1:v]xfade=transition=fade:duration=0.5:offset=2.500[x1];"
                     "[x1][2:v]xfade=transition=fade:duration=0.5:offset=4.500[v]")
    assert a[a.index("-map") + 1] == "[v]" and a[-1] == str(tmp_path / "o.mp4")
    assert "yuv420p" in a


# --- direct ------------------------------------------------------------------

def direct_setup(b, shots_per_chapter=1):
    tl(b, 1, 3000, [sent(0, 1000, "A.")])
    tl(b, 2, 2000, [sent(0, 1000, "B.")])
    scenes(b, {str(n): {"shots": [{"at_ms": i * 500, "image": f"images/{n:02d}-{i:02d}.jpg"}
                                  for i in range(shots_per_chapter)]} for n in (1, 2)})
    for n in (1, 2):
        b.audio_path(n).write_bytes(b"x")


def test_direct_sequence_stages_and_commands(mini, runs, monkeypatch, capsys):
    direct_setup(mini)
    monkeypatch.setenv("MW_PROGRESS", "1")
    mini.set_stage("approve", "approved")
    seen_running = []
    monkeypatch.setattr(director, "_run", lambda a: (seen_running.append(mini.stage_status("direct")), runs.append(a)))
    cfg = vcfg(ken_burns=True, burn_subtitles=False, crossfade_ms=300)
    out = director.direct(cfg, mini)
    assert out == mini.video_path
    assert set(seen_running) == {"running"}
    assert mini.stage_status("direct") == "done"
    assert mini.stage_status("approve") == "awaiting_review"      # a new cut needs watching again
    assert mini.srt_path.exists()
    cmds = [" ".join(map(str, r)) for r in runs]
    assert "libmp3lame" in cmds[0]                                  # narration first
    assert sum("force_original_aspect_ratio" in c for c in cmds) == 2   # one fit per shot
    assert any("xfade" in c for c in cmds)
    final = runs[-1]
    assert final[:2] == ["ffmpeg", "-y"] and final[final.index("-c:v") + 1] == "copy"
    assert "-shortest" in final and "+faststart" in final and final[-1] == str(mini.video_path)
    assert "-vf" not in final
    out_text = capsys.readouterr().out
    assert "2 shots" in out_text and "::progress" in out_text
    assert '"event": "stitch"' in out_text and "upload subtitles.srt" in out_text
    assert "approve" in out_text and "--video" in out_text


def test_direct_extra_fade_time_only_after_first_clip(mini, runs, monkeypatch):
    direct_setup(mini)
    cfg = vcfg(ken_burns=False, burn_subtitles=False, crossfade_ms=400)
    director.direct(cfg, mini, verbose=False)
    ts = [r[r.index("-t") + 1] for r in runs if "-loop" in r]
    assert ts == ["3.000", "2.400"]


def test_direct_single_shot_has_no_fade(mini, runs, monkeypatch):
    monkeypatch.setattr(director, "_stitch", lambda clips, shots, fade, out: runs.append(["stitch", len(clips), fade]))
    tl(mini, 1, 3000, [sent(0, 1000, "A.")])
    tl(mini, 2, 0, [])
    scenes(mini, {"1": {"shots": [{"at_ms": 0, "image": "images/01-00.jpg"}]},
                  "2": {"shots": []}})
    cfg = vcfg(ken_burns=False, burn_subtitles=False, crossfade_ms=400)
    director.direct(cfg, mini, verbose=False)
    ts = [r[r.index("-t") + 1] for r in runs if "-loop" in r]
    assert ts == ["3.000"] and ["stitch", 1, 0] in runs


def test_direct_burn_subtitles_reencodes(mini, runs):
    direct_setup(mini)
    cfg = vcfg(ken_burns=False, burn_subtitles=True, crossfade_ms=0)
    director.direct(cfg, mini, verbose=False)
    final = runs[-1]
    vf = final[final.index("-vf") + 1]
    assert vf.startswith(f"subtitles={mini.srt_path}:force_style=") and "FontName=Georgia" in vf
    assert final[final.index("-c:v") + 1] == "libx264"


def test_direct_notes_when_already_published(mini, runs, capsys):
    direct_setup(mini)
    mini.set_stage("publish", "done", url="https://youtu.be/abc", video_id="abc", published="2026-01-01")
    director.direct(vcfg(ken_burns=False, burn_subtitles=False, crossfade_ms=0), mini)
    assert "already published at https://youtu.be/abc" in capsys.readouterr().out


def test_direct_failure_leaves_stage_running(mini, monkeypatch):
    direct_setup(mini)
    monkeypatch.setattr(director, "_run", lambda a: (_ for _ in ()).throw(RuntimeError("ffmpeg failed")))
    with pytest.raises(RuntimeError):
        director.direct(vcfg(ken_burns=False, burn_subtitles=False, crossfade_ms=0), mini, verbose=False)
    assert mini.stage_status("direct") == "running"                # pinned: nothing resets it


# --- real renders (slow) -------------------------------------------------------

def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


def probe(path, entry):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entry, "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def real_assets(b, seconds=1):
    for n in (1, 2):
        ff("-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono", "-t", seconds, "-b:a", "64k", b.audio_path(n))
        for i in range(2):
            ff("-f", "lavfi", "-i", f"color=c={'red' if i else 'blue'}:s=128x72", "-frames:v", 1,
               b.root / "images" / f"{n:02d}-{i:02d}.jpg")
    for n in (1, 2):
        tl(b, n, seconds * 1000, [sent(0, 500, "One."), sent(500, 1000, "Two.", speaker="Ann Lee")])
    scenes(b, {str(n): {"shots": [{"at_ms": 0, "image": f"images/{n:02d}-00.jpg"},
                                  {"at_ms": 500, "image": f"images/{n:02d}-01.jpg"}]} for n in (1, 2)})


@pytest.mark.slow
@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_real_direct_renders_playable_video(mini):
    real_assets(mini, seconds=2)
    cfg = vcfg(ken_burns="lite", burn_subtitles=False, crossfade_ms=200)
    out = director.direct(cfg, mini, verbose=False)
    assert out.exists() and out.stat().st_size > 1000
    streams = probe(out, "stream=codec_name,width,height,pix_fmt").split()
    assert any(l.startswith("h264,64,36,yuv420p") or l.startswith("h264") for l in streams)
    assert "aac" in probe(out, "stream=codec_name")
    dur = float(probe(out, "format=duration"))
    assert 3.5 < dur < 4.6                                          # narration is 2 x 2 s
    assert mini.stage_status("direct") == "done"
    assert mini.narration_path.exists() and mini.srt_path.exists()


@pytest.mark.slow
@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_real_stitch_concat_and_xfade(tmp_path):
    clips, lengths = [], [1, 1.3, 1.3]       # as direct() renders them: later clips carry the fade
    for i, (colour, secs) in enumerate(zip(("red", "green", "blue"), lengths)):
        c = tmp_path / f"c{i}.mp4"
        ff("-f", "lavfi", "-i", f"color=c={colour}:s=64x36:r=10", "-t", secs, "-pix_fmt", "yuv420p", c)
        clips.append(c)
    shots = [shot("a", 1000)] * 3
    xf = tmp_path / "xf.mp4"
    director._stitch(clips, shots, 300, xf)
    # the overlaps give back exactly what they consume: 3 shots of 1 s stay 3 s
    assert 2.8 < float(probe(xf, "format=duration")) < 3.2
    assert probe(xf, "stream=pix_fmt") == "yuv420p"
    cat = tmp_path / "cat.mp4"
    director._stitch(clips, shots, 0, cat)
    assert 3.4 < float(probe(cat, "format=duration")) < 3.8          # plain concat just adds lengths


@pytest.mark.slow
@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_real_concat_audio_joins_chapters(mini):
    real_assets(mini, seconds=1)
    out = director.concat_audio(mini)
    assert 1.9 < float(probe(out, "format=duration")) < 2.3


@pytest.mark.slow
@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_real_ken_burns_clip_length(mini, tmp_path):
    real_assets(mini)
    out = tmp_path / "clip.mp4"
    director._ken_burns(shot(mini.root / "images" / "01-00.jpg", 1000), vcfg(ken_burns=True), out, 500, 1)
    assert 1.3 < float(probe(out, "format=duration")) < 1.7
    assert not out.with_suffix(".png").exists()
