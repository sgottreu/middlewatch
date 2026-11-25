"""The CLI end to end, with story_pipeline.llm.complete faked (no network)."""
import csv
import json

import pytest

from novella_pipeline import cli
from novella_pipeline.bundle import Bundle
from novella_pipeline.workflow import Stage

from .conftest import REVIEW_REPLY, WRITER_REPLY


def run(*argv):
    cli.main([str(a) for a in argv])


def ledger_rows(repo_root):
    p = repo_root / "ledger" / "costs.csv"
    return list(csv.DictReader(p.open())) if p.exists() else []


def test_new_defaults_series_and_genre_from_bible(bible, repo_root, capsys):
    run("new", "--title", "Glasswood", "--bible", bible)
    b = Bundle.open(repo_root / "novellas" / "glass-roads" / "glasswood")
    assert b.pinned("series") == "Glass Roads" and b.pinned("genre") == "fantasy"
    assert "created" in capsys.readouterr().out


def test_new_unknown_bible_exits(repo_root, capsys):
    with pytest.raises(SystemExit):
        run("new", "--title", "X", "--bible", "nope")
    assert not (repo_root / "novellas").exists()


def test_beats_command(book, tmp_path):
    f = tmp_path / "b.md"
    f.write_text("- two things happen")
    run("beats", "glasswood", "--chapter", 2, "--title", "Two", "--beats-file", f)
    assert book.chapter(2)["title"] == "Two" and book.beats(2) == "- two things happen\n"


def test_draft_streams_records_spend_and_saves(book, fake_llm, repo_root, capsys, monkeypatch):
    monkeypatch.setenv("MW_PROGRESS", "1")
    fake_llm.replies = [WRITER_REPLY]
    run("draft", book.root, "--chapter", 1, "--note", "More frost.")
    out = capsys.readouterr().out
    assert "Garret listened to the road." in out              # streamed text
    marks = [json.loads(l[len("::progress "):]) for l in out.splitlines()
             if l.startswith("::progress ")]
    assert [m["event"] for m in marks] == ["start", "done"]
    assert marks[-1]["version"] == 1 and marks[-1]["total"] == marks[-1]["done"] == 1
    call = fake_llm.calls[0]
    assert call["model"] == "claude-opus-5" and call["max_tokens"] == 16000
    assert "<author_note>\nMore frost.\n</author_note>" in call["user"]
    assert book.stage(1) == Stage.DRAFTED
    assert book.draft(1).startswith("### The Chiming")
    assert book.writer_meta(1)["note"] == "More frost."
    assert book.manifest()["spend"] == {"anthropic_input_tokens": 1000,
                                        "anthropic_output_tokens": 500}
    rows = ledger_rows(repo_root)
    assert {r["pipeline"] for r in rows} == {"novella"}
    assert {r["stage"] for r in rows} == {"draft"}
    assert {r["story_slug"] for r in rows} == {"glasswood"}
    assert {r["unit_type"] for r in rows} == {"input_tokens", "output_tokens"}


def test_draft_without_progress_env_prints_no_markers(book, fake_llm, capsys, monkeypatch):
    monkeypatch.delenv("MW_PROGRESS", raising=False)
    fake_llm.replies = [WRITER_REPLY]
    run("draft", "glasswood", "--chapter", 1)
    assert "::progress" not in capsys.readouterr().out


def test_draft_refused_after_edit_costs_nothing(book, fake_llm, capsys):
    book.save_draft(1, text="x", summary="", new_details="", flags="")
    book.save_edit(1, "mine")
    with pytest.raises(SystemExit):
        run("draft", "glasswood", "--chapter", 1)
    assert fake_llm.calls == [] and "Send it back" in capsys.readouterr().err


def test_review_lints_and_stores(book, fake_llm, repo_root, capsys):
    book.save_draft(1, text="x", summary="", new_details="", flags="")
    book.save_edit(1, "It was a crucial moment.\n\nShe left.")
    fake_llm.replies = [REVIEW_REPLY]
    run("review", "glasswood", "--chapter", 1)
    r = book.review(1)
    assert book.stage(1) == Stage.EDITOR_REVIEWED
    assert r["sections"]["Must Fix"].startswith('- "It was a crucial')
    assert r["lint"]["errors"] >= 1
    assert any(f["rule"] == "ai_vocab" for f in r["lint"]["findings"])
    assert r["raw"] == REVIEW_REPLY
    assert {x["stage"] for x in ledger_rows(repo_root)} == {"review"}
    assert "Review saved." in capsys.readouterr().out


def _reviewed(book):
    book.save_draft(1, text="x", summary="- s", new_details="- **Hollin Ferry:** a crossing",
                    flags="")
    book.save_edit(1, "e")
    book.save_review(1, REVIEW_REPLY, {})


def test_approve_logs_details_to_changelog_not_bible(book, repo_root, tmp_path):
    _reviewed(book)
    bible_md = repo_root / "bibles" / "glass" / "glass.md"
    before = bible_md.read_text()
    (tmp_path / "s.md").write_text("- Garret hears the road.")
    (tmp_path / "d.md").write_text("- The Mill Pond lies east of Hollin.\n")
    run("approve", "glasswood", "--chapter", 1, "--summary-file", tmp_path / "s.md",
        "--details-file", tmp_path / "d.md", "--author", "Tester")
    assert book.stage(1) == Stage.APPROVED
    assert bible_md.read_text() == before
    log = (repo_root / "bibles" / "glass" / "glass.changelog.md").read_text()
    assert "Tester · novella" in log and "The Mill Pond lies east of Hollin." in log
    assert "Glasswood" in log
    assert book.summary(1) == "- Garret hears the road."


def test_approve_validates_before_writing(cfg, repo_root, tmp_path, capsys):
    b = Bundle.create(cfg["novellas_dir"], "Solo", "Standalone")
    b.save_plan(1, "One", 3000, "- b")
    b.save_draft(1, text="x", summary="", new_details="", flags="")
    b.save_edit(1, "e")
    b.save_review(1, "x", {})
    (tmp_path / "s.md").write_text("- sum")
    (tmp_path / "d.md").write_text("- detail")
    with pytest.raises(SystemExit):
        run("approve", "solo", "--chapter", 1, "--summary-file", tmp_path / "s.md",
            "--details-file", tmp_path / "d.md")
    assert "no series bible" in capsys.readouterr().err
    assert b.stage(1) == Stage.EDITOR_REVIEWED and not b.summary_path(1).exists()


def test_approve_validates_missing_bible(book, repo_root, tmp_path):
    import shutil
    _reviewed(book)
    shutil.rmtree(repo_root / "bibles" / "glass")
    (tmp_path / "s.md").write_text("- sum")
    (tmp_path / "d.md").write_text("- detail")
    with pytest.raises(SystemExit):
        run("approve", "glasswood", "--chapter", 1, "--summary-file", tmp_path / "s.md",
            "--details-file", tmp_path / "d.md")
    assert book.stage(1) == Stage.EDITOR_REVIEWED and not book.summary_path(1).exists()


def test_send_back_and_status(book, capsys):
    _reviewed(book)
    run("send-back", "glasswood", "--chapter", 1, "--to", "drafted")
    assert book.stage(1) == Stage.DRAFTED
    run("status", "glasswood")
    out = capsys.readouterr().out
    assert "Glasswood" in out and "The Chiming" in out and "Drafted" in out
