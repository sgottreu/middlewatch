"""Genre registry.

Each genre is one markdown file under prompts/genres/ with a YAML frontmatter
block for the structured parts (subgenres, tropes, art direction, voice casting)
and prose below it for the voice guide the agents actually read.

One file per genre rather than a genre table plus separate prose, because the
two drift the moment they live apart: someone adds a trope to the list and never
touches the voice guide that explains how that genre handles one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

GENRES_DIR = Path(__file__).parent / "prompts" / "genres"


@dataclass
class Genre:
    name: str
    meta: dict[str, Any]
    guide: str  # the prose voice guide

    @property
    def subgenres(self) -> list[str]:
        return list(self.meta.get("subgenres", {}))

    @property
    def tropes(self) -> list[dict]:
        return self.meta.get("tropes", [])

    @property
    def art_style(self) -> str:
        return self.meta["art_style"].strip()

    def subgenre_note(self, subgenre: str) -> str:
        return self.meta.get("subgenres", {}).get(subgenre, "")

    def voices(self) -> dict[str, Any]:
        return self.meta.get("voices", {})

    def trope_menu(
        self,
        sample: int | None = None,
        seed: int | None = None,
        weights: dict[str, float] | None = None,
    ) -> str:
        """Render the trope list for a prompt.

        Sampling matters more than it looks. Handed all twenty every time, the
        ideator reaches for the same three highest-probability ones and the
        channel's stories start rhyming with each other. A rotating subset is
        the cheapest fix for that.

        `weights` biases which subset. A uniform sample is as likely to offer a
        trope used in the last four stories as one never used, so what stops
        repetition inside a run does nothing across one. Keys are lowercased
        trope names; anything absent weighs 1.0. Nothing is ever excluded — a
        weight only changes the odds, because fifteen tropes cannot fill a
        channel without eventually coming round again.
        """
        items = self.tropes
        if sample and sample < len(items):
            rng = random.Random(seed)
            if weights:
                items = _weighted_sample(items, sample, weights, rng)
            else:
                items = rng.sample(items, sample)
        return "\n".join(f"- **{t['name']}** — {t['note']}" for t in items)

    def validate_subgenre(self, subgenre: str | None) -> str | None:
        if subgenre and subgenre not in self.subgenres:
            raise ValueError(
                f"unknown subgenre {subgenre!r} for {self.name}; "
                f"available: {', '.join(self.subgenres)}"
            )
        return subgenre


def _weighted_sample(
    items: list[dict], k: int, weights: dict[str, float], rng: random.Random
) -> list[dict]:
    """Pick k items without replacement, favouring the heavier ones.

    Uses the exponential-jump trick: give each item a key of `random ** (1/w)`
    and take the largest k. One pass, no renormalising after each draw, and a
    zero weight still cannot make an item impossible — it makes it last.
    """
    ranked = []
    for item in items:
        w = max(weights.get(str(item["name"]).lower(), 1.0), 1e-6)
        ranked.append((rng.random() ** (1.0 / w), item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in ranked[:k]]


def _parse(path: Path) -> Genre:
    raw = path.read_text()
    if not raw.startswith("---"):
        raise ValueError(f"{path} has no frontmatter block")
    _, front, body = raw.split("---", 2)
    return Genre(name=path.stem, meta=yaml.safe_load(front) or {}, guide=body.strip())


def load(name: str) -> Genre:
    path = GENRES_DIR / f"{name}.md"
    if not path.exists():
        raise ValueError(f"unknown genre {name!r}; available: {', '.join(available())}")
    return _parse(path)


def available() -> list[str]:
    return sorted(p.stem for p in GENRES_DIR.glob("*.md"))


def all_subgenres() -> dict[str, list[str]]:
    return {name: load(name).subgenres for name in available()}
