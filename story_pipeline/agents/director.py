"""The director. Turns audio + stills + timings into one mp4.

Every cut point comes from the speech marks the actor already captured, so the
image changes on the sentence the designer chose rather than on a guess.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..bundle import Bundle


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(args[:8])} ...\n{tail}")


def _progress(**fields) -> None:
    """One progress marker for the review page — same contract as cli._progress.

    Shot clips render into a temp directory, so unlike record and design there
    is nothing on disk to count; the marker is the only way the page can see how
    far a render has got. Off unless MW_PROGRESS is set.
    """
    if os.environ.get("MW_PROGRESS"):
        print("::progress " + json.dumps(fields), flush=True)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def build_shot_list(b: Bundle) -> list[dict]:
    """Flatten every chapter's shots onto one global timeline."""
    scenes = json.loads(b.scenes_path.read_text())
    shots: list[dict] = []
    offset = 0

    for n in b.chapter_numbers():
        timeline = b.timeline(n)
        chapter_shots = sorted(scenes[str(n)]["shots"], key=lambda s: s["at_ms"])

        for i, shot in enumerate(chapter_shots):
            start = offset + (shot["at_ms"] if i else 0)  # first shot holds from chapter start
            end = (
                offset + chapter_shots[i + 1]["at_ms"]
                if i + 1 < len(chapter_shots)
                else offset + timeline["duration_ms"]
            )
            shots.append(
                {
                    "image": b.root / shot["image"],
                    "start_ms": start,
                    "duration_ms": max(end - start, 1200),
                    "chapter": n,
                }
            )
        offset += timeline["duration_ms"]

    return shots


def concat_audio(b: Bundle) -> Path:
    out = b.narration_path
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for n in b.chapter_numbers():
            f.write(f"file '{b.audio_path(n).resolve()}'\n")
        listing = f.name
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing,
          "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
    Path(listing).unlink(missing_ok=True)
    return out


def write_srt(b: Bundle) -> Path:
    def stamp(ms: int) -> str:
        h, ms = divmod(int(ms), 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines, idx, offset = [], 1, 0
    for n in b.chapter_numbers():
        timeline = b.timeline(n)
        for sentence in timeline["sentences"]:
            text = sentence["text"].strip()
            if sentence["speaker"] != "narrator":
                text = f"\u201c{text}\u201d"
            lines += [
                str(idx),
                f"{stamp(offset + sentence['start_ms'])} --> {stamp(offset + sentence['end_ms'])}",
                text,
                "",
            ]
            idx += 1
        offset += timeline["duration_ms"]

    b.srt_path.parent.mkdir(parents=True, exist_ok=True)
    b.srt_path.write_text("\n".join(lines))
    return b.srt_path


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _ken_burns(shot: dict, cfg: dict, out: Path, extra_ms: int, index: int) -> None:
    v = cfg["video"]
    w, h, fps = v["width"], v["height"], v["fps"]
    seconds = (shot["duration_ms"] + extra_ms) / 1000
    frames = max(int(seconds * fps), 2)

    # "lite" and true differ only in interpolation and encoder preset; both
    # are equally smooth. Linear and cubic measured the same for motion.
    mode = v["ken_burns"]
    lite = mode == "lite"
    preset = "veryfast" if lite else "medium"

    if mode:
        # `perspective`, not `zoompan`. zoompan crops at whole source pixels and
        # truncates the crop size separately from its position, so on a zoom
        # this slow the rounding is bigger than the motion and the picture
        # wobbles instead of gliding — 0.68 px of frame-to-frame noise at 2x
        # supersample, still 0.47 at 4x. perspective resamples at 1/256 px with
        # the rectangle re-evaluated every frame: 0.03 px at 1x, no supersample.
        rate = 0.14 / frames
        if index % 2:
            z = f"min(1.001+{rate}*on,1.14)"        # push in
        else:
            z = f"max(1.14-{rate}*on,1.001)"        # pull out
        # The centred source rectangle at zoom z, stretched onto the frame.
        hw, hh = f"(W/2/({z}))", f"(H/2/({z}))"
        x0, x1 = f"W/2-{hw}", f"W/2+{hw}"
        y0, y1 = f"H/2-{hh}", f"H/2+{hh}"
        vf = (
            f"perspective=x0='{x0}':y0='{y0}':x1='{x1}':y1='{y0}'"
            f":x2='{x0}':y2='{y1}':x3='{x1}':y3='{y1}'"
            f":interpolation={'linear' if lite else 'cubic'}:eval=frame,"
            f"scale=out_range=tv,format=yuv420p"
        )
    else:
        vf = "scale=out_range=tv,format=yuv420p"

    # Fit the still to the frame once, losslessly, rather than decoding and
    # rescaling the JPEG for every frame of a looped input — that was ~40% of
    # the render time. Looped at the output rate, so no fps conversion either.
    still = out.with_suffix(".png")
    _run(["ffmpeg", "-y", "-i", str(shot["image"]),
          "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
          "-frames:v", "1", str(still)])
    _run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(still),
          "-t", f"{seconds:.3f}", "-vf", vf,
          *_X264(preset), str(out)])
    still.unlink(missing_ok=True)


