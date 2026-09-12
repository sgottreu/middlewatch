"""The actor. Casts voices, synthesizes each segment, stitches, and emits a
timeline.

The timeline is the highest-value artifact in the pipeline. Sentence-level
millisecond offsets drive both the subtitle file and the image cut points, so the
designer and director never guess at timing.

Provider choice lives in `tts.py`. This module only cares that whatever comes
back has PCM and sentence spans in it.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .. import tts
from ..bundle import BREAK, Bundle


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for sentence in re.split(r"(?<=[.!?\u201d])\s+", text):
        if len(cur) + len(sentence) + 1 > limit and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += sentence + " "
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


# --------------------------------------------------------------------------- #
# Casting
# --------------------------------------------------------------------------- #


def cast_voices(cfg: dict, b: Bundle, provider) -> dict[str, str]:
    """Map speaker -> voice name. Pinned on first run so a re-record months later
    produces the same performance."""
    existing = b.pinned("voices")
    if existing:
        return existing

    defaults = {**cfg["casting"], **(b.pinned("genre_voices") or {})}

    # Solo narration needs one voice, so it asks the account for one. Casting the
    # other six anyway would refuse to record a story whose extra voices you do
    # not have and were never going to hear.
    if cfg.get("casting", {}).get("mode", "solo") == "solo":
        only = cfg["casting"].get("solo_voice") or defaults["narrator"]
        try:
            resolved = provider.resolve_voice(only)
        except ValueError as e:
            raise ValueError(f"casting the narrator: {e}") from None
        if hasattr(provider, "list_voices"):
            try:
                available = {v["voice_id"] for v in provider.list_voices()}
            except Exception:
                available = set()
            if available and resolved not in available:
                raise ValueError(
                    f"{only} ({resolved}) is not in your ElevenLabs account.\n"
                    "Run\n  python3 -m story_pipeline.cli voices\n"
                    "and put your own ids in elevenlabs.voice_library.\n"
                    "Nothing has been synthesized, so nothing has been billed."
                )
        voices = {"narrator": only}
        b.pin("voices", voices)
        b.pin("tts_provider", provider.name)
        return voices

    counts: dict[str, int] = {}
    for n in b.chapter_numbers():
        if not b.chapter_json(n).exists():
            continue
        for seg in b.chapter(n)["segments"]:
            if seg["speaker"] not in ("narrator", BREAK):
                counts[seg["speaker"]] = counts.get(seg["speaker"], 0) + 1

    voices = {"narrator": defaults["narrator"]}
    cast = {c["name"]: c for c in b.outline().get("cast", [])}

    pool = defaults["character_voices"]
    if isinstance(pool, list):  # flat pool, no gender information
        pool = {"female": list(pool), "male": list(pool)}
    used: set[str] = set()

    # A voice set by hand in the outline is always honoured and never counts
    # against max_named_voices. Casting someone deliberately outranks turn count.
    for name in counts:
        override = cast.get(name, {}).get("voice")
        if override:
            voices[name] = override
            used.add(override)

    # The remaining slots go to whoever speaks most, cast by gender rather than
    # by rank. Ranking alone will happily read Elinor in a baritone, and that
    # error only surfaces after the audio is paid for.
    remaining = [n for n in sorted(counts, key=lambda k: -counts[k]) if n not in voices]
    for name in remaining[: defaults["max_named_voices"]]:
        member = cast.get(name, {})
        options = pool.get(member.get("gender", "female"), []) or pool.get("female", [])
        pick = next((v for v in options if v not in used), None)
        # Pool exhausted for that gender. It used to take any free voice, which
        # is the error the comment above warns about arriving by another door:
        # on the first story to cast four women against three female voices it
        # put Mrs Hale in a baritone, silently, and the only way to find out was
        # to pay for the chapter and listen to it. Leave her to the narrator
        # instead — the fallback everyone else already gets, and what an
        # audiobook does with a minor part anyway.
        if pick:
            voices[name] = pick
            used.add(pick)

    # Everyone else is read by the narrator, which is how an audiobook does it.
    for name in counts:
        voices.setdefault(name, defaults["narrator"])

    # Resolve every voice now, not forty segments into a paid render.
    for speaker, voice in voices.items():
        try:
            provider.resolve_voice(voice)
        except ValueError as e:
            raise ValueError(f"casting {speaker}: {e}") from None

    # Resolving only proves the name is in `voice_library`. Whether the *account*
    # has that voice is a different question, and the answer arrives as a 404 on
    # the first paid call — after chapter one's narrator segments are billed and
    # thrown away. The shipped library is the public default IDs, so this is the
    # first thing a new key hits. One listing call settles it for nothing.
    if hasattr(provider, "list_voices"):
        try:
            available = {v["voice_id"] for v in provider.list_voices()}
        except Exception:
            available = set()   # a listing that fails is not a casting failure
        missing = [(s, v, provider.resolve_voice(v)) for s, v in voices.items()
                   if available and provider.resolve_voice(v) not in available]
        if missing:
            lines = "\n".join(f"  {s}: {v} ({vid})" for s, v, vid in missing)
            raise ValueError(
                "these voices are not in your ElevenLabs account:\n" + lines
                + "\n\nThe ids shipped in config are the public library defaults, "
                  "which only work if you have added those voices to your account. "
                  "Run\n  python3 -m story_pipeline.cli voices\n"
                  "and put your own ids in elevenlabs.voice_library.\n"
                  "Nothing has been synthesized, so nothing has been billed."
            )

    b.pin("voices", voices)
    b.pin("tts_provider", provider.name)
    return voices


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #


def announcement(cfg: dict, b: Bundle, n: int) -> str | None:
    """The spoken chapter heading, or None if the template is off.

    Generated here rather than written by the model. Left to the writer it was
    inconsistent in a way nobody could hear until the audio existed: of two
    stories written the same week, one announced seven of its eight chapters and
    the other announced none. A template is consistent by construction, applies
    to chapters already written, and can be changed later without touching a
    word of the prose.

    It is deliberately not stored in `chapters/NN.json`. The announcement is a
    production choice, not story content — keeping it out means the chapter text
    stays the chapter text, and turning the template off does not require
    rewriting every chapter to remove a line.
    """
    template = cfg.get("casting", {}).get("chapter_announcement")
    if not template:
        return None
    title = next((c.get("title", "") for c in b.outline().get("chapters", [])
                  if c["n"] == n), "")
    said = template.format(n=n, title=title).strip()
    return said or None


def _plan(chapter: dict, limit: int, solo: str | None = None) -> list[dict]:
    """Flatten segments into synthesis units, each carrying its neighbours' text.

    Continuity is why this is its own pass. Every unit is a separate API call, and
    a provider that accepts surrounding text uses it to keep pace and pitch
    continuous across the join. Without it a narrator/dialogue switch restarts the
    prosody cold and the seam is audible.

    `solo` is the whole reason the seam can be *removed* rather than smoothed.
    With one voice reading everything, consecutive segments are the same voice by
    definition, so they are joined into runs and cut only where the provider's
    character limit forces it: The Cracked Beam goes from 182 separate
    generations to 16. A cast cannot do that — dialogue alternates every few
    lines, so merging same-speaker runs there saves 29 joins out of 181 and the
    switching noise stays. That is a fact about casting, not about this code.
    """
    units: list[dict] = []
    if chapter.get("_announcement"):
        units.append({"speaker": "narrator", "text": chapter["_announcement"]})

    # (speaker, text) pairs, tags folded in, breaks kept as their own marker.
    parts: list[tuple[str, str]] = []
    for seg in chapter["segments"]:
        if seg["speaker"] == BREAK:
            parts.append((BREAK, ""))
            continue
        # An audio tag is direction, not words: it rides with the text to the
        # model and is stripped back out of the alignment, so it never reaches
        # the page, the subtitles or the listener.
        tag = (seg.get("tag") or "").strip()
        text = seg["text"].strip()
        parts.append((solo or seg["speaker"], f"{tag} {text}".strip() if tag else text))

    for speaker, text in parts:
        if speaker == BREAK:
            # Carried through as a unit so the silence lands in the right place
            # in the timeline. It is never synthesized and never billed.
            units.append({"speaker": BREAK, "text": ""})
            continue
        # Join onto the previous unit while it is the same voice and there is
        # room: one generation reads a whole scene instead of restarting at
        # every paragraph.
        if (units and units[-1]["speaker"] == speaker
                and len(units[-1]["text"]) + 1 + len(text) <= limit):
            units[-1]["text"] += " " + text
            continue
        for chunk in _split(text, limit):
            units.append({"speaker": speaker, "text": chunk})
    # Neighbours are prosody context for the provider, so a break — which has no
    # text — must not become an empty `previous` that restarts the voice cold in
    # the middle of a scene. The silence is what separates the two scenes.
    spoken = [u for u in units if u["speaker"] != BREAK]
    for i, u in enumerate(spoken):
        u["previous"] = spoken[i - 1]["text"] if i else None
        u["following"] = spoken[i + 1]["text"] if i + 1 < len(spoken) else None
    return units


def solo_voice(cfg: dict, voices: dict) -> str | None:
    """The one voice reading everything, or None when the story is cast.

    Solo is the default: it is what an audiobook is, and it is the only way the
    seam between two generations stops being audible several times a minute.
    """
    casting = cfg.get("casting", {})
    if casting.get("mode", "solo") != "solo":
        return None
    return casting.get("solo_voice") or voices["narrator"]


def record_chapter(cfg: dict, b: Bundle, n: int, voices: dict, provider) -> dict:
    gap_ms = cfg["casting"]["gap_ms"]
    # A jump in time or place. Silence is all a listener gets — the page has
    # `* * *` and the ear has nothing — so it has to be long enough that it
    # cannot be mistaken for the pause between two paragraphs, and short enough
    # that it is not mistaken for the end of the chapter. A struck bell in the
    # middle of it is the obvious next move; the length is the part worth
    # settling first, by listening to one.
    break_ms = cfg["casting"].get("break_ms", 2000)
    rate = provider.sample_rate
    gap_bytes = b"\x00" * int(rate * 2 * gap_ms / 1000)
    break_bytes = b"\x00" * int(rate * 2 * break_ms / 1000)

    pcm = bytearray()
    sentences: list[dict] = []
    cursor = 0.0
    billed = 0
    unit_label = "characters"

    said = announcement(cfg, b, n)
    chapter = {**b.chapter(n), "_announcement": said}
    solo = solo_voice(cfg, voices)
    plan = _plan(chapter, provider.max_chars, solo=solo)
    for i, unit in enumerate(plan):
        if unit["speaker"] == BREAK:
            pcm += break_bytes
            cursor += break_ms
            continue
        # In solo mode every line is the one voice. Looking the unit's speaker up
        # would miss — the speaker is a voice name, not a character — and fall
        # back to the narrator, which is right only when they are the same voice.
        voice_name = solo or voices.get(unit["speaker"], voices["narrator"])
        clip = provider.synthesize(
            unit["text"],
            provider.resolve_voice(voice_name),
            previous=unit["previous"],
            following=unit["following"],
        )
        billed += clip.billed_units
        unit_label = clip.unit

        for s in clip.sentences:
            sentences.append({
                "start_ms": round(cursor + s["start_ms"]),
                "end_ms": round(cursor + s["end_ms"]),
                "text": s["text"],
                "speaker": unit["speaker"],
                "voice": voice_name,
            })

        pcm += clip.pcm
        cursor += clip.duration_ms
        # A gap belongs at a change of voice, not between two halves of one
        # sentence run that only split because of a character limit. Stapling
        # 400ms into the middle of a paragraph is half of what "switching
        # noise" turns out to be.
        nxt = plan[i + 1] if i + 1 < len(plan) else None
        if nxt and nxt["speaker"] not in (unit["speaker"], BREAK):
            pcm += gap_bytes
            cursor += gap_ms

    # Extend each cue toward the next so the subtitle track has no holes, without
    # ever overrunning the sentence that follows.
    for i, s in enumerate(sentences[:-1]):
        s["end_ms"] = max(s["end_ms"], min(sentences[i + 1]["start_ms"], s["end_ms"] + gap_ms))

    _encode(bytes(pcm), rate, b.audio_path(n))

    timeline = {
        "n": n,
        # What was actually said, so a later edit to the chapter is detectable.
        # Without it, record_all's skip-if-audio-exists silently keeps narration
        # of words no longer in the story.
        "chapter_hash": b.chapter_hash(n),
        # The announcement is generated, not stored, so a changed template would
        # otherwise leave narration that says the old heading with no sign of
        # it. Recorded here so `narration_stale` can see that too.
        "announcement": said,
        "duration_ms": round(cursor),
        "billed_units": billed,
        "unit": unit_label,
        "provider": provider.name,
        "voices": voices,
        "sentences": sentences,
    }
    b.timeline_path(n).write_text(json.dumps(timeline, indent=2) + "\n")
    b.add_spend(f"{provider.name}_{unit_label}", billed)
    return timeline


def _encode(pcm: bytes, rate: int, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1",
         "-i", "pipe:0", "-b:a", "128k", str(out)],
        input=pcm, check=True, capture_output=True,
    )


def record_all(cfg: dict, b: Bundle, verbose: bool = True, force: bool = False) -> None:
    _require_ffmpeg()
    # The CLI checks this too, but narration is the largest cost in the pipeline
    # and the check belongs where the money is spent — a caller that skipped the
    # wrapper should not get further than one that did.
    if not force and not b.all_chapters_approved():
        left = [n for n in b.chapter_numbers()
                if b.chapter_json(n).exists() and not b.chapter_approved(n)]
        raise ValueError(
            f"chapters {left} are not approved. Approve them, or pass force=True."
        )
    provider = tts.build(cfg)
    voices = cast_voices(cfg, b, provider)
    solo = solo_voice(cfg, voices)
    if verbose:
        print(f"  provider: {provider.name}"
              + (f", solo narration by {solo}" if solo else ", cast"))
        narrator = voices["narrator"]
        print("  casting:  " + ", ".join(
            f"{k}={v}" + (" (narrator covers)" if k != "narrator" and v == narrator else "")
            for k, v in voices.items()))

    b.set_stage("record", "running")
    for n in b.chapter_numbers():
        if b.audio_path(n).exists() and b.timeline_path(n).exists():
            if not b.narration_stale(n, announcement(cfg, b, n), voices):
                if verbose:
                    print(f"  chapter {n}: audio exists, skipping (delete it to re-record)")
                continue
            if verbose:
                was = b.timeline(n).get("voices") or {}
                why = ("the casting changed" if was and was != voices
                       else "the text changed since it was narrated")
                print(f"  chapter {n}: {why}, re-recording")
        if verbose:
            print(f"  chapter {n}: synthesizing...")
        t = record_chapter(cfg, b, n, voices, provider)
        if verbose:
            print(f"  chapter {n}: {t['duration_ms'] / 1000:.0f}s, "
                  f"{t['billed_units']} {t['unit']}")
    b.set_stage("record", "done")

    # The whole length dial rests on an assumed 150 wpm. This is the first
    # moment the real figure exists, so it is said here rather than waiting to
    # be asked for.
    from .. import calibration
    m = calibration.measure(b)
    if m and verbose:
        print("\n" + calibration.report(m))


def estimate(cfg: dict, b: Bundle) -> dict:
    """Dry run: cost and format check with no API call and no credentials, so it
    works before the account is even set up."""
    chars = 0
    for n in b.chapter_numbers():
        if b.chapter_json(n).exists():
            chars += sum(len(s["text"]) for s in b.chapter(n)["segments"])

    if cfg.get("tts_provider", "elevenlabs") == "elevenlabs":
        e = cfg["elevenlabs"]
        credits = round(chars * tts.CREDIT_RATE.get(e["model"], 1.0))
        est = {
            "billed_units": credits,
            "unit": "credits",
            "engine": e["model"],
            "usd": round(credits / 1000 * e.get("usd_per_1k_credits", 0.0), 2),
        }
    else:
        engine = cfg["polly"]["engine"]
        est = {
            "billed_units": chars,
            "unit": "characters",
            "engine": engine,
            "usd": round(chars / 1_000_000 * tts.POLLY_RATES.get(engine, 0), 2),
        }

    words = b.word_count()
    return {**est, "words": words, "runtime_min": round(words / 150, 1), "chars": chars}


def _require_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError(
            "ffmpeg is not on PATH. Check this before recording, not after — "
            "synthesis is already paid for by the time encoding runs."
        )
