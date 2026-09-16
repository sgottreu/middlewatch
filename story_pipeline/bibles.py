"""Series bibles.

A genre says what Regency stories sound like. A bible says what *this series* is:
the same parish, the same recurring busybody, the fact established three stories
ago that the living at Combe is worth two hundred a year.

Bibles are yours, not shipped with the package, so they live in `bibles/` at the
repo root rather than under `story_pipeline/prompts/`. Same format as a genre file —
YAML frontmatter for the structured parts, prose below for what the agents read.

The recurring cast is the part that earns its keep. A character listed here keeps
their voice and their face across every story in the series, because both are
pinned in one place instead of being re-decided per story.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .bundle import ear_key


@dataclass
class Bible:
    name: str
    meta: dict[str, Any]
    guide: str
    path: Path

    @property
    def series(self) -> str:
        return self.meta.get("series", self.name)

    @property
    def genre(self) -> str:
        if "genre" not in self.meta:
            raise ValueError(f"bible {self.name!r} does not declare a genre")
        return self.meta["genre"]

    @property
    def recurring_cast(self) -> list[dict]:
        return self.meta.get("recurring_cast", [])

    @property
    def canon(self) -> list[str]:
        return self.meta.get("canon", [])

    @property
    def cast_dir(self) -> Path:
        """Where this series' shared character portraits live."""
        return self.path.parent / f"{self.name}.cast"

    def member(self, name: str) -> dict | None:
        for c in self.recurring_cast:
            if c["name"].lower() == name.lower():
                return c
        return None

    def portrait(self, name: str) -> Path | None:
        member = self.member(name)
        if not member:
            return None
        named = member.get("portrait")
        if named:
            path = self.cast_dir / named
            return path if path.exists() else None
        # No explicit filename: whichever format the portrait was generated in.
        for ext in ("jpg", "png"):
            path = self.cast_dir / f"{_slug(name)}.{ext}"
            if path.exists():
                return path
        return None

    def save_portrait(self, name: str, image: Path) -> Path | None:
        """Promote a freshly generated portrait into the series, so story two
        reuses the face rather than inventing a new one.

        Returns the path only when a file was actually written, so the caller can
        record the promotion in the changelog without logging a no-op on every
        later story that reuses the same face.
        """
        if not self.member(name):
            return None
        self.cast_dir.mkdir(parents=True, exist_ok=True)
        target = self.cast_dir / (self.member(name).get("portrait")
                                  or f"{_slug(name)}{image.suffix or '.jpg'}")
        if self.portrait(name):
            return None
        shutil.copy(image, target)
        return target

    def prompt_block(self) -> str:
        """The bible as the agents see it."""
        parts = [f"# Series bible: {self.series}", "", self.guide.strip()]

        if self.canon:
            parts += ["", "## Established facts", "",
                      "These are true in every story in this series. Do not "
                      "contradict them, and do not re-explain them at length — "
                      "a viewer may be watching this one first, so introduce what "
                      "the story needs and no more.", ""]
            parts += [f"- {c}" for c in self.canon]

        if self.recurring_cast:
            parts += ["", "## Recurring cast", "",
                      "These people exist in this series already. Use the ones "
                      "this story needs and leave the rest out — a story that "
                      "parades the whole cast has no room for its own. Their "
                      "names, ages, stations and appearances are fixed.", ""]
            for c in self.recurring_cast:
                bits = [f"**{c['name']}**"]
                if c.get("age"):
                    bits.append(f"age {c['age']}")
                if c.get("station"):
                    bits.append(c["station"])
                parts.append("- " + ", ".join(bits))
                if c.get("note"):
                    parts.append(f"  - {c['note']}")
        return "\n".join(parts)


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")


def _parse(path: Path) -> Bible:
    raw = path.read_text()
    if not raw.startswith("---"):
        raise ValueError(f"{path} has no frontmatter block")
    _, front, body = raw.split("---", 2)
    return Bible(name=path.stem, meta=yaml.safe_load(front) or {},
                 guide=body.strip(), path=path)


def load(name: str, bibles_dir: str | Path = "bibles") -> Bible:
    path = Path(bibles_dir) / f"{name}.md"
    if not path.exists():
        found = available(bibles_dir)
        raise ValueError(
            f"no bible {name!r} in {bibles_dir}/"
            + (f"; available: {', '.join(found)}" if found else "; none found")
        )
    return _parse(path)


