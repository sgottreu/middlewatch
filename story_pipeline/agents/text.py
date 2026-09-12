"""The three text agents: ideator, writer, editor — and the loop between the last two."""

from __future__ import annotations

import json
import re
from typing import Any

from .. import bibles, genres, history, lint, llm
from ..bundle import BREAK, HONORIFICS, Bundle, ear_key, slugify
from ..config import prompt_text

SYSTEM = "You return only the JSON object requested. No preamble, no fences, no commentary."

# --------------------------------------------------------------------------- #
# Length
# --------------------------------------------------------------------------- #

# One table drives the whole dial. Everything downstream — the structure block
# the agents read, the outline bounds, the per-chapter word target, the image
# count — is derived from here rather than restated, so a length is changed in
# exactly one place.
#
# Chapter length grows with chapter count deliberately. Holding chapters at 450
# words would put 30 minutes at ten chapters, and chapter count is what drives
# the two costs that scale worst: _context() ships every prior chapter to the
# writer for each new one, and images are minted per chapter.
#
# Word counts assume WPM below. That figure is a convention, not a measurement —
# see the note on calibration in docs/story/narration.md.
WPM = 150

LENGTHS: dict[int, dict[str, Any]] = {
    15: {
        "words": 2250,
        "chapters": 5,
        "chapter_words": 450,
        "chapters_min": 4,
        "chapters_max": 6,
        "cast_min": 3,
        "cast_max": 6,
        "beats_min": 3,
        "beats_max": 4,
        "scenes_per_chapter": 3,
    },
    30: {
        "words": 4500,
        "chapters": 8,
        "chapter_words": 560,
        "chapters_min": 7,
        "chapters_max": 9,
        "cast_min": 4,
        "cast_max": 7,
        # Beats scale with chapter length, and this is the number that decides
        # whether a longer story is bigger or merely slower. Asking for the same
        # 2-4 beats in a 560-word chapter as in a 450-word one buys the extra
        # runtime with padding: the first 30-minute outline produced came back at
        # 187 words per beat against 112 at 15 minutes, which is the same story
        # told more slowly. Four to five holds the density roughly level.
        "beats_min": 4,
        "beats_max": 5,
        "scenes_per_chapter": 4,
    },
}

DEFAULT_LENGTH = 15


def length_spec(minutes: int | None) -> dict[str, Any]:
    """The spec for a dial setting. Unknown values fail loudly rather than
    silently falling back — a typo'd length that quietly produced a 15-minute
    story would not be visible until after narration was paid for."""
    m = int(minutes or DEFAULT_LENGTH)
    if m not in LENGTHS:
        raise ValueError(
            f"no length spec for {m} minutes; available: "
            f"{', '.join(str(k) for k in sorted(LENGTHS))}"
        )
    return {**LENGTHS[m], "minutes": m}


def _structure_block(spec: dict[str, Any]) -> str:
    """The Structure section of the house rules, written per length.

    This is substituted into house.md, which every text agent receives, so the
    writer and the editor are held to one target rather than two.
    """
    return (
        f"{_spell(spec['chapters_min']).capitalize()} to "
        f"{_spell(spec['chapters_max'])} chapters, "
        f"about {spec['chapter_words']} words each, {spec['words']:,} words total — "
        f"roughly {spec['minutes']} minutes read aloud, which is the shape of the "
        f"video.\n\n"
        "Each chapter ends on a turn: information, a reversal, a decision, or a "
        "line of dialogue that changes what the next chapter is about. A chapter "
        "that only continues is a chapter to cut.\n\n"
        f"{_spell(spec['cast_min']).capitalize()} to {_spell(spec['cast_max'])} named "
        f"characters. More than {_spell(spec['cast_max'])} is unfollowable by ear."
    )


def estimate_write(cfg: dict, b: Bundle, restart: bool = False) -> dict[str, Any]:
    """What `write` will cost, before committing to it.

    Structural rather than measured: the writer is sent the shared prompt layers
    plus every prior chapter, so the input grows with each chapter and the total
    is quadratic in chapter count. That is the number worth seeing before you
    press the button, and it is why chapter count is capped rather than words.

    Counts only the chapters that will actually be written. A resumed run pays
    for the remainder, but each of those still carries the *whole* story so far
    as context — so resuming chapter 8 of 8 is not an eighth of the price, and
    quoting a per-chapter average here would understate it.

    Deliberately approximate. Retries are the biggest unknown — a chapter that
    fails review is written and judged again — so the range spans no retries to
    every chapter taking one.
    """
    outline = b.outline()
    chapters = outline.get("chapters", [])
    if not chapters:
        return {"known": False}

    todo = set(pending_chapters(b, restart))

    # ~1.33 tokens a word, and the fixed layers (house, boundaries, genre guide,
    # ai-tells, bible) run to roughly 5k tokens on every call.
    TOK = 1.33
    FIXED = 5000

    writer_in = writer_out = editor_in = editor_out = 0
    prior_words = 0
    for c in chapters:
        words = c.get("target_words", 0)
        # Context accumulates over every chapter, written now or already on
        # disk — a resumed chapter is still sent everything before it.
        ctx = int(prior_words * TOK)
        prior_words += words
        if c.get("n") not in todo:
            continue
        writer_in += FIXED + ctx
        writer_out += int(words * TOK)
        # The editor sees the same context plus the finished chapter.
        editor_in += FIXED + ctx + int(words * TOK)
        editor_out += 900          # a verdict object, not prose

    counts = {
        "chapters": len(chapters),
        "to_write": len(todo),
        "already_written": len(chapters) - len(todo),
        "restart": restart,
    }
    if not todo:
        return {"known": True, "low": 0.0, "high": 0.0, **counts,
                "writer": cfg["models"]["writer"], "editor": cfg["models"]["editor"],
                "max_edit_passes": cfg.get("max_edit_passes", 2)}

    try:
        rates = _anthropic_rates()
        w = rates[cfg["models"]["writer"]]
        e = rates[cfg["models"]["editor"]]
    except (KeyError, OSError, ValueError):
        return {"known": False, **counts}

    def usd(tokens: int, per_million: float) -> float:
        return tokens / 1_000_000 * per_million

    low = (usd(writer_in, w["input_tokens"]) + usd(writer_out, w["output_tokens"])
           + usd(editor_in, e["input_tokens"]) + usd(editor_out, e["output_tokens"]))
    return {
        "known": True,
        **counts,
        "low": round(low, 2),
        "high": round(low * 2, 2),     # every chapter needing one revision
        "writer": cfg["models"]["writer"],
        "editor": cfg["models"]["editor"],
        "max_edit_passes": cfg.get("max_edit_passes", 2),
    }


def _anthropic_rates() -> dict[str, dict]:
    """The ledger's rate card, so one price list serves estimates and accounting."""
    import json as _json
    from pathlib import Path as _Path

    path = _Path("ledger/rates.json")
    return _json.loads(path.read_text())["providers"]["anthropic"]["models"]


