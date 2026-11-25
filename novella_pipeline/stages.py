"""The paid steps (draft, review) and approve, shared by the CLI and the page.

Every model call goes through story_pipeline.llm with a model name from config
and is recorded with story_pipeline.spend.record_llm, so a novella's cost lands
in the manifest and the ledger the same way a story's does.
"""

from __future__ import annotations

from typing import Callable

from story_pipeline import bibles, changelog, llm, spend

from . import lint_md, prompts
from .bundle import Bundle, NovellaError
from .parsing import WriterResult, bullets, parse_writer

OnText = Callable[[str], None]


def _model(cfg: dict, role: str) -> tuple[str, int]:
    key = f"novella_{role}"
    model = (cfg.get("models") or {}).get(key)
    if not model:
        raise NovellaError(f"no models.{key} in config")
    return model, int((cfg.get("max_tokens") or {}).get(key) or 16000)


def draft(cfg: dict, b: Bundle, n: int, note: str = "",
          on_text: OnText | None = None) -> tuple[int, WriterResult]:
    """Run the writer and save its reply as the chapter's next version."""
    b.check_draft(n)
    p = prompts.writer_prompt(b, cfg, n, note)   # raises before spending if the bible is gone
    model, ceiling = _model(cfg, "writer")
    text, usage = llm.complete(model, p.system, p.user, max_tokens=ceiling, on_text=on_text)
    spend.record_llm(b, "draft", model, usage, notes=f"chapter {n}")
    result = parse_writer(text)
    v = b.save_draft(n, text=result.text, summary=result.summary,
                     new_details=result.new_details, flags=result.flags,
                     parsed=result.parsed, model=model, note=note)
    return v, result


def review(cfg: dict, b: Bundle, n: int, on_text: OnText | None = None) -> dict:
    """Run the editor on Scott's edit, lint the same text, save both together."""
    b.check_review(n)
    p = prompts.editor_prompt(b, cfg, n)
    model, ceiling = _model(cfg, "editor")
    reviewed = b.edited(n)
    text, usage = llm.complete(model, p.system, p.user, max_tokens=ceiling, on_text=on_text)
    spend.record_llm(b, "review", model, usage, notes=f"chapter {n}")
    findings = lint_md.lint_markdown(reviewed)
    b.save_review(n, text, findings, model=model, reviewed_text=reviewed)
    return b.review(n)


def suggested_details(b: Bundle, n: int) -> list[str]:
    """New details the writer listed and the editor's `New detail:` bullets, for
    the approve form to offer. Scott picks; nothing is added unasked."""
    out: list[str] = []
    for item in bullets(b.writer_meta(n).get("new_details") or ""):
        out.append(item)
    for item in bullets((b.review(n).get("sections") or {}).get("Canon", "")):
        if item.lower().startswith("new detail"):
            out.append(item.split(":", 1)[1].strip() if ":" in item else item)
    seen, uniq = set(), []
    for d in out:
        if d and d.lower() not in seen:
            seen.add(d.lower())
            uniq.append(d)
    return uniq


def approve(cfg: dict, b: Bundle, n: int, summary: str,
            details: list[str] | None = None, author: str | None = None) -> dict:
    """Lock the chapter; approved new details go to the bible's changelog.

    Everything is checked before anything is written — kindle wrote the summary
    and the stage before finding there was no bible to add details to. The
    bible body is never touched and nothing is promoted: the changelog entry is
    a note for Scott to fold in by hand.
    """
    details = [d.strip().lstrip("-* ").strip() for d in (details or []) if d and d.strip()]
    b.check_approve(n, summary)
    bible = None
    if details:
        name = b.pinned("bible") or ""
        if not name:
            raise NovellaError("This novella has no series bible to log new details against.")
        bible = bibles.load(name, cfg.get("bibles_dir") or "bibles")   # raises if missing
        if not bible.path.exists():
            raise NovellaError(f"bible {name} is missing at {bible.path}")
    who = changelog.resolve_author(author, cfg) if bible else None

    b.approve(n, summary, details)
    logged = None
    if bible:
        ch = b.chapter(n)
        entry = (f"New details approved in the novella *{b.title}*, chapter {n}"
                 f"{(': ' + ch['title']) if ch.get('title') else ''} "
                 f"(`{b.slug}`). Not yet in the bible body.\n\n"
                 + "\n".join(f"- {d}" for d in details))
        logged = str(changelog.append(bible.path, entry, who, kind="novella",
                                      series=bible.series))
    return {"ok": True, "stage": b.stage(n).value, "details": details,
            "changelog": logged}