def available(bibles_dir: str | Path = "bibles") -> list[str]:
    d = Path(bibles_dir)
    return sorted(p.stem for p in d.glob("*.md")) if d.exists() else []


TEMPLATE = '''---
series: "{series}"
genre: {genre}

# Facts true in every story. Keep these to things that constrain plotting —
# geography, money, who is related to whom, what happened that everyone knows.
# Not atmosphere; that belongs in the prose below.
canon:
  - "Replace this with something a story could contradict."

# Characters who appear across stories. Name, age, gender and station are fixed
# once written. `voice` pins their narration voice across the whole series and
# must name a voice in elevenlabs.voice_library — run `cli voices` to check.
#
# `name` is matched on the full string, case-insensitively. "Honoria Pike" will
# not join to "Mrs Honoria Pike", and nothing warns you: the character just
# quietly gets a new face and a new voice. Write it as it will appear.
#
# `appearance` and `dress` go straight into the image prompt unrewritten, so
# write them for an illustrator: concrete and visual, no personality adjectives.
# Neither is shown to the text agents — put temperament in `note` instead.
#
# `portrait` is an optional filename override inside <bible>.cast/. It is NOT
# written back here automatically; the generated image is saved under the
# slugified name (Mrs Honoria Pike -> mrs-honoria-pike.png) whether or not this
# key is set. See docs/story/bible-format.md.
recurring_cast: []
#  - name: Mrs Honoria Pike        # full name, exactly as it will appear
#    age: 58
#    gender: female                # female | male | neutral
#    station: widow of a naval surgeon; three hundred a year
#    appearance: Short, broad, grey hair pinned severely under a lace cap.
#    dress: Black bombazine, always, ten years out of fashion.
#    voice: Matilda                # must be in elevenlabs.voice_library
#    note: Knows everything first and is wrong about a third of it.
#
# Uncomment the block above and edit it, or run `cli bible new <name> --genre <g>
# --example` for a version that ships filled in. Then check your work:
#
#     python3 -m story_pipeline.cli bible check <name>

# Optional. Overrides the genre's art direction and casting for this series.
# art_style: >
# voices:
#   narrator: Charlotte
---

# {series}

Two or three paragraphs on what this series is. What ties the stories together —
a place, an institution, a family, a season. What a viewer who has seen three of
them would recognize on the fourth.

## What recurs

The furniture of the series. A house, a village, a ship, a shop. Name the things
that will show up again so the writer reaches for them instead of inventing new
ones each time.

## What must stay consistent

Anything a later story could break by accident. Distances, incomes, who is
related to whom, what season it is relative to the others.

## What each story does on its own

Every story stands alone. A viewer arrives at any one of them first, so nothing
here may require having seen another. No recaps, no cliffhangers, no arcs that
only pay off elsewhere. The series is a shared world, not a serial.
'''


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

# The same ear check the outline validator runs, from the same helper — two
# bible characters that collide poison every future story using both, and the
# outline validator only catches it after the ideator has been paid for.
VALID_GENDERS = {"female", "male", "neutral"}

# Text the scaffold ships. Still present means the field was never filled in.
PLACEHOLDERS = ("Replace this with something a story could contradict.",)


class Problem:
    """One finding. `fatal` means the ideator would fail or silently misbehave."""

    def __init__(self, fatal: bool, where: str, message: str, fix: str = ""):
        self.fatal, self.where, self.message, self.fix = fatal, where, message, fix

    def __str__(self) -> str:
        tag = "ERROR" if self.fatal else "warn "
        out = f"  {tag}  {self.where}: {self.message}"
        return out + (f"\n         → {self.fix}" if self.fix else "")