def _scenes_per_chapter(cfg: dict, spec: dict[str, Any]) -> int:
    """Images per chapter, derived from length unless the config names a number.

    Chapters get longer as the dial goes up, so a fixed count would stretch the
    gap between images. Deriving it holds the cadence at roughly one image a
    minute, which is what 15 minutes at three per chapter already does.
    """
    override = cfg.get("gemini", {}).get("scenes_per_chapter")
    return int(override) if override is not None else int(spec["scenes_per_chapter"])


def _spell(n: int) -> str:
    return {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
            8: "eight", 9: "nine", 10: "ten"}.get(n, str(n))


def _tolerance(cfg: dict) -> float:
    """How far a chapter may drift from its word target before it is sent back.
    One number, read by the writer's prompt and by the check that enforces it."""
    return float(cfg.get("length_tolerance", 0.20))


def _ceiling(cfg: dict, agent: str) -> int:
    """This agent's reply ceiling, from config.

    Hitting one fails a whole stage after the call has been billed, so the
    number belongs in `config.yaml` where it can be raised without editing
    source. Falls back to the shipped default for an agent the file omits,
    which keeps a hand-trimmed config working.
    """
    from ..config import DEFAULTS
    shipped = DEFAULTS["max_tokens"]
    return int(cfg.get("max_tokens", {}).get(agent, shipped.get(agent, 8000)))


def _bible_of(b: Bundle, cfg: dict) -> bibles.Bible | None:
    name = b.outline().get("bible")
    return bibles.load(name, cfg["bibles_dir"]) if name else None


def _render(
    name: str,
    genre: genres.Genre,
    bible=None,
    spec: dict[str, Any] | None = None,
    **extra: str,
) -> str:
    """Assemble a prompt from the four shared layers plus the genre guide.

    Every text agent gets house rules, content boundaries, the genre voice guide,
    and (writer and editor only) the AI-tells reference. Assembling them in one
    place is what keeps the writer and the editor judging against identical
    text — the moment those diverge, the editor starts failing chapters for
    rules the writer was never given. The length block is part of that: it
    reaches the writer and the editor through the same house text.

    Ordering in `fields` is load-bearing. `{house}` is substituted first and the
    text it brings in contains `{structure}`, which the next key fills. Moving
    `structure` above `house` would leave the placeholder unsubstituted.
    """
    fields = {
        "house": prompt_text("house"),
        "structure": _structure_block(spec or length_spec(DEFAULT_LENGTH)),
        "boundaries": prompt_text("boundaries"),
        "ai_tells": prompt_text("ai-tells"),
        "genre_guide": genre.guide,
        "genre_label": genre.meta.get("label", genre.name),
        "genre": genre.name,
        "bible_block": bible.prompt_block() if bible else "",
        **extra,
    }
    out = prompt_text(name)
    for key, value in fields.items():
        out = out.replace("{" + key + "}", str(value))
    if "{structure}" in out:
        raise RuntimeError(
            "'{structure}' survived substitution — house.md and _render have "
            "drifted, and the agents would be given a placeholder as a rule."
        )
    return out


def _genre_of(b: Bundle) -> genres.Genre:
    return genres.load(b.outline()["genre"])


def _check_bible(bible: bibles.Bible, cfg: dict) -> None:
    """Refuse to spend on a bible that cannot work.

    The ear-collision check is the one that pays for this: two recurring
    characters whose names start alike are rejected by `_validate_outline`, so
    every story using both would fail *after* the ideator call was billed. The
    same is true of a voice that isn't in the library — it raises at cast time,
    once the story is already written.
    """
    problems = bibles.fatal(bibles.validate(bible.path, cfg))
    if not problems:
        return
    raise ValueError(
        f"bible {bible.name!r} has {len(problems)} error(s):\n"
        + "\n".join(str(p) for p in problems)
        + f"\n\nFix them, then: python3 -m story_pipeline.cli bible check {bible.name}"
    )


def _length_of(b: Bundle) -> dict[str, Any]:
    """The story's pinned length. Falls back to the outline for bundles created
    before the dial existed, then to the default."""
    return length_spec(b.pinned("length_min", b.outline().get("length_min")))


# --------------------------------------------------------------------------- #
# Ideator
# --------------------------------------------------------------------------- #


NOTES_TEMPLATE = """## Notes from the person commissioning this

These are the shape they want. Treat them as binding on premise, character and
setting, and treat everything they leave unsaid as yours to decide. Where a note
conflicts with the content boundaries above, the boundaries win and you say so in
`notes_conflict`; where a note conflicts with genre convention, the note wins.

{notes}
"""


def build_outline(
    cfg: dict,
    genre_name: str | None = None,
    subgenre: str | None = None,
    notes: str | None = None,
    bible_name: str | None = None,
    feedback: str | None = None,
    previous: dict | None = None,
    seed: int | None = None,
    length_min: int | None = None,
) -> dict:
    bible = bibles.load(bible_name, cfg["bibles_dir"]) if bible_name else None
    if bible:
        _check_bible(bible, cfg)
    spec = length_spec(length_min if length_min is not None else cfg.get("length_min"))

    # A bible declares its genre, so --genre becomes optional alongside one.
    # Passing both and disagreeing is a mistake worth stopping on rather than
    # silently resolving, because the wrong one produces a plausible bad story.
    if bible:
        if genre_name and genre_name != bible.genre:
            raise ValueError(
                f"bible {bible.name!r} is a {bible.genre} series, but --genre "
                f"said {genre_name}. Drop --genre or pick a different bible."
            )
        genre_name = bible.genre
    if not genre_name:
        raise ValueError("need a --genre, or a --bible that declares one")

    genre = genres.load(genre_name)
    subgenre = genre.validate_subgenre(subgenre) or genre.subgenres[0]

    # What has already been approved, so this outline can avoid rhyming with it.
    # Scoped to the bible when there is one — Amberlight's repetitions say
    # nothing about what a different series should do next.
    past = history.scan(cfg["stories_dir"], bible=bible.name if bible else None)
    trope_weights = {t["name"].lower(): past.weight(t["name"]) for t in genre.tropes}

    system = _render(
        "ideator",
        genre,
        bible,
        spec=spec,
        subgenre=subgenre,
        subgenre_note=genre.subgenre_note(subgenre),
        trope_menu=genre.trope_menu(sample=cfg["trope_sample"], seed=seed,
                                    weights=trope_weights),
        notes_block=NOTES_TEMPLATE.format(notes=notes) if notes else "",
        history_block=history.avoid_block(past),
        chapters_min=str(spec["chapters_min"]),
        chapters_max=str(spec["chapters_max"]),
        chapters_target=str(spec["chapters"]),
        cast_min=str(spec["cast_min"]),
        cast_max=str(spec["cast_max"]),
        total_words=str(spec["words"]),
        chapter_words=str(spec["chapter_words"]),
        minutes=str(spec["minutes"]),
        beats_min=str(spec["beats_min"]),
        beats_max=str(spec["beats_max"]),
    )

    ask = "Produce one outline. Return the JSON object."
    if feedback and previous:
        ask = (
            "Here is the outline you produced:\n\n"
            + json.dumps(previous, indent=2)
            + "\n\nIt was reviewed and these changes are required:\n\n"
            + feedback
            + "\n\nProduce a revised outline. Keep everything that was not "
            "objected to: same title, cast names, tropes, logline, promise and "
            "setting **unless the notes above ask otherwise**. The notes win "
            "over that default every time — if they ask for a new cast or new "
            "chapters, replace them wholesale rather than adjusting what is "
            "there. Return the JSON object."
        )

    # A 30-minute outline is nine chapters of beats plus seven cast members with
    # full appearance and dress, and 7,000 was set when 15 minutes was the only
    # length. The ceiling is not a charge — only tokens actually produced are
    # billed — so it costs nothing to leave room the longer settings may need.
    outline, _ = llm.complete_json(cfg["models"]["ideator"], system, ask,
                                   max_tokens=_ceiling(cfg, "ideator"))
    outline["genre"] = genre_name
    outline["length_min"] = spec["minutes"]
    outline.setdefault("subgenre", subgenre)
    if notes:
        outline["notes"] = notes
    if bible:
        outline["bible"] = bible.name
        outline["series"] = bible.series
        _apply_bible_casting(outline, bible)
    _validate_outline(outline, genre, spec)
    return outline


