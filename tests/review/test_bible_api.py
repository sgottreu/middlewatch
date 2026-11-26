"""bible_api.py: the review server's half of `bible promote`."""
from __future__ import annotations

import json
import sys

import pytest

from story_pipeline import bible_promote as bp
from story_pipeline.review import bible_api, runner

NORTH = "salt"


@pytest.fixture(autouse=True)
def _synthetic_draft(sample_draft):
    """Every test here works on the synthetic draft bibles/salt/salt.draft.md."""
    return sample_draft


def draft_file(name=NORTH):
    return bp.draft_path(name, "bibles")


def write_job(runs_dir, name, status="done", exit_code=0, marks=(), **over):
    d = runs_dir / f"_bible-{name}" / "20260101T000000-promote-preview"
    d.mkdir(parents=True)
    meta = {"job_id": d.name, "slug": f"_bible-{name}", "stage": "promote-preview",
            "kind": "promote-preview", "status": status, "started": 1.0,
            "finished": 2.0, "exit_code": exit_code, "pid": None, **over}
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "output.log").write_text(
        "".join(runner.MARKER + json.dumps(m) + "\n" for m in marks))
    return d


# --- name validation / draft presence --------------------------------------


@pytest.mark.parametrize("bad", ["", "../etc", "Has Caps", "a/b", "-lead", ".hidden"])
def test_every_entry_point_rejects_bad_names(cfg, real_bibles, started, bad):
    for fn in (lambda: bible_api.job(cfg, bad),
               lambda: bible_api.log(cfg, bad),
               lambda: bible_api.preview_start(cfg, bad),
               lambda: bible_api.fill(cfg, bad, "genre", "x"),
               lambda: bible_api.apply_plan(cfg, bad),
               lambda: bible_api.push_waiting(cfg, bad),
               lambda: bible_api.apply(cfg, bad)):
        with pytest.raises(bp.PromoteError, match="is not a bible name"):
            fn()
    assert started == []


def test_missing_draft_is_a_value_error(cfg, real_bibles, started):
    with pytest.raises(ValueError, match="no draft nope.draft.md"):
        bible_api.preview_start(cfg, "nope")
    with pytest.raises(ValueError, match="no draft"):
        bible_api.apply_plan(cfg, "nope")
    with pytest.raises(ValueError, match="no draft"):
        bible_api.apply(cfg, "nope")
    with pytest.raises(ValueError, match="no draft"):
        bible_api.fill(cfg, "nope", "genre", "x")
    with pytest.raises(ValueError, match="no draft"):
        bible_api.push_waiting(cfg, "nope")


def test_bibles_dir_taken_from_cfg(cfg, repo_root, started):
    alt = repo_root / "elsewhere" / "tiny"
    alt.mkdir(parents=True)
    (alt / "tiny.draft.md").write_text("# Tiny\n")
    cfg = {**cfg, "bibles_dir": str(repo_root / "elsewhere")}
    bible_api.preview_start(cfg, "tiny")
    assert started[0]["argv"][-1] == str(repo_root / "elsewhere")


# --- preview_start ----------------------------------------------------------


def test_preview_start_launches_dry_run_promote_job(cfg, real_bibles, started):
    out = bible_api.preview_start(cfg, NORTH)
    assert out["ok"] is True and out["job"]["name"] == NORTH
    (call,) = started
    assert call["key"] == "_bible-salt"
    assert call["stage"] == "promote-preview"
    assert call["extra"] == {"bible": NORTH}
    assert "already running" in call["busy"]
    argv = call["argv"]
    assert argv[:3] == [sys.executable, "-m", "story_pipeline.cli"]
    assert argv[argv.index("--config") + 1] == "config.yaml"
    tail = argv[argv.index("bible"):]
    assert tail[:3] == ["bible", "promote", NORTH]
    assert "--dry-run" in tail and "--json" in tail
    assert tail[tail.index("--bibles-dir") + 1] == "bibles"


def test_preview_start_uses_config_path_when_present(cfg, real_bibles, started):
    bible_api.preview_start({**cfg, "_config_path": "/x/cfg.yaml"}, NORTH)
    argv = started[0]["argv"]
    assert argv[argv.index("--config") + 1] == "/x/cfg.yaml"


def test_preview_start_busy_propagates(cfg, real_bibles, monkeypatch):
    def busy(*a, **k):
        raise ValueError(k["busy"])
    monkeypatch.setattr(runner, "start_command", busy)
    with pytest.raises(ValueError, match="A preview of salt is already running"):
        bible_api.preview_start(cfg, NORTH)


# --- job -------------------------------------------------------------------


def test_job_with_no_runs_is_state_none_and_named(cfg, runs_dir):
    assert bible_api.job(cfg, "anything") == {"state": "none", "name": "anything"}


def test_job_returns_preview_from_last_preview_marker(cfg, runs_dir):
    write_job(runs_dir, NORTH, marks=[
        {"event": "other", "x": 1},
        {"event": "preview", "preview": {"v": 1}},
        {"event": "preview", "preview": {"v": 2}},
    ])
    j = bible_api.job(cfg, NORTH)
    assert j["state"] == "done" and j["preview"] == {"v": 2}
    assert "progress" not in j and j["name"] == NORTH


