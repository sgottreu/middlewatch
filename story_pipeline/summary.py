"""`story.md` — the one file in a bundle that reads like a story rather than a
record of one.

Everything here is derived from `outline.json`, `manifest.json`, the chapter
files and the timelines. Nothing is stored only in `story.md`, so deleting it
loses nothing and regenerating it can never disagree with the bundle. It is a
view, not a source.

It exists because the numbers that matter for calibration — planned length
against measured length — were otherwise spread across two JSON files and a
directory of timelines, and had to be assembled by hand every time anyone wanted
to see whether a 30-minute story actually ran 30 minutes.

Rewritten after ideate, write and record. Each stage fills in more of it; the
fields a stage cannot know yet are marked pending rather than guessed.
"""

from __future__ import annotations

from .agents.text import WPM, length_spec
from .bundle import STAGES, Bundle


def _mmss(ms: int) -> str:
    s = round(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def _drift(actual: float, target: float) -> str:
    """Signed percentage against target, or an empty string when it is negligible."""
    if not target:
        return ""
    pct = 100 * (actual - target) / target
    return "on target" if abs(pct) < 2 else f"{pct:+.0f}%"


def render(b: Bundle) -> str:
    outline = b.outline()
    m = b.manifest()
    spec = length_spec(m.get("pinned", {}).get("length_min", outline.get("length_min")))
    chapters = outline["chapters"]

    planned_words = sum(c["target_words"] for c in chapters)
    actual_words = b.word_count()
    actual_ms = b.duration_ms()

    out: list[str] = []
    add = out.append

    add(f"# {outline['title']}")
    add("")
    series = outline.get("series")
    line = f"{outline['genre']} / {outline.get('subgenre', '')}".rstrip(" /")
    if series:
        line += f" · {series}"
    add(line)
    add("")
    if outline.get("logline"):
        add(f"> {outline['logline']}")
        add("")

    # --- length ------------------------------------------------------------
    # The whole point of the file. Planned sits beside measured so a story that
    # came in short is visible without opening anything else.
    add("## Length")
    add("")
    add("| | Words | Runtime |")
    add("|---|---|---|")
    add(f"| Target ({spec['minutes']} min) | {spec['words']:,} | {spec['minutes']}:00 |")
    add(f"| Planned | {planned_words:,} | {_mmss(planned_words / WPM * 60000)} |")
    if actual_words:
        add(f"| Written | {actual_words:,} | {_mmss(actual_words / WPM * 60000)} |")
    else:
        add("| Written | *not yet written* | |")
    if actual_ms is not None:
        add(f"| **Narrated** | | **{_mmss(actual_ms)}** |")
    else:
        add("| **Narrated** | | *not yet recorded* |")
    add("")

    if actual_ms is not None and actual_words:
        measured = actual_words / (actual_ms / 60000)
        add(
            f"Measured pace **{measured:.0f} wpm** against an assumed {WPM}. "
            f"Runtime is {_drift(actual_ms / 60000, spec['minutes'])} for a "
            f"{spec['minutes']}-minute story."
        )
        add("")
        add(
            "This is the figure the length table is built on. See "
            "[calibration](../../docs/story/calibration.md) before changing it "
            "on the strength of one story."
        )
        add("")

    # --- chapters ----------------------------------------------------------
    add("## Chapters")
    add("")
    beats = sum(len(c.get("beats", [])) for c in chapters)
    if beats:
        per_beat = planned_words / beats
        add(f"{beats} beats, **{per_beat:.0f} words per beat** "
            f"(this length asks for {spec['beats_min']}-{spec['beats_max']} per "
            f"chapter). Much above ~150 and the story is long rather than big.")
        add("")

    add("| # | Title | Beats | Target | Written | Narrated |")
    add("|---|---|---|---|---|---|")
    for c in chapters:
        n = c["n"]
        words = b.chapter_words(n)
        tl = b.timeline_path(n)
        dur = _mmss(b.timeline(n)["duration_ms"]) if tl.exists() else "—"
        written = f"{words:,}" if words else "—"
        if words:
            written += f" ({_drift(words, c['target_words'])})" if _drift(words, c["target_words"]) != "on target" else ""
        add(f"| {n} | {c.get('title', '')} | {len(c.get('beats', []))} | "
            f"{c['target_words']:,} | {written} | {dur} |")
    add("")

    # --- cast --------------------------------------------------------------
    add("## Cast")
    add("")
    for c in outline.get("cast", []):
        tag = " *(recurring)*" if c.get("recurring") else ""
        add(f"- **{c['name']}**, {c.get('age', '?')} — {c.get('role', '')}{tag}")
    add("")

    # --- production --------------------------------------------------------
    add("## Production")
    add("")
    add(f"- Slug: `{m['slug']}`")
    add(f"- Created: {m.get('created', '')}")
    if outline.get("tropes"):
        add(f"- Tropes: {', '.join(outline['tropes'])}")
    if outline.get("setting"):
        add(f"- Setting: {outline['setting']}")
    if outline.get("bible"):
        add(f"- Bible: `{outline['bible']}`")
    pinned = m.get("pinned", {})
    add(f"- Length: {spec['minutes']} min (pinned)")
    if pinned.get("scenes_per_chapter"):
        total_images = pinned["scenes_per_chapter"] * len(chapters)
        add(f"- Images: {pinned['scenes_per_chapter']}/chapter, {total_images} total")
    add("")
    add("| Stage | |")
    add("|---|---|")
    for stage in STAGES:
        add(f"| {stage} | {m['stages'].get(stage, 'pending')} |")
    add("")
    if m.get("spend"):
        add("| Spend | |")
        add("|---|---|")
        for k, v in m["spend"].items():
            add(f"| {k} | {v} |")
        add("")

    add("---")
    add("")
    add(
        "*Generated from `outline.json`, `manifest.json` and the timelines. "
        "Edits here are overwritten by the next stage — change the source, not "
        "this file.*"
    )
    return "\n".join(out) + "\n"


def write(b: Bundle) -> None:
    b.story_md_path.write_text(render(b))
