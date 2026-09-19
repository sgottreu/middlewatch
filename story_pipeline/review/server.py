"""`cli review` — a local page for the one part of this pipeline that needs a human.

Stdlib only, bound to localhost, one HTML file. This is a single-user tool
looking at files in your own repo: a framework, a database and a login screen
would all be work spent on problems that do not exist here. The bundles on disk
stay the only state, the way every other stage treats them.

Routes:

    GET  /                     the page
    GET  /api/queue            everything waiting, grouped
    GET  /api/story/<slug>     one story: outline, chapters, reviews, spend
    POST /api/approve          outline, all chapters, or {"chapters": [3]} for
                               one at a time; {"undo": true} withdraws it
    GET  /api/job/<slug>       progress of the running (or last) stage
    GET  /api/log/<slug>       that stage's own output; ?from=<byte offset>
    POST /api/cancel           stop it — kills the process group
    POST /api/revise           regenerate the outline — a paid call
    POST /api/write            draft the chapters not yet written; the most
                               expensive action. {"restart": true} rewrites all
    POST /api/critique         re-run the editor on written chapters — paid,
                               but rewrites nothing
    POST /api/chapter          save an edited chapter's segments — free
    POST /api/redraft          send one chapter back to the writer with your
                               note — paid; runs as a job like write
    POST /api/record           narrate the approved chapters — paid; a job
    POST /api/design           draw the cast and scenes — paid; a job
    GET  /api/estimate/<slug>  what record and design would cost; free
    GET  /api/package/<slug>   audio and images as a zip, to render elsewhere
    POST /api/reject           delete the bundle

`direct` is deliberately absent. It is the one stage that needs real hardware —
see docs/story/aws.md — so the page hands you the assets instead.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import runner
from .. import genres, history, summary
from ..agents import text as textagent
from ..bundle import Bundle, real_answer

APP_HTML = Path(__file__).with_name("app.html")

# The only static files this server has. Named explicitly rather than serving the
# directory: `app.html` sits here too, and so would anything else that ever lands
# in the package, and a review tool has no business handing out arbitrary files
# even on localhost.
ICONS = {
    "icon.svg": "image/svg+xml",
    "icon-180.png": "image/png",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png",
}

# Enough for a browser to offer "install" and give the window its own icon in
# the dock or taskbar, rather than a generic browser tile.
MANIFEST = {
    "name": "Middle Watch — review",
    "short_name": "Middle Watch",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#14140f",
    "theme_color": "#14140f",
    "icons": [
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"},
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
}


# --------------------------------------------------------------------------- #
# Reading the bundles
# --------------------------------------------------------------------------- #


def _spend(manifest: dict) -> dict:
    """Money already committed, in the units the manifest records."""
    spend = manifest.get("spend", {})
    return {
        "usd": round(sum(v for k, v in spend.items() if k.endswith("usd")), 2),
        "raw": spend,
    }


def _outline_flags(outline: dict, spec: dict) -> list[dict]:
    """Everything about this outline that should catch your eye.

    The terminal printed these as lines that scroll past. A badge does not.
    """
    flags = []
    minors = [c for c in outline.get("cast", [])
              if isinstance(c.get("age"), int) and c["age"] < 18]
    if minors:
        flags.append({
            "level": "danger",
            "text": "Under 18 in cast: " + ", ".join(
                f"{c['name']} ({c['age']}, {c.get('role','?')})" for c in minors),
            "note": "Confirm they are peripheral and carry no romantic charge.",
        })
    # Both fields are "null unless there is something to say", and a model that
    # answers with the instruction instead of null would otherwise raise a
    # warning that says nothing.
    if real_answer(outline.get("bible_conflict")):
        flags.append({"level": "warn", "text": "Notes contradicted the bible",
                      "note": real_answer(outline["bible_conflict"])})
    if real_answer(outline.get("notes_conflict")):
        flags.append({"level": "warn", "text": "Notes conflicted with the boundaries",
                      "note": real_answer(outline["notes_conflict"])})

    beats = sum(len(c.get("beats", [])) for c in outline.get("chapters", []))
    words = sum(c.get("target_words", 0) for c in outline.get("chapters", []))
    if beats and words / beats > 150:
        flags.append({
            "level": "warn",
            "text": f"{words / beats:.0f} words per beat",
            "note": "Thin. The prose will stretch to fill the space rather than "
                    "the story getting bigger.",
        })
    return flags


def _story_row(root: Path) -> dict | None:
    try:
        b = Bundle.open(root)
        m = b.manifest()
        outline = b.outline()
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None

    stages = m.get("stages", {})
    spec = textagent.length_spec(
        m.get("pinned", {}).get("length_min", outline.get("length_min")))
    beats = sum(len(c.get("beats", [])) for c in outline.get("chapters", []))
    words = sum(c.get("target_words", 0) for c in outline.get("chapters", []))

    unpassed = m.get("stage_info", {}).get("edit", {}).get("unpassed") or []
    stale = [n for n in b.chapter_numbers() if b.narration_stale(n)]

    # `edit` is stored, but the truth is the per-chapter approvals. Editing a
    # chapter by hand changes its hash without going through an approve action,
    # so the stored value can say approved while holding prose nobody agreed to.
    # Report what the chapters actually say; the next approve writes it back.
    written = [n for n in b.chapter_numbers() if b.chapter_json(n).exists()]
    if written and stages.get("edit") in ("approved", "awaiting_review"):
        stages = {**stages,
                  "edit": "approved" if b.all_chapters_approved() else "awaiting_review"}

    return {
        "slug": root.name,
        "title": outline.get("title", root.name),
        "series": outline.get("series", ""),
        "genre": outline.get("genre", ""),
        "subgenre": outline.get("subgenre", ""),
        "bible": outline.get("bible"),
        "length": spec["minutes"],
        "stages": stages,
        "chapters": len(outline.get("chapters", [])),
        "beats": beats,
        "words": words,
        "per_beat": round(words / beats) if beats else 0,
        "tropes": outline.get("tropes", []),
        "logline": outline.get("logline", ""),
        "unpassed": unpassed,
        "stale": stale,
        "spend": _spend(m),
        "flags": _outline_flags(outline, spec),
    }


def queue(cfg: dict) -> dict:
    """Everything waiting on a human, grouped by what kind of waiting it is."""
    root = Path(cfg["stories_dir"])
    rows = [r for r in (_story_row(p.parent)
                        for p in sorted(root.glob("*/manifest.json"))) if r]

    groups = {"outlines": [], "approved": [], "chapters": [], "failed": [],
              "stale": [], "done": []}
    for r in rows:
        st = r["stages"]
        if st.get("ideate") == "awaiting_review":
            groups["outlines"].append(r)
        # Approved, but the writer has not finished — the state a story sits in
        # between the two review gates. It was falling into the catch-all,
        # where the one thing you can actually do next was the hardest to find.
        elif st.get("ideate") == "approved" and st.get("write") != "done":
            groups["approved"].append(r)
        elif r["stale"]:
            groups["stale"].append(r)
        elif r["unpassed"]:
            groups["failed"].append(r)
        elif st.get("edit") == "awaiting_review":
            groups["chapters"].append(r)
        else:
            groups["done"].append(r)

    return {"groups": groups, "total": len(rows)}


def story_detail(cfg: dict, slug: str) -> dict:
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    row = _story_row(b.root)
    outline = b.outline()

    chapters = []
    for n in b.chapter_numbers():
        review = {}
        if b.review_path(n).exists():
            try:
                review = json.loads(b.review_path(n).read_text())
            except json.JSONDecodeError:
                review = {}
        chapters.append({
            "n": n,
            "title": next((c.get("title", "") for c in outline["chapters"]
                           if c["n"] == n), ""),
            "beats": next((c.get("beats", []) for c in outline["chapters"]
                           if c["n"] == n), []),
            "target_words": next((c.get("target_words", 0) for c in outline["chapters"]
                                  if c["n"] == n), 0),
            "words": b.chapter_words(n),
            "markdown": b.chapter_md(n).read_text() if b.chapter_md(n).exists() else "",
            "drafts": b.drafts(n),
            "stale": b.narration_stale(n),
            # The segments are what an edit works on — the markdown is a view
            # rendered from them, so editing arrives as segments and no prose
            # is ever parsed back into a speaker.
            "segments": (b.chapter(n).get("segments", [])
                         if b.chapter_json(n).exists() else []),
            "approved": b.chapter_approved(n),
            "review_stale": textagent.review_stale(b, n),
            # Approved once, then rewritten. Worth distinguishing from never
            # approved: it means you already read this chapter and the words
            # have changed underneath that decision.
            "approval_lapsed": (str(n) in b.chapter_approvals()
                                and not b.chapter_approved(n)),
            "review": {
                "pass": review.get("pass"),
                "score": review.get("score"),
                "exhausted": review.get("exhausted", False),
                "fixes": review.get("fixes", []),
                "generic_prose": review.get("generic_prose", []),
                "continuity": review.get("continuity", []),
                "anachronisms": review.get("anachronisms", []),
                "listenability": review.get("listenability", []),
                "boundary_breaches": review.get("boundary_breaches", []),
                "length_note": review.get("length_note", ""),
                "writer_note": review.get("writer_note", ""),
            } if review else None,
            "redraft_estimate": (textagent.estimate_redraft(cfg, b, n)
                                 if b.chapter_json(n).exists() else None),
        })

    return {
        **row,
        "outline": outline,
        "chapter_detail": chapters,
        "story_md": b.story_md_path.read_text() if b.story_md_path.exists() else "",
        "impact": _impact(cfg, outline),
        "write_estimate": textagent.estimate_write(cfg, b),
        # Both, so the dialog can re-price the "rewrite everything" checkbox
        # without another round trip. Neither costs anything to compute.
        "write_estimate_restart": textagent.estimate_write(cfg, b, restart=True),
        "job": job_view(cfg, b.root.name),
        # What the zip would contain. The button is dark until there is
        # something in it, rather than handing over an empty archive.
        "assets": {
            "audio": sum(1 for n in b.chapter_numbers() if b.audio_path(n).exists()),
            "images": (len([p for p in (b.root / "images").rglob("*")
                            if p.is_file() and p.suffix in (".jpg", ".png")])
                       if (b.root / "images").is_dir() else 0),
        },
    }


def _impact(cfg: dict, outline: dict) -> dict:
    """What approving this would do to the odds for the next story.

    The weighting is otherwise invisible: it happens inside sampling and nobody
    sees it. Showing the before-and-after at the moment of approval is what turns
    it from a mechanism into a decision.
    """
    bible = outline.get("bible")
    before = history.scan(cfg["stories_dir"], bible=bible)
    rows = []
    for t in outline.get("tropes", []):
        key = str(t).lower()
        used = before.tropes.get(key, 0)
        rows.append({
            "trope": t,
            "used": used,
            "weight_now": round(before.weight(key), 2),
            "weight_after": round(1.0 / (2.0 + used) ** history.DECAY, 2),
        })
    return {
        "stories": before.stories,
        "tropes": rows,
        "leader": (before.protagonists.most_common(1)[0]
                   if before.protagonists else None),
    }


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #


def critique(cfg: dict, slug: str, chapters: list | None = None) -> dict:
    """Re-run the editor over written chapters. Costs money; rewrites nothing.

    The only route here that spends without changing a word of the story. It is
    what you want after a hand edit, and after the rules change — the editor has
    gained checks the chapters on disk were never judged against, and re-reading
    one costs an editor call where rewriting it costs writer plus editor.
    """
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    ns = ([int(n) for n in chapters] if chapters
          else [n for n in b.chapter_numbers() if b.chapter_json(n).exists()])
    missing = [n for n in ns if not b.chapter_json(n).exists()]
    if missing:
        raise ValueError(f"chapter(s) {missing} have not been written yet.")

    results = []
    for n in ns:
        before = {}
        if b.review_path(n).exists():
            try:
                before = json.loads(b.review_path(n).read_text())
            except json.JSONDecodeError:
                pass
        r = textagent.review_chapter(cfg, b, n)
        results.append({
            "n": n,
            "pass": bool(r.get("pass")),
            "score": r.get("score"),
            "was_pass": bool(before.get("pass")) if before else None,
            "findings": (len(r.get("continuity") or []) + len(r.get("fixes") or [])
                         + len(r.get("listenability") or [])
                         + len(r.get("generic_prose") or [])),
        })

    # A verdict that now fails takes the chapter out of `done`, so the queue and
    # the edit stage both have to be recomputed rather than left stale.
    textagent._sync_edit_stage(b)
    summary.write(b)
    return {"ok": True, "slug": slug, "results": results,
            "failed": [x["n"] for x in results if not x["pass"]]}


def save_chapter(cfg: dict, slug: str, n: int, segments: list) -> dict:
    """Write an edited chapter back. Free — no model is called."""
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    if not b.chapter_json(int(n)).exists():
        raise ValueError(f"chapter {n} has not been written yet.")
    chapter = textagent.save_chapter(b, int(n), segments or [])
    summary.write(b)
    n = int(n)
    return {
        "ok": True, "slug": slug, "n": n,
        "words": b.chapter_words(n),
        "segments": chapter["segments"],
        "markdown": b.chapter_md(n).read_text(),
        "dropped_empty_segments": chapter.get("dropped_empty_segments", 0),
        # Both lapse on their own, because both are stamped with the chapter
        # hash and the hash has just changed. Reported so the page can say so
        # rather than leaving it to be discovered.
        "approval_lapsed": (str(n) in b.chapter_approvals()
                            and not b.chapter_approved(n)),
        "review_stale": textagent.review_stale(b, n),
        "narration_stale": b.narration_stale(n),
    }


def approve(cfg: dict, slug: str, what: str, chapters: list | None = None,
            undo: bool = False) -> dict:
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    if what == "chapters" and chapters:
        # One chapter at a time, so a story can be read across several sittings
        # instead of all at once. Only the chapters named are touched.
        picked = [int(n) for n in chapters]
        missing = [n for n in picked if not b.chapter_json(n).exists()]
        if missing:
            raise ValueError(f"chapter(s) {missing} have not been written yet.")
        if undo:
            approved = textagent.unapprove_chapters(b, picked)
        else:
            approved = textagent.approve_chapters(b, picked)
        summary.write(b)
        written = [n for n in b.chapter_numbers() if b.chapter_json(n).exists()]
        return {"ok": True, "slug": slug, "what": "chapters",
                "approved": approved, "written": len(written),
                "all_approved": b.all_chapters_approved()}
    if what == "chapters":
        # Approving chapters unlocks `record`, the most expensive stage. Doing it
        # for a story whose chapters were never written would send the actor at
        # files that do not exist.
        missing = [n for n in b.chapter_numbers() if not b.chapter_json(n).exists()]
        if missing:
            raise ValueError(
                f"{slug} has no drafted chapters yet ({len(missing)} missing) — "
                f"run `write` before approving them."
            )
        textagent.approve_chapters(b)
        summary.write(b)
        return {"ok": True, "slug": slug, "what": what,
                "approved": b.approved_chapters(),
                "written": len([n for n in b.chapter_numbers()
                                if b.chapter_json(n).exists()]),
                "all_approved": b.all_chapters_approved()}
    textagent.approve(b)
    summary.write(b)
    return {"ok": True, "slug": slug, "what": what}


# What a revision can hold fixed. Order matters — it is the order the checkboxes
# appear in and the order the sentences are composed in.
KEEPABLE = [
    ("logline", "the logline"),
    ("promise", "the promise"),
    ("setting", "the setting"),
    ("tropes", "the tropes, exactly as named"),
    ("title", "the title"),
    ("cast", "the cast, including their names, ages and stations"),
    ("chapters", "the chapters and their beats"),
]


def compose_feedback(keep: list[str], note: str, spec: dict) -> str:
    """Turn the checkboxes into the note the ideator actually reads.

    `revise` already defaults to keeping everything not objected to, so the
    checkboxes are really about what to *replace* — saying both halves out loud
    is what stops "make it better" changing nothing.
    """
    kept = [label for key, label in KEEPABLE if key in keep]
    dropped = [(key, label) for key, label in KEEPABLE if key not in keep]

    parts = []
    if kept:
        parts.append("Keep these exactly as they are, word for word where they "
                     "are text: " + "; ".join(kept) + ".")
    if dropped:
        parts.append("Replace these wholesale rather than adjusting what is "
                     "there: " + "; ".join(label for _, label in dropped) + ".")

    keys = {k for k, _ in dropped}
    if "chapters" in keys:
        parts.append(
            f"The new chapters must carry {spec['beats_min']} to "
            f"{spec['beats_max']} concrete beats each — things that happen, not "
            f"moods. That density is what stops the prose stretching to fill the "
            f"words rather than the story getting bigger."
        )
    if "cast" in keys:
        parts.append(
            "Invent a genuinely new cast. Recurring characters from the series "
            "bible may still appear where the story needs them, copied verbatim "
            "as always, but the guest characters should be new people with new "
            "names — not the previous ones renamed."
        )
    if "cast" in keys and "chapters" not in keys:
        parts.append(
            "The chapters are being kept, so rewrite any beat that names a "
            "character who no longer exists, keeping the event the beat "
            "describes."
        )
    if note.strip():
        parts.append(note.strip())
    return "\n\n".join(parts)


def revise(cfg: dict, slug: str, keep: list[str], note: str) -> dict:
    """Regenerate the outline. **This spends money** — one ideator call."""
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    spec = textagent._length_of(b)
    keep = keep or []

    # Keeping everything still composes a "keep these" sentence, so the emptiness
    # test has to be on intent rather than on the string. A revision that asks
    # for nothing is a paid call that returns what you already had.
    replacing = [k for k, _ in KEEPABLE if k not in keep]
    if not replacing and not (note or "").strip():
        raise ValueError(
            "Nothing to revise — everything is being kept and there is no note. "
            "Uncheck what you want replaced, or say what to change."
        )

    feedback = compose_feedback(keep, note or "", spec)
    outline = textagent.revise_outline(cfg, b, feedback)
    summary.write(b)

    # Enough for the dialog to say what changed rather than just "done". Beat
    # density is the number a revision is usually chasing.
    chapters = outline.get("chapters", [])
    beats = sum(len(c.get("beats", [])) for c in chapters)
    words = sum(c.get("target_words", 0) for c in chapters)
    return {
        "ok": True,
        "slug": slug,
        "title": outline.get("title", ""),
        "chapters": len(chapters),
        "beats": beats,
        "per_beat": round(words / beats) if beats else 0,
        "revision": len(outline.get("revisions", [])),
        "feedback": feedback,
    }


# --------------------------------------------------------------------------- #
# Stages, which run as detached processes
# --------------------------------------------------------------------------- #
#
# Every slow action here — write, redraft, record, design — is several minutes
# and real money. They used to be threads inside this server, which meant a
# deploy took the run down with it, there was no way to stop one, and the only
# record of what happened lived in memory and vanished on restart. They are
# child processes now: see `runner.py`.
#
# The split is deliberate. The runner knows about processes, logs and pids and
# nothing else; everything below is the part that knows what a chapter is, what
# a run costs, and which gate each stage sits behind.

# Both record and design write their output one file at a time and skip what is
# already there, so progress is counted off disk rather than tracked. That is
# also what makes a resumed run report honestly: a counter would start at zero
# and look like it was redoing work it actually skips.
STAGE_KINDS = ("record", "design")

# Stages that stamp `running` into the manifest on entry and clear it only on
# success. A killed run leaves that behind, so it has to be released.
RELEASABLE = ("write", "record", "design")


def _scenes_per(cfg: dict, b: Bundle) -> int:
    return (b.pinned("scenes_per_chapter")
            or textagent._scenes_per_chapter(cfg, textagent._length_of(b)))


def _stage_counts(cfg: dict, slug: str, kind: str) -> dict:
    try:
        b = Bundle.open(Path(cfg["stories_dir"]) / slug)
        if kind == "record":
            ns = b.chapter_numbers()
            return {"done": sum(1 for n in ns if b.audio_path(n).exists()),
                    "total": len(ns)}
        cast = [m["name"] for m in b.outline().get("cast", [])]
        per = _scenes_per(cfg, b)
        done = sum(1 for name in cast if b.cast_image(name).exists())
        done += sum(1 for n in b.chapter_numbers() for i in range(per)
                    if b.scene_image(n, i).exists())
        return {"done": done, "total": len(cast) + len(b.chapter_numbers()) * per}
    except Exception:
        # Progress is decoration. A bundle that cannot be read is the running
        # job's problem to report, not this function's.
        return {}


def _release_stage(cfg: dict, slug: str, stage: str) -> None:
    """Put a stage that a killed run left `running` back to `pending`.

    Without this the story looks neither finished nor startable, which is the
    state a crash used to leave behind. Whatever completed stays on disk and the
    next run resumes from it — that is what makes cancelling cheap.
    """
    if stage not in RELEASABLE:
        return
    try:
        b = Bundle.open(Path(cfg["stories_dir"]) / slug)
        if b.stage_status(stage) == "running":
            b.set_stage(stage, "pending")
            summary.write(b)
    except Exception:
        pass


def recover(cfg: dict) -> list[dict]:
    """Settle jobs that outlived the server that started them.

    A deploy restarts this process while the children keep running; those are
    found again by pid and left alone. The ones that are genuinely gone are
    marked failed here and their stages released, so the queue never shows work
    that stopped hours ago.
    """
    settled = runner.reap_dead()
    for meta in settled:
        _release_stage(cfg, meta.get("slug", ""), meta.get("stage", ""))
    return settled


def _write_done(b: Bundle) -> dict:
    """What the page says after a write, read back off disk.

    The numbers used to be collected by the thread that did the writing. Read
    from the bundle instead, they are also correct for a run this server never
    saw — one started before a deploy, or finished after it.
    """
    words = b.word_count()
    spec = textagent.length_spec(b.manifest().get("pinned", {}).get("length_min"))
    unpassed = []
    for n in b.chapter_numbers():
        if not b.review_path(n).exists():
            continue
        try:
            if not json.loads(b.review_path(n).read_text()).get("pass"):
                unpassed.append(n)
        except json.JSONDecodeError:
            pass
    return {
        "words": words,
        # A story well over its target is worth saying while rewriting is still
        # cheap — after narration it is not.
        "minutes": round(words / textagent.WPM, 1),
        "target_minutes": spec["minutes"],
        "unpassed": unpassed,
        "chapters_written": len([n for n in b.chapter_numbers()
                                 if b.chapter_json(n).exists()]),
        "spend": _spend(b.manifest())["usd"],
    }


def _redraft_done(b: Bundle, n: int) -> dict:
    review = {}
    if b.review_path(n).exists():
        try:
            review = json.loads(b.review_path(n).read_text())
        except json.JSONDecodeError:
            review = {}
    target = next((c.get("target_words", 0) for c in b.outline()["chapters"]
                   if c["n"] == n), 0)
    return {
        "passed": bool(review.get("pass")),
        "score": review.get("score"),
        "exhausted": bool(review.get("exhausted")),
        "words": b.chapter_words(n),
        "target": target,
        # Written with the old version of this chapter as context, and not
        # rewritten. Named so you know what to reread.
        "later": [m for m in b.chapter_numbers()
                  if m > n and b.chapter_json(m).exists()],
        "approval_lapsed": (str(n) in b.chapter_approvals()
                            and not b.chapter_approved(n)),
        "narration_stale": b.narration_stale(n),
        "spend": _spend(b.manifest())["usd"],
    }


def job_view(cfg: dict, slug: str) -> dict:
    """The running (or last) job for a story, in the shape the page reads."""
    j = runner.status(slug)
    if j.get("state") == "none":
        return {"state": "none"}

    kind = j.get("kind")
    marks = j.pop("progress", []) or []
    try:
        b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    except Exception:
        return j

    if kind == "write":
        chapters = [{"n": m.get("n"), "pass": bool(m.get("ok")),
                     "score": m.get("score"),
                     "exhausted": bool(m.get("exhausted"))}
                    for m in marks if m.get("event") == "chapter"]
        total = next((m["total"] for m in reversed(marks) if m.get("total")),
                     len(b.outline().get("chapters", [])))
        j.update(chapters=chapters, done=len(chapters), total=total)
        if j["state"] == "done":
            j.update(_write_done(b))
        elif j["state"] in ("failed", "cancelled"):
            j["kept"] = len([n for n in b.chapter_numbers()
                             if b.chapter_json(n).exists()])
    elif kind == "redraft":
        j["passes"] = [{"attempt": m.get("attempt"), "pass": bool(m.get("ok")),
                        "score": m.get("score"), "words": m.get("words")}
                       for m in marks if m.get("event") == "pass"]
        j["max_passes"] = cfg.get("max_edit_passes", 2)
        if j["state"] == "done" and j.get("n"):
            j.update(_redraft_done(b, int(j["n"])))
    elif kind in STAGE_KINDS:
        j.update(_stage_counts(cfg, slug, kind))
        if j["state"] == "done":
            j["spend"] = _spend(b.manifest())["usd"]
    return j


def cancel_job(cfg: dict, slug: str) -> dict:
    """Stop the running stage.

    The whole process group goes, so the provider call and ffmpeg stop with it.
    Everything already written stays on disk and the stage is released, so
    starting again carries on rather than repeating what was paid for.
    """
    meta = runner.cancel(slug)
    _release_stage(cfg, slug, meta.get("stage", ""))
    return {"ok": True, "slug": slug, **job_view(cfg, slug)}


def job_log(cfg: dict, slug: str, offset: int = 0) -> dict:
    """The stage's own output, from `offset`. This is what the CLI printed."""
    return runner.log_chunk(slug, offset)