def validate(path: Path, cfg: dict | None = None) -> list[Problem]:
    """Check one bible without calling any model.

    Errors are things that will raise, or that will quietly produce a broken
    series — a name that never joins, a voice that does not exist. Warnings are
    things that work but will drift.
    """
    from . import genres  # local: genres does not import bibles, but keep it one-way

    cfg = cfg or {}
    found: list[Problem] = []
    err = lambda w, m, f="": found.append(Problem(True, w, m, f))   # noqa: E731
    warn = lambda w, m, f="": found.append(Problem(False, w, m, f))  # noqa: E731

    # --- parse ------------------------------------------------------------
    try:
        bible = _parse(path)
    except ValueError as e:
        return [Problem(True, path.name, str(e),
                        "A bible must begin with a `---` frontmatter block.")]
    except yaml.YAMLError as e:
        return [Problem(True, path.name, f"frontmatter is not valid YAML: {e}",
                        "Check indentation and that any colon inside a value is quoted.")]

    # --- genre ------------------------------------------------------------
    installed = genres.available()
    if "genre" not in bible.meta:
        err("genre", "not declared",
            f"Add `genre:` to the frontmatter. Installed: {', '.join(installed)}")
    elif bible.meta["genre"] not in installed:
        err("genre", f"{bible.meta['genre']!r} is not installed",
            f"Installed: {', '.join(installed)}")

    if "series" not in bible.meta:
        warn("series", f"not set; will display as {bible.name!r}",
             'Add `series: "..."` for a proper display name.')

    if not bible.guide.strip():
        warn("prose", "the body below the frontmatter is empty",
             "The agents are given it verbatim. Describe what ties the series together.")

    # --- canon ------------------------------------------------------------
    canon = bible.meta.get("canon")
    if canon is not None and not isinstance(canon, list):
        err("canon", "must be a list of strings")
    else:
        for i, fact in enumerate(canon or []):
            if not isinstance(fact, str) or not fact.strip():
                err(f"canon[{i}]", "not a non-empty string")
            elif fact.strip() in PLACEHOLDERS:
                warn(f"canon[{i}]", "still the scaffold placeholder",
                     "Replace it or delete the line.")

    # --- recurring cast ---------------------------------------------------
    cast = bible.meta.get("recurring_cast")
    if cast is not None and not isinstance(cast, list):
        err("recurring_cast", "must be a list")
        cast = []
    cast = cast or []

    names: list[str] = []
    for i, member in enumerate(cast):
        where = f"recurring_cast[{i}]"
        if not isinstance(member, dict):
            err(where, "must be a mapping of fields")
            continue
        name = member.get("name")
        if not name or not str(name).strip():
            err(where, "has no `name`",
                "Every recurring character needs one; it is the join key.")
            continue
        where = f"cast {name!r}"
        names.append(str(name))

        age = member.get("age")
        if age is not None and not isinstance(age, int):
            err(where, f"age {age!r} is not a whole number")

        gender = member.get("gender")
        if gender is None:
            warn(where, "no `gender`",
                 "Voice casting falls back to the female pool. Set it to "
                 f"{' / '.join(sorted(VALID_GENDERS))}.")
        elif gender not in VALID_GENDERS:
            err(where, f"gender {gender!r} is not one of "
                       f"{', '.join(sorted(VALID_GENDERS))}")

        for field in ("appearance", "dress"):
            if not str(member.get(field) or "").strip():
                warn(where, f"no `{field}`",
                     "The ideator will invent one per story, so the character's "
                     "look will drift. It goes straight into the image prompt.")

        voice = member.get("voice")
        if voice:
            library = cfg.get("elevenlabs", {}).get("voice_library", {})
            if library and voice not in library and not re.fullmatch(r"[A-Za-z0-9]{20}", str(voice)):
                err(where, f"voice {voice!r} is not in elevenlabs.voice_library",
                    "Run `cli voices` to list your account's voices.")

        portrait = member.get("portrait")
        if portrait and not (bible.cast_dir / portrait).exists():
            warn(where, f"portrait {portrait!r} not found in {bible.cast_dir.name}/",
                 "It will be generated on the first story that uses this character.")

    # --- names ------------------------------------------------------------
    lowered = [n.lower() for n in names]
    dupes = {n for n in lowered if lowered.count(n) > 1}
    for d in sorted(dupes):
        err("recurring_cast", f"duplicate name {d!r}",
            "Names are the join key and must be unique.")

    heads = [ear_key(n) for n in names]
    clash = sorted({n for n in names if heads.count(ear_key(n)) > 1})
    if clash:
        err("recurring_cast",
            f"names too similar to tell apart by ear: {', '.join(clash)}",
            "The outline validator rejects this, so every story using both would "
            "fail after the ideator had been paid for. Rename one.")

    # --- overrides --------------------------------------------------------
    if "art_style" in bible.meta and not str(bible.meta["art_style"] or "").strip():
        err("art_style", "present but empty",
            "Delete the key, or describe the art direction.")

    voices = bible.meta.get("voices")
    if voices is not None:
        if not isinstance(voices, dict):
            err("voices", "must be a mapping, e.g. `narrator: Charlotte`")
        else:
            library = cfg.get("elevenlabs", {}).get("voice_library", {})
            narrator = voices.get("narrator")
            if narrator and library and narrator not in library:
                err("voices.narrator", f"{narrator!r} is not in elevenlabs.voice_library",
                    "Run `cli voices`.")

    # --- portraits on disk with nobody to own them ------------------------
    if bible.cast_dir.exists():
        claimed = {(m.get("portrait") or f"{_slug(str(m['name']))}{ext}")
                   for m in cast if isinstance(m, dict) and m.get("name")
                   for ext in (".jpg", ".png")}
        for png in sorted(p for ext in ("*.jpg", "*.png")
                          for p in bible.cast_dir.glob(ext)):
            if png.name not in claimed:
                warn(f"{bible.cast_dir.name}/{png.name}", "no cast member claims this portrait",
                     "A renamed character leaves its old face behind. Delete it, "
                     "or point a `portrait:` at it.")

    return found