def test_job_without_preview_marker_has_no_preview_key(cfg, runs_dir):
    write_job(runs_dir, NORTH, marks=[{"event": "other"}])
    assert "preview" not in bible_api.job(cfg, NORTH)


def test_job_exit_1_with_preview_counts_as_done(cfg, runs_dir):
    d = write_job(runs_dir, NORTH, status="failed", exit_code=1,
                  marks=[{"event": "preview", "preview": {"errors": 2}}])
    (d / "output.log").write_text(
        (d / "output.log").read_text() + "bible check found errors\n")
    j = bible_api.job(cfg, NORTH)
    assert j["state"] == "done" and "error" not in j
    assert j["preview"] == {"errors": 2}


def test_job_exit_1_without_preview_stays_failed(cfg, runs_dir):
    write_job(runs_dir, NORTH, status="failed", exit_code=1, marks=[])
    j = bible_api.job(cfg, NORTH)
    assert j["state"] == "failed" and j["error"] == "exited 1"


def test_job_other_exit_code_with_preview_stays_failed(cfg, runs_dir):
    write_job(runs_dir, NORTH, status="failed", exit_code=2,
              marks=[{"event": "preview", "preview": {}}])
    assert bible_api.job(cfg, NORTH)["state"] == "failed"


# --- log -------------------------------------------------------------------


def test_log_reads_job_output_from_offset(cfg, runs_dir):
    d = write_job(runs_dir, NORTH, marks=[{"event": "preview", "preview": {}}])
    (d / "output.log").write_text("first\nsecond\n")
    c = bible_api.log(cfg, NORTH)
    assert c["text"] == "first\nsecond" and c["offset"] == 13
    assert bible_api.log(cfg, NORTH, 6)["text"] == "second"


def test_log_with_no_job(cfg, runs_dir):
    assert bible_api.log(cfg, NORTH)["state"] == "none"


# --- fill ------------------------------------------------------------------


def test_fill_writes_value_into_draft_and_restarts_preview(cfg, real_bibles, started):
    before = draft_file().read_text()
    out = bible_api.fill(cfg, NORTH, "cast:Hal Venn:appearance", "tall, ink-stained hands")
    after = draft_file().read_text()
    assert after != before and "tall, ink-stained hands" in after
    assert out["ok"] is True
    assert started[0]["key"] == "_bible-salt"


def test_fill_unknown_field_is_value_error_and_does_not_start_preview(
        cfg, real_bibles, started):
    before = draft_file().read_text()
    with pytest.raises(ValueError):
        bible_api.fill(cfg, NORTH, "cast:Nobody:appearance", "x")
    assert draft_file().read_text() == before
    assert started == []


def test_fill_series_field(cfg, real_bibles, started):
    bible_api.fill(cfg, NORTH, "series", "The Salt Roads Cycle")
    assert "The Salt Roads Cycle" in draft_file().read_text()


# --- apply_plan ------------------------------------------------------------


def test_apply_plan_shape_for_new_bible(cfg, real_bibles):
    plan = bible_api.apply_plan(cfg, NORTH)
    assert plan["name"] == NORTH
    assert plan["blocked"] is False and plan["changed"] is True and plan["errors"] == []
    actions = {f["path"].rsplit("/", 1)[-1]: f["action"] for f in plan["files"]}
    assert actions == {"salt.md": "create", "salt.changelog.md": "create",
                       "salt.draft.md": "commit"}
    bible_entry = plan["files"][0]
    assert bible_entry["added"] > 0 and bible_entry["removed"] == 0
    assert plan["git"]["repo"] is False
    assert not (real_bibles / NORTH / "salt.md").exists()  # free: writes nothing


def test_apply_plan_is_unchanged_after_apply(cfg, real_bibles):
    bible_api.apply(cfg, NORTH, commit=False, push=False)
    plan = bible_api.apply_plan(cfg, NORTH)
    assert plan["changed"] is False
    assert {f["action"] for f in plan["files"][:2]} == {"unchanged"}


def test_apply_plan_blocked_when_bible_has_fatal_errors(cfg, real_bibles, monkeypatch):
    fatal = [{"fatal": True, "where": "genre", "message": "no genre", "fix": "pick",
              "fields": []}]
    monkeypatch.setattr(bp, "check", lambda pv, cfg=None: fatal)
    plan = bible_api.apply_plan(cfg, NORTH)
    assert plan["blocked"] is True and plan["errors"] == fatal


# --- apply -----------------------------------------------------------------


def test_apply_writes_bible_and_changelog_without_commit(cfg, real_bibles):
    out = bible_api.apply(cfg, NORTH, commit=False, push=False, author="Tester")
    assert out["written"] is True and "git" not in out
    bible = real_bibles / NORTH / "salt.md"
    log = real_bibles / NORTH / "salt.changelog.md"
    assert bible.is_file() and log.is_file()
    assert "Tester" in log.read_text()