def estimates(cfg: dict, slug: str) -> dict:
    """What record and design would cost, before either is started.

    Free and credential-free by design — this is the figure the confirm dialogs
    put on screen, and it has to work on a box where the provider SDKs may not
    even be installed.
    """
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    out: dict = {"slug": slug}

    try:
        from ..agents import actor
        out["record"] = actor.estimate(cfg, b)
    except ImportError as e:
        out["record"] = {"unavailable": f"{e}"}

    cast = [m["name"] for m in b.outline().get("cast", [])]
    per = _scenes_per(cfg, b)
    todo = sum(1 for name in cast if not b.cast_image(name).exists())
    todo += sum(1 for n in b.chapter_numbers() for i in range(per)
                if not b.scene_image(n, i).exists())
    rate = cfg.get("gemini", {}).get("usd_per_image", 0.05)
    out["design"] = {"images": todo, "usd": round(todo * rate, 2),
                     "per_chapter": per}
    return out


def write_start(cfg: dict, slug: str, restart: bool = False) -> dict:
    """Kick off `write`. **This spends money** — the most of any action here.

    Resumes by default: chapters already through the write/edit loop are loaded
    from disk rather than paid for again. `restart` writes them all afresh.
    """
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    if b.stage_status("ideate") != "approved":
        raise ValueError(
            f"{slug} has not been approved yet — approve the outline first, "
            "which is the gate that stops the writer running against something "
            "you have not read."
        )
    runner.start(cfg, slug, "write", restart=restart)
    return {"ok": True, "slug": slug, **job_view(cfg, slug)}


