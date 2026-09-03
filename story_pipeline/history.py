"""What the channel has already done, and how to stop it repeating itself.

`trope_sample` was the first defence against a channel of stories that rhyme:
show the ideator eight of the fifteen tropes rather than all of them, so it
cannot reach for the same three every time. That helps within a run and does
nothing across one — a uniform sample is as likely to offer a trope used in the
last four stories as one never used at all.

This module reads what has actually been made and biases the next sample away
from it. A trope used three times is roughly a quarter as likely to be offered
as one never used; it is never excluded, because a trope the audience liked is
worth returning to eventually, and a genre with fifteen tropes cannot fill a
channel without repeating.

**Only approved stories count.** An outline you looked at and rejected is not
part of the channel and should not push anything down the list — the whole point
of the review gate is that rejecting is free. That makes this a feedback loop on
your taste rather than on the model's first instinct.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# How hard use pushes something down the list. weight = 1 / (1 + n) ** DECAY.
# At 1.0 a trope used once is half as likely as an unused one, used three times
# a quarter. Steep enough to matter within a dozen stories, shallow enough that
# nothing is ever effectively banned.
DECAY = 1.0

# Stages that mean "you decided to make this". `approve` sets ideate=approved,
# and anything further along was approved first.
APPROVED = {"approved", "done", "drafted", "complete"}


@dataclass
class History:
    stories: int = 0
    tropes: Counter = field(default_factory=Counter)
    subgenres: Counter = field(default_factory=Counter)
    protagonists: Counter = field(default_factory=Counter)
    cast: Counter = field(default_factory=Counter)
    invented_cast: Counter = field(default_factory=Counter)
    titles: list[str] = field(default_factory=list)

    def weight(self, name: str) -> float:
        return 1.0 / (1.0 + self.tropes.get(name.lower(), 0)) ** DECAY

    def trope_weights(self, names: list[str]) -> list[float]:
        return [self.weight(n) for n in names]

    def overused(self, threshold: int = 2) -> list[tuple[str, int]]:
        return [(t, n) for t, n in self.tropes.most_common() if n >= threshold]


def _approved(manifest: dict) -> bool:
    return manifest.get("stages", {}).get("ideate") in APPROVED


def scan(stories_dir: str | Path, bible: str | None = None) -> History:
    """Walk the story bundles and count what has been approved.

    A bundle missing or mid-write is skipped rather than raising — this informs a
    sampling weight, and no history is a fine answer that simply means uniform
    sampling, which is what the pipeline did before.
    """
    h = History()
    root = Path(stories_dir)
    if not root.exists():
        return h

    for manifest_path in sorted(root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text())
            if not _approved(manifest):
                continue
            outline = json.loads((manifest_path.parent / "outline.json").read_text())
        except (OSError, json.JSONDecodeError, KeyError):
            continue

        # A per-series view: Amberlight's repetitions say nothing about a
        # different series' next story.
        if bible is not None and outline.get("bible") != bible:
            continue

        h.stories += 1
        h.titles.append(outline.get("title", ""))
        for t in outline.get("tropes", []):
            h.tropes[str(t).lower()] += 1
        if outline.get("subgenre"):
            h.subgenres[outline["subgenre"]] += 1
        for c in outline.get("cast", []):
            name = c.get("name")
            if not name:
                continue
            h.cast[name] += 1
            if not c.get("recurring"):
                h.invented_cast[name] += 1
            if c.get("role") == "protagonist":
                h.protagonists[name] += 1
    return h


def avoid_block(h: History, limit: int = 12) -> str:
    """A short brief for the ideator about what it has already done.

    Deliberately not a ban list. The tropes are handled by the sampling weights,
    which the model never sees; this covers the things sampling cannot reach —
    invented names it keeps reusing, and which recurring character has led too
    often. Both are soft, because the right answer is sometimes to repeat.
    """
    if not h.stories:
        return ""

    parts = [
        "## What this series has already done",
        "",
        f"{h.stories} approved "
        f"{'story' if h.stories == 1 else 'stories'} so far. None of this is "
        "forbidden — but a viewer working through the playlist will notice "
        "repetition faster than you will, so prefer a fresh choice where the "
        "story does not need the familiar one.",
        "",
    ]

    if h.titles:
        parts += ["Titles already used, so this one reads as its own:", ""]
        parts += [f"- {t}" for t in h.titles[-limit:]]
        parts.append("")

    leaders = [f"{n} ({c}x)" for n, c in h.protagonists.most_common(3) if c > 1]
    if leaders:
        parts += [
            "Has led recently: " + ", ".join(leaders) + ". A recurring character "
            "who has carried the last several stories should step back and let "
            "someone else's problem drive this one — they can still appear.",
            "",
        ]

    reused = [f"{n} ({c}x)" for n, c in h.invented_cast.most_common(8) if c > 1]
    if reused:
        parts += [
            "Invented names already used more than once: " + ", ".join(reused)
            + ". These are not in the series bible, so reusing one makes two "
            "unrelated people share a name across the channel. Pick new ones.",
            "",
        ]

    heavy = [f"{s} ({c}x)" for s, c in h.subgenres.most_common(3) if c > 1]
    if heavy:
        parts.append("Subgenres already well covered: " + ", ".join(heavy) + ".")
        parts.append("")

    return "\n".join(parts).strip()