def test_apply_twice_is_a_noop_the_second_time(cfg, real_bibles):
    bible_api.apply(cfg, NORTH, commit=False, push=False)
    again = bible_api.apply(cfg, NORTH, commit=False, push=False)
    assert again["written"] is False and "up to date" in again["message"]


def test_apply_commit_and_push_flags_reach_the_committer(cfg, real_bibles, monkeypatch):
    seen = {}

    def fake_commit(files, name, message, push):
        seen.update(files=files, name=name, push=push)
        return {"committed": True, "pushed": push}
    monkeypatch.setattr(bp, "commit", fake_commit)

    out = bible_api.apply(cfg, NORTH, commit=True, push=False)
    assert out["git"] == {"committed": True, "pushed": False}
    assert seen["name"] == NORTH and seen["push"] is False
    names = [p.name for p in seen["files"]]
    assert names == ["salt.draft.md", "salt.md", "salt.changelog.md"]


def test_apply_without_git_checkout_reports_not_committed(cfg, real_bibles):
    out = bible_api.apply(cfg, NORTH, commit=True, push=True)
    assert out["written"] is True
    assert out["git"]["committed"] is False
    assert "not a git checkout" in out["git"]["message"]


def test_apply_refused_on_fatal_problem_is_value_error_and_writes_nothing(
        cfg, real_bibles, monkeypatch):
    monkeypatch.setattr(bp, "check", lambda pv, cfg=None: [
        {"fatal": True, "where": "genre", "message": "bad genre", "fix": "", "fields": []}])
    with pytest.raises(ValueError, match="bible check found 1 error"):
        bible_api.apply(cfg, NORTH, commit=False)
    assert not (real_bibles / NORTH / "salt.md").exists()


# --- push_waiting ----------------------------------------------------------


def test_push_waiting_outside_a_checkout_is_value_error(cfg, real_bibles):
    with pytest.raises(ValueError, match="not a git checkout"):
        bible_api.push_waiting(cfg, NORTH)


def test_push_waiting_returns_result_with_refreshed_plan(cfg, real_bibles, monkeypatch):
    got = {}

    def fake(paths):
        got["paths"] = [p.name for p in paths]
        return {"pushed": True, "count": 2}
    monkeypatch.setattr(bp, "push_waiting", fake)
    out = bible_api.push_waiting(cfg, NORTH)
    assert out["pushed"] is True and out["count"] == 2
    assert out["plan"]["name"] == NORTH
    assert got["paths"] == ["salt.draft.md", "salt.md",
                            "salt.changelog.md"]


def test_push_waiting_promote_error_becomes_value_error(cfg, real_bibles, monkeypatch):
    def boom(paths):
        raise bp.PromoteError("main has no upstream to push to")
    monkeypatch.setattr(bp, "push_waiting", boom)
    with pytest.raises(ValueError, match="no upstream"):
        bible_api.push_waiting(cfg, NORTH)


# --- view: the Bible button --------------------------------------------------


def test_view_draft_only(cfg, real_bibles):
    v = bible_api.view(cfg, NORTH)
    assert v["name"] == "salt" and v["agents"] is None and v["stale"] is True
    assert v["draft"].startswith("# Salt Roads (Working Draft)")


def test_view_promoted_shows_agents_view_and_staleness(cfg, real_bibles):
    bp.promote(NORTH, {}, author="t")
    v = bible_api.view(cfg, NORTH)
    assert v["series"] == "Salt Roads" and v["stale"] is False and v["problems"] == []
    assert v["agents"].startswith("# Genre settings\n")
    assert "- **Subgenres:** romance, mystery" in v["agents"]        # whole genre, noted
    assert "New outline uses the first" in v["agents"]
    assert "# Series bible:" not in v["agents"]                       # one title, not two
    assert "## Story ideas" in v["agents"]                            # the ideator's view
    draft_file().write_text(draft_file().read_text() + "\n## Extra\n\nMore.\n")
    assert bible_api.view(cfg, NORTH)["stale"] is True


def test_view_shows_series_genre_settings(cfg, real_bibles):
    draft_file().write_text(draft_file().read_text().replace(
        "Series: Salt Roads\n",
        "Series: Salt Roads\nSubgenres: mystery\nExclude tropes: The hedge-witch\n"))
    bp.promote(NORTH, {}, author="t")
    a = bible_api.view(cfg, NORTH)["agents"]
    assert "- **Subgenres:** mystery\n" in a
    assert "- **Excluded tropes:** The hedge-witch" in a and "14 tropes" in a


def test_view_bible_without_draft(cfg, real_bibles):
    v = bible_api.view(cfg, "amberlight")
    assert v["draft"] is None and v["stale"] is None and v["agents"]


def test_view_unknown_and_bad_names(cfg, real_bibles):
    with pytest.raises(ValueError, match="no bible or draft"):
        bible_api.view(cfg, "nope")
    with pytest.raises(Exception):
        bible_api.view(cfg, "../etc")
