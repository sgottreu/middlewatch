"""The story bundle: one directory per story, one manifest tracking stage state.

Every agent reads files and writes files. Nothing is passed through agent context,
which is what makes a run resumable — a failed image render costs you images, not
a rewrite.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STAGES = ["ideate", "write", "edit", "record", "design", "direct"]

# A scene break: a jump in time or place inside a chapter. It is a segment with
# this in the speaker field and no text, rather than a flag on the segment after
# it, for three reasons — everything downstream already walks segments in order,
# a flag would be silently dropped by every path that rebuilds a segment as
# `{speaker, text}` (the review editor does exactly that), and `chapter_hash`
# hashes the speaker/text pairs, so a break as a segment makes moving one count
# as a change to the chapter and correctly marks the narration out of date.
#
# It renders three ways: `* * *` on the page, a long silence in the narration,
# and nothing at all to the voice — it is never spoken.
BREAK = "break"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "untitled"


# Honorifics carry no information for a listener trying to tell two characters
# apart — half a Regency cast is "Mr" or "Mrs" — so they are stripped before the
# ear check. Without this, "Mrs Honoria Pike" and "Mrs Judith Vane" are judged
# indistinguishable because both begin "Mrs".
HONORIFICS = {
    "mr", "mrs", "ms", "miss", "mister", "mistress", "dr", "doctor",
    "sir", "dame", "lady", "lord", "rev", "reverend", "father", "sister",
    "captain", "capt", "colonel", "col", "major", "general", "gen",
    "lieutenant", "lt", "admiral", "sergeant", "sgt", "professor", "prof",
    "st", "saint", "master", "madam", "madame", "mme", "mlle", "señor", "señora",
}

EAR_PREFIX = 3


# A model asked to "omit unless…" sometimes answers with the instruction rather
# than omitting — `bible_conflict: "omit"` is what prompted this. The schemas now
# ask for null instead, but a field that only sometimes carries a real answer is
# always one bad generation away from a false alarm, so the readers check too.
NON_ANSWERS = {
    "", "-", "n/a", "na", "none", "null", "nil", "no", "false", "omit",
    "omitted", "not applicable", "no conflict", "no conflicts", "nothing",
}


def real_answer(value: Any) -> str:
    """The value if it says something, otherwise an empty string.

    Deliberately narrow: only exact matches against known non-answers are
    dropped. Anything with actual content is passed through, because silently
    swallowing a genuine conflict would be far worse than showing a stray word.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text.lower().rstrip(".") in NON_ANSWERS else text


def ear_key(name: str) -> str:
    """What a name sounds like at its start, for collision checking.

    Two characters whose names open on the same syllable are hard to tell apart
    in narration, and the mistake only becomes obvious after the audio is paid
    for. Compared on the first distinguishing word, not the honorific.
    """
    words = re.sub(r"[^a-z\s]", " ", name.lower()).split()
    while words and words[0].rstrip(".") in HONORIFICS:
        words.pop(0)
    return (words[0] if words else name.lower().strip())[:EAR_PREFIX]