def _apply_bible_casting(outline: dict, bible: bibles.Bible) -> None:
    """Force recurring characters back onto their series-pinned identity.

    The ideator is told to copy these fields verbatim and mostly does, but
    "mostly" is not continuity. Anything the bible states wins outright, so a
    paraphrased age or a drifted description can't reach the illustrator.
    """
    for member in outline.get("cast", []):
        canon = bible.member(member["name"])
        if not canon:
            continue
        for field in ("age", "gender", "station", "appearance", "dress", "voice"):
            if canon.get(field):
                member[field] = canon[field]
        member["recurring"] = True


def ideate(
    cfg: dict,
    stories_dir,
    genre_name: str | None = None,
    subgenre: str | None = None,
    notes: str | None = None,
    bible_name: str | None = None,
    length_min: int | None = None,
) -> Bundle:
    outline = build_outline(
        cfg, genre_name, subgenre, notes, bible_name, length_min=length_min
    )
    genre = genres.load(outline["genre"])
    bible = bibles.load(bible_name, cfg["bibles_dir"]) if bible_name else None

    b = Bundle.create(stories_dir, slugify(outline["title"]))
    outline["slug"] = b.root.name
    b.outline_path.write_text(json.dumps(outline, indent=2) + "\n")

    # Art direction and casting come from the genre, and are pinned per story so
    # editing a genre file never changes how an existing story re-renders.
    # A bible may override the genre's art direction and casting, so the series
    # looks and sounds like itself rather than like its genre.
    b.pin("art_style", (bible.meta.get("art_style") if bible else None) or genre.art_style)
    # The ledger's comparison dimensions, pinned for the same reason. Every cost
    # row copies these labels, so leaving them to be read live from the outline
    # would let a later revision restate what an earlier version cost — the
    # comparison would still print, and would quietly be about nothing.
    b.pin("genre", outline["genre"])
    b.pin("flavor", outline.get("subgenre") or "")
    b.pin("title", outline["title"])
    if outline.get("series"):
        b.pin("series", outline["series"])
    # Length is pinned for the same reason: it shaped the outline, so a re-run
    # months later has to reach for the same one. It is fixed at creation and
    # `revise` will not move it — changing length means a different story, not
    # a revision of this one.
    spec = length_spec(outline["length_min"])
    b.pin("length_min", spec["minutes"])
    b.pin("scenes_per_chapter", _scenes_per_chapter(cfg, spec))
    voices = {**genre.voices(), **((bible.meta.get("voices") if bible else None) or {})}
    if voices:
        b.pin("genre_voices", voices)
    if bible:
        b.pin("bible", bible.name)
    b.set_stage("ideate", "awaiting_review")
    return b


def revise_outline(cfg: dict, b: Bundle, feedback: str) -> dict:
    """Regenerate the outline against review notes, keeping the same bundle."""
    previous = b.outline()
    outline = build_outline(
        cfg,
        previous["genre"],
        previous.get("subgenre"),
        previous.get("notes"),
        previous.get("bible"),
        feedback=feedback,
        previous=previous,
        # Held to the pinned length: a revision reshapes the story, it does not
        # resize it. Changing length is `new`, not `revise`.
        length_min=b.pinned("length_min", previous.get("length_min")),
    )
    outline["slug"] = previous["slug"]

    history = previous.get("revisions", [])
    history.append({"feedback": feedback, "previous_title": previous["title"]})
    outline["revisions"] = history

    # Keep the outline being replaced, the way the write loop keeps drafts. A
    # revision costs a paid call and overwrites in place, so without this the
    # version you were comparing against is gone — including the case where the
    # revision is worse than what it replaced.
    snapshot = b.root / "drafts" / f"outline.{len(history)}.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(previous, indent=2) + "\n")

    b.outline_path.write_text(json.dumps(outline, indent=2) + "\n")
    b.set_stage("ideate", "awaiting_review")
    return outline


def approve(b: Bundle) -> None:
    b.set_stage("ideate", "approved")


ROMANTIC_ROLES = {"protagonist", "love_interest"}


def _validate_outline(o: dict, genre: genres.Genre, spec: dict[str, Any] | None = None) -> None:
    spec = spec or length_spec(o.get("length_min"))
    for key in ("title", "genre", "cast", "chapters", "setting", "tropes"):
        if key not in o:
            raise ValueError(f"outline missing '{key}'")

    lo, hi = spec["chapters_min"], spec["chapters_max"]
    if not lo <= len(o["chapters"]) <= hi:
        raise ValueError(
            f"outline has {len(o['chapters'])} chapters; a {spec['minutes']}-minute "
            f"story wants {lo}-{hi}"
        )

    names = [c["name"] for c in o["cast"]]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate cast names: {names}")
    if not spec["cast_min"] <= len(names) <= spec["cast_max"]:
        raise ValueError(
            f"outline has {len(names)} named characters; a {spec['minutes']}-minute "
            f"story wants {spec['cast_min']}-{spec['cast_max']}"
        )

    # Cheap ear check: two names sharing a first syllable are indistinguishable
    # in narration, and the failure only becomes obvious after you've paid for it.
    # Honorifics are stripped first — otherwise any two "Mr"s collide.
    heads = [ear_key(n) for n in names]
    if len(heads) != len(set(heads)):
        clash = [n for n in names if heads.count(ear_key(n)) > 1]
        raise ValueError(f"names too similar to tell apart by ear: {clash}")

    if not 2 <= len(o["tropes"]) <= 3:
        raise ValueError(f"outline declares {len(o['tropes'])} tropes; want 2 or 3")
    known = {t["name"].lower() for t in genre.tropes}
    unknown = [t for t in o["tropes"] if t.lower() not in known]
    if unknown:
        raise ValueError(f"tropes not in the {genre.name} list: {unknown}")

    # Boundary check that doesn't need a model: ages are structured data.
    for member in o["cast"]:
        if member.get("role") in ROMANTIC_ROLES:
            age = member.get("age")
            if not isinstance(age, int) or age < 18:
                raise ValueError(
                    f"{member['name']} is in a romantic role with age {age!r}; "
                    "every such character must have a stated age of 18 or over"
                )
    for i, ch in enumerate(o["chapters"], 1):
        ch["n"] = i
        ch.setdefault("target_words", spec["chapter_words"])
        if not ch.get("beats"):
            raise ValueError(f"chapter {i} has no beats")
        # A single-beat chapter is a scene, not a chapter — it has nothing to
        # turn on. Deliberately far below the beats_min the prompt asks for:
        # every rejection here costs an ideator call, so this catches only the
        # degenerate case and leaves near-misses to the review gate, which is
        # free and shows beat density.
        if len(ch["beats"]) < 2:
            raise ValueError(
                f"chapter {i} has {len(ch['beats'])} beat; a chapter needs at "
                f"least 2 to have something to turn on "
                f"(this length asks for {spec['beats_min']}-{spec['beats_max']})"
            )

    # The ideator sets per-chapter targets and they are what the writer is held
    # to, so their sum is the real length of the story, not the headline figure.
    # Catching drift here costs nothing; catching it after narration costs the
    # narration. 15% is loose enough to leave the ideator room to shape chapters
    # unevenly, which it should.
    planned = sum(ch["target_words"] for ch in o["chapters"])
    if abs(planned - spec["words"]) > 0.15 * spec["words"]:
        raise ValueError(
            f"chapter targets sum to {planned:,} words (~{planned / WPM:.0f} min), "
            f"but a {spec['minutes']}-minute story wants about {spec['words']:,}. "
            "Re-run, or revise the outline."
        )


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #


