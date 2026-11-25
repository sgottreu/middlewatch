"""Shared fixtures for the middlewatch test suite.

Every test runs with:
- no outbound network (only loopback is allowed, for the review server tests);
- fake API keys in the environment, so nothing can bill a real account;
- the working directory set to a fresh temporary "repo root", because
  config.yaml, stories/, bibles/, runs/ and LEDGER_DIR all resolve against cwd.
  Tests that need the real repo files read them through REPO_ROOT, read-only.
"""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_real_connect = socket.socket.connect


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and host in ("127.0.0.1", "localhost", "::1"):
        return _real_connect(self, address)
    if self.family == getattr(socket, "AF_UNIX", None):
        return _real_connect(self, address)
    raise RuntimeError(f"network access blocked in tests: {address!r}")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


@pytest.fixture(autouse=True)
def _fake_keys(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                 "ELEVENLABS_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "test-not-a-real-key")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)


@pytest.fixture(autouse=True)
def repo_root(tmp_path, monkeypatch):
    """A scratch repo root as cwd. Copies the tracked, non-secret files the
    code expects to find there (prompts and the voice library live in the
    package or in voices.yaml; config.yaml is never copied)."""
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("voices.yaml",):
        src = REPO_ROOT / name
        if src.exists():
            shutil.copy(src, root / name)
    (root / "stories").mkdir()
    (root / "bibles").mkdir()
    (root / "ledger").mkdir()
    if (REPO_ROOT / "ledger" / "rates.json").exists():
        shutil.copy(REPO_ROOT / "ledger" / "rates.json", root / "ledger" / "rates.json")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def real_bibles(repo_root):
    """Copy only the promoted amberlight bible (bibles/amberlight) into the scratch
    root; read the original, never write it. In-progress drafts are never copied:
    tests that need a draft use the synthetic `sample_draft` / `broken_draft`."""
    shutil.copytree(REPO_ROOT / "bibles" / "amberlight",
                    repo_root / "bibles" / "amberlight", dirs_exist_ok=True)
    return repo_root / "bibles"


SAMPLE_NAME = "salt"
SAMPLE_SERIES = "Salt Roads"
SAMPLE_CAST_COUNT = 3

SAMPLE_DRAFT_TEXT = (
    "# Salt Roads (Working Draft)\n\n"
    "Genre: fantasy\nSeries: Salt Roads\n"
    "Working title: Salt Roads Status: early.\n\n"
    "## Premise\n\nA coast of salt towns linked by one road that floods at spring tide.\n\n"
    "## Canon\n\n- The road floods at every spring tide.\n"
    "- Salt is the only currency the\n  towns all accept.\n\n"
    "## Regions\n\n- **Brinehold:** the largest town. It owns the pans.\n"
    "- **Marrow Flats:** mud and birds.\n\n"
    "## Recurring Cast\n\nA small cast that rotates by town.\n\n"
    "### Ada Penrose (road warden)\n\n- Gender: female\n- Appearance: tall, grey coat.\n\n"
    "Keeps the tide tables.\n\n"
    "### Hal Venn (carter)\n\n- Gender: male\n\nDrives salt carts.\n\n"
    "### Ottoline Shaw (reader)\n\n- Gender: female\n\nReads the tide bells.\n\n"
    "## Story Ideas\n\n- A cart is caught by an early tide.\n"
)

# Fatal errors on purpose: no genre, and a placeholder cast name.
BROKEN_NAME = "salt-broken"
BROKEN_DRAFT_TEXT = (
    "# Salt Roads (Working Draft)\n\n"
    "Working title: Salt Roads Status: early.\n\n"
    "## Premise\n\nA coast of salt towns linked by one road that floods at spring tide.\n\n"
    "## Canon\n\n- The road floods at every spring tide.\n\n"
    "## Recurring Cast\n\n"
    "### Ada Penrose (road warden)\n\n- Gender: female\n\nKeeps the tide tables.\n\n"
    "### Ada's father\n\nA man of sixty who drives carts.\n"
)


def _write_draft(root, name, text):
    p = root / "bibles" / name / f"{name}.draft.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def sample_draft(repo_root):
    """A synthetic, promotable draft bibles/salt/salt.draft.md. Returns its name."""
    _write_draft(repo_root, SAMPLE_NAME, SAMPLE_DRAFT_TEXT)
    return SAMPLE_NAME


@pytest.fixture
def broken_draft(repo_root):
    """A synthetic draft with fatal errors (no genre, placeholder cast name)."""
    _write_draft(repo_root, BROKEN_NAME, BROKEN_DRAFT_TEXT)
    return BROKEN_NAME


@pytest.fixture
def real_story(repo_root):
    """Copy the one real story bundle's text files (no audio/images/video)."""
    src = REPO_ROOT / "stories" / "amberlight" / "the-cracked-beam"
    dst = repo_root / "stories" / "amberlight" / "the-cracked-beam"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("audio", "video", "*.jpg", "*.png", "*.mp3", "*.mp4"))
    return dst


has_ffmpeg = shutil.which("ffmpeg") is not None