def fatal(problems: list[Problem]) -> list[Problem]:
    return [p for p in problems if p.fatal]


# A filled-in scaffold. Every field populated and nothing commented out, so the
# YAML shape is unambiguous and `bible check` passes on it immediately. The
# content is deliberately generic — it is a shape to edit, not a series to keep.
EXAMPLE = '''---
series: "{series}"
genre: {genre}

# Facts true in every story. Keep these to things a story could contradict —
# geography, money, who is related to whom, what everyone knows already.
# Not atmosphere; that belongs in the prose below.
canon:
  - "The village has some four hundred souls and one road out of it."
  - "The inn is the only place a stranger can sleep, and the innkeeper talks."
  - "The house at the end of the lane has stood empty for two years."

# Characters who appear across stories.
#
# `name` is the join key and is matched on the FULL string, case-insensitively.
# "Honoria Pike" will not match "Mrs Honoria Pike", and nothing warns you — the
# character simply gets a new face and a new voice. Write it as it will appear.
#
# `appearance` and `dress` go straight into the image prompt unrewritten, so
# write them for an illustrator: concrete and visual, no personality adjectives.
# Neither is shown to the text agents — put temperament in `note` instead.
recurring_cast:
  - name: Mrs Honoria Pike
    age: 58
    gender: female
    station: widow of a naval surgeon; three hundred a year and a house on the green
    appearance: Short and broad, with grey hair pinned severely under a lace cap, and small quick eyes in a round weathered face.
    dress: Black bombazine with a plain white fichu, ten years out of fashion and immaculately kept.
    note: Knows everything first and is wrong about a third of it. Never malicious, which is what makes her impossible to stop.

  - name: Mr Silas Rowe
    age: 34
    gender: male
    station: curate of the parish; eighty a year and no expectations
    appearance: Tall and thin, dark hair worn slightly too long, with the stoop of a man used to low doorways.
    dress: Worn black coat, carefully brushed, linen turned at the cuffs.
    note: Good at the work and hopeless at everything adjacent to it, particularly money.

# Optional. Overrides the genre's art direction and casting for this series.
# Add `voice: <name>` to a cast member above to pin their narration voice —
# the name must exist in elevenlabs.voice_library. Run `cli voices` to check.
# art_style: >
# voices:
#   narrator: Charlotte
---

# {series}

Two or three paragraphs on what this series is. What ties the stories together —
a place, an institution, a family, a season. What a viewer who has seen three of
them would recognize on the fourth.

## What recurs

The furniture of the series. A house, a village, a ship, a shop. Name the things
that will show up again so the writer reaches for them instead of inventing new
ones each time.

## What must stay consistent

Anything a later story could break by accident. Distances, incomes, who is
related to whom, what season it is relative to the others.

## What each story does on its own

Every story stands alone. A viewer arrives at any one of them first, so nothing
here may require having seen another. No recaps, no cliffhangers, no arcs that
only pay off elsewhere. The series is a shared world, not a serial.
'''


def scaffold(name: str, genre: str, series: str | None, bibles_dir: str | Path,
             example: bool = False) -> Path:
    d = Path(bibles_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.md"
    if path.exists():
        raise ValueError(f"{path} already exists")
    body = EXAMPLE if example else TEMPLATE
    path.write_text(body.format(series=series or name.replace("-", " ").title(),
                                genre=genre))
    return path