def _context(b: Bundle, n: int) -> str:
    """Everything the writer or editor needs about what came before."""
    outline = b.outline()
    parts = [f"# Outline\n\n{json.dumps(outline, indent=2)}"]
    prior = []
    for m in b.chapter_numbers():
        if m >= n:
            break
        if b.chapter_json(m).exists():
            prior.append(f"## Chapter {m}\n\n{render_markdown(b.chapter(m), outline)}")
    if prior:
        parts.append("# Chapters so far\n\n" + "\n\n".join(prior))
    return "\n\n".join(parts)


def write_chapter(cfg: dict, b: Bundle, n: int, fixes: list[str] | None = None,
                  note: str | None = None) -> dict:
    """Draft chapter `n`, or revise the one on disk.

    A revision happens when there are editor `fixes`, a `note` from you, or
    both. The note is stated first and ranked above the fixes: it is the one
    instruction nobody else in the loop can overrule, and an editor pass that
    quietly undid it would spend money to put the problem back.
    """
    outline = b.outline()
    spec = next(c for c in outline["chapters"] if c["n"] == n)

    system = _render(
        "writer",
        _genre_of(b),
        _bible_of(b, cfg),
        spec=_length_of(b),
        n=str(n),
        title=outline["title"],
        subgenre=outline.get("subgenre", ""),
        tropes=", ".join(outline.get("tropes", [])) or "none declared",
        target_words=str(spec["target_words"]),
        # The band the machine check enforces, stated to the writer in the same
        # numbers — so the instruction it is given and the rule it is measured
        # against cannot drift apart.
        length_floor=f"{int(spec['target_words'] * (1 - _tolerance(cfg))):,}",
        length_ceiling=f"{int(spec['target_words'] * (1 + _tolerance(cfg))):,}",
    )

    ask = _context(b, n) + f"\n\n# Write chapter {n}: {spec['title']}\n\nBeats to cover:\n"
    ask += "\n".join(f"- {x}" for x in spec["beats"])

    if fixes or note:
        ask += ("\n\n# This is a revision\n\nYour previous draft:\n\n"
                + json.dumps(b.chapter(n), indent=2))
    if note:
        ask += (
            "\n\n## A note from the person commissioning this story\n\n"
            "They have read the draft and ask for this change. It outranks every "
            "other instruction here, including the beat list: make it, and change "
            "nothing else that was working.\n\n"
            + note.strip()
        )
    if fixes:
        ask += (
            "\n\nThe editor requires these fixes. Apply all of them "
            + ("without undoing the change the note above asks for, "
               if note else "")
            + "and change nothing else that was working:\n"
            + "\n".join(f"- {f}" for f in fixes)
        )

    chapter, _ = llm.complete_json(cfg["models"]["writer"], SYSTEM + "\n\n" + system, ask,
                                   max_tokens=_ceiling(cfg, "writer"))
    chapter["n"] = n

    # Keep the draft before judging it. Validation raises on a speaker that
    # cannot be resolved, and until this the raised exception threw away a
    # chapter that had already been generated and billed — leaving nothing to
    # read, nothing to correct by hand, and no way to see what went wrong
    # beyond the one quoted line in the error.
    b.rejected_path(n).parent.mkdir(parents=True, exist_ok=True)
    b.rejected_path(n).write_text(json.dumps(chapter, indent=2) + "\n")
    _validate_chapter(chapter, outline)
    b.rejected_path(n).unlink(missing_ok=True)

    b.chapter_json(n).write_text(json.dumps(chapter, indent=2) + "\n")
    b.chapter_md(n).write_text(render_markdown(chapter, outline))
    return chapter


def _parts(name: str) -> tuple[set[str], set[str]]:
    """A name split into (naming parts, honorifics), lower-cased.

    `Miss Judith Vane` -> ({'judith', 'vane'}, {'miss'}). The two are kept
    apart because they carry different weight: a shared surname makes two
    people candidates for the same line, a shared `Mrs` makes them nothing but
    both adults.
    """
    words = {w.strip(".,'’") for w in name.lower().split()}
    words.discard("")
    titles = {w for w in words if w in HONORIFICS}
    return words - titles, titles


def rank_speaker(name: str, cast: list[str]) -> list[str]:
    """Cast members the written name could mean, best first — and if more than
    one comes back, they scored identically.

    Scored on how many parts of the name match rather than on an exact string,
    because the writer shortens names the way prose does: `Vernon Hartwell`,
    `Hartwell`, `Miss Vane`. Naming parts decide it; a title only breaks a tie
    between people already equal on name, which is what separates `Miss Vane`
    (Miss Judith Vane, 1 name part and the title) from Mr Owen Vane (1 name
    part, wrong title).

    A title alone is never a match — `Mrs Fenner` against a cast holding two
    other `Mrs` is a character the outline never had, not an ambiguous one.
    """
    want_names, want_titles = _parts(name)
    if not want_names:
        return []

    scored = []
    for c in cast:
        names, titles = _parts(c)
        hits = len(want_names & names)
        if hits:
            scored.append((hits, len(want_titles & titles), c))
    if not scored:
        return []

    best = max(s[:2] for s in scored)
    return [c for hits, tit, c in scored if (hits, tit) == best]


def resolve_speaker(name: str, cast: list[str]) -> str | None:
    """The one cast member the written name means, or None.

    The speaker field is a join key: it casts the voice, and it labels the line
    in the rendered markdown. So it has to end up matching the outline exactly.
    A tie returns None rather than guessing — two sisters both answering to
    `Miss Smith` are a question only the author can settle, and picking one
    would narrate her lines in the other's voice, discovered after the audio is
    paid for.
    """
    if name in cast:
        return name
    hits = rank_speaker(name, cast)
    return hits[0] if len(hits) == 1 else None


