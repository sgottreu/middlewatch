"""cli.py: argument parsing and dispatch (main), and each command's behaviour with
the paid stages monkeypatched. Everything runs from the scratch repo root, which is
how the CLI is meant to be run (paths resolve against cwd)."""
import json
import sys
import types
from pathlib import Path

import pytest

from story_pipeline import cli, genres, release
from story_pipeline import config as cfgmod
from story_pipeline.agents import text
from story_pipeline.bundle import Bundle
from tests.pipeline.conftest import FakeProvider, make_mini, ns

REAL_SLUG_PATH = "stories/amberlight/the-cracked-beam"


def run(*argv):
    return cli.main(list(argv))


def exit_code(*argv):
    with pytest.raises(SystemExit) as e:
        cli.main(list(argv))
    return e.value.code


# =========================================================================== #
# main(): parsing and dispatch
# =========================================================================== #

COMMANDS = {
    ("new",): "cmd_new", ("history",): "cmd_history", ("review",): "cmd_review",
    ("voices",): "cmd_voices", ("bibles",): "cmd_bibles", ("genres",): "cmd_genres",
    ("bible", "new", "x", "--genre", "regency"): "cmd_bible_new",
    ("bible", "check"): "cmd_bible_check", ("bible", "log", "x"): "cmd_bible_log",
    ("bible", "promote", "x"): "cmd_bible_promote",
    ("revise", "s", "fb"): "cmd_revise", ("approve", "s"): "cmd_approve",
    ("publish", "s", "u"): "cmd_publish", ("write", "s"): "cmd_write",
    ("redraft", "s", "--chapter", "1"): "cmd_redraft", ("design", "s"): "cmd_design",
    ("status", "s"): "cmd_status", ("lint", "s"): "cmd_lint", ("render", "s"): "cmd_render",
    ("attribute", "s"): "cmd_attribute", ("tts-check",): "cmd_tts_check",
    ("critique", "s"): "cmd_critique", ("record", "s"): "cmd_record",
    ("direct", "s"): "cmd_direct", ("archive", "s"): "cmd_archive", ("all",): "cmd_all",
}


@pytest.fixture
def spy(monkeypatch):
    got = {}
    names = {n for n in dir(cli) if n.startswith("cmd_")} | {"cmd_archive"}
    for n in sorted(names):
        if n == "cmd_archive":
            monkeypatch.setattr(cli.archive, "cmd_archive", lambda a, c, n=n: got.update(name=n, args=a, cfg=c))
        else:
            monkeypatch.setattr(cli, n, lambda a, c, n=n: got.update(name=n, args=a, cfg=c))
    return got


@pytest.mark.parametrize("argv,fn", list(COMMANDS.items()), ids=lambda v: v if isinstance(v, str) else "-".join(v))
def test_dispatch(spy, argv, fn):
    run(*argv)
    assert spy["name"] == fn


def test_no_command_is_a_usage_error(capsys):
    assert exit_code() == 2
    assert "required" in capsys.readouterr().err


def test_unknown_command(capsys):
    assert exit_code("frobnicate") == 2


def test_main_loads_env_then_config(spy, monkeypatch, repo_root):
    order = []
    monkeypatch.setattr(cli.cfgmod, "load_env", lambda: order.append("env") or [])
    real_load = cfgmod.load
    monkeypatch.setattr(cli.cfgmod, "load", lambda path: order.append(("cfg", path)) or real_load(path))
    run("bibles")
    assert order == ["env", ("cfg", "config.yaml")]
    order.clear()
    (repo_root / "other.yaml").write_text("length_min: 30\n")
    run("--config", "other.yaml", "bibles")
    assert order[1] == ("cfg", "other.yaml") and spy["cfg"]["length_min"] == 30


def test_main_dotenv_in_cwd_is_read(spy, repo_root, monkeypatch):
    monkeypatch.delenv("MW_TEST_VAR", raising=False)
    (repo_root / ".env").write_text("MW_TEST_VAR=hello\n")
    run("bibles")
    import os
    assert os.environ["MW_TEST_VAR"] == "hello"
    monkeypatch.delenv("MW_TEST_VAR")


def test_main_passes_config_with_defaults_when_no_file(spy):
    run("bibles")
    assert spy["cfg"]["stories_dir"] == "stories" and spy["cfg"]["tts_provider"] == "elevenlabs"
    assert "Charlotte" in spy["cfg"]["elevenlabs"]["voice_library"]      # voices.yaml merged


def test_new_args(spy):
    run("new", "--genre", "regency", "--bible", "amberlight", "--subgenre", "x", "--notes", "n", "--length", "30")
    a = spy["args"]
    assert (a.genre, a.bible, a.subgenre, a.notes, a.length) == ("regency", "amberlight", "x", "n", 30)


@pytest.mark.parametrize("argv", [("new", "--genre", "nonsense"), ("new", "--length", "45"),
                                  ("new", "--length", "60"), ("all", "--length", "10"),
                                  ("bible", "new", "x"), ("bible", "new", "x", "--genre", "zzz"),
                                  ("redraft", "s"), ("history", "--genre", "zzz")])
def test_argument_errors_exit_2(spy, argv):
    assert exit_code(*argv) == 2
    assert "name" not in spy


def test_approve_flags(spy):
    run("approve", "s", "--chapter", "2", "--chapter", "5", "--video", "--undo")
    a = spy["args"]
    assert a.chapter == [2, 5] and a.video and a.undo and not a.chapters


def test_publish_args(spy):
    run("publish", "s", "--date", "2026-01-01", "--force")
    a = spy["args"]
    assert a.url is None and a.date == "2026-01-01" and a.force and not a.undo


