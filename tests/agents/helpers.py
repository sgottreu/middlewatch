"""Builders for outlines, chapters and bundles used across the text-agent tests."""

from __future__ import annotations

import copy
import json

from story_pipeline import config
from story_pipeline.bundle import Bundle

CAST15 = [
    {"name": "Mr Silas Rowe", "role": "protagonist", "age": 34, "gender": "male"},
    {"name": "Miss Judith Vane", "role": "love_interest", "age": 27, "gender": "female"},
    {"name": "Mrs Honoria Pike", "role": "supporting", "age": 58, "gender": "female"},
    {"name": "Mr Owen Vane", "role": "supporting", "age": 30, "gender": "male"},
]


def mini_outline(minutes: int = 15) -> dict:
    """A valid regency outline for the 15- or 30-minute setting (no bible)."""
    if minutes == 15:
        n_ch, words, beats = 5, 450, 3
        cast = copy.deepcopy(CAST15)
    else:
        n_ch, words, beats = 8, 560, 4
        cast = copy.deepcopy(CAST15) + [
            {"name": "Mrs Hale", "role": "supporting", "age": 45},
            {"name": "Miss Louisa Marchmont", "role": "supporting", "age": 22}]
    return {
        "title": "The Cracked Beam",
        "genre": "regency",
        "subgenre": "slice_of_life",
        "tropes": ["Enemies of manner", "The meddling matchmaker"],
        "logline": "A curate and a librarian quarrel over a roof.",
        "promise": "Small stakes, fully worked.",
        "setting": "Amberlight, Hampshire, early summer of 1817",
        "cast": cast,
        "chapters": [
            {"n": i, "title": f"Chapter title {i}",
             "beats": [f"Beat {i}.{j}" for j in range(beats)],
             "target_words": words}
            for i in range(1, n_ch + 1)
        ],
    }


def chapter_json(n: int, title: str | None = None, text: str | None = None) -> dict:
    return {
        "n": n,
        "title": title or f"Chapter title {n}",
        "segments": [
            {"speaker": "narrator",
             "text": text or "Rowe took the ladder from the shed. It was longer than he remembered."},
            {"speaker": "Mr Silas Rowe", "text": "I will go up myself."},
            {"speaker": "narrator", "text": "said Rowe."},
        ],
    }


def verdict(passed: bool = True, fixes: list[str] | None = None, **extra) -> dict:
    return {"pass": passed, "score": 7 if passed else 4,
            "beats": [], "fixes": fixes or [], "boundary_breaches": [], **extra}


def make_bundle(root, outline: dict | None = None, minutes: int = 15) -> Bundle:
    outline = outline or mini_outline(minutes)
    b = Bundle.create(root / "stories", "the-cracked-beam")
    outline = dict(outline, slug="the-cracked-beam")
    b.outline_path.write_text(json.dumps(outline, indent=2))
    b.pin("length_min", minutes)
    return b


def cfg() -> dict:
    # deep-copied: config.load() shares nested dicts with config.DEFAULTS, so a test
    # that edited cfg['models'] etc. would leak into every later test.
    return copy.deepcopy(config.load())