def _validate_chapter(ch: dict, outline: dict) -> int:
    """Check a draft and repair what is safely repairable. Returns how many
    empty segments were dropped."""
    cast = [c["name"] for c in outline["cast"]]
    known = set(cast) | {"narrator"}
    if not ch.get("segments"):
        raise ValueError(f"chapter {ch.get('n')} has no segments")

    # An empty segment carries nothing — no prose to read and no audio to
    # narrate — so failing a finished chapter over one threw away everything
    # else the model wrote to punish a stray comma in a list. Drop them and
    # carry on. A chapter that lost a beat this way still has to satisfy the
    # editor's beat coverage, which is the check that can actually judge it.
    # The writer is shown the chapters so far as rendered markdown, which now
    # carries `**Mrs Pike**` labels — useful context, and something it can copy
    # back into a segment's text. The speaker belongs in the field, and a label
    # left in the prose would be read aloud as literal asterisks.
    #
    # Only a bolded *cast name* is stripped. Matching any bold prefix would eat
    # a legitimate sentence that happens to open on emphasis; matching a name
    # cannot, because a segment never opens by naming its own speaker.
    # A break is punctuation, not speech: whatever text arrives with one would
    # be read aloud. And a segment that is nothing but asterisks is a break the
    # writer typed as prose — it has seen `* * *` in the chapters it was given
    # as context — so take it as one rather than dropping it as empty later.
    for s in ch["segments"]:
        if s.get("speaker") == BREAK:
            s["text"] = ""
        elif re.fullmatch(r"[*\s\u2014\u2013-]{3,9}", s.get("text", "") or ""):
            s["speaker"], s["text"] = BREAK, ""

    # A performance tag is direction, not prose: `[quietly]` written into the
    # text would be printed on the page and read into the subtitles. Lift a
    # leading one into its own field, and drop any others — a tag mid-sentence
    # is the model being told to act in the middle of a line, which reads worse
    # than it performs.
    for s in ch["segments"]:
        if s.get("speaker") == BREAK:
            continue
        text = s.get("text", "")
        lead = re.match(r"\s*(\[[^\]\n]{1,40}\])\s*", text)
        if lead:
            s.setdefault("tag", lead.group(1))
            text = text[lead.end():]
        s["text"] = re.sub(r"\s*\[[^\]\n]{1,40}\]\s*", " ", text).strip()

    for s in ch["segments"]:
        if s.get("speaker") == BREAK:
            continue
        m = re.match(r"\s*\*\*([^*]{1,40})\*\*[:.,]?\s*", s.get("text", ""))
        if m and (m.group(1).strip().lower() == "narrator"
                  or resolve_speaker(m.group(1).strip(), cast)):
            s["text"] = s["text"][m.end():]

    segs = ch["segments"]
    kept = [s for s in segs
            if s.get("text", "").strip() or s.get("speaker") == BREAK]
    dropped = len(segs) - len(kept)
    ch["segments"] = kept
    if not kept:
        raise ValueError(
            f"chapter {ch['n']}: every one of its {dropped} segments was empty, "
            f"so there is no chapter here to keep."
        )
    if dropped:
        # Recorded rather than only logged: the run is a background job in the
        # UI, where a printed warning goes nowhere, and this is a fact about
        # the chapter worth finding later.
        ch["dropped_empty_segments"] = dropped

    # A break needs a scene on each side. One at either end of the chapter marks
    # nothing — the chapter break already did that — and two in a row is a hole
    # the reader reads as a fault. Repaired rather than raised: the prose is
    # fine and already paid for, and this is a misplaced mark, not a broken
    # chapter.
    placed, previous_break = [], True      # True so a leading break is dropped
    for s in kept:
        is_break = s.get("speaker") == BREAK
        if is_break and previous_break:
            continue
        placed.append(s)
        previous_break = is_break
    while placed and placed[-1].get("speaker") == BREAK:
        placed.pop()
    if len(placed) != len(kept):
        ch["dropped_breaks"] = len(kept) - len(placed)
    kept = placed
    ch["segments"] = kept
    if not kept:
        raise ValueError(
            f"chapter {ch['n']}: nothing left but scene breaks, which mark "
            f"nothing on their own."
        )

    for i, seg in enumerate(kept):
        speaker = seg.get("speaker")
        if speaker in known or speaker == BREAK:
            continue
        # Repair in place. Everything downstream — voice casting, the markdown
        # label, the editor's continuity check — joins on this string, so the
        # canonical form has to be what lands on disk.
        if resolved := resolve_speaker(speaker or "", cast):
            seg["speaker"] = resolved
            continue
        near = rank_speaker(speaker or "", cast)
        raise ValueError(
            f"chapter {ch['n']} segment {i}: unknown speaker {speaker!r}.\n\n"
            + (f"Ambiguous — it fits {near} equally well, and picking one would "
               f"narrate a line in the wrong voice. Use the full name from the "
               f"outline.\n\n"
               if near else
               "Not a near-miss on any cast name, so the writer has given a "
               "line to someone the outline never cast — usually a walk-on it "
               "invented. Either add them to the outline's cast, which costs a "
               "voice, or make the line narration.\n\n")
            + f"Cast: {sorted(known)}\n"
            f"Quote: {seg.get('text', '')[:120]!r}"
        )
    return dropped


def _is_tag(text: str) -> bool:
    """Is this narrator segment an attribution tag rather than narration?

    `writer.md` splits `'I could not,' said Elizabeth, 'think of it.'` into
    three segments, so a tag arrives as its own narrator segment and would
    otherwise become its own paragraph \u2014 a bare `said Elizabeth.` on a line of
    its own, which reads as a rendering fault rather than as prose.

    The signal is capitalisation, which is exact enough to rely on: narration is
    a sentence and starts with a capital; a tag continues the quoted sentence
    and starts lower-case, or with the comma or dash that resumes it. A tag
    opening on a proper noun (`Mrs Pike said`) is capitalised and reads
    correctly as its own paragraph, so the failure is one-sided.
    """
    head = text.lstrip("\u201c\"'\u2018 ")
    return bool(head) and (head[0].islower() or head[0] in ",;\u2014\u2013-")


def _short_name(name: str, outline: dict) -> str:
    """`Mrs Honoria Pike` -> `Mrs Pike`. A label is read many times a page, and
    the full form is what makes a labelled chapter tiring rather than helpful.
    Shortened only when it stays unique across the cast, so it can never point
    at the wrong character."""
    parts = name.split()
    if len(parts) < 3:
        return name
    short = f"{parts[0]} {parts[-1]}"
    cast = [c.get("name", "") for c in outline.get("cast", [])] or [name]
    clashes = sum(1 for c in cast
                  if c.split()[:1] == parts[:1] and c.split()[-1:] == parts[-1:])
    return short if clashes <= 1 else name


# What a break looks like on the page. Asterisks rather than a rule because a
# lone `---` is a setext heading marker in markdown and would silently promote
# whatever line sits above it.
SCENE_BREAK = "* * *"


