"""Novella command line. Run from the repo root:

    python3 -m novella_pipeline.cli new --series "Broken Edges" --title "Glasswood" --bible NAME
    python3 -m novella_pipeline.cli beats <bundle> --chapter 1 --title T --beats-file F
    python3 -m novella_pipeline.cli draft <bundle> --chapter 1 [--note "..."]     # paid
    python3 -m novella_pipeline.cli review <bundle> --chapter 1                   # paid
    python3 -m novella_pipeline.cli approve <bundle> --chapter 1 --summary-file F [--details-file F]
    python3 -m novella_pipeline.cli send-back <bundle> --chapter 1 --to outlined
    python3 -m novella_pipeline.cli status <bundle>

<bundle> is a path (novellas/<series>/<slug>) or a bare slug. draft and review
stream the model's text to stdout as it arrives, which is what the review
page's job log shows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from story_pipeline import bibles
from story_pipeline import config as cfgmod

from . import stages
from .bundle import DEFAULT_BOOK_WORDS, Bundle, resolve
from .parsing import bullets
from .workflow import NEXT_ACTION, Stage


def _progress(**fields) -> None:
    """One marker line for the review server (runner.MARKER), only under MW_PROGRESS."""
    if os.environ.get("MW_PROGRESS"):
        print("::progress " + json.dumps(fields), flush=True)


def _open(args, cfg) -> Bundle:
    return Bundle.open(resolve(cfg.get("novellas_dir") or "novellas", args.bundle))


def _stream(piece: str) -> None:
    sys.stdout.write(piece)
    sys.stdout.flush()


def _read_file(path: str | None) -> str:
    return Path(path).read_text() if path else ""


def cmd_new(args, cfg) -> None:
    bible = None
    if args.bible:
        bible = bibles.load(args.bible, cfg.get("bibles_dir") or "bibles")
    series = args.series or (bible.series if bible else "")
    genre = args.genre or (bible.meta.get("genre", "") if bible else "")
    b = Bundle.create(cfg.get("novellas_dir") or "novellas", args.title, series,
                      genre=genre, bible=args.bible or "", slug=args.slug,
                      target_words=args.target_words)
    print(f"created {b.root}")
    print("next: write brief.md and outline.md, then add chapter beats "
          "(cli beats, or the Novellas page)")


def cmd_beats(args, cfg) -> None:
    b = _open(args, cfg)
    beats = _read_file(args.beats_file) if args.beats_file else None
    ch = b.save_plan(args.chapter, args.title or "", args.target_words, beats)
    print(f"chapter {ch['n']}: {ch.get('title') or 'untitled'} "
          f"({ch['target_words']:,} words) — {ch['stage']}")


def cmd_draft(args, cfg) -> None:
    b = _open(args, cfg)
    note = args.note or _read_file(args.note_file)
    _progress(event="start", stage="draft", n=args.chapter, total=1, done=0)
    print(f"Drafting chapter {args.chapter} of {b.title}…\n", flush=True)
    v, result = stages.draft(cfg, b, args.chapter, note=note, on_text=_stream)
    print(f"\n\nSaved as version {v} ({len(result.text.split()):,} words).", flush=True)
    if not result.parsed:
        print("warning: the reply did not follow the format; the whole reply was "
              "kept as the draft.", flush=True)
    _progress(event="done", stage="draft", n=args.chapter, version=v,
              words=len(result.text.split()), parsed=result.parsed, total=1, done=1)


def cmd_review(args, cfg) -> None:
    b = _open(args, cfg)
    _progress(event="start", stage="review", n=args.chapter, total=1, done=0)
    print(f"Reviewing chapter {args.chapter} of {b.title}…\n", flush=True)
    r = stages.review(cfg, b, args.chapter, on_text=_stream)
    lint = r.get("lint") or {}
    print(f"\n\nReview saved. Linter: {lint.get('errors', 0)} errors, "
          f"{lint.get('total', 0)} findings in all.", flush=True)
    _progress(event="done", stage="review", n=args.chapter,
              lint_errors=lint.get("errors", 0), lint_total=lint.get("total", 0),
              total=1, done=1)


def cmd_approve(args, cfg) -> None:
    b = _open(args, cfg)
    summary = _read_file(args.summary_file)
    details = bullets(_read_file(args.details_file)) if args.details_file else []
    if args.details_file and not details:
        # A details file of plain lines rather than bullets.
        details = [l.strip() for l in _read_file(args.details_file).splitlines() if l.strip()]
    out = stages.approve(cfg, b, args.chapter, summary, details, author=args.author)
    print(f"chapter {args.chapter} approved")
    if out["changelog"]:
        print(f"{len(out['details'])} new detail(s) logged in {out['changelog']}")


def cmd_send_back(args, cfg) -> None:
    b = _open(args, cfg)
    st = b.send_back(args.chapter, args.to)
    print(f"chapter {args.chapter} is now {st.label}")


def cmd_status(args, cfg) -> None:
    b = _open(args, cfg)
    m = b.manifest()
    p = m.get("pinned", {})
    print(f"{p.get('title')}  [{p.get('series')}]  bible: {p.get('bible') or '-'}  "
          f"genre: {p.get('genre') or '-'}")
    print(f"{b.word_count():,} of {p.get('target_words', 0):,} words")
    for c in b.chapters():
        st = Stage(c["stage"])
        v = b.current(c["n"])
        print(f"  {c['n']:>2}. {c.get('title') or 'untitled':<32} {st.label:<16} "
              f"v{v or 0}  next: {NEXT_ACTION[st]}")
    if m.get("spend"):
        print("spend: " + ", ".join(f"{k}={v}" for k, v in sorted(m["spend"].items())))


def main(argv=None):
    p = argparse.ArgumentParser(prog="novella_pipeline", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new", help="create a novella bundle; free")
    s.add_argument("--title", required=True)
    s.add_argument("--series", help="defaults to the bible's series")
    s.add_argument("--bible", help="series bible, read in place from bibles/")
    s.add_argument("--genre", help="defaults to the bible's genre")
    s.add_argument("--slug")
    s.add_argument("--target-words", type=int, default=DEFAULT_BOOK_WORDS)
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("beats", help="add a chapter or set its title, length and beats; free")
    s.add_argument("bundle")
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--title")
    s.add_argument("--target-words", type=int)
    s.add_argument("--beats-file")
    s.set_defaults(fn=cmd_beats)

    s = sub.add_parser("draft", help="run the writer on one chapter; PAID")
    s.add_argument("bundle")
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--note", default="")
    s.add_argument("--note-file")
    s.set_defaults(fn=cmd_draft)

    s = sub.add_parser("review", help="run the editor on your edit; PAID")
    s.add_argument("bundle")
    s.add_argument("--chapter", type=int, required=True)
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("approve", help="lock a reviewed chapter; free")
    s.add_argument("bundle")
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--summary-file", required=True)
    s.add_argument("--details-file", help="new details, one bullet each, for the bible's changelog")
    s.add_argument("--author")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("send-back", help="move a chapter to an earlier stage; free")
    s.add_argument("bundle")
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--to", required=True, choices=[x.value for x in Stage])
    s.set_defaults(fn=cmd_send_back)

    s = sub.add_parser("status", help="chapters and stages; free")
    s.add_argument("bundle")
    s.set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    cfgmod.load_env()
    cfg = cfgmod.load(args.config)
    try:
        args.fn(args, cfg)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