@dataclass
class Bundle:
    root: Path

    # --- paths -------------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def outline_path(self) -> Path:
        return self.root / "outline.json"

    @property
    def story_md_path(self) -> Path:
        return self.root / "story.md"

    def chapter_json(self, n: int) -> Path:
        return self.root / "chapters" / f"{n:02d}.json"

    def chapter_md(self, n: int) -> Path:
        return self.root / "chapters" / f"{n:02d}.md"

    def review_path(self, n: int) -> Path:
        return self.root / "reviews" / f"{n:02d}.json"

    def draft_path(self, n: int, attempt: int) -> Path:
        """One pass of the write/edit loop, kept.

        The loop overwrote `chapters/NN.json` on every revision, so a chapter
        that took two passes left no evidence of what the editor changed — and
        no way to recover a first draft you preferred. These are small JSON
        files; keeping them costs nothing and is the only record of whether the
        edit loop is earning its keep.
        """
        return self.root / "drafts" / f"{n:02d}.{attempt}.json"

    def draft_review_path(self, n: int, attempt: int) -> Path:
        return self.root / "drafts" / f"{n:02d}.{attempt}.review.json"

    def rejected_path(self, n: int) -> Path:
        """A draft that failed validation and never became a chapter.

        Written before the check and deleted the moment it passes, so it exists
        only after a failure. Without it a chapter rejected for an unresolvable
        speaker was generated, billed, and then discarded unread — this keeps
        the prose so the offending line can be corrected by hand rather than
        paid for twice. `drafts()` ignores it: it is not an attempt in the
        write/edit loop and never had a review.
        """
        return self.root / "drafts" / f"{n:02d}.rejected.json"

    def drafts(self, n: int) -> list[int]:
        """Attempt numbers kept for one chapter, oldest first."""
        d = self.root / "drafts"
        if not d.exists():
            return []
        out = []
        for p in d.glob(f"{n:02d}.*.json"):
            if p.name.endswith(".review.json"):
                continue
            try:
                out.append(int(p.stem.split(".")[1]))
            except (IndexError, ValueError):
                continue
        return sorted(out)

    def audio_path(self, n: int) -> Path:
        return self.root / "audio" / f"{n:02d}.mp3"

    def timeline_path(self, n: int) -> Path:
        return self.root / "audio" / f"{n:02d}.timeline.json"

    # The image API returns JPEG — it refuses `image/png` outright — so new
    # files are .jpg. Portraits generated before that, and any promoted into a
    # series bible as .png, are still found: a face on disk is a face on disk,
    # and regenerating one costs money and returns a different person.
    IMAGE_EXTS = ("jpg", "png")

    def _image(self, path: Path) -> Path:
        if path.exists():
            return path
        for ext in self.IMAGE_EXTS:
            alt = path.with_suffix(f".{ext}")
            if alt.exists():
                return alt
        return path

    def cast_image(self, name: str) -> Path:
        return self._image(self.root / "images" / "cast" / f"{slugify(name)}.jpg")

    @property
    def scenes_path(self) -> Path:
        return self.root / "images" / "scenes.json"

    def scene_image(self, chapter: int, idx: int) -> Path:
        return self._image(self.root / "images" / f"{chapter:02d}-{idx:02d}.jpg")

    @property
    def srt_path(self) -> Path:
        return self.root / "video" / "subtitles.srt"

    @property
    def video_path(self) -> Path:
        return self.root / "video" / "final.mp4"

    @property
    def narration_path(self) -> Path:
        return self.root / "video" / "narration.mp3"

    # --- lifecycle ---------------------------------------------------------

    @classmethod
    def create(cls, stories_dir: Path, slug: str) -> "Bundle":
        root = Path(stories_dir) / slug
        b = cls(root)
        for sub in ("chapters", "drafts", "reviews", "audio", "images/cast", "video"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        if not b.manifest_path.exists():
            b.write_manifest(
                {
                    # Which pipeline made this. The ledger requires it and will
                    # not guess: an ambience slug and a story slug look alike,
                    # and merging the two channels' economics is not a mistake
                    # any report could later detect.
                    "pipeline": "story",
                    "slug": slug,
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "stages": {s: "pending" for s in STAGES},
                    "spend": {},
                }
            )
        return b

    @classmethod
    def open(cls, path: Path) -> "Bundle":
        b = cls(Path(path))
        if not b.manifest_path.exists():
            raise FileNotFoundError(f"no manifest at {b.manifest_path}")
        return b

    # --- manifest ----------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text())

    def write_manifest(self, data: dict[str, Any]) -> None:
        self.manifest_path.write_text(json.dumps(data, indent=2) + "\n")

    def set_stage(self, stage: str, status: str, **extra: Any) -> None:
        m = self.manifest()
        m["stages"][stage] = status
        if extra:
            m.setdefault("stage_info", {})[stage] = extra
        self.write_manifest(m)

    def stage_status(self, stage: str) -> str:
        return self.manifest()["stages"].get(stage, "pending")

    # ----------------------------------------------------------------- #
    # Per-chapter approval
    # ----------------------------------------------------------------- #
    #
    # Approving a story was all-or-nothing, which asked you to read eight
    # chapters in one sitting before anything could move. Approval is recorded
    # per chapter instead, against the hash of what was approved — so the
    # story-level gate is derived from the chapters rather than set beside them,
    # and it cannot say approved while holding prose nobody agreed to.

    def chapter_approvals(self) -> dict[str, str]:
        """Chapter number (as a string, since JSON keys are) -> approved hash."""
        return self.manifest().get("chapter_approvals", {}) or {}

    def approve_chapter(self, n: int) -> None:
        m = self.manifest()
        m.setdefault("chapter_approvals", {})[str(n)] = self.chapter_hash(n)
        self.write_manifest(m)

    def unapprove_chapter(self, n: int) -> None:
        m = self.manifest()
        m.get("chapter_approvals", {}).pop(str(n), None)
        self.write_manifest(m)

    def chapter_approved(self, n: int) -> bool:
        """Approved, and unchanged since. A chapter rewritten after approval
        lapses back to unapproved rather than carrying the old verdict — the
        same rule `narration_stale` applies to audio, for the same reason."""
        recorded = self.chapter_approvals().get(str(n))
        return bool(recorded) and recorded == self.chapter_hash(n)

    def approved_chapters(self) -> list[int]:
        return [n for n in self.chapter_numbers() if self.chapter_approved(n)]

    def all_chapters_approved(self) -> bool:
        written = [n for n in self.chapter_numbers() if self.chapter_json(n).exists()]
        return bool(written) and all(self.chapter_approved(n) for n in written)

    def add_spend(self, key: str, amount: float) -> None:
        m = self.manifest()
        m["spend"][key] = round(m["spend"].get(key, 0.0) + amount, 4)
        self.write_manifest(m)

    def pin(self, key: str, value: Any) -> None:
        """Pin a decision (style preamble, voice map) so re-runs are identical."""
        m = self.manifest()
        m.setdefault("pinned", {})[key] = value
        self.write_manifest(m)

    def pinned(self, key: str, default: Any = None) -> Any:
        return self.manifest().get("pinned", {}).get(key, default)

    def ledger_story(self) -> dict[str, Any]:
        """The dimension labels for a ledger cost event, read off the manifest.

        Assembled here so that no call site hand-builds the dict and drifts from
        the others. Everything but `pipeline` and `slug` is pinned at creation,
        which is what stops a genre file edited next year from restating what a
        story cost to make — the ledger's comparisons are only worth reading if
        the labels are as immutable as the rows.
        """
        m = self.manifest()
        p = m.get("pinned", {})
        return {
            "pipeline": m.get("pipeline", ""),
            "slug": m.get("slug", ""),
            "title": p.get("title", ""),
            "series": p.get("series", ""),
            "genre": p.get("genre", ""),
            "flavor": p.get("flavor", ""),
            "arc_slug": p.get("arc_slug", ""),
            "volume": p.get("volume", ""),
            "episode": p.get("episode", ""),
        }

    # --- convenience -------------------------------------------------------

    def outline(self) -> dict[str, Any]:
        return json.loads(self.outline_path.read_text())

    def chapter_numbers(self) -> list[int]:
        return [c["n"] for c in self.outline()["chapters"]]

    def chapter(self, n: int) -> dict[str, Any]:
        return json.loads(self.chapter_json(n).read_text())

    def timeline(self, n: int) -> dict[str, Any]:
        return json.loads(self.timeline_path(n).read_text())

    def chapter_words(self, n: int) -> int | None:
        """Words in one drafted chapter, or None if it isn't written yet."""
        p = self.chapter_json(n)
        if not p.exists():
            return None
        return sum(len(s["text"].split()) for s in json.loads(p.read_text())["segments"])

    def word_count(self) -> int:
        return sum(self.chapter_words(n) or 0 for n in self.chapter_numbers())

    def chapter_hash(self, n: int) -> str | None:
        """Hash of the spoken text of one chapter, or None if unwritten.

        Only the words that reach the voice. A retitled chapter is not a reason
        to re-narrate; a changed line is.
        """
        p = self.chapter_json(n)
        if not p.exists():
            return None
        segs = json.loads(p.read_text())["segments"]
        # Serialised rather than string-joined: no separator to pick, and
        # nothing to collide with text that happens to contain one.
        #
        # A performance tag changes the audio, so it belongs in the hash — but
        # only when there is one. A segment without a tag hashes exactly as it
        # did before tags existed, which is what stops every chapter already
        # approved and narrated from lapsing the day this shipped.
        payload = json.dumps(
            [[s["speaker"], s["text"]] + ([s["tag"]] if s.get("tag") else [])
             for s in segs],
            ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def narration_stale(self, n: int, announcement: str | None = None,
                        voices: dict | None = None) -> bool:
        """True when the audio on disk was made from different words.

        `record_all` skips any chapter that already has audio, so without this an
        edited chapter silently keeps its old narration and the video ends up
        with prose on screen the voice never says. Timelines written before this
        existed carry no hash and are trusted rather than re-narrated, since
        re-recording costs money and they were correct when made.

        `announcement` is the spoken chapter heading the actor would generate
        now. It is not part of the chapter text — it comes from a template — so
        changing that template would otherwise leave audio saying the old
        heading with nothing to detect it. Pass it to have that counted too;
        omit it to check the prose alone.

        `voices` is the casting the actor would use now. Same argument a third
        time: switching a story from a cast to a single reader changes every
        second of the audio and not one character of the text, so without this
        `record` looks at the mp3 on disk, finds the words unchanged, and keeps
        a recording made by seven voices you have just decided against.
        """
        if not (self.audio_path(n).exists() and self.timeline_path(n).exists()):
            return False
        t = self.timeline(n)
        recorded = t.get("chapter_hash")
        if not recorded:
            return False
        if recorded != self.chapter_hash(n):
            return True
        # Only when the caller knows what the template says now. A timeline from
        # before announcements existed has no key at all, and `None == None`
        # leaves it correctly untouched.
        if voices is not None and t.get("voices") and t["voices"] != voices:
            return True
        return announcement is not None and t.get("announcement") != announcement

    def duration_ms(self) -> int | None:
        """Measured narration length, or None until the audio exists.

        This is the only honest runtime figure in the pipeline. Everything else
        is words divided by an assumed words-per-minute; this is the audio.
        """
        total = 0
        for n in self.chapter_numbers():
            if not self.timeline_path(n).exists():
                return None
            total += self.timeline(n)["duration_ms"]
        return total
