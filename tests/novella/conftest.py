"""Fixtures for novella_pipeline and the Novellas page. The autouse fixtures in
tests/conftest.py block the network, fake the keys and chdir into a scratch
repo root. Bibles here are fake (bibles/glass) or the amberlight copy; the
in-progress drafts are never read."""
from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import pytest

from story_pipeline import config, llm

BIBLE_NAME = "glass"
BIBLE = """---
series: "Glass Roads"
genre: fantasy
canon:
  - "The glass road runs from Hollin to the sea and cracks every winter."
recurring_cast:
  - name: Garret Vane
    gender: male
    age: 30
    station: road warden
---

## Premise

A road of glass that remembers who walks it.

## Open Questions

- Who laid the road? SECRET-WORKING-NOTE
"""

WRITER_REPLY = """## 1. Chapter Text
### The Chiming

Garret listened to the road. It was a crucial moment.

* * *

By morning the frost had gone.

## Chapter Summary
- Garret hears the road chime.

## New Details
- **Hollin Ferry:** a river crossing.

## Flags
None.
"""

REVIEW_REPLY = """## Must Fix
- "It was a crucial moment": telling. Suggest: cut.

## Canon
- New detail: the Mill Pond, east of Hollin.
- The road cracks in winter, per canon; fine.

## Should Consider
None.

## Minor
- typo

## What's Working
- Voice.
"""


@pytest.fixture
def cfg(repo_root):
    return config.load()


@pytest.fixture
def bible(repo_root):
    p = repo_root / "bibles" / BIBLE_NAME / f"{BIBLE_NAME}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(BIBLE)
    return BIBLE_NAME


@pytest.fixture
def book(cfg, bible):
    from novella_pipeline.bundle import Bundle
    b = Bundle.create(cfg["novellas_dir"], "Glasswood", "Glass Roads",
                      genre="fantasy", bible=bible)
    b.brief_path.write_text("# Glasswood: Story Brief\n\nBRIEF-MARKER\n")
    b.outline_path.write_text("# Glasswood: Outline\n\nOUTLINE-MARKER\n")
    b.save_plan(1, "The Chiming", 2500, "- Garret hears the road chime")
    return b


class FakeLLM:
    """Scripted stand-in for story_pipeline.llm.complete."""

    def __init__(self):
        self.replies: list[str] = []
        self.calls: list[dict] = []

    def __call__(self, model, system, user, max_tokens=8000, prefill=None, on_text=None):
        self.calls.append({"model": model, "system": system, "user": user,
                           "max_tokens": max_tokens})
        text = self.replies.pop(0)
        if on_text:
            mid = len(text) // 2
            on_text(text[:mid])
            on_text(text[mid:])
        return text, {"input_tokens": 1000, "output_tokens": 500, "stop_reason": "end_turn"}


@pytest.fixture
def fake_llm(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(llm, "complete", f)
    return f


@pytest.fixture
def runs_dir(repo_root, monkeypatch):
    d = repo_root / "runs"
    monkeypatch.setenv("MW_RUNS_DIR", str(d))
    return d


@pytest.fixture
def client(cfg, runs_dir):
    from story_pipeline.review import server
    from tests.review.conftest import Client
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server._handler(cfg))
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01},
                         daemon=True)
    t.start()
    try:
        yield Client(httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


@pytest.fixture
def started(monkeypatch):
    """Capture runner.start_command calls instead of launching anything."""
    from story_pipeline.review import runner
    calls = []

    def fake(key, stage, argv, busy=None, **extra):
        calls.append({"key": key, "stage": stage, "argv": argv, "extra": extra})
        return {"job_id": "j", "slug": key, "stage": stage}
    monkeypatch.setattr(runner, "start_command", fake)
    return calls