def test_attribute_tags_tristate(spy):
    run("attribute", "s")
    assert spy["args"].tags is None
    run("attribute", "s", "--tags")
    assert spy["args"].tags is True
    run("attribute", "s", "--no-tags", "-y", "--chapter", "3")
    a = spy["args"]
    assert a.tags is False and a.yes and a.chapter == [3]


def test_review_args(spy):
    run("review", "--port", "9000", "--no-open", "--reload")
    a = spy["args"]
    assert (a.port, a.no_open, a.reload) == (9000, True, True)
    run("review")
    assert spy["args"].port == 8765 and not spy["args"].no_open


def test_bible_subcommand_args(spy):
    run("bible", "log", "amberlight", "-m", "msg", "--kind", "canon", "--limit", "3", "--author", "me")
    a = spy["args"]
    assert (a.name, a.message, a.kind, a.limit, a.author) == ("amberlight", "msg", "canon", 3, "me")
    run("bible", "promote", "x", "--dry-run", "--json", "--yes", "--bibles-dir", "b", "--author", "z")
    a = spy["args"]
    assert a.dry_run and a.json and a.yes and a.bibles_dir == "b" and a.author == "z"
    run("bible", "new", "x", "--genre", "regency", "--series", "S", "--example", "--author", "me")
    a = spy["args"]
    assert (a.series, a.example, a.author) == ("S", True, "me")


def test_archive_args(spy):
    run("archive", "slug", "--arc", "a", "--dry-run")
    assert spy["args"].slug == "slug" and spy["args"].arc == "a" and spy["args"].dry_run


def test_bundle_helper_opens_path(mini):
    assert cli._bundle(ns(story=str(mini.root))).root == mini.root
    with pytest.raises(FileNotFoundError):
        cli._bundle(ns(story="stories/nope/nope"))


def test_progress_marker_gated_by_env(monkeypatch, capsys):
    cli._progress(event="x", n=1)
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("MW_PROGRESS", "1")
    cli._progress(event="x", n=1)
    assert capsys.readouterr().out == '::progress {"event": "x", "n": 1}\n'


