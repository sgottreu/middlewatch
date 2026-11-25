"""server.recover() hands each dead job to its own pipeline's releaser."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from story_pipeline.bundle import Bundle
from story_pipeline.review import runner, server

from .test_server_functions import SLUG, edit_manifest, job  # shared helpers


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_start_marks_story_pipeline(cfg, real_story, runs_dir, monkeypatch):
    monkeypatch.setattr(runner, "_launch", lambda d, argv, meta: meta)
    assert runner.start(cfg, SLUG, "write")["pipeline"] == "story"


def test_start_command_records_pipeline_only_when_given(runs_dir, monkeypatch):
    monkeypatch.setattr(runner, "_launch", lambda d, argv, meta: meta)
    assert runner.start_command("ambience-x", "sound", ["true"],
                                pipeline="ambience")["pipeline"] == "ambience"
    assert "pipeline" not in runner.start_command("_casting-arc", "audition", ["true"])


def test_non_story_job_with_story_stage_name_leaves_story_alone(cfg, real_story, runs_dir):
    # An ambience job keyed by the story's own slug and stage name `design`
    # must not reset the story's design stage.
    edit_manifest(real_story, lambda m: m["stages"].update(design="running"))
    job(runs_dir, SLUG, "design", status="running", exit_code=None,
        pid=_dead_pid(), pipeline="ambience")
    settled = server.recover(cfg)
    assert [(m["stage"], m["status"]) for m in settled] == [("design", "failed")]
    assert Bundle.open(real_story).stage_status("design") == "running"


def test_registered_releaser_receives_cfg_and_meta(cfg, runs_dir, monkeypatch):
    calls = []
    monkeypatch.setitem(server.RELEASERS, "novella",
                        lambda c, meta: calls.append((c is cfg, meta["slug"], meta["stage"])))
    job(runs_dir, "novella-salt", "draft", status="running", exit_code=None,
        pid=_dead_pid(), pipeline="novella")
    server.recover(cfg)
    assert calls == [(True, "novella-salt", "draft")]


def test_failing_releaser_does_not_stop_recovery(cfg, runs_dir, monkeypatch):
    def boom(c, meta):
        raise RuntimeError("x")
    monkeypatch.setitem(server.RELEASERS, "novella", boom)
    job(runs_dir, "novella-a", "draft", status="running", exit_code=None,
        pid=_dead_pid(), pipeline="novella")
    job(runs_dir, "novella-b", "draft", status="running", exit_code=None,
        pid=_dead_pid(), pipeline="novella")
    assert len(server.recover(cfg)) == 2


def test_meta_without_pipeline_is_still_a_story_job(cfg, real_story, runs_dir):
    edit_manifest(real_story, lambda m: m["stages"].update(record="running"))
    job(runs_dir, SLUG, "record", status="running", exit_code=None, pid=_dead_pid())
    server.recover(cfg)
    assert Bundle.open(real_story).stage_status("record") == "pending"
