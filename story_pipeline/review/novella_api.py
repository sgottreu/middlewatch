"""The Novellas page: board, chapter workflow and canon, for novella_pipeline.

A plugin of the review server (see server.PLUGIN_MODULES). It must not import
server — that module is still loading when this one is imported.

Paid work (draft, review) runs as a detached job through
`runner.start_command(key="novella-<slug>", ..., pipeline="novella")`, so it
outlives a restart and can be cancelled; the CLI streams the model's text into
the job log. Everything else is an instant read or write here.

Every path is confined under novellas_dir: a slug must match the slug pattern
and resolve inside it, and a chapter is an integer.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from novella_pipeline import prompts as nprompts
from novella_pipeline import stages
from novella_pipeline.bundle import (Bundle, NovellaError, bundle_roots, find_bundle,
                                     words)
from novella_pipeline.workflow import LABELS, NEXT_ACTION, ORDER, Stage

from . import runner
from .. import bibles, changelog

PIPELINE = "novella"
PAGE = Path(__file__).with_name("novellas.html")


def release(cfg: dict, meta: dict) -> None:
    """Nothing to put back. A draft or review writes the bundle only once the
    reply is complete, so a job killed mid-reply leaves the chapter exactly as
    it was; there is no `running` state to release."""
    return None


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def _dir(cfg: dict) -> Path:
    return Path(cfg.get("novellas_dir") or "novellas")


def _key(slug: str) -> str:
    return f"novella-{slug}"


def _bundle(cfg: dict, slug: str) -> Bundle:
    root = find_bundle(_dir(cfg), slug)
    if _dir(cfg).resolve() not in root.resolve().parents:
        raise FileNotFoundError(f"no novella called {slug}")
    return Bundle.open(root)


def _chapter_row(b: Bundle, c: dict) -> dict:
    st = Stage(c.get("stage", "outlined"))
    return {"n": c["n"], "title": c.get("title", ""), "stage": st.value,
            "label": st.label, "next": NEXT_ACTION[st],
            "target_words": c.get("target_words"),
            "words": words(b.edited(c["n"])), "version": b.current(c["n"])}


def _novella_row(b: Bundle) -> dict:
    m = b.manifest()
    p = m.get("pinned", {})
    return {"slug": m["slug"], "title": p.get("title"), "series": p.get("series"),
            "bible": p.get("bible"), "genre": p.get("genre"),
            "target_words": p.get("target_words"), "words": b.word_count(),
            "chapters": [_chapter_row(b, c) for c in b.chapters()],
            "spend": m.get("spend", {})}


def board(cfg: dict) -> dict:
    series: dict[str, dict] = {}
    for root in bundle_roots(_dir(cfg)):
        try:
            b = Bundle.open(root)
            row = _novella_row(b)
        except Exception:
            continue
        s = series.setdefault(root.parent.name, {"dir": root.parent.name,
                                                 "name": row["series"], "novellas": []})
        s["novellas"].append(row)
    return {"series": [series[k] for k in sorted(series)],
            "bibles": bibles.available(cfg.get("bibles_dir") or "bibles"),
            "stages": [{"value": s.value, "label": LABELS[s]} for s in ORDER],
            "models": {k: (cfg.get("models") or {}).get(f"novella_{k}")
                       for k in ("writer", "editor")}}


def novella(cfg: dict, slug: str) -> dict:
    b = _bundle(cfg, slug)
    out = _novella_row(b)
    out["brief"] = b.brief_path.read_text() if b.brief_path.exists() else ""
    out["outline"] = b.outline_path.read_text() if b.outline_path.exists() else ""
    out["job"] = job(cfg, slug)
    return out


def chapter(cfg: dict, slug: str, n: int) -> dict:
    b = _bundle(cfg, slug)
    c = b.chapter(n)
    row = _chapter_row(b, c)
    v = b.current(n)
    versions = []
    for x in b.versions(n):
        r = b.review(n, x)
        versions.append({"version": x, "draft_words": words(b.draft(n, x)),
                         "edited": b.edit_path(n, x).exists(),
                         "edit_words": words(b.edited(n, x)),
                         "reviewed": bool(r), "created": b.writer_meta(n, x).get("created")})
    writer = b.writer_meta(n)
    row.update({
        "novella": b.title, "bible": b.pinned("bible"),
        "beats": b.beats(n), "draft": b.draft(n), "edited": b.edited(n),
        "edit_saved": bool(v and b.edit_path(n, v).exists()),
        "writer": {k: writer.get(k, "") for k in
                   ("summary", "new_details", "flags", "parsed", "model", "note", "created")},
        "review": b.review(n),
        "summary": b.summary(n),
        "approved_details": (b.details_path(n).read_text()
                             if b.details_path(n).exists() else ""),
        "suggested_details": stages.suggested_details(b, n) if v else [],
        "versions": versions,
        "can": b.can(n),
        "earlier": [{"value": s.value, "label": s.label}
                    for s in ORDER if s.index < Stage(c["stage"]).index],
        "chapters": [{"n": x["n"], "title": x.get("title", "")} for x in b.chapters()],
        "job": job(cfg, slug),
    })
    return row


def prompt_preview(cfg: dict, slug: str, n: int, kind: str) -> dict:
    """What the writer or editor would be sent now. Free; nothing is called."""
    b = _bundle(cfg, slug)
    b.chapter(n)
    if kind == "draft":
        p = nprompts.writer_prompt(b, cfg, n)
    elif kind == "review":
        p = nprompts.editor_prompt(b, cfg, n)
    else:
        raise NovellaError("prompt kind is draft or review")
    return {"kind": kind, "system": p.system, "user": p.user,
            "words": words(p.system) + words(p.user)}


def canon(cfg: dict, slug: str) -> dict:
    """The bible as the agents see it, plus its changelog. Read-only."""
    b = _bundle(cfg, slug)
    name = b.pinned("bible") or ""
    if not name:
        return {"bible": "", "series": b.pinned("series"), "prompt_block": "",
                "changelog": []}
    bible = bibles.load(name, cfg.get("bibles_dir") or "bibles")
    return {"bible": name, "series": bible.series, "prompt_block": bible.prompt_block(),
            "changelog": [{"when": e.when, "author": e.author, "kind": e.kind,
                           "summary": e.summary} for e in changelog.entries(bible.path)]}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #


def job(cfg: dict, slug: str) -> dict:
    j = runner.status(_key(slug))
    marks = j.pop("progress", []) or []
    last = marks[-1] if marks else {}
    j.update(total=1, done=1 if any(m.get("event") == "done" for m in marks) else 0,
             stage=j.get("kind"), detail=last)
    return j


def log(cfg: dict, slug: str, offset: int = 0) -> dict:
    _bundle(cfg, slug)
    return runner.log_chunk(_key(slug), offset)


def _start(cfg: dict, slug: str, kind: str, n: int, extra: list[str]) -> dict:
    b = _bundle(cfg, slug)
    argv = [sys.executable, "-m", "novella_pipeline.cli",
            "--config", cfg.get("_config_path", "config.yaml"),
            kind, str(b.root), "--chapter", str(int(n)), *extra]
    runner.start_command(_key(slug), kind, argv, pipeline=PIPELINE,
                         busy=f"{b.title} already has a job running. Wait for it, "
                              "or stop it first.",
                         chapter=int(n), novella=slug)
    return {"ok": True, "job": job(cfg, slug)}


def start_draft(cfg: dict, slug: str, n: int, note: str = "") -> dict:
    b = _bundle(cfg, slug)
    b.check_draft(n)                        # refuse now, not in the job log
    nprompts.bible_of(b, cfg)               # a missing bible fails here, free
    return _start(cfg, slug, "draft", n, ["--note", note] if (note or "").strip() else [])


def start_review(cfg: dict, slug: str, n: int) -> dict:
    b = _bundle(cfg, slug)
    b.check_review(n)
    nprompts.bible_of(b, cfg)
    return _start(cfg, slug, "review", n, [])


def cancel(cfg: dict, slug: str) -> dict:
    _bundle(cfg, slug)
    runner.cancel(_key(slug))
    return {"ok": True, "job": job(cfg, slug)}


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


def create(cfg: dict, body: dict) -> dict:
    name = (body.get("bible") or "").strip()
    bible = bibles.load(name, cfg.get("bibles_dir") or "bibles") if name else None
    series = (body.get("series") or "").strip() or (bible.series if bible else "")
    genre = (body.get("genre") or "").strip() or (bible.meta.get("genre", "") if bible else "")
    kw = {}
    if body.get("target_words"):
        kw["target_words"] = int(body["target_words"])
    b = Bundle.create(_dir(cfg), (body.get("title") or "").strip(), series,
                      genre=genre, bible=name, slug=(body.get("slug") or None), **kw)
    return {"ok": True, "slug": b.slug}


def save_doc(cfg: dict, slug: str, which: str, text: str) -> dict:
    b = _bundle(cfg, slug)
    path = {"brief": b.brief_path, "outline": b.outline_path}[which]
    path.write_text((text or "").replace("\r\n", "\n").rstrip() + "\n")
    return {"ok": True}


def save_beats(cfg: dict, slug: str, n: int, body: dict) -> dict:
    b = _bundle(cfg, slug)
    tw = body.get("target_words")
    b.save_plan(n, (body.get("title") or "").strip(), int(tw) if tw else None,
                body.get("beats") if "beats" in body else None)
    return {"ok": True, "chapter": chapter(cfg, slug, n)}


def save_edit(cfg: dict, slug: str, n: int, text: str) -> dict:
    b = _bundle(cfg, slug)
    st = b.save_edit(n, text or "")
    return {"ok": True, "stage": st.value, "words": words(text)}


def approve(cfg: dict, slug: str, n: int, body: dict) -> dict:
    b = _bundle(cfg, slug)
    details = body.get("details") or []
    if isinstance(details, str):
        details = [l for l in details.splitlines() if l.strip()]
    return stages.approve(cfg, b, n, body.get("summary") or "", details)


def send_back(cfg: dict, slug: str, n: int, to: str) -> dict:
    b = _bundle(cfg, slug)
    return {"ok": True, "stage": b.send_back(n, to).value}


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #

_ROUTE = re.compile(r"^/api/novella/(?P<slug>[^/]+)"
                    r"(?:/(?P<what>[a-z-]+)(?:/(?P<n>[^/]+)(?:/(?P<sub>[a-z-]+)(?:/(?P<kind>[a-z]+))?)?)?)?$")


def _n(raw: str | None) -> int:
    if raw is None or not re.fullmatch(r"\d{1,2}", raw):
        raise NovellaError(f"not a chapter number: {raw!r}")
    return int(raw)


def _answer(handler, fn) -> bool:
    """Run fn and send its result; map our errors to 400/404 instead of a 500."""
    try:
        handler._json(fn())
    except FileNotFoundError as e:
        handler._json({"error": str(e) or "no such novella"}, 404)
    except (ValueError, KeyError) as e:
        handler._json({"error": str(e)}, 400)
    return True


def handle_get(handler, cfg: dict, path: str) -> bool:
    if path in ("/novellas", "/novellas.html"):
        handler._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        return True
    if path == "/api/novella":
        return _answer(handler, lambda: board(cfg))
    m = _ROUTE.match(path)
    if not m:
        return False
    slug, what, n, sub, kind = (unquote(m["slug"]), m["what"], m["n"], m["sub"], m["kind"])
    q = parse_qs(urlparse(handler.path).query)
    if what is None:
        return _answer(handler, lambda: novella(cfg, slug))
    if what == "job" and n is None:
        return _answer(handler, lambda: job(cfg, _bundle(cfg, slug).slug))
    if what == "log" and n is None:
        return _answer(handler, lambda: log(cfg, slug, int((q.get("from") or ["0"])[0] or 0)))
    if what == "canon" and n is None:
        return _answer(handler, lambda: canon(cfg, slug))
    if what == "chapter" and n is not None:
        if sub is None:
            return _answer(handler, lambda: chapter(cfg, slug, _n(n)))
        if sub == "prompt" and kind:
            return _answer(handler, lambda: prompt_preview(cfg, slug, _n(n), kind))
    return False


def handle_post(handler, cfg: dict, path: str, body: dict) -> bool:
    if path == "/api/novella/new":
        return _answer(handler, lambda: create(cfg, body))
    m = _ROUTE.match(path)
    if not m:
        return False
    slug, what, n, sub = unquote(m["slug"]), m["what"], m["n"], m["sub"]
    if what in ("brief", "outline") and n is None:
        return _answer(handler, lambda: save_doc(cfg, slug, what, body.get("text", "")))
    if what == "cancel" and n is None:
        return _answer(handler, lambda: cancel(cfg, slug))
    if what != "chapter" or n is None or m["kind"]:
        return False
    if sub is None:
        return _answer(handler, lambda: save_edit(cfg, slug, _n(n), body.get("text", "")))
    actions = {
        "beats": lambda: save_beats(cfg, slug, _n(n), body),
        "draft": lambda: start_draft(cfg, slug, _n(n), body.get("note", "")),
        "review": lambda: start_review(cfg, slug, _n(n)),
        "approve": lambda: approve(cfg, slug, _n(n), body),
        "send-back": lambda: send_back(cfg, slug, _n(n), body.get("to", "")),
    }
    if sub in actions:
        return _answer(handler, actions[sub])
    return False
