"""Fixtures for the bundle / CLI / media group.

`story` is the real Cracked Beam bundle (text files only) copied into the
scratch repo root, plus the real timeline JSON files and empty stand-in mp3s so
that narration-dependent code has something to read. Nothing here writes to the
real repo.
"""
from __future__ import annotations

import copy
import json
import shutil
import types
from pathlib import Path

import pytest

from story_pipeline import config as cfgmod
from story_pipeline.bundle import Bundle
from tests.conftest import REPO_ROOT

REAL = REPO_ROOT / "stories" / "amberlight" / "the-cracked-beam"


@pytest.fixture
def cfg():
    """Default config (no config.yaml in the scratch root), voices.yaml merged."""
    # deepcopy: with no config.yaml, load() hands back a shallow copy of
    # DEFAULTS, so mutating cfg["casting"] would leak into every later test.
    return copy.deepcopy(cfgmod.load())


@pytest.fixture
def story(real_story):
    b = Bundle.open(real_story)          # recreates audio/ and the other empty dirs
    for p in (REAL / "audio").glob("*.timeline.json"):
        shutil.copy(p, b.root / "audio" / p.name)
        (b.root / "audio" / p.name.replace(".timeline.json", ".mp3")).write_bytes(b"x")
    return b


def make_mini(root: Path, slug="mini", chapters=2, arc="standalone") -> Bundle:
    """A tiny synthetic story: outline + written chapters, nothing else."""
    b = Bundle.create(root, slug, arc)
    outline = {
        "title": "Mini", "genre": "regency", "subgenre": "slice", "tropes": ["a", "b"],
        "logline": "ll", "setting": "here", "length_min": 15,
        "cast": [{"name": "Ann Lee", "age": 30, "role": "protagonist", "want": "w",
                  "appearance": "tall.", "dress": "grey."},
                 {"name": "Bob Ray", "age": 40, "role": "supporting", "want": "w",
                  "appearance": "short.", "dress": "blue."}],
        "chapters": [{"n": i, "title": f"T{i}", "target_words": 100,
                      "beats": ["x", "y"]} for i in range(1, chapters + 1)],
    }
    b.outline_path.write_text(json.dumps(outline))
    for i in range(1, chapters + 1):
        b.chapter_json(i).write_text(json.dumps({"n": i, "title": f"T{i}", "segments": [
            {"speaker": "narrator", "text": "One two three four."},
            {"speaker": "Ann Lee", "text": "Five six."},
        ]}))
    return b


@pytest.fixture
def mini(repo_root):
    return make_mini(repo_root / "stories")


class FakeClip:
    def __init__(self, pcm=b"\x00\x00" * 2400, rate=24000, sentences=None, billed=10, unit="credits"):
        self.pcm, self.sample_rate = pcm, rate
        self.sentences = sentences if sentences is not None else [
            {"start_ms": 0, "end_ms": 100, "text": "hi."}]
        self.billed_units, self.unit = billed, unit

    @property
    def duration_ms(self):
        return len(self.pcm) / 2 / self.sample_rate * 1000


class FakeProvider:
    """Records every synthesize call; never touches the network."""
    name = "fake"
    sample_rate = 24000
    max_chars = 2400

    def __init__(self, voices=None, listed=None):
        self.calls = []
        self.voices = voices if voices is not None else {}
        if listed is not None:
            self._listed = listed
            self.list_voices = lambda: [{"voice_id": v} for v in self._listed]

    def resolve_voice(self, name):
        if self.voices and name not in self.voices:
            raise ValueError(f"voice {name!r} is not in voices.yaml")
        return self.voices.get(name, name)

    def synthesize(self, text, voice, previous=None, following=None):
        self.calls.append({"text": text, "voice": voice, "previous": previous, "following": following})
        return FakeClip(sentences=[{"start_ms": 0, "end_ms": 100, "text": text}])


def ns(**kw):
    return types.SimpleNamespace(**kw)