def _X264(preset: str) -> list[str]:
    """Every x264 encode in this module. The pixel format is pinned because the
    scene images are JPEGs: left to negotiate, the xfade and subtitle re-encodes
    came out full-range 4:4:4 (yuvj444p, High 4:4:4 Predictive), which no
    browser decodes — the review page's player showed a duration and never a
    frame. 4:2:0 is also what YouTube delivers, so nothing is lost."""
    return ["-c:v", "libx264", "-preset", preset, "-crf", "18",
            "-pix_fmt", "yuv420p", "-profile:v", "high"]


def _stitch(clips: list[Path], shots: list[dict], fade_ms: int, out: Path) -> None:
    if len(clips) == 1:
        clips[0].replace(out)
        return

    if fade_ms <= 0:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            for c in clips:
                f.write(f"file '{c.resolve()}'\n")
            listing = f.name
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing,
              "-c", "copy", str(out)])
        Path(listing).unlink(missing_ok=True)
        return

    # Progressive xfade. Each clip was rendered fade_ms long so the overlaps
    # give back exactly what they consume and the video stays locked to audio.
    fade = fade_ms / 1000
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]

    chain, prev, elapsed = [], "[0:v]", shots[0]["duration_ms"] / 1000
    for i in range(1, len(clips)):
        label = f"[x{i}]" if i < len(clips) - 1 else "[v]"
        chain.append(
            f"{prev}[{i}:v]xfade=transition=fade:duration={fade}"
            f":offset={elapsed - fade:.3f}{label}"
        )
        prev = label
        elapsed += shots[i]["duration_ms"] / 1000

    _run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chain),
          "-map", "[v]", *_X264("medium"), str(out)])


def direct(cfg: dict, b: Bundle, verbose: bool = True) -> Path:
    b.set_stage("direct", "running")
    shots = build_shot_list(b)
    audio = concat_audio(b)
    srt = write_srt(b)
    fade_ms = cfg["video"]["crossfade_ms"] if len(shots) > 1 else 0

    if verbose:
        total = sum(s["duration_ms"] for s in shots) / 1000
        print(f"  {len(shots)} shots, {total / 60:.1f} min")

    with tempfile.TemporaryDirectory() as tmp:
        clips = []
        for i, shot in enumerate(shots):
            clip = Path(tmp) / f"clip{i:03d}.mp4"
            # An xfade eats its duration from the START of the incoming clip,
            # so every clip but the first needs those frames back or the video
            # finishes short of the narration.
            extra = fade_ms if i > 0 else 0
            if verbose:
                print(f"  rendering shot {i + 1}/{len(shots)} "
                      f"({shot['duration_ms'] / 1000:.1f}s)...")
            _ken_burns(shot, cfg, clip, extra, i)
            clips.append(clip)
            _progress(event="shot", done=i + 1, total=len(shots))

        if verbose:
            print("  stitching shots and laying in the narration...")
        _progress(event="stitch", done=len(shots), total=len(shots))

        silent = Path(tmp) / "silent.mp4"
        _stitch(clips, shots, fade_ms, silent)

        args = ["ffmpeg", "-y", "-i", str(silent), "-i", str(audio)]
        if cfg["video"]["burn_subtitles"]:
            args += ["-vf", f"subtitles={srt}:force_style="
                            "'FontName=Georgia,FontSize=22,PrimaryColour=&H00FFFFFF,"
                            "OutlineColour=&H80000000,BorderStyle=3,MarginV=60'"]
            args += _X264("medium")
        else:
            args += ["-c:v", "copy"]
        # faststart puts the index at the front, so a browser can begin
        # playing before it has fetched a 30-minute file.
        args += ["-c:a", "aac", "-b:a", "160k", "-shortest",
                 "-movflags", "+faststart", str(b.video_path)]
        _run(args)

    b.set_stage("direct", "done")
    # A new cut needs watching again, even if an earlier one was approved.
    b.set_stage("approve", "awaiting_review")
    if verbose:
        print(f"  wrote {b.video_path}")
        if b.stage_status("publish") == "done":
            print(f"  note: already published at {b.publish_info().get('url', '?')}"
                  " — re-upload by hand if this cut replaces it")
        print(f"Next: watch it, then python3 -m story_pipeline.cli approve "
              f"{b.root} --video")
        if not cfg["video"]["burn_subtitles"]:
            print(f"  upload {srt.name} to YouTube as the caption track")
    return b.video_path
