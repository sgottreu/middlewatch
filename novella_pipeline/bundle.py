"""The novella bundle: one directory per book, one manifest tracking chapter stages.

    novellas/<series>/<slug>/
        manifest.json               pipeline, slug, pinned labels, chapters + stages, spend
        brief.md  outline.md        written by Scott (outline.md also by the outliner)
        outlines/VV.md  VV.json     kept outlines: each outliner run, and Scott's
                                    outline.md as it stood before a run replaced it
        chapters/NN/beats.md        what the chapter has to do
        chapters/NN/summary.md      the approved summary (fed to later chapters)
        chapters/NN/details.md      new details approved into the bible's changelog
        drafts/NN.V.md              the writer's draft, version V (never edited)
        drafts/NN.V.writer.json     its summary, new details, flags, model, note
        drafts/NN.V.edit.md         Scott's edit of version V (the working text)
        drafts/NN.V.review.json     the editor's review of that edit, plus lint findings

Versioning is numbered files, as story bundles do, not git: the server never
commits. A redraft is a new V, so sending a chapter back and redrafting it
keeps the old draft and the old edit on disk.

Same shape as story_pipeline.bundle.Bundle where the shared code needs it:
`add_spend` and `ledger_story` are what spend.record_llm calls.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .workflow import (Stage, TransitionError, can_approve, can_draft, can_edit,
                       can_review, send_back, stage_after_edit)

PIPELINE = "novella"
DEFAULT_CHAPTER_WORDS = 3000
DEFAULT_BOOK_WORDS = 30000
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


class NovellaError(ValueError):
    pass


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60].strip("-")
    if not s:
        raise NovellaError("Name must contain letters or numbers.")
    return s


def words(text: str) -> int:
    return len((text or "").split())


def bundle_roots(novellas_dir: str | Path) -> list[Path]:
    return sorted(p.parent for p in Path(novellas_dir).glob("*/*/manifest.json"))


def find_bundle(novellas_dir: str | Path, slug: str) -> Path:
    """The bundle for a slug, whichever series it is in. Slugs are unique across
    series (create enforces it). The pattern check matters: a slug arrives over
    HTTP, and `../..` must resolve to nothing."""
    if not _SLUG.fullmatch(slug or ""):
        raise NovellaError(f"not a novella slug: {slug!r}")
    base = Path(novellas_dir).resolve()
    hits = [p.parent for p in Path(novellas_dir).glob(f"*/{slug}/manifest.json")]
    hits = [h for h in hits if base in h.resolve().parents]
    if not hits:
        raise FileNotFoundError(f"no novella called {slug} under {novellas_dir}/<series>/")
    if len(hits) > 1:
        raise NovellaError(f"{slug} exists in several series: "
                           f"{', '.join(sorted(p.parent.name for p in hits))}")
    return hits[0]


def resolve(novellas_dir: str | Path, ref: str) -> Path:
    """A bundle from a path (the CLI) or a bare slug (the page)."""
    p = Path(ref)
    if (p / "manifest.json").exists():
        return p
    return find_bundle(novellas_dir, ref)


@dataclass
class Bundle:
    root: Path

    SUBDIRS = ("chapters", "drafts")

    # --- paths -------------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def brief_path(self) -> Path:
        return self.root / "brief.md"

    @property
    def outline_path(self) -> Path:
        return self.root / "outline.md"

    def chapter_dir(self, n: int) -> Path:
        return self.root / "chapters" / f"{int(n):02d}"

    def beats_path(self, n: int) -> Path:
        return self.chapter_dir(n) / "beats.md"

    def summary_path(self, n: int) -> Path:
        return self.chapter_dir(n) / "summary.md"

    def details_path(self, n: int) -> Path:
        return self.chapter_dir(n) / "details.md"

    def draft_path(self, n: int, v: int) -> Path:
        return self.root / "drafts" / f"{int(n):02d}.{int(v)}.md"

    def writer_path(self, n: int, v: int) -> Path:
        return self.root / "drafts" / f"{int(n):02d}.{int(v)}.writer.json"

    def edit_path(self, n: int, v: int) -> Path:
        return self.root / "drafts" / f"{int(n):02d}.{int(v)}.edit.md"

    def review_path(self, n: int, v: int) -> Path:
        return self.root / "drafts" / f"{int(n):02d}.{int(v)}.review.json"

    # --- lifecycle ---------------------------------------------------------

    @classmethod
    def create(cls, novellas_dir: str | Path, title: str, series: str,
               genre: str = "", bible: str = "", slug: str | None = None,
               target_words: int = DEFAULT_BOOK_WORDS) -> "Bundle":
        if not (title or "").strip():
            raise NovellaError("A novella needs a title.")
        if not (series or "").strip():
            raise NovellaError("A novella needs a series (or a bible that names one).")
        slug = slug or slugify(title)
        if not _SLUG.fullmatch(slug):
            raise NovellaError(f"not a novella slug: {slug!r}")
        series_dir = slugify(series)
        root = Path(novellas_dir) / series_dir / slug
        taken = list(Path(novellas_dir).glob(f"*/{slug}/manifest.json"))
        if taken:
            raise NovellaError(f"{slug} already exists at {taken[0].parent}; slugs "
                               "must be unique across series")
        b = cls(root)
        b._ensure_dirs()
        b.write_manifest({
            # The ledger requires it and will not guess.
            "pipeline": PIPELINE,
            "slug": slug,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            # Pinned at creation so a ledger row says what the book was then.
            "pinned": {"title": title.strip(), "series": series.strip(),
                       "series_dir": series_dir, "genre": genre or "",
                       "bible": bible or "", "target_words": int(target_words)},
            "chapters": [],
            "spend": {},
        })
        b.brief_path.write_text(
            f"# {title}: Story Brief\n\n## Premise\n\n## Main Characters\n\n## Setting\n\n"
            "## Tone\n\n## Point of View and Tense\n\n## Intended Ending\n")
        b.outline_path.write_text(f"# {title}: Outline\n")
        return b

    @classmethod
    def open(cls, path: str | Path) -> "Bundle":
        b = cls(Path(path))
        if not b.manifest_path.exists():
            raise FileNotFoundError(f"no manifest at {b.manifest_path}")
        m = b.manifest()
        if m.get("pipeline") != PIPELINE:
            raise NovellaError(f"{path} is not a novella bundle")
        b._ensure_dirs()
        return b

    def _ensure_dirs(self) -> None:
        for sub in self.SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # --- manifest ----------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text())

    def write_manifest(self, data: dict[str, Any]) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(self.manifest_path)

    @property
    def slug(self) -> str:
        return self.manifest()["slug"]

    def pinned(self, key: str, default: Any = None) -> Any:
        return self.manifest().get("pinned", {}).get(key, default)

    @property
    def title(self) -> str:
        return self.pinned("title", self.root.name)

    def add_spend(self, key: str, amount: float) -> None:
        m = self.manifest()
        m.setdefault("spend", {})
        m["spend"][key] = round(m["spend"].get(key, 0.0) + amount, 4)
        self.write_manifest(m)

    def ledger_story(self) -> dict[str, Any]:
        """Ledger dimensions, read off the manifest (see story Bundle.ledger_story)."""
        m = self.manifest()
        p = m.get("pinned", {})
        return {
            "pipeline": m.get("pipeline", ""),
            "slug": m.get("slug", ""),
            "title": p.get("title", ""),
            "series": p.get("series", ""),
            "genre": p.get("genre", ""),
            "flavor": "",
            "arc_slug": p.get("series_dir", ""),
            "volume": "",
            "episode": "",
        }

    # --- outline versions ----------------------------------------------------

    @property
    def outlines_dir(self) -> Path:
        return self.root / "outlines"

    def outline_version_path(self, v: int, ext: str = "md") -> Path:
        return self.outlines_dir / f"{int(v):02d}.{ext}"

    def outline_versions(self) -> list[dict[str, Any]]:
        """Every kept outline, oldest first: {version, source, created, note,
        chapters, words}. `source` is "outliner" (a generated outline) or
        "edited" (outline.md as it stood before the outliner replaced it)."""
        out = []
        if not self.outlines_dir.exists():
            return out
        for p in sorted(self.outlines_dir.glob("[0-9][0-9].md")):
            v = int(p.stem)
            meta = _json(self.outline_version_path(v, "json"))
            out.append({"version": v, "source": meta.get("source", "edited"),
                        "created": meta.get("created", ""), "note": meta.get("note", ""),
                        "model": meta.get("model", ""),
                        "chapters": len((meta.get("outline") or {}).get("chapters") or []),
                        "words": words(_read(p))})
        return out

    def outline_version(self, v: int) -> str:
        return _read(self.outline_version_path(v))

    def save_outline(self, text: str, meta: dict[str, Any]) -> int:
        """Replace outline.md with a generated outline, keeping what it replaces.

        If outline.md holds anything the kept versions do not (Scott's edits, or
        the hand-written outline from before the outliner ran), it is saved
        first as its own version, so nothing he wrote is lost. Returns the new
        outline's version number."""
        self.outlines_dir.mkdir(parents=True, exist_ok=True)
        kept = self.outline_versions()
        last = kept[-1]["version"] if kept else 0
        current = _read(self.outline_path)
        body = "\n".join(l for l in current.splitlines() if not l.startswith("# ")).strip()
        if body and (not last or current.strip() != self.outline_version(last).strip()):
            last += 1
            self.outline_version_path(last).write_text(current)
            self.outline_version_path(last, "json").write_text(json.dumps(
                {"source": "edited", "created": time.strftime("%Y-%m-%dT%H:%M:%S")},
                indent=2) + "\n")
        v = last + 1
        self.outline_version_path(v).write_text(_text(text))
        self.outline_version_path(v, "json").write_text(json.dumps(
            {"source": "outliner", "created": time.strftime("%Y-%m-%dT%H:%M:%S"), **meta},
            indent=2) + "\n")
        self.outline_path.write_text(_text(text))
        m = self.manifest()
        m["outline_version"] = v
        self.write_manifest(m)
        return v

    # --- chapters ----------------------------------------------------------

    def chapters(self) -> list[dict[str, Any]]:
        return sorted(self.manifest().get("chapters", []), key=lambda c: c["n"])

    def chapter(self, n: int) -> dict[str, Any]:
        for c in self.chapters():
            if c["n"] == int(n):
                return c
        raise NovellaError(f"No chapter {n} in {self.title}.")

    def stage(self, n: int) -> Stage:
        return Stage(self.chapter(n).get("stage", Stage.OUTLINED.value))

    def _set_stage(self, n: int, stage: Stage) -> None:
        m = self.manifest()
        for c in m["chapters"]:
            if c["n"] == int(n):
                c["stage"] = stage.value
        self.write_manifest(m)

    def save_plan(self, n: int, title: str = "", target_words: int | None = None,
                  beats: str | None = None) -> dict[str, Any]:
        """Add a chapter, or update its title, target length and beats."""
        n = int(n)
        if n < 1 or n > 99:
            raise NovellaError("Chapter numbers run from 1 to 99.")
        m = self.manifest()
        ch = next((c for c in m["chapters"] if c["n"] == n), None)
        if ch is None:
            ch = {"n": n, "title": title or "",
                  "target_words": int(target_words or DEFAULT_CHAPTER_WORDS),
                  "stage": Stage.OUTLINED.value}
            m["chapters"].append(ch)
            m["chapters"].sort(key=lambda c: c["n"])
        elif ch.get("stage") == Stage.APPROVED.value:
            raise NovellaError("Chapter is approved and locked. Send it back first.")
        else:
            if title:
                ch["title"] = title
            if target_words:
                ch["target_words"] = int(target_words)
        if beats is not None:
            self.chapter_dir(n).mkdir(parents=True, exist_ok=True)
            self.beats_path(n).write_text(_text(beats))
        self.write_manifest(m)
        return ch

    def beats(self, n: int) -> str:
        return _read(self.beats_path(n))

    def versions(self, n: int) -> list[int]:
        out = []
        for p in (self.root / "drafts").glob(f"{int(n):02d}.*.md"):
            parts = p.name.split(".")
            if len(parts) == 3 and parts[1].isdigit():
                out.append(int(parts[1]))
        return sorted(out)

    def current(self, n: int) -> int | None:
        v = self.versions(n)
        return v[-1] if v else None

    def draft(self, n: int, v: int | None = None) -> str:
        v = self.current(n) if v is None else v
        return _read(self.draft_path(n, v)) if v else ""

    def writer_meta(self, n: int, v: int | None = None) -> dict[str, Any]:
        v = self.current(n) if v is None else v
        return _json(self.writer_path(n, v)) if v else {}

    def edited(self, n: int, v: int | None = None) -> str:
        """Scott's working text: his edit if he has saved one, else the draft."""
        v = self.current(n) if v is None else v
        if not v:
            return ""
        p = self.edit_path(n, v)
        return _read(p) if p.exists() else self.draft(n, v)

    def review(self, n: int, v: int | None = None) -> dict[str, Any]:
        v = self.current(n) if v is None else v
        return _json(self.review_path(n, v)) if v else {}

    def summary(self, n: int) -> str:
        """The approved summary, else the writer's summary of the latest draft —
        what kindle's summary.md held at each point."""
        approved = _read(self.summary_path(n)).strip()
        return approved or (self.writer_meta(n).get("summary") or "").strip()

    def earlier_summaries(self, n: int) -> list[tuple[dict, str]]:
        out = []
        for c in self.chapters():
            if c["n"] < int(n):
                s = self.summary(c["n"])
                if s:
                    out.append((c, s))
        return out

    # --- transitions -------------------------------------------------------

    def check_draft(self, n: int) -> None:
        st = self.stage(n)
        if not can_draft(st):
            raise TransitionError(
                f"Chapter {n} is {st.label}. Send it back to Outlined to redraft; "
                "your edits stay on disk as the previous version.")
        if not self.beats(n).strip():
            raise NovellaError(f"Add beats to chapter {n} before drafting.")

    def save_draft(self, n: int, *, text: str, summary: str, new_details: str,
                   flags: str, parsed: bool = True, model: str = "",
                   note: str = "") -> int:
        """Store the writer's output as the next version. Checked again here,
        since the reply took minutes and the page may have moved on."""
        self.check_draft(n)
        v = (self.current(n) or 0) + 1
        self.draft_path(n, v).write_text(_text(text))
        self.writer_path(n, v).write_text(json.dumps({
            "summary": summary, "new_details": new_details, "flags": flags,
            "parsed": parsed, "model": model, "note": note,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2) + "\n")
        self._set_stage(n, Stage.DRAFTED)
        return v

    def save_edit(self, n: int, text: str) -> Stage:
        st = self.stage(n)
        new = stage_after_edit(st)
        v = self.current(n)
        if not v:
            raise TransitionError(f"Chapter {n} has no draft to edit.")
        self.edit_path(n, v).write_text(_text(text))
        if new != st:
            self._set_stage(n, new)
        return new

    def check_review(self, n: int) -> None:
        st = self.stage(n)
        if not can_review(st):
            raise TransitionError(
                f"Chapter {n} is {st.label}. Save your edits first; the editor "
                "reviews the author-edited text.")

    def save_review(self, n: int, raw: str, lint_summary: dict, model: str = "",
                    reviewed_text: str = "") -> None:
        self.check_review(n)
        from .parsing import parse_review
        v = self.current(n)
        self.review_path(n, v).write_text(json.dumps({
            "sections": parse_review(raw), "raw": raw, "lint": lint_summary,
            "model": model, "reviewed_words": words(reviewed_text),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2) + "\n")
        self._set_stage(n, Stage.EDITOR_REVIEWED)

    def check_approve(self, n: int, summary: str) -> None:
        st = self.stage(n)
        if not can_approve(st):
            raise TransitionError(f"Chapter {n} must be editor reviewed before approval.")
        if not (summary or "").strip():
            raise NovellaError("Add a summary before approving.")

    def approve(self, n: int, summary: str, details: list[str] | None = None) -> None:
        """Lock the chapter and save its summary and approved new details.

        Bundle files only. The details also go to the bible's changelog, which
        the caller (stages.approve) does after validating that the bible exists.
        """
        self.check_approve(n, summary)
        self.chapter_dir(n).mkdir(parents=True, exist_ok=True)
        self.summary_path(n).write_text(_text(summary))
        if details:
            self.details_path(n).write_text(
                "\n".join(f"- {d}" for d in details) + "\n")
        self._set_stage(n, Stage.APPROVED)

    def send_back(self, n: int, target: str | Stage) -> Stage:
        try:
            tgt = Stage(target)
        except ValueError:
            raise NovellaError(f"unknown stage {target!r}; one of "
                               f"{', '.join(s.value for s in Stage)}") from None
        new = send_back(self.stage(n), tgt)
        self._set_stage(n, new)
        return new

    def can(self, n: int) -> dict[str, bool]:
        st = self.stage(n)
        return {"draft": can_draft(st) and bool(self.beats(n).strip()),
                "edit": can_edit(st), "review": can_review(st),
                "approve": can_approve(st), "plan": st != Stage.APPROVED}

    def word_count(self) -> int:
        return sum(words(self.edited(c["n"])) for c in self.chapters())


def _read(p: Path) -> str:
    return p.read_text() if p.exists() else ""


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _text(t: str) -> str:
    t = (t or "").replace("\r\n", "\n")
    return t.rstrip() + "\n" if t.strip() else ""