def redraft_start(cfg: dict, slug: str, n, note: str) -> dict:
    """Send chapter `n` back to the writer with a note. **This spends money.**"""
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    n = int(n)
    note = (note or "").strip()
    if not note:
        raise ValueError("The note is empty — say what the writer should change.")
    if not b.chapter_json(n).exists():
        raise ValueError(f"chapter {n} has not been written yet.")
    runner.start(cfg, slug, "redraft", chapter=n, note=note)
    return {"ok": True, "slug": slug, **job_view(cfg, slug)}


def record_start(cfg: dict, slug: str, force: bool = False) -> dict:
    """Narrate the approved chapters. **This spends money** — the largest single
    cost in the pipeline, which is why the approval gate is checked here as well
    as in the CLI."""
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    if not force and not b.all_chapters_approved():
        written = [n for n in b.chapter_numbers() if b.chapter_json(n).exists()]
        left = [n for n in written if not b.chapter_approved(n)]
        raise ValueError(
            f"{len(written) - len(left)} of {len(written)} chapters are approved. "
            f"Not yet: {', '.join(str(n) for n in left)}. Approve them first — "
            "or, from a terminal, `cli record --force` to narrate them anyway."
        )
    runner.start(cfg, slug, "record", force=force)
    return {"ok": True, "slug": slug, **job_view(cfg, slug)}


