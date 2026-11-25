"""The novella outliner: brief + bible + target length -> a chapter plan.

One paid call (`models.novella_outliner`, through `llm.complete_json`), checked
by `validate`. A reply that fails is sent back once with the problems listed;
a second failure stops with every problem named and nothing written (the
rejected reply is kept in outlines/rejected.json so the spend is not wasted
unread). Both calls are recorded with `spend.record_llm` (stage "outline").

A valid outline replaces outline.md (rendered as Markdown for Scott to edit;
the previous outline.md is kept as a numbered version first, see
`Bundle.save_outline`) and creates or updates each chapter's title, target and
beats. A chapter past Outlined is never touched: its beats are what its draft
was written to, so it is reported as skipped instead.

The story ideator's `_validate_outline` does not apply here (4-9 chapters of
~500 words, ear-checked names): a novella is 10-16 chapters of 2.5-4k words.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from story_pipeline import llm, spend

from . import prompts
from .bundle import DEFAULT_BOOK_WORDS, Bundle, NovellaError
from .prompts import CHAPTERS_MAX, CHAPTERS_MIN, _read, _tag
from .workflow import Stage

OnText = Callable[[str], None]

BEATS_MIN = 3
# Chapter targets may sum to within this fraction of the book's pinned target.
WORDS_TOLERANCE = 0.10
# Any one chapter, loosely around the 2.5-4k the writer is told.
CHAPTER_FLOOR, CHAPTER_CEILING = 1500, 6000


class OutlineError(NovellaError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("outline rejected:\n" + "\n".join(f"- {p}" for p in problems))


@dataclass
class Result:
    version: int
    outline: dict
    written: list[int] = field(default_factory=list)   # chapters created or updated
    skipped: list[int] = field(default_factory=list)   # past Outlined, left alone
    extra: list[int] = field(default_factory=list)     # in the book, not in this outline
    attempts: int = 1


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #


def _has_body(text: str) -> bool:
    """True when a Markdown file holds more than its headings (the templates
    Bundle.create writes are headings only)."""
    return any(l.strip() and not l.lstrip().startswith("#") for l in (text or "").splitlines())


def target_words(b: Bundle) -> int:
    return int(b.pinned("target_words") or DEFAULT_BOOK_WORDS)


def user_prompt(b: Bundle, cfg: dict, note: str = "") -> str:
    parts = []
    bible = prompts.bible_of(b, cfg)        # raises before spending if the bible is gone
    if bible:
        # ideator=True: Story Ideas reach the outliner, as they reach the story ideator.
        parts.append(_tag("series_bible", bible.prompt_block(ideator=True)))
    parts.append(_tag("story_brief", _read(b.brief_path)))
    outline = _read(b.outline_path)
    if _has_body(outline):
        parts.append(_tag("outline_notes", outline))
    if (note or "").strip():
        parts.append(_tag("author_note", note))
    total = target_words(b)
    parts.append(
        f"Outline this novella: {CHAPTERS_MIN} to {CHAPTERS_MAX} chapters whose "
        f"target_words add up to about {total:,} words (within "
        f"{int(WORDS_TOLERANCE * 100)}%), at least {BEATS_MIN} beats per chapter. "
        "Recurring characters keep their names exactly as the bible writes them. "
        "Return the JSON object.")
    return "\n\n".join(parts)


def check_ready(b: Bundle, cfg: dict) -> None:
    """Refuse before any spend: a brief with nothing in it, or a missing bible."""
    if not _has_body(_read(b.brief_path)):
        raise NovellaError("Write the brief first: the outliner plans from it.")
    prompts.bible_of(b, cfg)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _blank(v: Any) -> bool:
    return not isinstance(v, str) or not v.strip()


def validate(o: Any, total: int, bible=None) -> list[str]:
    """Every problem with an outline reply; empty when it is usable. Normalises
    in place: chapters numbered, empty optional `pov` dropped, recurring flags
    set for bible names."""
    if not isinstance(o, dict):
        return ["the reply is not a JSON object"]
    problems: list[str] = []
    for key in ("title", "logline"):
        if _blank(o.get(key)):
            problems.append(f"'{key}' is missing or empty")
    if "pov" in o and _blank(o.get("pov")):
        o.pop("pov")

    chapters = o.get("chapters")
    if not isinstance(chapters, list):
        problems.append("'chapters' is missing")
        chapters = []
    elif not CHAPTERS_MIN <= len(chapters) <= CHAPTERS_MAX:
        problems.append(f"{len(chapters)} chapters; a novella wants "
                        f"{CHAPTERS_MIN}-{CHAPTERS_MAX}")
    planned = 0
    for i, ch in enumerate(chapters, 1):
        if not isinstance(ch, dict):
            problems.append(f"chapter {i} is not an object")
            continue
        ch["n"] = i
        if _blank(ch.get("title")):
            problems.append(f"chapter {i} has no title")
        if "pov" in ch and _blank(ch.get("pov")):
            ch.pop("pov")
        tw = ch.get("target_words")
        if not isinstance(tw, int) or isinstance(tw, bool):
            problems.append(f"chapter {i} has no whole-number target_words")
        else:
            planned += tw
            if not CHAPTER_FLOOR <= tw <= CHAPTER_CEILING:
                problems.append(f"chapter {i} targets {tw:,} words; a chapter runs "
                                f"{CHAPTER_FLOOR:,}-{CHAPTER_CEILING:,}")
        beats = ch.get("beats")
        if not isinstance(beats, list):
            problems.append(f"chapter {i} has no beats list")
        else:
            if any(_blank(x) for x in beats):
                problems.append(f"chapter {i} has an empty beat")
            if len([x for x in beats if not _blank(x)]) < BEATS_MIN:
                problems.append(f"chapter {i} has {len(beats)} beats; each chapter "
                                f"needs at least {BEATS_MIN}")
    if chapters and abs(planned - total) > WORDS_TOLERANCE * total:
        problems.append(f"chapter targets sum to {planned:,} words; this book is "
                        f"pinned at {total:,} (within {int(WORDS_TOLERANCE * 100)}%)")

    cast = o.get("cast", [])
    if not isinstance(cast, list):
        problems.append("'cast' is not a list")
        cast = []
    for j, member in enumerate(cast, 1):
        if not isinstance(member, dict) or _blank(member.get("name")):
            problems.append(f"cast entry {j} has no name")
            continue
        if _blank(member.get("role")):
            problems.append(f"{member['name']} has no role")
    problems += _cast_problems(cast, bible)
    return problems


def _canon_names(bible) -> dict[str, str]:
    """lower-cased name or alias -> the exact spelling the bible uses."""
    out = {}
    for c in (bible.recurring_cast if bible else []):
        for n in [c["name"], *(c.get("aliases") or [])]:
            out[n.lower()] = n
    return out


def _cast_problems(cast: list, bible) -> list[str]:
    """Recurring characters keep their bible identity: a name marked recurring
    must be a bible name or alias written exactly, and a bible name in other
    capitalisation is drift."""
    canon = _canon_names(bible)
    problems = []
    for member in cast:
        if not isinstance(member, dict) or _blank(member.get("name")):
            continue
        name = member["name"].strip()
        exact = canon.get(name.lower())
        if exact is not None and exact != name:
            problems.append(f"{name!r} is the bible's {exact!r}; write the name exactly")
        elif exact is not None:
            member["recurring"] = True
        elif member.get("recurring"):
            problems.append(f"{name!r} is marked recurring but is not in the bible's "
                            "recurring cast; use the bible's name exactly, or mark "
                            "them new")
    return problems


# --------------------------------------------------------------------------- #
# Rendering and applying
# --------------------------------------------------------------------------- #


def beats_md(ch: dict) -> str:
    lines = []
    if ch.get("pov"):
        lines += [f"POV: {ch['pov']}", ""]
    lines += [f"- {str(x).strip()}" for x in ch.get("beats", []) if str(x).strip()]
    return "\n".join(lines)


def render(o: dict) -> str:
    total = sum(c.get("target_words", 0) for c in o["chapters"])
    out = [f"# {o['title']}: Outline", "", f"*{o['logline'].strip()}*", ""]
    if o.get("pov"):
        out += [f"**Point of view:** {o['pov']}", ""]
    out += [f"**Length:** {len(o['chapters'])} chapters, about {total:,} words", ""]
    if o.get("cast"):
        out += ["## Cast", ""]
        for c in o["cast"]:
            tag = ", recurring" if c.get("recurring") else ""
            note = f" — {c['note'].strip()}" if not _blank(c.get("note")) else ""
            out.append(f"- **{c['name']}** ({c.get('role', '')}{tag}){note}")
        out.append("")
    out += ["## Chapters", ""]
    for ch in o["chapters"]:
        meta = f"{ch['target_words']:,} words"
        if ch.get("pov"):
            meta = f"POV: {ch['pov']} · " + meta
        out += [f"### {ch['n']}. {ch['title']}", "", f"*{meta}*", ""]
        out += [f"- {str(x).strip()}" for x in ch["beats"]]
        out.append("")
    return "\n".join(out)


def apply(b: Bundle, o: dict, model: str = "", note: str = "",
          attempts: int = 1) -> Result:
    v = b.save_outline(render(o), {"model": model, "note": note, "outline": o,
                                   "attempts": attempts})
    res = Result(version=v, outline=o, attempts=attempts)
    existing = {c["n"]: c for c in b.chapters()}
    for ch in o["chapters"]:
        n = ch["n"]
        if n in existing and existing[n].get("stage") != Stage.OUTLINED.value:
            res.skipped.append(n)
            continue
        b.save_plan(n, ch["title"].strip(), ch["target_words"], beats_md(ch))
        res.written.append(n)
    res.extra = sorted(n for n in existing if n > len(o["chapters"]))
    return res


# --------------------------------------------------------------------------- #
# The paid step
# --------------------------------------------------------------------------- #


def outline(cfg: dict, b: Bundle, note: str = "", on_text: OnText | None = None,
            on_retry: Callable[[list[str]], None] | None = None) -> Result:
    check_ready(b, cfg)
    model, ceiling = stages_model(cfg)
    bible = prompts.bible_of(b, cfg)
    system = prompts.agent_system("outliner", b)
    user = user_prompt(b, cfg, note)
    total = target_words(b)
    ask, problems, reply = user, [], None
    for attempt in (1, 2):
        try:
            reply, usage = llm.complete_json(model, system, ask, max_tokens=ceiling,
                                             on_text=on_text)
        except ValueError as e:      # unparseable; complete_json says why
            reply, usage, problems = None, None, [f"the reply was not valid JSON: {e}"]
        else:
            spend.record_llm(b, "outline", model, usage, notes=f"attempt {attempt}")
            problems = validate(reply, total, bible)
        if not problems:
            return apply(b, reply, model=model, note=note, attempts=attempt)
        if attempt == 1:
            if on_retry:
                on_retry(problems)
            ask = (user + "\n\nYour previous reply:\n\n"
                   + (json.dumps(reply, indent=2) if reply is not None else "(not JSON)")
                   + "\n\nIt was rejected for these reasons:\n\n"
                   + "\n".join(f"- {p}" for p in problems)
                   + "\n\nReturn the whole corrected outline as one JSON object.")
    if reply is not None:
        b.outlines_dir.mkdir(parents=True, exist_ok=True)
        (b.outlines_dir / "rejected.json").write_text(json.dumps(
            {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "problems": problems,
             "reply": reply}, indent=2) + "\n")
    raise OutlineError(problems)


def stages_model(cfg: dict) -> tuple[str, int]:
    from .stages import _model
    return _model(cfg, "outliner")


def summary_line(res: Result) -> str:
    o = res.outline
    total = sum(c["target_words"] for c in o["chapters"])
    return (f"Outline v{res.version}: {o['title']}, {len(o['chapters'])} chapters, "
            f"{total:,} words.")
