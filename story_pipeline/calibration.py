"""Measured narration pace against the assumed one.

`WPM = 150` in `agents/text.py` decides the whole length dial: it sets how many
words a 15- or 30-minute story is, which sets the per-chapter target, which the
length check now enforces to within a fifth. Every one of those numbers rests on
a figure nobody has measured.

That was tolerable while chapters overran by 29% anyway — the error in the
assumption was smaller than the error in the output. Now that the output lands
within about 12%, the assumption is the larger unknown, and tightening the
tolerance further would be spending revisions to hit a target that may itself be
wrong.

This module closes that. Once a story has been narrated, the audio says exactly
how long the words took, and the answer is either "150 was close enough" or a
corrected figure to put in `text.WPM`.

Stdlib only, no API calls, free to run on anything already recorded.
"""

from __future__ import annotations

from typing import Any

from .agents.text import LENGTHS, WPM
from .bundle import Bundle


def measure(b: Bundle) -> dict[str, Any] | None:
    """Words against measured audio for one narrated story, or None.

    Two paces come out of this and they answer different questions:

    - **spoken** excludes the silence inserted between segments. It is how fast
      the voice actually reads, and it is the figure that transfers to another
      story with a different gap setting.
    - **effective** includes the gaps. It is words divided by the runtime you
      actually get, which is what the dial is trying to predict — so it is the
      one that belongs in `WPM`.

    The difference between them is the cost of `casting.gap_ms`, which is worth
    seeing on its own: at 400ms a chapter of forty segments spends sixteen
    seconds saying nothing.
    """
    total_ms = b.duration_ms()
    if not total_ms:
        return None

    words = 0
    speech_ms = 0
    chapters = []
    for n in b.chapter_numbers():
        if not b.chapter_json(n).exists() or not b.timeline_path(n).exists():
            continue
        t = b.timeline(n)
        w = b.chapter_words(n)
        # Sum the cues rather than the chapter duration: what is left over is
        # the inserted silence.
        spoken = sum(s["end_ms"] - s["start_ms"] for s in t.get("sentences", []))
        words += w
        speech_ms += spoken
        chapters.append({
            "n": n,
            "words": w,
            "duration_ms": t["duration_ms"],
            "wpm": round(w / (t["duration_ms"] / 60000), 1) if t["duration_ms"] else None,
        })

    if not words or not total_ms:
        return None

    effective = words / (total_ms / 60000)
    spoken_wpm = words / (speech_ms / 60000) if speech_ms else None
    # Pinned at creation; the outline carries it too for bundles written before
    # pinning, and `int` because a YAML-sourced value can arrive as a string.
    dial = (b.manifest().get("pinned", {}).get("length_min")
            or b.outline().get("length_min"))
    # LENGTHS is keyed by the dial and does not carry it as a field —
    # `length_spec()` adds it. Read it from the key.
    spec_minutes = int(dial) if dial and int(dial) in LENGTHS else None

    return {
        "words": words,
        "duration_ms": total_ms,
        "minutes": round(total_ms / 60000, 1),
        "assumed_wpm": WPM,
        "effective_wpm": round(effective, 1),
        "spoken_wpm": round(spoken_wpm, 1) if spoken_wpm else None,
        "gap_ms": total_ms - speech_ms,
        "error_pct": round((WPM / effective - 1) * 100, 1),
        "predicted_minutes": round(words / WPM, 1),
        "target_minutes": spec_minutes,
        "chapters": chapters,
    }


def report(m: dict[str, Any]) -> str:
    """The measurement as something to read and act on."""
    lines = [
        f"{m['words']:,} words narrated in {m['minutes']} min",
        "",
        f"  assumed    {m['assumed_wpm']} wpm  ->  predicted {m['predicted_minutes']} min",
        f"  measured   {m['effective_wpm']} wpm  ->  actual    {m['minutes']} min",
    ]
    if m["spoken_wpm"]:
        gap_s = m["gap_ms"] / 1000
        lines.append(
            f"  speaking   {m['spoken_wpm']} wpm excluding {gap_s:.0f}s of gaps "
            f"({gap_s / (m['duration_ms'] / 1000) * 100:.0f}% of the runtime)"
        )

    err = m["error_pct"]
    lines += ["", f"  The assumption is {abs(err):.0f}% "
                  f"{'fast' if err > 0 else 'slow'}."]
    if abs(err) < 5:
        lines.append(f"  Within 5% — leave WPM at {m['assumed_wpm']}.")
    else:
        lines += [
            f"  Set WPM = {round(m['effective_wpm'])} in story_pipeline/agents/text.py.",
            "",
            "  That changes what every length setting means, so it is a decision",
            "  about the dial, not a bug fix. What it would do to the targets:",
            "",
            f"  {'dial':>6}  {'words now':>10}  {'words at ' + str(round(m['effective_wpm'])):>14}"
            f"  {'per chapter':>12}",
        ]
        for minutes, spec in sorted(LENGTHS.items()):
            new_total = round(minutes * m["effective_wpm"])
            lines.append(
                f"  {minutes:>4}min  {spec['words']:>10,}  {new_total:>14,}"
                f"  {round(new_total / spec['chapters']):>12,}"
            )

    if m.get("target_minutes"):
        drift = m["minutes"] / m["target_minutes"] * 100 - 100
        lines += ["", f"  This story: {m['minutes']} min against a "
                      f"{m['target_minutes']}-minute dial ({drift:+.0f}%)."]
    return "\n".join(lines)
