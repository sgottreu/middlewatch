"""The director. Turns audio + stills + timings into one mp4.

Every cut point comes from the speech marks the actor already captured, so the
image changes on the sentence the designer chose rather than on a guess.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from ..bundle import Bundle


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(args[:8])} ...\n{tail}")


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

    if v["ken_burns"]:
        # Upscaling before zoompan is what stops the pan from stuttering —
        # zoompan quantises its crop to integer source pixels.
        rate = 0.14 / frames
        if index % 2:
            z = f"'min(1.001+{rate}*on,1.14)'"      # push in
        else:
            z = f"'max(1.14-{rate}*on,1.001)'"      # pull out
        vf = (
            f"scale={w * 4}:{h * 4}:force_original_aspect_ratio=increase,"
            f"crop={w * 4}:{h * 4},"
            f"zoompan=z={z}:d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={w}x{h}:fps={fps},"
            f"format=yuv420p"
        )
    else:
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps={fps},format=yuv420p"
        )

    _run(["ffmpeg", "-y", "-loop", "1", "-i", str(shot["image"]),
          "-t", f"{seconds:.3f}", "-vf", vf,
          "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(out)])


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
          "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(out)])


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

        silent = Path(tmp) / "silent.mp4"
        _stitch(clips, shots, fade_ms, silent)

        args = ["ffmpeg", "-y", "-i", str(silent), "-i", str(audio)]
        if cfg["video"]["burn_subtitles"]:
            args += ["-vf", f"subtitles={srt}:force_style="
                            "'FontName=Georgia,FontSize=22,PrimaryColour=&H00FFFFFF,"
                            "OutlineColour=&H80000000,BorderStyle=3,MarginV=60'"]
            args += ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        else:
            args += ["-c:v", "copy"]
        args += ["-c:a", "aac", "-b:a", "160k", "-shortest", str(b.video_path)]
        _run(args)

    b.set_stage("direct", "done")
    if verbose:
        print(f"  wrote {b.video_path}")
        if not cfg["video"]["burn_subtitles"]:
            print(f"  upload {srt.name} to YouTube as the caption track")
    return b.video_path
