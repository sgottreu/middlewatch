""""Expand to novella": seed a new novella bundle from a finished story bundle.

Free: no model is called. The story's outline.json becomes the novella's
brief.md (title, logline, promise, setting, genre and subgenre, tropes, cast
with roles, and every chapter's beats as source material), the story's bible
is pinned, and the manifest records `source_story`. The outliner is then run
on the new book separately (`cli outline`), which plans 10-16 chapters from
that brief.

The source story is only read. Its files are opened directly rather than
through story_pipeline.bundle.Bundle.open, which creates missing folders.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from story_pipeline import bibles

from .bundle import DEFAULT_BOOK_WORDS, Bundle, NovellaError


def read_story(path: str | Path) -> tuple[dict, dict]:
    """(manifest, outline) of a story bundle, refusing anything else."""
    root = Path(path)
    mp, op = root / "manifest.json", root / "outline.json"
    if not mp.exists():
        raise FileNotFoundError(f"no story bundle at {root} (no manifest.json)")
    manifest = json.loads(mp.read_text())
    if manifest.get("pipeline", "story") != "story":
        raise NovellaError(f"{root} is a {manifest.get('pipeline')} bundle, not a story")
    if not op.exists():
        raise NovellaError(f"{root} has no outline.json yet; ideate the story first")
    return manifest, json.loads(op.read_text())


def _cast_line(c: dict) -> str:
    bits = [str(c.get("role") or "").strip()]
    if c.get("age"):
        bits.append(f"age {c['age']}")
    if c.get("station"):
        bits.append(str(c["station"]).strip())
    head = f"- **{c.get('name', '?')}** ({', '.join(b for b in bits if b)})"
    if c.get("recurring"):
        head += " — recurring (series bible)"
    lines = [head]
    if c.get("want"):
        lines.append(f"  - Wants: {c['want']}")
    if c.get("voice_note"):
        lines.append(f"  - Voice: {c['voice_note']}")
    return "\n".join(lines)


def brief_md(title: str, story_ref: str, manifest: dict, o: dict) -> str:
    p = manifest.get("pinned", {})
    genre = o.get("genre") or p.get("genre") or ""
    sub = o.get("subgenre") or p.get("flavor") or ""
    out = [f"# {title}: Story Brief", "",
           f"> Expanded from the story *{o.get('title', '')}* (`{story_ref}`), "
           f"a {o.get('length_min') or p.get('length_min') or '?'}-minute narrated "
           "story. Its beats are below as source material: the novella keeps its "
           "premise and cast and has room to deepen them.", "",
           "## Premise", "", str(o.get("logline") or "").strip(), ""]
    if o.get("promise"):
        out += ["**Promise to the reader:** " + str(o["promise"]).strip(), ""]
    out += ["## Genre", "", f"{genre}" + (f" / {sub}" if sub else ""), ""]
    if o.get("tropes"):
        out += ["Tropes:", ""] + [f"- {t}" for t in o["tropes"]] + [""]
    out += ["## Main Characters", ""]
    out += [_cast_line(c) for c in o.get("cast", [])] + [""]
    out += ["## Setting", "", str(o.get("setting") or "").strip(), "",
            "## Tone", "", "",
            "## Point of View and Tense", "", "",
            "## Intended Ending", "",
            "As the story ends (its last chapter's beats below), unless changed here.", "",
            "## Source Story Beats", ""]
    for ch in o.get("chapters", []):
        out += [f"### {ch.get('n', '?')}. {ch.get('title', '')}", ""]
        out += [f"- {b}" for b in ch.get("beats", [])] + [""]
    return "\n".join(out)


def expand(cfg: dict, story_path: str | Path, series: str | None = None,
           title: str | None = None, slug: str | None = None,
           target_words: int = DEFAULT_BOOK_WORDS) -> Bundle:
    root = Path(story_path)
    manifest, o = read_story(root)
    p = manifest.get("pinned", {})
    bible_name = o.get("bible") or p.get("bible") or ""
    bible = bibles.load(bible_name, cfg.get("bibles_dir") or "bibles") if bible_name else None
    title = (title or o.get("title") or p.get("title") or "").strip()
    series = (series or (bible.series if bible else "") or o.get("series")
              or p.get("series") or root.parent.name)
    genre = o.get("genre") or p.get("genre") or (bible.genre if bible else "")
    b = Bundle.create(cfg.get("novellas_dir") or "novellas", title, series,
                      genre=genre, bible=bible_name, slug=slug,
                      target_words=target_words)
    ref = f"{root.parent.name}/{root.name}"
    b.brief_path.write_text(brief_md(title, ref, manifest, o))
    m = b.manifest()
    m["source_story"] = {"path": str(root), "arc": root.parent.name,
                         "slug": manifest.get("slug") or root.name,
                         "title": o.get("title") or p.get("title") or "",
                         "length_min": o.get("length_min") or p.get("length_min")}
    b.write_manifest(m)
    return b


def stories(cfg: dict) -> list[dict[str, Any]]:
    """Story bundles that can be expanded (those with an outline), for the page."""
    from story_pipeline.bundle import bundle_roots
    out = []
    for root in bundle_roots(cfg.get("stories_dir") or "stories"):
        try:
            manifest, o = read_story(root)
        except (ValueError, OSError):
            continue
        out.append({"ref": f"{root.parent.name}/{root.name}",
                    "title": o.get("title") or root.name,
                    "bible": o.get("bible") or manifest.get("pinned", {}).get("bible") or "",
                    "genre": o.get("genre") or "",
                    "chapters": len(o.get("chapters", []))})
    return out


def story_path(cfg: dict, ref: str) -> Path:
    """The bundle for an `arc/slug` from the page, only if it is a listed story."""
    for s in stories(cfg):
        if s["ref"] == ref:
            arc, slug = ref.split("/", 1)
            return Path(cfg.get("stories_dir") or "stories") / arc / slug
    raise FileNotFoundError(f"no story {ref!r} with an outline")
