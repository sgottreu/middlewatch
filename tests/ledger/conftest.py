"""Ledger test fixtures. Everything resolves against the tmp cwd from tests/conftest.py."""

from __future__ import annotations

import os

import pytest

from ledger import ledger as L


@pytest.fixture(autouse=True)
def _ledger_is_cwd_relative():
    """LEDGER_DIR is read from MW_LEDGER_DIR at import time. If it is absolute,
    tests could write into a real ledger; refuse to run."""
    if L.LEDGER_DIR.is_absolute():
        pytest.fail(f"MW_LEDGER_DIR={os.environ.get('MW_LEDGER_DIR')!r} is absolute; "
                    "unset it before running the ledger tests")


@pytest.fixture
def ldir(repo_root):
    d = repo_root / "ledger"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def story():
    return {"pipeline": "story", "slug": "amberlight-01", "title": "The Curate",
            "series": "Amberlight", "genre": "regency", "flavor": "comedy",
            "arc_slug": "amberlight-vol-1", "volume": 1, "episode": 2}