def render_markdown(chapter: dict, outline: dict, attribute: bool = True) -> str:
    """Human-readable chapter. Dialogue gets its quotation marks back here.

    The speaker of each segment is the only record of who is talking \u2014 the
    writer is told not to put a name inside a dialogue segment, and for
    narration that is right, because each character is cast to their own voice
    and a spoken 'said Elizabeth' is redundant. On the page it is not: run the
    segments together and the attribution is gone entirely. Paragraph breaks at
    every change of speaker are what carry it, which is the same thing they do
    in any printed novel.

    Breaks alone still leave a long exchange to be counted out by alternation,
    so with `attribute` each turn is labelled from its speaker field as well.
    The label is an annotation and deliberately not prose: nothing here invents
    a `said Mrs Pike` that the writer did not write and the narration will
    never say, which would leave the page and the audio telling different
    stories. Pass `attribute=False` for the unlabelled reading copy.
    """
    out = [f"## Chapter {chapter['n']}. {chapter.get('title', '')}".rstrip(), ""]
    paras: list[list[str]] = []
    labels: list[str] = []
    speaker_here = None   # who last spoke in the paragraph being built
    after_tag = False     # the previous segment was a tag joined into it

    for seg in chapter["segments"]:
        speaker, text = seg["speaker"], seg["text"].strip()
        if speaker == BREAK:
            # The printed form of a jump in time or place. Its own paragraph,
            # never labelled, and it closes whoever was speaking — the scene
            # after it starts cold, the same way the narration does.
            paras.append([SCENE_BREAK])
            labels.append("")
            speaker_here = None
            after_tag = False
            continue
        if not text:
            continue
        piece = text if speaker == "narrator" else f"\u201c{text}\u201d"

        if speaker == "narrator":
            # A tag belongs to the line it tags. Narration proper does not.
            join = bool(paras) and speaker_here is not None and _is_tag(text)
            after_tag = join
        else:
            # Dialogue resuming after its own interrupting tag, or continuing
            # uninterrupted \u2014 one speaker is one paragraph either way. A break
            # is earned by a *change* of speaker, which is the whole point.
            # `speaker_here` is cleared by any paragraph of narration, so this
            # cannot reach back across one.
            join = speaker == speaker_here
            after_tag = False
            speaker_here = speaker

        if join:
            paras[-1].append(piece)
        else:
            paras.append([piece])
            labels.append("" if speaker == "narrator"
                          else _short_name(speaker, outline))
            if speaker == "narrator":
                speaker_here = None

    body = [f"**{lab}** {' '.join(p)}" if attribute and lab else " ".join(p)
            for lab, p in zip(labels, paras)]
    out.append("\n\n".join(body))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Editor
# --------------------------------------------------------------------------- #


def review_chapter(cfg: dict, b: Bundle, n: int) -> dict:
    outline = b.outline()
    spec = next(c for c in outline["chapters"] if c["n"] == n)

    system = _render(
        "editor",
        _genre_of(b),
        _bible_of(b, cfg),
        spec=_length_of(b),
        n=str(n),
        target_words=str(spec["target_words"]),
        tropes=", ".join(outline.get("tropes", [])) or "none declared",
    )
    ask = (
        _context(b, n)
        + f"\n\n# Beats chapter {n} was required to cover\n\n"
        + "\n".join(f"- {x}" for x in spec["beats"])
        + f"\n\n# The draft\n\n{json.dumps(b.chapter(n), indent=2)}"
        + "\n\nJudge it."
    )

    # The verdict schema is open-ended: `generic_prose` is a list of quote and
    # replacement pairs, `beats` carries a note each, and `fixes` quotes the
    # offending text. A strict editor on a 560-word chapter can fill 6,000
    # tokens easily, and a truncated verdict fails the whole chapter rather than
    # merely being terse. The ceiling is not a charge, so it costs nothing to
    # leave room the editor may not use.
    review, _ = llm.complete_json(cfg["models"]["editor"], SYSTEM + "\n\n" + system,
                                  ask, max_tokens=_ceiling(cfg, "editor"))
    review["n"] = n

    # The linter is authoritative on its own rules. An LLM editor asked to judge
    # its own register will pass text it would not have written, so a clean
    # editor verdict cannot override a lint error.
    findings = lint.lint_chapter(b.chapter(n))
    review["lint"] = lint.summarize(findings)
    if lint.errors(findings) or review.get("boundary_breaches"):
        review["pass"] = False

    # What this verdict was passed on. An edited chapter carries a review of
    # prose that no longer exists, and saying so is the difference between a
    # score you can trust and one you cannot — the same stamp `record` puts on
    # a timeline, for the same reason.
    review["chapter_hash"] = b.chapter_hash(n)
    b.review_path(n).write_text(json.dumps(review, indent=2) + "\n")
    return review


def length_fix(b: Bundle, n: int, target: int, tolerance: float) -> str | None:
    """A trim instruction when a chapter is over its word target, or None.

    Length is the one thing in a verdict that a machine can settle exactly, and
    it was the one thing being decided by judgement. The editor measured every
    overrun on the first story correctly — "68% over, well past the one-fifth
    tolerance" — and passed the chapter anyway, because it is told not to fail
    on length alone and read the excess as incident rather than padding. So the
    finding was written down and never acted on, eight times, and the story came
    out 29% long.

    Counting here instead makes it exact (the editor estimated 719 where the
    count is 721) and unarguable, and leaves the editor judging prose, which is
    what it is for.
    """
    words = b.chapter_words(n)
    if not target or words <= target * (1 + tolerance):
        return None
    ceiling = int(target * (1 + tolerance / 2))
    return (
        f"LENGTH: this chapter is {words:,} words against a target of {target:,} — "
        f"{words / target * 100 - 100:.0f}% over. Cut it to at most {ceiling:,} "
        f"words. Lose no beat and no incident: take the cut out of restatement, "
        f"out of dialogue that makes its point twice, and out of scene-setting "
        f"that repeats what the reader already has. Do not summarise to hit the "
        f"number — cut whole sentences rather than compressing every one."
    )