def test_read_notes(tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("  from file \n")
    assert cli._read_notes(ns(notes_file=str(f), notes="inline")) == "from file"
    assert cli._read_notes(ns(notes_file=None, notes="inline")) == "inline"
    assert cli._read_notes(ns()) is None


# =========================================================================== #
# free commands against real data
# =========================================================================== #

def test_cmd_bibles_none(capsys):
    run("bibles")
    out = capsys.readouterr().out
    assert "No bibles in bibles/." in out and "bible new amberlight --genre regency" in out


def test_cmd_bibles_real(real_bibles, capsys):
    run("bibles")
    out = capsys.readouterr().out
    assert "amberlight  (Middle Watch: Amberlight)  [regency]" in out
    assert "canon facts:    18" in out and "recurring cast: 6" in out
    assert "- Mr Silas Rowe, 34 [voice Sam]  (portrait)" in out
    assert "- Mr Owen Vane, 30 [voice Archer]  (no portrait yet)" in out


def test_cmd_genres(capsys):
    run("genres")
    out = capsys.readouterr().out
    assert "regency" in out and "subgenres:" in out and "tropes:" in out
    assert "    - " not in out
    run("genres", "--tropes")
    assert "    - Enemies of manner:" in capsys.readouterr().out


def test_bible_new_scaffold_and_check_roundtrip(repo_root, capsys):
    run("bible", "new", "fresh", "--genre", "regency", "--series", "Fresh Series", "--author", "tester")
    out = capsys.readouterr().out
    assert "Wrote bibles/fresh/fresh.md" in out or "Wrote bibles" in out
    assert "bible check fresh" in out and "new --bible fresh" in out
    assert "Fill in canon" in out
    path = next((repo_root / "bibles").rglob("fresh.md"))
    assert path.exists()
    assert next((repo_root / "bibles").rglob("fresh.changelog.md")).exists()


def test_bible_new_example(repo_root, capsys):
    run("bible", "new", "demo", "--genre", "regency", "--example", "--author", "tester")
    out = capsys.readouterr().out
    assert "worked example" in out or "Filled in as a worked example" in out
    run("bible", "check", "demo")
    assert "demo: ok" in capsys.readouterr().out


def test_bible_check_real_ok_and_unknown(real_bibles, capsys):
    run("bible", "check")
    assert "amberlight: ok" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        run("bible", "check", "ghost")
    assert "no bible 'ghost'" in str(e.value)


def test_bible_check_none(capsys):
    run("bible", "check")
    assert "No bibles in bibles/." in capsys.readouterr().out


def test_bible_check_errors_exit_1(repo_root, capsys):
    d = repo_root / "bibles" / "bad"
    d.mkdir()
    (d / "bad.md").write_text("---\nseries: Bad\ngenre: nonexistent-genre\n---\n\nbody\n")
    code = exit_code("bible", "check", "bad")
    out = capsys.readouterr().out
    assert code == 1 and "error(s)" in out and "Errors will stop `new --bible`" in out


def test_bible_log_show_and_append(real_bibles, capsys):
    run("bible", "log", "amberlight", "--limit", "2")
    out = capsys.readouterr().out
    assert out.count("sha=") == 2
    run("bible", "log", "amberlight", "-m", "Added a test line", "--author", "tester", "--kind", "canon")
    out = capsys.readouterr().out
    assert "Logged to " in out and "as tester." in out
    run("bible", "log", "amberlight", "--limit", "1")
    out = capsys.readouterr().out
    assert "tester" in out and "[canon]" in out and "Added a test line" in out


def test_bible_log_unknown_bible():
    with pytest.raises(SystemExit) as e:
        run("bible", "log", "ghost")
    assert "no bible 'ghost'" in str(e.value)


def test_bible_log_empty_history(repo_root, capsys):
    run("bible", "new", "e", "--genre", "regency", "--author", "t")
    capsys.readouterr()
    log = next((repo_root / "bibles").rglob("e.changelog.md"))
    log.unlink()
    run("bible", "log", "e")
    assert "No changelog for e yet." in capsys.readouterr().out


def test_bible_promote_dry_run_json(real_bibles, sample_draft, capsys):
    assert exit_code("bible", "promote", sample_draft, "--dry-run", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == sample_draft and data["blocked"] is False and data["changed"] is True
    assert len(data["cast"]) == 3 and data["series"] == "Salt Roads"
    assert not (real_bibles / sample_draft / f"{sample_draft}.md").exists()


def test_bible_promote_dry_run_progress_marker(real_bibles, sample_draft, capsys, monkeypatch):
    monkeypatch.setenv("MW_PROGRESS", "1")
    assert exit_code("bible", "promote", sample_draft, "--dry-run", "--json") == 0
    line = capsys.readouterr().out.strip()
    payload = json.loads(line[len("::progress "):])
    assert payload["event"] == "preview" and payload["preview"]["name"] == sample_draft


def test_bible_promote_dry_run_text(real_bibles, sample_draft, capsys):
    assert exit_code("bible", "promote", sample_draft, "--dry-run") == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{sample_draft}: Salt Roads  [fantasy]")
    assert "recurring cast: 3" in out and "- Hal Venn: carter, male" in out
    assert "kept:    Premise -> prose" in out and "dropped: " in out


def test_bible_promote_writes_with_yes(real_bibles, sample_draft, capsys):
    run("bible", "promote", sample_draft, "--yes", "--author", "tester")
    assert "Wrote " in capsys.readouterr().out
    assert (real_bibles / sample_draft / f"{sample_draft}.md").exists()
    run("bible", "promote", sample_draft, "--yes")
    assert "No change: the bible already matches the draft." in capsys.readouterr().out


def test_bible_promote_refuses_without_tty(real_bibles, sample_draft, monkeypatch):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit) as e:
        run("bible", "promote", sample_draft)
    assert str(e.value) == "Not written."
    assert not (real_bibles / sample_draft / f"{sample_draft}.md").exists()


def test_bible_promote_unknown(real_bibles):
    with pytest.raises(SystemExit):
        run("bible", "promote", "ghost")


def test_history_empty(capsys):
    run("history")
    out = capsys.readouterr().out
    assert "No approved stories yet." in out and "costs nothing" in out
    run("history", "--bible", "amberlight")
    assert "No approved stories in amberlight yet." in capsys.readouterr().out


def test_history_real(story, capsys):
    run("history")
    out = capsys.readouterr().out
    assert out.startswith("1 approved story\n")
    assert "Tropes used" in out and "pass --genre" in out and "Protagonists" in out
    run("history", "--genre", "regency", "--bible", "amberlight")
    out = capsys.readouterr().out
    assert "1 approved story in amberlight" in out and "Tropes — regency (16 available, 8 shown per run)" in out
    assert "odds vs unused" in out
    first_trope = [l for l in out.splitlines() if l.startswith("  ") and "0.50" in l]
    assert first_trope                                                  # used once => weight 0.50


def test_cmd_review_passes_config_path_and_flags(monkeypatch):
    got = {}
    monkeypatch.setattr(cli, "serve_review", lambda cfg, port, open_browser, reload: got.update(
        cfg=cfg, port=port, open=open_browser, reload=reload))
    run("--config", "my.yaml", "review", "--port", "1234", "--no-open")
    assert got["port"] == 1234 and got["open"] is False and got["reload"] is False
    assert got["cfg"]["_config_path"] == "my.yaml"


# =========================================================================== #
# status
# =========================================================================== #

def test_status_real_story(story, capsys):
    run("status", str(story.root))
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "the-cracked-beam"
    for stage, st in [("ideate", "approved"), ("write", "done"), ("edit", "approved"), ("record", "done"),
                      ("design", "done"), ("direct", "done"), ("approve", "approved"), ("publish", "pending")]:
        assert f"  {stage:8} {st}" in out
    assert " ch   ok   words  target  drafts  verdict" in out
    assert "  1   ok     649     570       3  passed (7)  +14%" in out
    assert "5,113 words ~34 min against a 30-minute target (+14%)" in out
    assert "Approved: 8 of 8" in out and "Reviews: " in out
    assert "5,113 words narrated in 30.8 min" in out            # calibration block
    assert "Spend:\n  elevenlabs_credits 78275.0" in out and "  gemini_images    41.0" in out
    assert "Casting:\n  narrator             Samara X" in out
    assert "  Mrs Hale             Elizabeth" in out
    assert out.rstrip().endswith(f"Summary: {story.root}/story.md")


def test_status_no_chapters_written_prints_no_table(mini, capsys):
    for n in (1, 2):
        mini.chapter_json(n).unlink()
    run("status", str(mini.root))
    out = capsys.readouterr().out
    assert out.startswith("mini\n\n") and "  ideate   pending" in out
    assert "ch   ok" not in out and "Spend" not in out and "Casting" not in out


def test_status_published_and_unwritten_rows(mini, capsys):
    mini.chapter_json(2).unlink()
    mini.set_stage("publish", "done", url="https://youtu.be/abc", published="2026-02-03")
    mini.add_spend("gemini_images", 2)
    run("status", str(mini.root))
    out = capsys.readouterr().out
    assert "https://youtu.be/abc (2026-02-03)" in out
    import re
    assert re.search(r"^\s+2\s+—\s+not written$", out, re.M)
    assert re.search(r"^\s+1\s+6\s+100\s+0\s+written, not reviewed", out, re.M)
    assert "Approved: 0 of 1" in out and "gemini_images" in out


def test_status_verdicts_and_lapsed_and_stale(mini, capsys, cfg):
    mini.approve_chapter(1)
    c = mini.chapter(1)
    c["segments"][0]["text"] += " Edited after approval."
    mini.chapter_json(1).write_text(json.dumps(c))
    mini.review_path(1).write_text(json.dumps({"pass": False, "fixes": ["a", "b"]}))
    mini.review_path(2).write_text(json.dumps({"pass": False, "exhausted": True}))
    run("status", str(mini.root))
    out = capsys.readouterr().out
    assert "FAILED (2 fixes outstanding)" in out
    assert "kept after 2 revisions — still failing" in out
    assert "!" in out and "approved then rewritten: [1]" in out


def test_status_corrupt_review_file_is_ignored(mini, capsys):
    mini.review_path(1).write_text("{not json")
    run("status", str(mini.root))
    assert "written, not reviewed" in capsys.readouterr().out


def test_status_missing_manifest(repo_root):
    with pytest.raises(FileNotFoundError):
        run("status", "stories/standalone/nothing")


def test_status_narration_stale_flag(story, capsys):
    c = story.chapter(1)
    c["segments"][0]["text"] += " Changed."
    story.chapter_json(1).write_text(json.dumps(c))
    run("status", str(story.root))
    assert "narration stale" in capsys.readouterr().out


# =========================================================================== #
# _show_outline / new / revise
# =========================================================================== #

def test_show_outline_real(story, capsys):
    cli._show_outline(story, story.outline())
    out = capsys.readouterr().out
    assert "\nThe Cracked Beam\nMiddle Watch: Amberlight\nregency / " in out
    assert "Cast:\n  Mr Silas Rowe, 34 (protagonist) [recurring] — " in out
    assert "Chapters:\n  1. " in out and "(570w)" in out
    assert "Planned: 4,500 words across 8 chapters ~30 min (target 30)" in out
    assert "Density: " in out and "(asked for 4-5 per chapter)" in out
    assert f"Bundle: {story.root}" in out
    assert f"cli revise  {story.root}" in out and f"cli approve {story.root}" in out
    assert "Under 18" not in out and "contradicted" not in out


def test_show_outline_warnings(mini, capsys):
    o = mini.outline()
    o["cast"][1]["age"] = 15
    o["bible_conflict"] = "she is dead in canon"
    o["notes_conflict"] = "omit"
    o["promise"] = "A promise."
    o["chapters"][0]["target_words"] = 1000
    cli._show_outline(mini, o)
    out = capsys.readouterr().out
    assert "! Your notes contradicted the series bible: she is dead in canon" in out
    assert "content boundaries" not in out                     # "omit" is not an answer
    assert "! Under 18 in this cast: Bob Ray (15, supporting)" in out
    assert "Promise: A promise." in out
    assert "<- thin; the prose will stretch to fill this" in out


def test_cmd_new_calls_ideate_and_summarises(mini, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(cli.text, "ideate", lambda cfg, sd, genre, sub, notes, bible, length_min: (
        seen.update(args=(sd, genre, sub, notes, bible, length_min)), mini)[1])
    run("new", "--genre", "regency", "--notes", "hello", "--length", "30")
    assert seen["args"] == ("stories", "regency", None, "hello", None, 30)
    assert mini.story_md_path.exists()
    assert "Mini" in capsys.readouterr().out


def test_cmd_revise(mini, monkeypatch, capsys):
    monkeypatch.setattr(cli.text, "revise_outline", lambda cfg, b, fb: (
        capsys.readouterr() and None) or {**mini.outline(), "title": "Revised " + fb})
    run("revise", str(mini.root), "older")
    out = capsys.readouterr().out
    assert "Revised older" in out and mini.story_md_path.exists()


def test_cmd_all_runs_every_stage_in_order(mini, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.text, "ideate", lambda *a, **k: calls.append("ideate") or mini)
    monkeypatch.setattr(cli.text, "run_text_stages", lambda cfg, b: calls.append("text"))
    monkeypatch.setattr(cli.actor, "estimate", lambda cfg, b: {"words": 1, "runtime_min": 0.1, "usd": 0.5})
    monkeypatch.setattr(cli.actor, "record_all", lambda cfg, b, force: calls.append(("record", force)))
    monkeypatch.setattr(cli.designer, "design_all", lambda cfg, b: calls.append("design"))
    monkeypatch.setattr(cli.director, "direct", lambda cfg, b: calls.append("direct"))
    run("all", "--genre", "regency")                      # goes through the real parser
    assert calls == ["ideate", "text", ("record", True), "design", "direct"]
    assert mini.stage_status("ideate") == "approved"                # no review gate: approved for you
    assert "Done: " in capsys.readouterr().out


# =========================================================================== #
# approve
# =========================================================================== #

def test_approve_outline(mini, capsys):
    run("approve", str(mini.root))
    assert mini.stage_status("ideate") == "approved"
    assert f"Approved. Next: python3 -m story_pipeline.cli write {mini.root}" in capsys.readouterr().out
    assert mini.story_md_path.exists()


def test_approve_all_chapters(mini, capsys):
    run("approve", str(mini.root), "--chapters")
    assert mini.all_chapters_approved() and mini.stage_status("edit") == "approved"
    assert "All chapters approved. Next:" in capsys.readouterr().out


def test_approve_one_chapter_reports_remainder(mini, capsys):
    run("approve", str(mini.root), "--chapter", "1")
    out = capsys.readouterr().out
    assert mini.approved_chapters() == [1] and mini.stage_status("edit") == "awaiting_review"
    assert "Approved 1 of 2. Still to read: 2" in out and "--chapter 2" in out


def test_approve_chapter_ignores_unwritten(mini, capsys):
    mini.chapter_json(2).unlink()
    run("approve", str(mini.root), "--chapter", "2")
    assert mini.approved_chapters() == []
    assert "Approved 0 of 1" in capsys.readouterr().out


def test_approve_edit_stage_records_unpassed(mini):
    mini.review_path(2).write_text(json.dumps({"pass": False}))
    run("approve", str(mini.root), "--chapters")
    assert mini.manifest()["stage_info"]["edit"] == {"unpassed": [2]}


def test_approve_undo_without_video_exits(mini):
    with pytest.raises(SystemExit) as e:
        run("approve", str(mini.root), "--undo")
    assert "--undo only applies with --video" in str(e.value)


def test_approve_video_flow(mini, capsys):
    with pytest.raises(SystemExit) as e:
        run("approve", str(mini.root), "--video")
    assert "no rendered video" in str(e.value)
    mini.set_stage("direct", "done")
    run("approve", str(mini.root), "--video")
    out = capsys.readouterr().out
    assert mini.stage_status("approve") == "approved"
    assert f"publish {mini.root} <youtube-url>" in out
    run("approve", str(mini.root), "--video", "--undo")
    assert mini.stage_status("approve") == "awaiting_review"
    assert "Video approval withdrawn." in capsys.readouterr().out


# =========================================================================== #
# publish
# =========================================================================== #

def test_publish_requires_url(mini):
    with pytest.raises(SystemExit) as e:
        run("publish", str(mini.root))
    assert "give the YouTube URL" in str(e.value)


def test_publish_gate_and_success(mini, capsys):
    with pytest.raises(SystemExit) as e:
        run("publish", str(mini.root), "dQw4w9WgXcQ")
    assert "approve the video before publishing" in str(e.value)
    run("publish", str(mini.root), "dQw4w9WgXcQ", "--force", "--date", "2026-05-06")
    assert "Published 2026-05-06: https://youtu.be/dQw4w9WgXcQ" in capsys.readouterr().out
    assert mini.stage_status("publish") == "done" and mini.story_md_path.exists()


def test_publish_bad_url_exits(mini):
    mini.set_stage("approve", "approved")
    with pytest.raises(SystemExit) as e:
        run("publish", str(mini.root), "https://example.com")
    assert "not a YouTube video URL" in str(e.value)


def test_publish_undo_needs_no_url(mini, capsys):
    mini.set_stage("approve", "approved")
    run("publish", str(mini.root), "dQw4w9WgXcQ")
    run("publish", str(mini.root), "--undo")
    assert "Marked unpublished." in capsys.readouterr().out
    assert mini.stage_status("publish") == "pending"


# =========================================================================== #
# write
# =========================================================================== #

def test_write_blocked_until_outline_approved(mini, monkeypatch):
    monkeypatch.setattr(cli.text, "run_text_stages", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as e:
        run("write", str(mini.root))
    msg = str(e.value)
    assert "hasn't been approved" in msg and "or pass --force" in msg and str(mini.root) in msg


def test_write_runs_and_summarises(mini, monkeypatch, capsys):
    mini.set_stage("ideate", "approved")
    seen = {}

    def fake(cfg, b, restart, on_chapter):
        seen["restart"] = restart
        on_chapter(1, 2, {"pass": True, "score": 8})
        on_chapter(2, 2, {"pass": False, "score": 5, "exhausted": True})
        return {1: {"pass": True}, 2: {"pass": False}}
    monkeypatch.setattr(cli.text, "run_text_stages", fake)
    monkeypatch.setenv("MW_PROGRESS", "1")
    run("write", str(mini.root), "--restart")
    out = capsys.readouterr().out
    assert seen["restart"] is True
    assert '::progress {"event": "chapter", "n": 1, "total": 2, "ok": true, "score": 8, "exhausted": false}' in out
    assert '"n": 2, "total": 2, "ok": false, "score": 5, "exhausted": true' in out
    assert "12 words across 2 chapters" in out
    assert "Chapters still failing review: [2]" in out and "record " in out and "--dry-run" in out
    assert mini.story_md_path.exists()


def test_write_force_skips_gate(mini, monkeypatch):
    monkeypatch.setattr(cli.text, "run_text_stages", lambda *a, **k: {})
    run("write", str(mini.root), "--force")


def test_write_failure_resets_stage_and_reraises(mini, monkeypatch):
    mini.set_stage("ideate", "approved")

    def boom(cfg, b, restart, on_chapter):
        b.set_stage("write", "running")
        raise RuntimeError("model down")
    monkeypatch.setattr(cli.text, "run_text_stages", boom)
    with pytest.raises(RuntimeError, match="model down"):
        run("write", str(mini.root))
    assert mini.stage_status("write") == "pending" and mini.story_md_path.exists()


# =========================================================================== #
# record
# =========================================================================== #

def test_record_blocked_until_chapters_approved(mini, monkeypatch):
    monkeypatch.setattr(cli.actor, "record_all", lambda *a, **k: pytest.fail("must not run"))
    mini.approve_chapter(1)
    c = mini.chapter(1)
    c["segments"][0]["text"] += " changed"
    mini.chapter_json(1).write_text(json.dumps(c))               # lapsed approval
    mini.set_stage("edit", "awaiting_review", unpassed=[2])
    with pytest.raises(SystemExit) as e:
        run("record", str(mini.root))
    msg = str(e.value)
    assert "0 of 2 chapters approved. Not yet: 1, 2" in msg
    assert "Changed since you approved them: [1]" in msg
    assert "Chapters that failed review: [2]" in msg
    assert f"approve {mini.root} --chapter 1" in msg and "--force to narrate them anyway" in msg


def test_record_dry_run_allowed_without_approval(mini, capsys, monkeypatch):
    monkeypatch.setattr(cli.actor, "record_all", lambda *a, **k: pytest.fail("must not run"))
    run("record", str(mini.root), "--dry-run")
    out = capsys.readouterr().out
    assert "12 words / 56 characters" in out
    assert "~0.1 min narration on eleven_multilingual_v2" in out
    assert "56 credits, about $0.01" in out and "Dry run. Nothing synthesized." in out


def test_record_dry_run_real_story(story, capsys):
    run("record", str(story.root), "--dry-run")
    out = capsys.readouterr().out
    assert f"{story.word_count()} words / " in out and "credits, about $" in out


def test_record_runs_when_approved(mini, monkeypatch, capsys):
    mini.approve_chapter(1)
    mini.approve_chapter(2)
    seen = {}
    monkeypatch.setattr(cli.actor, "record_all", lambda cfg, b, force: seen.update(force=force))
    run("record", str(mini.root))
    assert seen == {"force": False} and mini.story_md_path.exists()
    assert "Next:   python3 -m story_pipeline.cli design" in capsys.readouterr().out


def test_record_force_passes_through(mini, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.actor, "record_all", lambda cfg, b, force: seen.update(force=force))
    run("record", str(mini.root), "--force")
    assert seen == {"force": True}


# =========================================================================== #
# design / direct / redraft
# =========================================================================== #

def test_design_passes_replan(mini, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli.designer, "design_all", lambda cfg, b, replan: seen.append(replan))
    run("design", str(mini.root))
    run("design", str(mini.root), "--replan")
    assert seen == [False, True]
    assert "Next:   python3 -m story_pipeline.cli direct" in capsys.readouterr().out


def test_direct_refuses_to_overwrite(mini, monkeypatch):
    monkeypatch.setattr(cli.director, "direct", lambda *a, **k: pytest.fail("must not run"))
    mini.video_path.write_bytes(b"v")
    with pytest.raises(SystemExit) as e:
        run("direct", str(mini.root))
    assert "exists. Pass --force to overwrite." in str(e.value)


def test_direct_force_and_fresh(mini, monkeypatch):
    seen = []
    monkeypatch.setattr(cli.director, "direct", lambda cfg, b: seen.append(b.root))
    run("direct", str(mini.root))
    mini.video_path.write_bytes(b"v")
    run("direct", str(mini.root), "--force")
    assert seen == [mini.root, mini.root]


def test_redraft_note_inline_and_file(mini, monkeypatch, tmp_path, capsys):
    seen = []

    def fake(cfg, b, n, note, on_pass):
        seen.append((n, note))
        on_pass(1, {"pass": False, "score": 4})
        return {"pass": True, "score": 8}
    monkeypatch.setattr(cli.text, "redraft_chapter", fake)
    monkeypatch.setenv("MW_PROGRESS", "1")
    run("redraft", str(mini.root), "--chapter", "1", "--note", "shorter")
    out = capsys.readouterr().out
    assert seen == [(1, "shorter")]
    assert '::progress {"event": "pass", "attempt": 1, "ok": false, "score": 4, "words": 6}' in out
    assert "Chapter 1: passed (8)" in out and "Chapters [2] were written" in out
    f = tmp_path / "note.txt"
    f.write_text("from file\n")
    run("redraft", str(mini.root), "--chapter", "2", "--note-file", str(f))
    assert seen[-1] == (2, "from file\n")
    assert "Chapters [" not in capsys.readouterr().out.split("Chapter 2:")[1]


def test_redraft_failing_verdict(mini, monkeypatch, capsys):
    monkeypatch.setattr(cli.text, "redraft_chapter", lambda *a, **k: {"pass": False})
    run("redraft", str(mini.root), "--chapter", "2")
    assert "Chapter 2: still failing review" in capsys.readouterr().out


# =========================================================================== #
# attribute
# =========================================================================== #

@pytest.fixture
def attr(monkeypatch):
    calls = {"redraft": []}
    monkeypatch.setattr(cli.text, "chapter_dialogue_turns", lambda b, n: 3 if n == 1 else 0)
    monkeypatch.setattr(cli.text, "estimate_redraft", lambda cfg, b, n: {"known": True, "low": 0.5, "high": 1.5})
    monkeypatch.setattr(cli.text, "attribution_note", lambda tags: f"NOTE tags={tags}")

    def redraft(cfg, b, n, note):
        calls["redraft"].append((n, note))
        return {"pass": True, "score": 9}
    monkeypatch.setattr(cli.text, "redraft_chapter", redraft)
    return calls


def test_attribute_nothing_to_do(mini, monkeypatch):
    monkeypatch.setattr(cli.text, "chapter_dialogue_turns", lambda b, n: 0)
    with pytest.raises(SystemExit) as e:
        run("attribute", str(mini.root))
    assert str(e.value) == "Nothing to attribute."


def test_attribute_dry_run(mini, attr, capsys):
    run("attribute", str(mini.root), "--dry-run")
    out = capsys.readouterr().out
    assert "No dialogue, skipping: 2" in out
    assert "audio tags: not asked for — eleven_multilingual_v2 strips them (--tags to ask anyway)" in out
    assert "1 chapter(s) to rewrite: 1" in out and "about $0.50, up to $1.50" in out
    assert "Dry run. Nothing rewritten." in out and attr["redraft"] == []


def test_attribute_tags_flags(mini, attr, capsys):
    run("attribute", str(mini.root), "--dry-run", "--tags")
    assert "forced with --tags; this model strips them" in capsys.readouterr().out
    run("attribute", str(mini.root), "--dry-run", "--no-tags")
    assert "strips them (--tags to ask anyway)" in capsys.readouterr().out
    Path("config.yaml").write_text("elevenlabs:\n  model: eleven_v3\n")
    run("attribute", str(mini.root), "--dry-run", "--no-tags")
    assert "you passed --no-tags" in capsys.readouterr().out


def test_attribute_tags_default_follows_model(mini, attr, capsys, tmp_path):
    (Path("config.yaml")).write_text("elevenlabs:\n  model: eleven_v3\n")
    run("attribute", str(mini.root), "--dry-run")
    out = capsys.readouterr().out
    assert "audio tags: asked for — eleven_v3 performs them" in out


def test_attribute_cost_unknown(mini, attr, monkeypatch, capsys):
    monkeypatch.setattr(cli.text, "estimate_redraft", lambda cfg, b, n: {"known": False})
    run("attribute", str(mini.root), "--dry-run")
    assert "cost unknown — ledger/rates.json has no price" in capsys.readouterr().out


def test_attribute_declined_prompt(mini, attr, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda p: "n")
    run("attribute", str(mini.root))
    assert "stopped." in capsys.readouterr().out and attr["redraft"] == []


def test_attribute_confirmed_runs_and_continues_past_failure(mini, attr, monkeypatch, capsys):
    monkeypatch.setattr(cli.text, "chapter_dialogue_turns", lambda b, n: 2)

    def redraft(cfg, b, n, note):
        attr["redraft"].append((n, note))
        if n == 1:
            raise RuntimeError("bad chapter")
        return {"pass": False, "score": 3}
    monkeypatch.setattr(cli.text, "redraft_chapter", redraft)
    monkeypatch.setattr("builtins.input", lambda p: "yes")
    run("attribute", str(mini.root))
    out = capsys.readouterr().out
    assert [n for n, _ in attr["redraft"]] == [1, 2]
    assert "failed: RuntimeError: bad chapter" in out and "FAILED (3)" in out
    assert "1 of 2 rewritten." in out and mini.story_md_path.exists()


def test_attribute_yes_skips_prompt_and_chapter_filter(mini, attr, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail("no prompt with --yes"))
    run("attribute", str(mini.root), "-y", "--chapter", "1", "--chapter", "9")
    assert attr["redraft"] == [(1, "NOTE tags=False")]
    assert "1 of 1 rewritten." in capsys.readouterr().out


# =========================================================================== #
# tts-check / voices
# =========================================================================== #

class ProbeProvider(FakeProvider):
    def __init__(self, tags=False, context=True):
        super().__init__()
        self.tags, self.context = tags, context
        self.probed = []

    def probe(self, text, voice):
        self.probed.append((text, voice))
        return {"pcm": b"\x00\x00", "sample_rate": 24000, "billed": 12, "duration_ms": 1500,
                "sentences": [{"start_ms": 0, "end_ms": 1500, "text": "Hello."}]}


def test_tts_check_untagged(monkeypatch, capsys, repo_root):
    prov = ProbeProvider()
    monkeypatch.setattr(cli.tts, "build", lambda cfg: prov)
    encoded = []
    monkeypatch.setattr(cli.actor, "_encode", lambda pcm, rate, out: encoded.append((pcm, rate, out)))
    run("tts-check", "--text", "Hello.")
    out = capsys.readouterr().out
    assert prov.probed == [("Hello.", "Charlotte")]
    assert "model:  eleven_multilingual_v2" in out and "STRIPPED before sending" in out
    assert "joins:  previous/next text sent" in out
    assert "alignment returned: yes (1 sentence(s))" in out and "billed: 12 credits, 1.5s of audio" in out
    assert "[     0 -   1500ms] Hello." in out
    assert encoded[0][2] == Path("auditions") / "tts-check-eleven_multilingual_v2-Charlotte.mp3"
    assert "was stripped before sending" in out or "stripped before sending" in out


def test_tts_check_tagged_no_context_and_voice_override(monkeypatch, capsys):
    prov = ProbeProvider(tags=True, context=False)
    monkeypatch.setattr(cli.tts, "build", lambda cfg: prov)
    monkeypatch.setattr(cli.actor, "_encode", lambda *a: None)
    run("tts-check", "--voice", "Daniel")
    out = capsys.readouterr().out
    assert prov.probed[0][1] == "Daniel" and prov.probed[0][0].startswith("[quietly] Old Mr Fenner")
    assert "sent — this model performs them" in out and "no continuity fields" in out
    assert "A tag was sent." in out


def test_tts_check_provider_without_probe(monkeypatch):
    monkeypatch.setattr(cli.tts, "build", lambda cfg: FakeProvider())
    with pytest.raises(SystemExit) as e:
        run("tts-check")
    assert "fake has no probe" in str(e.value)


def test_voices_lists_and_flags_library_matches(monkeypatch, capsys, cfg):
    prov = FakeProvider()
    lib_id = cfg["elevenlabs"]["voice_library"]["Daniel"]
    prov.list_voices = lambda: [
        {"name": "Dan", "voice_id": lib_id, "labels": {"accent": "british", "gender": "male"}},
        {"name": "Other", "voice_id": "zzz", "labels": None}]
    monkeypatch.setattr(cli.tts, "build", lambda c: prov)
    run("voices")
    out = capsys.readouterr().out
    assert f"Dan                  {lib_id}  british, male  [voice_library: Daniel]" in out
    assert "Other                zzz  " in out and "[voice_library" not in out.splitlines()[1]


def test_voices_unsupported_provider(monkeypatch):
    monkeypatch.setattr(cli.tts, "build", lambda c: FakeProvider())
    with pytest.raises(SystemExit) as e:
        run("voices")
    assert "has no voice listing" in str(e.value)


# =========================================================================== #
# lint / render / critique
# =========================================================================== #

def test_lint_clean_exits_zero(mini, capsys):
    assert exit_code("lint", str(mini.root)) == 0
    out = capsys.readouterr().out
    assert "chapter 1: clean" in out and "chapter 2: clean" in out


def test_lint_reports_errors_and_exits_1(mini, capsys):
    c = mini.chapter(1)
    c["segments"][0]["text"] = ("She delved into the intricate tapestry of the vibrant moment, "
                                "underscoring the pivotal testament.")
    mini.chapter_json(1).write_text(json.dumps(c))
    mini.chapter_json(2).unlink()
    assert exit_code("lint", str(mini.root)) == 1
    out = capsys.readouterr().out
    assert "chapter 1: " in out and "error(s)" in out and "[error]" in out
    assert "chapter 2" not in out                              # unwritten chapters are skipped


def test_lint_real_story_matches_linter(story, capsys):
    from story_pipeline import lint
    expected = sum(len(lint.errors(lint.lint_chapter(story.chapter(n)))) for n in story.chapter_numbers())
    code = exit_code("lint", str(story.root))
    out = capsys.readouterr().out
    assert code == (1 if expected else 0)
    assert out.count("chapter ") >= 8


def test_render_rebuilds_markdown_from_json(story, capsys):
    for n in story.chapter_numbers():
        story.chapter_md(n).write_text("stale")
    run("render", str(story.root))
    assert "Re-rendered 8 chapter(s) with speaker labels." in capsys.readouterr().out
    expect = text.render_markdown(story.chapter(1), story.outline(), attribute=True)
    assert story.chapter_md(1).read_text() == expect
    run("render", str(story.root), "--plain")
    assert "Re-rendered 8 chapter(s) plain." in capsys.readouterr().out
    assert story.chapter_md(1).read_text() == text.render_markdown(story.chapter(1), story.outline(), attribute=False)


def test_render_nothing_written(repo_root, capsys):
    b = make_mini(repo_root / "stories", "none")
    for n in (1, 2):
        b.chapter_json(n).unlink()
    run("render", str(b.root))
    assert "No written chapters to render." in capsys.readouterr().out


def test_render_strip_titles(mini, monkeypatch, capsys):
    monkeypatch.setattr(cli.text, "strip_title_segments", lambda b: {2: "Chapter Two", 1: "One"})
    run("render", str(mini.root), "--strip-titles")
    out = capsys.readouterr().out
    assert "Removed 2 announced title(s):" in out
    assert out.index("chapter 1: 'One'") < out.index("chapter 2: 'Chapter Two'")
    monkeypatch.setattr(cli.text, "strip_title_segments", lambda b: {})
    run("render", str(mini.root), "--strip-titles")
    assert "No chapter titles written into the prose." in capsys.readouterr().out


def test_critique_no_chapters(repo_root):
    b = make_mini(repo_root / "stories", "none")
    for n in (1, 2):
        b.chapter_json(n).unlink()
    with pytest.raises(SystemExit) as e:
        run("critique", str(b.root))
    assert str(e.value) == "No written chapters to critique."


def test_critique_declined(mini, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda p: "")
    monkeypatch.setattr(cli.text, "review_chapter", lambda *a: pytest.fail("must not run"))
    run("critique", str(mini.root))
    out = capsys.readouterr().out
    assert "Re-reading 2 chapter(s) with claude-sonnet-5 — roughly $0.04" in out and "stopped." in out


def test_critique_reports_verdict_changes(mini, monkeypatch, capsys):
    mini.review_path(1).write_text(json.dumps({"pass": True}))

    def review(cfg, b, n):
        r = {"pass": n == 2, "score": 6, "continuity": ["c1"], "fixes": [{"quote": "x"}]}
        b.review_path(n).write_text(json.dumps(r))
        return r
    monkeypatch.setattr(cli.text, "review_chapter", review)
    run("critique", str(mini.root), "-y")
    out = capsys.readouterr().out
    assert "chapter 1: FAILED (6)  (was pass)" in out and "chapter 2: passed (6)" in out
    assert "continuity: c1" in out and 'fixes: {"quote": "x"}' in out
    assert "1 chapter(s) now fail: [1]" in out and f"cli write {mini.root}" in out


def test_critique_all_pass_and_chapter_filter(mini, monkeypatch, capsys):
    seen = []

    def review(cfg, b, n):
        seen.append(n)
        b.review_path(n).write_text(json.dumps({"pass": True}))
        return {"pass": True}
    monkeypatch.setattr(cli.text, "review_chapter", review)
    run("critique", str(mini.root), "-y", "--chapter", "2", "--chapter", "7")
    out = capsys.readouterr().out
    assert seen == [2] and "chapter 7: not written" in out and "All chapters still pass." in out
