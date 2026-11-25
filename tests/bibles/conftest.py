"""Helpers for the bibles and casting tests. Everything is built in the tmp
repo root the autouse fixtures chdir into; nothing here touches the real repo."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def base_meta(**over) -> dict:
    """A bible's frontmatter that validates with no errors and no warnings."""
    meta = {
        "series": "Test Parish",
        "genre": "regency",
        "canon": ["The living is worth two hundred a year."],
        "recurring_cast": [
            {"name": "Mrs Honoria Pike", "age": 58, "gender": "female",
             "station": "widow", "appearance": "Short and broad.",
             "dress": "Black bombazine.", "note": "Knows everything."},
            {"name": "Mr Silas Rowe", "age": 34, "gender": "male",
             "station": "curate", "appearance": "Tall and thin.",
             "dress": "Worn black coat."},
        ],
    }
    meta.update(over)
    return {k: v for k, v in meta.items() if v is not None}


def write_bible(root: Path, name: str, meta: dict | str | None = None,
                body: str = "# Test\n\nThe parish.\n", nested: bool = True) -> Path:
    """Write bibles/<name>/<name>.md (or the flat bibles/<name>.md)."""
    meta = base_meta() if meta is None else meta
    front = meta if isinstance(meta, str) else yaml.safe_dump(meta, sort_keys=False)
    path = (root / "bibles" / name / f"{name}.md") if nested else (root / "bibles" / f"{name}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}---\n\n{body}")
    return path


@pytest.fixture
def mk(repo_root):
    """`mk(name, meta=None, body=..., nested=True)` -> path of a synthetic bible."""
    def _mk(name="demo", meta=None, body="# Test\n\nThe parish.\n", nested=True):
        return write_bible(repo_root, name, meta, body, nested)
    return _mk
