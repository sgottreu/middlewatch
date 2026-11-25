"""changelog.py: per-bible change history."""

from __future__ import annotations

import getpass
import subprocess
from pathlib import Path

import pytest

from story_pipeline import changelog
from story_pipeline.changelog import Entry


@pytest.fixture
def bible(tmp_path):
    d = tmp_path / "bibles" / "demo"
    d.mkdir(parents=True)
    p = d / "demo.md"
    p.write_text("---\nseries: Demo\n---\nbody\n")
    return p


def test_path_for(bible):
    assert changelog.path_for(bible) == bible.parent / "demo.changelog.md"
    assert changelog.path_for(Path("a/b/x.md")) == Path("a/b/x.changelog.md")


def test_content_hash(bible):
    h = changelog.content_hash(bible)
    assert len(h) == 8 and int(h, 16) >= 0
    assert changelog.content_hash(bible) == h
    bible.write_text(bible.read_text() + "more")
    assert changelog.content_hash(bible) != h


# --- resolve_author ---------------------------------------------------------

def _git_returning(monkeypatch, stdout="", code=0, exc=None):
    def run(*a, **k):
        if exc:
            raise exc
        return subprocess.CompletedProcess(a, code, stdout=stdout, stderr="")
    monkeypatch.setattr(changelog.subprocess, "run", run)


def test_author_explicit_wins_and_is_stripped(monkeypatch):
    _git_returning(monkeypatch, "Git Name\n")
    assert changelog.resolve_author("  Scott ", {"author": "Cfg"}) == "Scott"


def test_author_config_beats_git(monkeypatch):
    _git_returning(monkeypatch, "Git Name\n")
    assert changelog.resolve_author(None, {"author": " Cfg "}) == "Cfg"


def test_author_git_then_login(monkeypatch):
    _git_returning(monkeypatch, "Git Name\n")
    assert changelog.resolve_author() == "Git Name"
    _git_returning(monkeypatch, "", code=1)
    monkeypatch.setattr(getpass, "getuser", lambda: "login")
    assert changelog.resolve_author() == "login"
    _git_returning(monkeypatch, "   \n", code=0)
    assert changelog.resolve_author(None, {}) == "login"


def test_author_git_missing_or_failing(monkeypatch):
    monkeypatch.setattr(getpass, "getuser", lambda: "login")
    _git_returning(monkeypatch, exc=FileNotFoundError("git"))
    assert changelog.resolve_author() == "login"
    _git_returning(monkeypatch, exc=subprocess.TimeoutExpired("git", 2))
    assert changelog.resolve_author() == "login"


def test_author_unknown_when_nothing_works(monkeypatch):
    _git_returning(monkeypatch, exc=OSError())

    def boom():
        raise KeyError("no user")
    monkeypatch.setattr(getpass, "getuser", boom)
    assert changelog.resolve_author() == "unknown"


def test_author_empty_explicit_falls_through(monkeypatch):
    _git_returning(monkeypatch, "Git Name\n")
    assert changelog.resolve_author("", None) == "Git Name"


# --- entries / append -------------------------------------------------------

def test_entries_missing_file(bible):
    assert changelog.entries(bible) == []
    assert changelog.last_sha(bible) is None
    assert changelog.is_stale(bible) is False


def test_append_creates_with_header(bible):
    log = changelog.append(bible, "  First change.  ", "Scott", series="Demo Parish")
    assert log == changelog.path_for(bible)
    text = log.read_text()
    assert text.startswith("# Changelog — Demo Parish")
    assert 'bible log demo -m "what changed"' in text
    assert "\n---\n\n## " in text
    (e,) = changelog.entries(bible)
    assert e == Entry(e.when, "Scott", "edit", changelog.content_hash(bible), "First change.")
    assert len(e.when) == 16 and e.when[4] == "-"


def test_append_series_defaults_to_stem(bible):
    changelog.append(bible, "x", "a")
    assert changelog.path_for(bible).read_text().startswith("# Changelog — demo")


def test_append_newest_first_and_header_preserved(bible):
    changelog.append(bible, "one", "A")
    changelog.append(bible, "two\nsecond line", "B", kind="promote")
    changelog.append(bible, "three", "C", kind="voices")
    es = changelog.entries(bible)
    assert [(e.author, e.kind, e.summary) for e in es] == [
        ("C", "voices", "three"), ("B", "promote", "two\nsecond line"), ("A", "edit", "one")]
    text = changelog.path_for(bible).read_text()
    assert text.count("# Changelog") == 1 and text.count("\n---\n") == 1
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert text.index("three") < text.index("two") < text.index("one")


def test_append_to_header_only_file(bible):
    path = changelog.path_for(bible)
    path.write_text("# Changelog — Demo\n\nhand written intro\n")
    changelog.append(bible, "first", "A")
    es = changelog.entries(bible)
    assert [e.summary for e in es] == ["first"]
    assert "hand written intro" in path.read_text()


def test_entries_malformed_file_yields_nothing(bible):
    changelog.path_for(bible).write_text("nothing here\n## not an entry\nrandom\n")
    assert changelog.entries(bible) == []


def test_entries_ignore_preamble_and_parse_manual_entry(bible):
    changelog.path_for(bible).write_text(
        "# Changelog\n\nintro\n\n## 2026-08-29 14:03 · Scott · edit · sha=4f2a9c1e\n\n"
        "Did a thing.\n\n## 2026-08-01 09:00 · pipeline · promote · sha=deadbeef\n\nOlder.\n")
    es = changelog.entries(bible)
    assert [(e.when, e.author, e.kind, e.sha, e.summary) for e in es] == [
        ("2026-08-29 14:03", "Scott", "edit", "4f2a9c1e", "Did a thing."),
        ("2026-08-01 09:00", "pipeline", "promote", "deadbeef", "Older.")]
    assert changelog.last_sha(bible) == "4f2a9c1e"


def test_staleness(bible):
    changelog.append(bible, "logged", "A")
    assert changelog.is_stale(bible) is False
    assert changelog.last_sha(bible) == changelog.content_hash(bible)
    bible.write_text(bible.read_text() + "edited after\n")
    assert changelog.is_stale(bible) is True
    changelog.append(bible, "caught up", "A")
    assert changelog.is_stale(bible) is False


def test_summary_with_hash_headings_does_not_confuse_parser(bible):
    changelog.append(bible, "Changed:\n\n## Not an entry heading\n\nmore", "A")
    (e,) = changelog.entries(bible)
    assert "## Not an entry heading" in e.summary
