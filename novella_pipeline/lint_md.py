"""story_pipeline.lint's AI-tell checks, run over Markdown prose.

lint.py works on a chapter of speaker segments. A novella chapter is Markdown,
so it is cut into the same shape here — one narrator segment per paragraph, a
`* * *` line as a break — and handed to `lint.lint_chapter` unchanged. Headings
are dropped (they are not prose) and so are emphasis markers, which would
otherwise sit inside the quotes the findings show. `segment` in a finding is
the paragraph's index, counted from 0 among the prose paragraphs.

Rules written for narration still run (long sentences, clause chains, the
sentence after a scene break). On the page they are advisory — they are all
warnings — and the editor is told the author sees this list beside its own.
"""

from __future__ import annotations

import re

from story_pipeline import lint
from story_pipeline.bundle import BREAK

_BREAK_LINE = re.compile(r"^\s*(?:\*\s*){3,}$|^\s*(?:-\s*){3,}$|^\s*(?:_\s*){3,}$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")


def to_chapter(markdown: str) -> dict:
    segments: list[dict] = []
    para: list[str] = []

    def flush():
        if para:
            text = re.sub(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1", r"\2", " ".join(para))
            segments.append({"speaker": "narrator", "text": text.strip()})
            para.clear()

    for line in (markdown or "").replace("\r\n", "\n").split("\n"):
        if _BREAK_LINE.match(line):
            flush()
            segments.append({"speaker": BREAK, "text": ""})
        elif _HEADING.match(line):
            flush()
        elif not line.strip():
            flush()
        else:
            para.append(line.strip())
    flush()
    return {"segments": segments}


def lint_markdown(markdown: str) -> dict:
    """lint.summarize() of the findings, with `segment` renumbered to prose
    paragraphs (breaks don't count) so the page can say "paragraph 12"."""
    chapter = to_chapter(markdown)
    findings = lint.lint_chapter(chapter)
    index, k = {}, 0
    for i, s in enumerate(chapter["segments"]):
        if s["speaker"] != BREAK:
            index[i] = k
            k += 1
    out = lint.summarize(findings)
    for f in out["findings"]:
        seg = f.pop("segment")
        f["paragraph"] = index.get(seg, -1) if seg >= 0 else -1
    out["paragraphs"] = k
    return out