def draft_and_edit(cfg: dict, b: Bundle, n: int, verbose: bool = True,
                   note: str | None = None, on_pass=None) -> dict:
    """Write, review, revise. Bounded, because an unbounded loop with a strict
    editor will burn tokens forever on a chapter that is already fine.

    With a `note`, the first pass revises the chapter already on disk rather
    than drafting from nothing, and the note rides along on every later pass so
    an editor revision cannot quietly undo it. Its drafts are numbered after the
    ones already kept, so the history of the original write survives.

    `on_pass(attempt, review)` fires after each write-and-judge pass.
    """
    max_passes = cfg["max_edit_passes"]
    tolerance = _tolerance(cfg)
    target = next((c.get("target_words", 0) for c in b.outline()["chapters"]
                   if c["n"] == n), 0)
    fixes: list[str] | None = None
    # A normal write starts the numbering at 0, as it always has. A redraft
    # from a note appends — overwriting drafts/NN.0.json would destroy the one
    # record of what the chapter looked like before you asked for the change.
    kept = b.drafts(n) if note else []
    first = (max(kept) + 1) if kept else 0

    for attempt in range(max_passes + 1):
        if verbose:
            label = ("redrafting from your note" if note and attempt == 0
                     else "drafting" if attempt == 0 else f"revision {attempt}")
            print(f"  chapter {n}: {label}...")
        chapter = write_chapter(cfg, b, n, fixes, note=note)
        review = review_chapter(cfg, b, n)
        if note:
            # Kept on the verdict, so the record says why this chapter changed.
            review["writer_note"] = note.strip()
            b.review_path(n).write_text(json.dumps(review, indent=2) + "\n")

        # Snapshot this pass before the next one overwrites the live files. The
        # first draft is the interesting one: it is what the model produces
        # unprompted, and comparing it to the final is the only way to see what
        # the editor actually bought.
        b.draft_path(n, first + attempt).parent.mkdir(parents=True, exist_ok=True)
        b.draft_path(n, first + attempt).write_text(json.dumps(chapter, indent=2) + "\n")
        b.draft_review_path(n, first + attempt).write_text(
            json.dumps(review, indent=2) + "\n")

        # Measured, not judged — and only while a revision is left to spend on
        # it. On the last pass an overrun is recorded and the chapter kept,
        # because a chapter that is good and long beats no chapter at all.
        trim = length_fix(b, n, target, tolerance)
        if trim:
            # Recorded whether or not it can be acted on. On the last pass the
            # chapter is kept — a good long chapter beats no chapter — but the
            # overrun should not vanish from the record because the loop ran
            # out of budget. `cli status` and the queue read this.
            review["length_over"] = b.chapter_words(n)
            review["length_target"] = target
        if trim and attempt < max_passes:
            review["pass"] = False
            if verbose:
                print(f"  chapter {n}: {b.chapter_words(n):,} words against "
                      f"{target:,} — sending back to trim")

        if trim:
            b.review_path(n).write_text(json.dumps(review, indent=2) + "\n")
            b.draft_review_path(n, first + attempt).write_text(
                json.dumps(review, indent=2) + "\n")

        if on_pass:
            on_pass(attempt, review)

        if review.get("pass"):
            if verbose:
                over = f", {b.chapter_words(n) / target * 100 - 100:+.0f}% on length" \
                    if trim else ""
                print(f"  chapter {n}: passed (score {review.get('score', '?')}, "
                      f"lint clean{over})")
            return review

        lint_fixes = lint.to_fixes(
            [lint.Finding(**{k: v for k, v in f.items()})
             for f in review["lint"]["findings"]]
        )
        # First, because it is the one instruction that shrinks the chapter and
        # the others mostly add to it.
        fixes = ([trim] if trim else []) + lint_fixes + (review.get("fixes") or [])
        if verbose:
            n_err = review["lint"]["errors"]
            extra = f", {n_err} lint error(s)" if n_err else ""
            print(f"  chapter {n}: {len(fixes)} fix(es) required{extra}")
        if not fixes:
            break

    if verbose:
        print(f"  chapter {n}: exhausted {max_passes} revisions, keeping last draft")
    review = json.loads(b.review_path(n).read_text())
    review["exhausted"] = True
    b.review_path(n).write_text(json.dumps(review, indent=2) + "\n")
    return review


# The note `cli attribute` sends. It is one string in one place because it will
# be sent to every chapter of every story written before narration went solo,
# and a note that drifts between chapters produces a story that drifts with it.
ATTRIBUTION_NOTE = """This story is now read by a single narrator rather than a
cast with a voice each, so the prose has to carry who is speaking. It was
written under the old rule, which forbade attribution because the voices made it
redundant.

Go through the chapter and add attribution wherever a listener — who cannot see
the page, and cannot look back — would lose track of who is speaking. A tag
belongs in its own narrator segment: `said Mrs Pike`, `Rowe said`, or an action
that does the same work: *Rowe turned the note over.* Two people alternating in
a plain exchange do not need one every line; the third turn usually does, and so
does every return to dialogue after narration, and every line in a scene of
three or more.

Do not attribute every line. An unbroken column of `said X` is padding, and it
is the opposite failure.

Where a line would be misread without it, you may add a `tag` to that segment —
`"[quietly]"`, `"[a beat]"`, `"[warmly]"` — as performance direction for the
voice. It is never printed and never spoken. A few a chapter at most, and never
to imitate a different person: one voice reads everyone.

Come back no longer than you started. These chapters are already at or over
their word ceiling, and attribution adds words — so take them back out of
restatement: a line of dialogue that lands followed by narration explaining that
it landed, a room established twice. If you come back long the check sends the
chapter straight back to be cut, which spends a revision on arithmetic instead
of on the prose.

Change nothing else. Same events, same beats, same jokes, same last line."""


def chapter_dialogue_turns(b: Bundle, n: int) -> int:
    """How many spoken turns a chapter has. Zero means nothing to attribute."""
    return sum(1 for s in b.chapter(n).get("segments", [])
               if s["speaker"] not in ("narrator", BREAK))


def redraft_chapter(cfg: dict, b: Bundle, n: int, note: str,
                    verbose: bool = True, on_pass=None) -> dict:
    """Send one written chapter back to the writer with a note from you.

    **This spends money** — a writer call and an editor call per pass, up to
    the usual revision limit. The note is what the review UI's *Note to
    writer* sends; it exists for the problems only a reader of the whole story
    sees, like a chapter that ends on a question the next one never answers.

    Nothing else is rewritten. Later chapters were written with this one as
    context and are left as they are; the caller says so. Approval and
    narration lapse on their own, because both are stamped with the hash.
    """
    note = (note or "").strip()
    if not note:
        raise ValueError("A note to the writer cannot be empty — say what to change.")
    if not b.chapter_json(n).exists():
        raise ValueError(f"chapter {n} has not been written yet.")
    review = draft_and_edit(cfg, b, n, verbose=verbose, note=note, on_pass=on_pass)
    _sync_edit_stage(b)
    return review


def estimate_redraft(cfg: dict, b: Bundle, n: int) -> dict[str, Any]:
    """What sending chapter `n` back with a note will cost, before sending it.

    Same structure as `estimate_write`, for one chapter: the writer is sent the
    fixed layers, every earlier chapter, and the current draft of this one, and
    the editor reads the same plus the result. The low figure is one pass; the
    high is the editor sending it back for every revision it is allowed.
    """
    TOK, FIXED = 1.33, 5000
    spec = next((c for c in b.outline().get("chapters", []) if c["n"] == n), None)
    if not spec or not b.chapter_json(n).exists():
        return {"known": False}
    words = b.chapter_words(n) or spec.get("target_words", 0)
    ctx = int(sum(b.chapter_words(m) for m in b.chapter_numbers()
                  if m < n and b.chapter_json(m).exists()) * TOK)
    out = int(spec.get("target_words", words) * TOK)
    passes = int(cfg.get("max_edit_passes", 2)) + 1
    base = {"passes": passes, "writer": cfg["models"]["writer"],
            "editor": cfg["models"]["editor"]}
    try:
        rates = _anthropic_rates()
        w = rates[cfg["models"]["writer"]]
        e = rates[cfg["models"]["editor"]]
    except (KeyError, OSError, ValueError):
        return {"known": False, **base}
    per = ((FIXED + ctx + int(words * TOK) + 300) * w["input_tokens"]
           + out * w["output_tokens"]
           + (FIXED + ctx + out) * e["input_tokens"]
           + 900 * e["output_tokens"]) / 1_000_000
    return {"known": True, "low": round(per, 2), "high": round(per * passes, 2),
            **base}


