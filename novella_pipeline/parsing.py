"""Split agent replies into the sections the bundle stores and the page shows.

Ported from kindle-novellas `storyflow/parsing.py`. The writer and editor reply
in Markdown under fixed level-2 headings rather than JSON: the chapter is prose,
and prose inside a JSON string is where escaping goes wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WRITER_SECTIONS = ("Chapter Text", "Chapter Summary", "New Details", "Flags")
EDITOR_SECTIONS = ("Must Fix", "Canon", "Should Consider", "Minor", "What's Working")

_EMPTY = {"", "none", "none.", "n/a", "-"}


def split_sections(text: str, names: tuple[str, ...]) -> dict[str, str]:
    """Return {name: body} for each known level-2 heading. Matching ignores case,
    numbering and bold markers, e.g. '## 1. **Chapter text**'. An unknown
    level-2 heading is not a boundary: its text stays in the section before."""
    wanted = {_norm(n): n for n in names}
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m and _norm(m.group(1)) in wanted:
            if current:
                out[current] = "\n".join(buf).strip()
            current, buf = wanted[_norm(m.group(1))], []
        elif current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


def _norm(heading: str) -> str:
    h = re.sub(r"^\d+[.)]\s*", "", heading.strip())
    return re.sub(r"[*_`:]", "", h).strip().lower().replace("’", "'")


def is_empty(body: str) -> bool:
    return body.strip().lower() in _EMPTY


def bullets(body: str) -> list[str]:
    """Top-level bullet items, with any wrapped lines joined."""
    items: list[str] = []
    for line in body.splitlines():
        if re.match(r"^[-*]\s+", line):
            items.append(re.sub(r"^[-*]\s+", "", line).strip())
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
    return [i for i in items if not is_empty(i)]


@dataclass
class WriterResult:
    text: str
    summary: str
    new_details: str
    flags: str
    parsed: bool  # False when the reply didn't follow the format


def parse_writer(reply: str) -> WriterResult:
    s = split_sections(reply, WRITER_SECTIONS)
    if not s.get("Chapter Text"):
        # Keep everything as the draft so nothing is lost; the author can tidy it.
        return WriterResult(reply.strip(), "", "",
                            "Writer reply did not follow the expected format.", False)

    def clean(k):
        return "" if is_empty(s.get(k, "")) else s.get(k, "")
    return WriterResult(s["Chapter Text"], clean("Chapter Summary"),
                        clean("New Details"), clean("Flags"), True)


def parse_review(review: str) -> dict[str, str]:
    """Review sections by severity. Unparseable reviews come back whole under 'Review'."""
    s = split_sections(review, EDITOR_SECTIONS)
    return s if s else {"Review": review}