def design_start(cfg: dict, slug: str, replan: bool = False) -> dict:
    """Draw the cast sheet and the scene images. **This spends money.**

    Shot timing comes from the narration timelines, so this cannot run before
    `record` — the agent raises on the first missing one, which would spend the
    cast sheet before finding out.
    """
    b = Bundle.open(Path(cfg["stories_dir"]) / slug)
    missing = [n for n in b.chapter_numbers() if not b.timeline_path(n).exists()]
    if missing:
        raise ValueError(
            f"chapters {missing} have no narration yet. Shot timings come from "
            "the audio timelines, so record before designing."
        )
    runner.start(cfg, slug, "design", replan=replan)
    return {"ok": True, "slug": slug, **job_view(cfg, slug)}


def package_files(cfg: dict, slug: str) -> tuple[Path, list[Path]]:
    """The bundle root and every asset file to put in the zip.

    Only what git ignores: the prose is already on the laptop through a pull, so
    shipping it again would mean two copies of the text and a merge question
    nobody asked for.
    """
    root = (Path(cfg["stories_dir"]) / slug).resolve()
    stories = Path(cfg["stories_dir"]).resolve()
    # Same guard as reject(): a slug arrives over HTTP and `../..` should not
    # read outside stories_dir.
    if root.parent != stories or not (root / "manifest.json").exists():
        raise ValueError(f"not a story bundle: {slug}")

    files = []
    for kind in ("audio", "images"):
        d = root / kind
        if d.is_dir():
            files += [p for p in sorted(d.rglob("*"))
                      if p.is_file() and p.name != ".DS_Store"]
    if not files:
        raise ValueError(
            f"{slug} has no audio or images yet — record and design first.")
    return root, files


