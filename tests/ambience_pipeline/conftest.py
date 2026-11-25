"""Fixtures for the ambience pipeline: the review server client is reused from
tests/review, plus small concepts and a synthetic story bundle. The autouse
fixtures in tests/conftest.py block the network and chdir into a scratch root."""
from __future__ import annotations

import json
import wave

import numpy as np
import pytest

from tests.review.conftest import cfg, client, runs_dir, started  # noqa: F401


def layer(lid="room", role="bed", kind="room_tone", gain=-22.0, **kw):
    return {"id": lid, "role": role, "source": "procedural",
            "recipe": {"type": kind, **kw.pop("params", {})}, "gain_db": gain, **kw}


@pytest.fixture
def concept():
    return {"seed": 7, "layers": [layer("rain", "bed", "rain", -20.0),
                                  layer("room", "texture", "room_tone", -28.0)]}


@pytest.fixture
def fake_story(repo_root):
    """A minimal story bundle: manifest, outline and shot prompts, no bible on disk."""
    root = repo_root / "stories" / "standalone" / "the-salt-house"
    (root / "images").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "pipeline": "story", "slug": "the-salt-house", "stages": {}, "spend": {},
        "pinned": {"art_style": "muted watercolour, grey light"}}))
    (root / "outline.json").write_text(json.dumps({
        "title": "The Salt House", "genre": "gothic", "series": "",
        "bible": "no-such-bible",
        "logline": "A keeper hears the sea speak through the walls.",
        "setting": "A lighthouse cottage on a storm coast, 1890",
        "chapters": [{"n": 1}]}))
    (root / "images" / "scenes.json").write_text(json.dumps({
        "1": {"n": 1, "shots": [{"prompt": "Waves breaking under a lighthouse at dusk"},
                                {"prompt": "A kitchen hearth with a kettle, rain on the window"}]}}))
    return root


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
    return x.reshape(-1, ch) / 32767.0, sr
