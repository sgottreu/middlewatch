"""The prompt builder: one place that assembles canon, brief, outline and chapter
context for the writer and the editor.

Ported from kindle-novellas `storyflow/prompts.py`. Two changes:

- The system prompt is the novella writer/editor file in novella_pipeline/prompts/
  with the shared layers substituted in (house.md, boundaries.md, ai-tells.md,
  authors/scott-voice.md and the genre guide), so a novella is judged against
  the same rules as a story and the writer and editor see identical text.
- The series bible is `Bible.prompt_block()`, read in place from bibles/, not
  a `bible.md` beside the book. Working-only sections never reach the model.

The canon block still comes first in the user message, but there is no cache
breakpoint: `story_pipeline.llm.complete` sends one plain-text user turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from story_pipeline import bibles, genres
from story_pipeline.config import prompt_text

from .bundle import Bundle
from .parsing import EDITOR_SECTIONS, WRITER_SECTIONS

PROMPTS_DIR = Path(__file__).parent / "prompts"

# What a novella is, in the words the writer and editor both get.
CHAPTERS_MIN, CHAPTERS_MAX = 10, 16
CHAPTER_WORDS_MIN, CHAPTER_WORDS_MAX = 2500, 4000


@dataclass
class Prompt:
    system: str
    content: list[str]

    @property
    def user(self) -> str:
        return "\n\n".join(self.content)

    def as_text(self) -> str:
        """Readable version for the page's prompt preview."""
        return self.user


def _tag(name: str, body: str, **attrs) -> str:
    attr = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return f"<{name}{attr}>\n{(body or '').strip() or '(none)'}\n</{name}>"


def structure_block(target_words: int | None) -> str:
    total = f"{int(target_words):,} words" if target_words else "25,000 to 40,000 words"
    return (
        f"The book is {CHAPTERS_MIN} to {CHAPTERS_MAX} chapters and about {total}. "
        f"A chapter runs {CHAPTER_WORDS_MIN:,} to {CHAPTER_WORDS_MAX:,} words; this "
        "chapter's own target is in its tag and wins over the range. There is no "
        "runtime and no words-per-minute: this is read, not narrated.\n\n"
        "Each chapter ends on a turn: information, a reversal, a decision, or a "
        "line of dialogue that changes what the next chapter is about."
    )


def _genre(b: Bundle) -> tuple[str, str]:
    name = b.pinned("genre") or ""
    if not name:
        return "none declared", "(No genre guide: follow the brief and the bible.)"
    g = genres.load(name)
    return g.meta.get("label", g.name), g.guide


def bible_of(b: Bundle, cfg: dict) -> bibles.Bible | None:
    name = b.pinned("bible") or ""
    return bibles.load(name, cfg.get("bibles_dir") or "bibles") if name else None


def agent_system(name: str, b: Bundle) -> str:
    label, guide = _genre(b)
    fields = {
        "structure": structure_block(b.pinned("target_words")),
        # house.md carries its own {structure} placeholder (the story length
        # block); filled below with the novella one so it never reaches a model.
        "house": prompt_text("house"),
        "boundaries": prompt_text("boundaries"),
        "ai_tells": prompt_text("ai-tells"),
        "author_voice": prompt_text("authors/scott-voice"),
        "genre_label": label,
        "genre_guide": guide,
    }
    out = (PROMPTS_DIR / f"{name}.md").read_text()
    for key, value in fields.items():
        out = out.replace("{" + key + "}", str(value))
    out = out.replace("{structure}", fields["structure"])
    return out


def canon_block(b: Bundle, cfg: dict) -> str:
    parts = []
    bible = bible_of(b, cfg)
    if bible:
        parts.append(_tag("series_bible", bible.prompt_block()))
    parts.append(_tag("story_brief", _read(b.brief_path)))
    parts.append(_tag("outline", _read(b.outline_path)))
    return "\n\n".join(parts)


def _summaries(b: Bundle, n: int) -> str:
    items = b.earlier_summaries(n)
    body = "\n\n".join(f"Chapter {c['n']}: {c.get('title', '')}\n{text}" for c, text in items)
    return _tag("previous_chapter_summaries", body or "This is the first chapter.")


def _format_rules(sections: tuple[str, ...]) -> str:
    heads = "\n".join(f"## {s}" for s in sections)
    return (
        "Format your whole reply as Markdown under exactly these level-2 headings, in this "
        f"order, and use no other level-2 headings:\n\n{heads}\n\n"
        "Write \"None.\" under any heading with nothing to report."
    )


def writer_prompt(b: Bundle, cfg: dict, n: int, note: str = "") -> Prompt:
    ch = b.chapter(n)
    parts = [
        _summaries(b, n),
        _tag("target_chapter", b.beats(n), number=ch["n"], title=ch.get("title", ""),
             target_words=ch.get("target_words", "")),
    ]
    if (note or "").strip():
        parts.append(_tag("author_note", note))
    parts += [
        f"Write chapter {ch['n']} following the rules in your instructions. "
        "Put the chapter title as a level-3 heading at the top of Chapter Text.",
        _format_rules(WRITER_SECTIONS),
    ]
    return Prompt(agent_system("writer", b), [canon_block(b, cfg), "\n\n".join(parts)])


def editor_prompt(b: Bundle, cfg: dict, n: int) -> Prompt:
    ch = b.chapter(n)
    task = "\n\n".join([
        _summaries(b, n),
        _tag("chapter_beats", b.beats(n), number=ch["n"], title=ch.get("title", ""),
             target_words=ch.get("target_words", "")),
        _tag("text_to_review", b.edited(n), number=ch["n"]),
        f"Review chapter {ch['n']} following your instructions. Suggest only; never "
        "rewrite the chapter. Put each suggestion as a bullet with its location, issue "
        "and suggestion. Canon problems go under Canon; also list there any new details "
        "not yet in the bible.",
        _format_rules(EDITOR_SECTIONS),
    ])
    return Prompt(agent_system("editor", b), [canon_block(b, cfg), task])


def _read(p: Path) -> str:
    return p.read_text() if p.exists() else ""