def reject(cfg: dict, slug: str) -> dict:
    """Delete the bundle. Free to regenerate, and a rejected outline should leave
    no trace — the history weights deliberately count approvals only."""
    import shutil

    root = (Path(cfg["stories_dir"]) / slug).resolve()
    stories = Path(cfg["stories_dir"]).resolve()
    # Refuse anything that is not directly inside stories_dir. A slug arrives
    # over HTTP, and `../..` should delete nothing.
    if root.parent != stories or not (root / "manifest.json").exists():
        raise ValueError(f"not a story bundle: {slug}")
    shutil.rmtree(root)
    return {"ok": True, "slug": slug}


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #


def _handler(cfg: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet; this is a UI, not a web server
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def _zip(self, cfg: dict, slug: str) -> None:
            """Stream the assets as a zip, rather than building one in memory.

            A 30-minute story is about 150 MB of audio and images and the server
            has 412 MiB of RAM, so this writes straight to the socket. The files
            are mp3 and jpg — already compressed — so it stores rather than
            deflates: same size, none of the CPU.
            """
            try:
                root, files = package_files(cfg, slug)
            except ValueError as e:
                self._json({"error": str(e)}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{slug}-assets.zip"')
            # No Content-Length is known up front, so the client learns the body
            # ended when the connection does.
            self.send_header("Connection", "close")
            self.end_headers()
            with zipfile.ZipFile(self.wfile, "w", zipfile.ZIP_STORED) as z:
                for p in files:
                    # Arcnames start with the slug, so `unzip -d stories` puts
                    # every file back exactly where it came from.
                    z.write(p, str(p.relative_to(root.parent)))

        def _enter(self):
            global _inflight_count
            with _inflight_lock:
                _inflight_count += 1

        def _leave(self):
            global _inflight_count
            with _inflight_lock:
                _inflight_count -= 1

        def do_GET(self):
            path = urlparse(self.path).path
            self._enter()
            try:
                if path in ("/", "/index.html"):
                    self._send(200, APP_HTML.read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/queue":
                    self._json(queue(cfg))
                elif path.startswith("/api/story/"):
                    self._json(story_detail(cfg, path.rsplit("/", 1)[-1]))
                elif path.startswith("/api/job/"):
                    self._json(job_view(cfg, path.rsplit("/", 1)[-1]))
                elif path.startswith("/api/log/"):
                    q = parse_qs(urlparse(self.path).query)
                    self._json(job_log(cfg, path.rsplit("/", 1)[-1],
                                       int((q.get("from") or ["0"])[0])))
                elif path.startswith("/api/estimate/"):
                    self._json(estimates(cfg, path.rsplit("/", 1)[-1]))
                elif path.startswith("/api/package/"):
                    self._zip(cfg, path.rsplit("/", 1)[-1])
                elif path == "/api/keepable":
                    self._json([{"key": k, "label": l} for k, l in KEEPABLE])
                elif path == "/manifest.webmanifest":
                    self._send(200, json.dumps(MANIFEST, indent=2).encode(),
                               "application/manifest+json")
                elif path.lstrip("/") in ICONS:
                    name = path.lstrip("/")
                    self._send(200, (APP_HTML.parent / name).read_bytes(),
                               ICONS[name])
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # a UI should show the error, not die
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            finally:
                self._leave()

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            self._enter()
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if path == "/api/approve":
                    self._json(approve(cfg, body["slug"], body.get("what", "outline"),
                                       chapters=body.get("chapters"),
                                       undo=bool(body.get("undo"))))
                elif path == "/api/revise":
                    self._json(revise(cfg, body["slug"], body.get("keep", []),
                                      body.get("note", "")))
                elif path == "/api/write":
                    self._json(write_start(cfg, body["slug"],
                                           restart=bool(body.get("restart"))))
                elif path == "/api/critique":
                    self._json(critique(cfg, body["slug"], body.get("chapters")))
                elif path == "/api/chapter":
                    self._json(save_chapter(cfg, body["slug"], body["n"],
                                            body.get("segments")))
                elif path == "/api/redraft":
                    self._json(redraft_start(cfg, body["slug"], body["n"],
                                             body.get("note", "")))
                elif path == "/api/record":
                    self._json(record_start(cfg, body["slug"],
                                            force=bool(body.get("force"))))
                elif path == "/api/design":
                    self._json(design_start(cfg, body["slug"],
                                            replan=bool(body.get("replan"))))
                elif path == "/api/cancel":
                    self._json(cancel_job(cfg, body["slug"]))
                elif path == "/api/reject":
                    self._json(reject(cfg, body["slug"]))
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            finally:
                self._leave()

    return Handler


# In-flight requests, so a reload cannot land in the middle of one. A revise is
# a paid call; restarting through it would spend the money and discard the reply.
_inflight_count = 0
_inflight_lock = threading.Lock()


def _watch_and_reload(interval: float = 1.0) -> None:
    """Re-exec when any module under story_pipeline/ changes.

    `app.html` is read from disk on every request, so the page is always current.
    The Python is not — it is loaded once at import — which makes "I edited the
    server and nothing changed" a trap worth removing rather than documenting.

    Waits for in-flight requests to finish first.
    """
    pkg = Path(__file__).resolve().parent.parent

    def snapshot():
        return {p: p.stat().st_mtime
                for p in pkg.rglob("*.py") if "__pycache__" not in str(p)}

    seen = snapshot()
    while True:
        time.sleep(interval)
        try:
            now = snapshot()
        except OSError:
            continue
        changed = [p.name for p in now if now[p] != seen.get(p)]
        changed += [p.name for p in seen if p not in now]
        if not changed:
            continue
        while True:
            with _inflight_lock:
                if _inflight_count == 0:
                    break
            print("  reload: waiting for a request to finish…")
            time.sleep(0.5)
        print(f"\n  reload: {', '.join(sorted(set(changed))[:4])} changed — restarting")
        os.execv(sys.executable, [sys.executable, "-m", "story_pipeline.cli", *sys.argv[1:]])


def serve(cfg: dict, port: int = 8765, open_browser: bool = True,
          reload: bool = False) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(cfg))
    url = f"http://127.0.0.1:{port}/"

    # Stages outlive this process, so some of them will have finished — or died
    # — while it was not running. Settle those before showing anyone a queue.
    dead = recover(cfg)
    if dead:
        print(f"{len(dead)} job(s) did not survive the last restart: "
              + ", ".join(f"{m['slug']} ({m['stage']})" for m in dead))

    q = queue(cfg)
    waiting = len(q["groups"]["outlines"]) + len(q["groups"]["chapters"]) \
        + len(q["groups"]["failed"]) + len(q["groups"]["stale"])
    print(f"Review UI on {url}")
    print(f"{waiting} waiting on you, {q['total']} stories in total.")
    if reload:
        print("Watching story_pipeline/ — the server restarts when the code changes.")
        threading.Thread(target=_watch_and_reload, daemon=True).start()
    else:
        # The distinction that cost an afternoon: the page is re-read per
        # request, the Python is not.
        print("app.html is re-read per request; Python changes need a restart "
              "(or --reload).")
    print("Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