def chapter_done(b: Bundle, n: int) -> bool:
    """Has this chapter already been through the write/edit loop?

    Both terminal states count: a chapter that passed, and one that exhausted
    its revisions and kept the last draft. Neither is improved by running the
    loop again unasked — the exhausted one would spend the same passes to fail
    the same way — and both cost real money to reproduce.
    """
    if not (b.chapter_json(n).exists() and b.review_path(n).exists()):
        return False
    try:
        review = json.loads(b.review_path(n).read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return bool(review.get("pass") or review.get("exhausted"))


def pending_chapters(b: Bundle, restart: bool = False) -> list[int]:
    """The chapters a write would actually pay for."""
    numbers = b.chapter_numbers()
    return numbers if restart else [n for n in numbers if not chapter_done(b, n)]


def run_text_stages(cfg: dict, b: Bundle, verbose: bool = True,
                    on_chapter=None, restart: bool = False) -> dict[int, Any]:
    """Draft and edit every chapter that has not been done already.

    Writing is the second-largest cost in the pipeline and this used to redo
    the whole story on every run, so a crash in chapter 4 meant paying for
    chapters 1 to 3 a second time to get back the identical prose. Chapters
    already through the loop are now loaded from disk instead. Pass `restart`
    to write them all again regardless.

    `on_chapter(n, total, review)` fires after each one, resumed or written, so
    a caller that is not a terminal — the review UI — can show progress through
    a run that takes minutes.
    """
    reviews: dict[int, Any] = {}
    b.set_stage("write", "running")
    numbers = b.chapter_numbers()
    todo = set(pending_chapters(b, restart))

    if verbose and len(todo) < len(numbers):
        kept = sorted(set(numbers) - todo)
        print(f"  resuming: chapter{'s' if len(kept) > 1 else ''} "
              f"{', '.join(str(n) for n in kept)} already written, "
              f"{len(todo)} to go")

    for n in numbers:
        if n in todo:
            reviews[n] = draft_and_edit(cfg, b, n, verbose)
        else:
            reviews[n] = json.loads(b.review_path(n).read_text())
        if on_chapter:
            on_chapter(n, len(numbers), reviews[n])
    b.set_stage("write", "done")
    failed = [n for n, r in reviews.items() if not r.get("pass")]
    # Chapters wait for you the way an outline does. `record` is the largest
    # single cost in the pipeline and had no gate at all — it would happily
    # narrate chapters the editor had just failed.
    b.set_stage("edit", "awaiting_review", unpassed=failed)
    return reviews


def save_chapter(b: Bundle, n: int, segments: list[dict]) -> dict:
    """Write an edited chapter back, through the same checks a draft passes.

    The segments are the truth — the markdown is rendered from them — so an
    edit arrives as segments and never as prose to be parsed. That is what makes
    editing safe: the `speaker` field, which casts the voice, cannot be lost or
    guessed at, because it was never folded into the text in the first place.

    Everything a fresh draft is normalised by applies here too: a shortened
    speaker name resolves against the cast, an empty segment is dropped, a
    stray label is stripped. Anything the validator refuses raises rather than
    landing on disk half-checked.

    Approval and narration both lapse on their own, because both are recorded
    against the chapter hash and the hash has just changed.
    """
    outline = b.outline()
    chapter = dict(b.chapter(n))
    chapter["segments"] = [
        {"speaker": s.get("speaker", "narrator"), "text": s.get("text", "")}
        | ({"tag": s["tag"]} if s.get("tag") else {})
        for s in segments
    ]
    _validate_chapter(chapter, outline)
    b.chapter_json(n).write_text(json.dumps(chapter, indent=2) + "\n")
    b.chapter_md(n).write_text(render_markdown(chapter, outline))
    _sync_edit_stage(b)
    return chapter


def title_segments(b: Bundle, n: int) -> list[int]:
    """Indexes of opening segments that only announce the chapter.

    The narration adds the heading from a template now, so one written into the
    prose is spoken twice. Deliberately narrow: the segment must be narration,
    must be at the very start, must be short, and must reduce to the chapter's
    own title once "Chapter N" and punctuation are removed. A first line that
    happens to mention the title in a real sentence is not touched.
    """
    if not b.chapter_json(n).exists():
        return []
    title = next((c.get("title", "") for c in b.outline().get("chapters", [])
                  if c["n"] == n), "")
    if not title:
        return []

    def norm(x: str) -> str:
        x = re.sub(r"^\s*chapter\s+[0-9ivxlc]+\s*[.:\-—]?\s*", "", x, flags=re.I)
        return re.sub(r"[^a-z0-9]+", " ", x.lower()).strip()

    want = norm(title)
    found = []
    for i, seg in enumerate(b.chapter(n)["segments"]):
        if seg["speaker"] != "narrator" or len(seg["text"].split()) > 12:
            break
        if norm(seg["text"]) == want:
            found.append(i)
            continue
        break
    return found


def strip_title_segments(b: Bundle) -> dict[int, str]:
    """Remove announced titles from every chapter. Returns what was removed."""
    removed = {}
    for n in b.chapter_numbers():
        idx = title_segments(b, n)
        if not idx:
            continue
        chapter = b.chapter(n)
        removed[n] = chapter["segments"][idx[0]]["text"].strip()
        chapter["segments"] = [s for i, s in enumerate(chapter["segments"])
                               if i not in set(idx)]
        save_chapter(b, n, chapter["segments"])
    return removed


def review_stale(b: Bundle, n: int) -> bool:
    """Has the chapter changed since the editor judged it?

    Reviews written before the stamp existed carry no hash and are trusted,
    rather than flagging every chapter written up to now as unjudged.
    """
    if not b.review_path(n).exists():
        return False
    try:
        recorded = json.loads(b.review_path(n).read_text()).get("chapter_hash")
    except (json.JSONDecodeError, OSError):
        return False
    return bool(recorded) and recorded != b.chapter_hash(n)


def approve_chapters(b: Bundle, chapters: list[int] | None = None) -> list[int]:
    """Approve some chapters, or all of them. Returns what is approved after.

    The story-level `edit` stage is derived, never set independently: it reads
    `approved` only once every written chapter is individually approved, and
    falls back to `awaiting_review` the moment one is not. That way a chapter
    rewritten after approval cannot leave the story sitting on a green light for
    prose that has since changed.
    """
    for n in (chapters if chapters is not None else b.chapter_numbers()):
        if b.chapter_json(n).exists():
            b.approve_chapter(n)
    _sync_edit_stage(b)
    return b.approved_chapters()


def unapprove_chapters(b: Bundle, chapters: list[int]) -> list[int]:
    for n in chapters:
        b.unapprove_chapter(n)
    _sync_edit_stage(b)
    return b.approved_chapters()


def _sync_edit_stage(b: Bundle) -> None:
    done = b.all_chapters_approved()
    unpassed = [n for n in b.chapter_numbers()
                if b.review_path(n).exists()
                and not json.loads(b.review_path(n).read_text()).get("pass")]
    b.set_stage("edit", "approved" if done else "awaiting_review", unpassed=unpassed)
