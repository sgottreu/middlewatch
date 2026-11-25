"""runner.py: detached jobs, their files, progress markers, cancel, settling."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from story_pipeline.review import runner

PY = sys.executable


def wait_for(pred, timeout=15.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("timed out waiting for condition")


def finished(key):
    return wait_for(lambda: (m := runner.latest(key)) and m["status"] != "running" and m)


def dead_pid():
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait()
    return p.pid


def write_meta(runs, key, job_id, **over):
    d = runs / key / job_id
    d.mkdir(parents=True)
    meta = {"job_id": job_id, "slug": key, "stage": "write", "kind": "write",
            "status": "running", "started": 1.0, "finished": None,
            "exit_code": None, "pid": dead_pid(), **over}
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def _is_zombie(pid):
    try:
        return "Z" in open(f"/proc/{pid}/stat").read().split(")")[-1][:3]
    except OSError:
        return True


# --- runs_dir / low-level helpers ----------------------------------------


def test_runs_dir_defaults_to_runs_under_cwd(monkeypatch):
    monkeypatch.delenv("MW_RUNS_DIR", raising=False)
    assert str(runner.runs_dir()) == "runs"


def test_runs_dir_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MW_RUNS_DIR", str(tmp_path / "x"))
    assert runner.runs_dir() == tmp_path / "x"


def test_read_returns_empty_dict_for_missing_or_corrupt(tmp_path):
    assert runner._read(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert runner._read(bad) == {}


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    d = tmp_path / "job"
    d.mkdir()
    p = d / "meta.json"
    runner._write(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert [x.name for x in d.iterdir()] == ["meta.json"]


@pytest.mark.parametrize("pid", [None, 0, "", "abc"])
def test_alive_false_for_missing_or_bad_pid(pid):
    assert runner.alive(pid) is False


def test_alive_false_for_dead_pid():
    assert runner.alive(dead_pid()) is False


def test_alive_true_for_this_process():
    assert runner.alive(os.getpid()) is True


# --- start_command ---------------------------------------------------------


def test_start_command_layout_and_meta(runs_dir):
    meta = runner.start_command(
        "k1", "demo", [PY, "-c", "print('hi')"], extra_field=7)
    finished("k1")
    jobs = list((runs_dir / "k1").iterdir())
    assert len(jobs) == 1 and jobs[0].name == meta["job_id"]
    assert meta["job_id"].endswith("-demo")
    assert (jobs[0] / "meta.json").is_file() and (jobs[0] / "output.log").is_file()
    saved = json.loads((jobs[0] / "meta.json").read_text())
    assert saved["slug"] == "k1" and saved["stage"] == "demo"
    assert saved["extra_field"] == 7 and saved["pid"] == meta["pid"]
    assert saved["status"] == "done" and saved["exit_code"] == 0
    assert saved["finished"] >= saved["started"]
    assert "hi" in (jobs[0] / "output.log").read_text()


def test_child_gets_progress_env_and_unbuffered(runs_dir):
    runner.start_command("k", "env", [PY, "-c",
        "import os;print(os.environ['MW_PROGRESS'], os.environ['PYTHONUNBUFFERED'])"])
    finished("k")
    assert runner.log_chunk("k")["text"].strip() == "1 1"


def test_child_runs_in_cwd_and_captures_stderr(runs_dir, repo_root):
    runner.start_command("k", "cwd", [PY, "-c",
        "import os,sys;print(os.getcwd());print('oops',file=sys.stderr)"])
    finished("k")
    text = runner.log_chunk("k")["text"]
    assert str(repo_root.resolve()) in text and "oops" in text


def test_nonzero_exit_is_failed_with_code(runs_dir):
    runner.start_command("k", "boom", [PY, "-c", "import sys;print('bad');sys.exit(3)"])
    m = finished("k")
    assert m["status"] == "failed" and m["exit_code"] == 3
    st = runner.status("k")
    assert st["state"] == "failed" and st["error"] == "bad"
    assert st["can_cancel"] is False


def test_failed_without_output_reports_exit_code(runs_dir):
    runner.start_command("k", "boom", [PY, "-c", "import sys;sys.exit(4)"])
    finished("k")
    assert runner.status("k")["error"] == "exited 4"


def test_one_job_per_key_busy_rule(runs_dir):
    runner.start_command("k", "slow", [PY, "-c", "import time;time.sleep(30)"])
    try:
        with pytest.raises(ValueError, match="already has a slow running"):
            runner.start_command("k", "other", [PY, "-c", "pass"])
        with pytest.raises(ValueError, match="custom busy message"):
            runner.start_command("k", "other", [PY, "-c", "pass"],
                                 busy="custom busy message")
        assert len(list((runs_dir / "k").iterdir())) == 1
    finally:
        runner.cancel("k")


def test_busy_rule_is_per_key(runs_dir):
    runner.start_command("a", "slow", [PY, "-c", "import time;time.sleep(30)"])
    try:
        runner.start_command("b", "quick", [PY, "-c", "pass"])
        assert finished("b")["status"] == "done"
    finally:
        runner.cancel("a")


def test_can_start_again_after_finish_and_job_ids_do_not_collide(runs_dir):
    for _ in range(3):
        runner.start_command("k", "quick", [PY, "-c", "pass"])
        finished("k")
    ids = sorted(p.name for p in (runs_dir / "k").iterdir())
    assert len(ids) == 3 and len(set(ids)) == 3
    assert runner.latest("k")["job_id"] == ids[-1]


# --- progress / log --------------------------------------------------------

PROGRESS_CHILD = (
    "print('::progress {\"pct\": 50}')\n"
    "print('hello')\n"
    "print('::progress not json')\n"
    "print('::progress {\"pct\": 100}')\n"
    "print('bye')\n"
)


def test_markers_parsed_and_malformed_skipped(runs_dir):
    runner.start_command("k", "p", [PY, "-c", PROGRESS_CHILD])
    finished("k")
    assert runner.markers("k") == [{"pct": 50}, {"pct": 100}]
    assert runner.status("k")["progress"] == [{"pct": 50}, {"pct": 100}]


def test_markers_for_explicit_job_id_and_unknown(runs_dir):
    meta = runner.start_command("k", "p", [PY, "-c", PROGRESS_CHILD])
    finished("k")
    assert runner.markers("k", meta["job_id"]) == [{"pct": 50}, {"pct": 100}]
    assert runner.markers("k", "no-such-job") == []
    assert runner.markers("never-ran") == []


def test_log_chunk_hides_markers_from_text(runs_dir):
    runner.start_command("k", "p", [PY, "-c", PROGRESS_CHILD])
    finished("k")
    chunk = runner.log_chunk("k")
    assert chunk["text"] == "hello\nbye"
    assert chunk["state"] == "done"


def test_log_chunk_offsets_resume_without_replay(runs_dir):
    runner.start_command("k", "p", [PY, "-c", "print('one');print('two');print('three')"])
    finished("k")
    first = runner.log_chunk("k")
    assert first["text"].splitlines() == ["one", "two", "three"]
    again = runner.log_chunk("k", first["offset"])
    assert again["text"] == "" and again["offset"] == first["offset"]
    mid = runner.log_chunk("k", len("one\n"))
    assert mid["text"].splitlines() == ["two", "three"]
    log = runner.job_dir("k", first["job_id"]) / "output.log"
    with log.open("ab") as fh:
        fh.write(b"four\n")
    more = runner.log_chunk("k", first["offset"])
    assert more["text"] == "four" and more["offset"] == first["offset"] + 5


def test_log_chunk_stops_at_last_newline(runs_dir):
    d = write_meta(runs_dir, "k", "j1", status="done")
    (d / "output.log").write_bytes(b"whole\npartial")
    c = runner.log_chunk("k")
    assert c["text"] == "whole" and c["offset"] == len(b"whole\n")


def test_log_chunk_offset_clamped_to_file_size(runs_dir):
    d = write_meta(runs_dir, "k", "j1", status="done")
    (d / "output.log").write_bytes(b"abc\n")
    c = runner.log_chunk("k", 9999)
    assert c["text"] == "" and c["offset"] == 4
    c = runner.log_chunk("k", -5)
    assert c["text"] == "abc"


def test_log_chunk_from_start_returns_only_tail_of_huge_log(runs_dir, monkeypatch):
    monkeypatch.setattr(runner, "LOG_TAIL", 20)
    d = write_meta(runs_dir, "k", "j1", status="done")
    (d / "output.log").write_bytes(b"".join(b"line%02d\n" % i for i in range(10)))
    c = runner.log_chunk("k")
    assert c["offset"] == 70
    assert c["text"].splitlines()[-1] == "line09"
    assert "line00" not in c["text"]


def test_log_chunk_non_utf8_does_not_raise(runs_dir):
    d = write_meta(runs_dir, "k", "j1", status="done")
    (d / "output.log").write_bytes(b"ok \xff\xfe\n")
    assert runner.log_chunk("k")["text"].startswith("ok ")


def test_log_chunk_no_job_and_no_log_file(runs_dir):
    assert runner.log_chunk("nobody") == {"offset": 0, "text": "", "job_id": None,
                                          "state": "none"}
    write_meta(runs_dir, "k", "j1", status="done")
    assert runner.log_chunk("k") == {"offset": 0, "text": "", "job_id": "j1",
                                     "state": "done"}


def test_tail_error_keeps_last_lines_without_markers_or_blanks(runs_dir):
    d = write_meta(runs_dir, "k", "j1", status="failed", exit_code=1)
    lines = [f"l{i}" for i in range(10)]
    (d / "output.log").write_text(
        "\n".join(lines[:5] + ["", '::progress {"a":1}'] + lines[5:]) + "\n")
    assert runner.tail_error("k", lines=3).splitlines() == ["l7", "l8", "l9"]
    assert runner.tail_error("nobody") == ""
    write_meta(runs_dir, "nolog", "j1", status="failed")
    assert runner.tail_error("nolog") == ""


# --- status ----------------------------------------------------------------


def test_status_none_when_no_job(runs_dir):
    assert runner.status("ghost") == {"state": "none"}


def test_status_includes_chapter_when_set(runs_dir):
    write_meta(runs_dir, "k", "j1", status="done", chapter=3, restart=True)
    st = runner.status("k")
    assert st["n"] == 3 and st["restart"] is True and st["kind"] == "write"
    assert st["job_id"] == "j1" and st["can_cancel"] is False


def test_status_omits_n_when_chapter_none(runs_dir):
    write_meta(runs_dir, "k", "j1", status="done", chapter=None)
    assert "n" not in runner.status("k")


# --- cancel ----------------------------------------------------------------


def test_cancel_kills_sleeping_child(runs_dir):
    meta = runner.start_command("k", "sleepy", [PY, "-c", "import time;time.sleep(60)"])
    assert runner.alive(meta["pid"])
    out = runner.cancel("k")
    assert out["status"] == "cancelled" and out["finished"]
    wait_for(lambda: not runner.alive(meta["pid"]) or _is_zombie(meta["pid"]))
    # the reaper must not overwrite "cancelled" with the signal's exit code
    time.sleep(0.4)
    saved = json.loads((runner.job_dir("k", meta["job_id"]) / "meta.json").read_text())
    assert saved["status"] == "cancelled"
    st = runner.status("k")
    assert st["state"] == "cancelled" and st["error"] == "stopped"
    assert st["can_cancel"] is False


def test_cancel_kills_the_whole_process_group(runs_dir, tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess,sys,time\n"
        "g=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        f"open({str(pidfile)!r},'w').write(str(g.pid))\n"
        "time.sleep(60)\n"
    )
    meta = runner.start_command("k", "tree", [PY, "-c", code])
    gpid = int(wait_for(lambda: pidfile.exists() and pidfile.read_text() or None))
    assert runner.alive(gpid)
    runner.cancel("k")
    wait_for(lambda: not runner.alive(gpid) or _is_zombie(gpid))
    wait_for(lambda: not runner.alive(meta["pid"]) or _is_zombie(meta["pid"]))


def test_child_is_in_its_own_session(runs_dir):
    meta = runner.start_command("k", "sleepy", [PY, "-c", "import time;time.sleep(60)"])
    try:
        assert os.getpgid(meta["pid"]) == meta["pid"] != os.getpgid(os.getpid())
    finally:
        runner.cancel("k")


def test_cancel_with_nothing_running_raises(runs_dir):
    with pytest.raises(ValueError, match="nothing is running for ghost"):
        runner.cancel("ghost")
    runner.start_command("k", "quick", [PY, "-c", "pass"])
    finished("k")
    with pytest.raises(ValueError, match="nothing is running for k"):
        runner.cancel("k")


def test_cancel_when_process_already_gone_still_records_cancelled(runs_dir, monkeypatch):
    write_meta(runs_dir, "k", "j1", pid=dead_pid())
    # latest() would settle this as failed first, so bypass settling to reach the
    # ProcessLookupError branch inside cancel()
    monkeypatch.setattr(
        runner, "latest",
        lambda slug: runner._read(runner.job_dir(slug, "j1") / "meta.json"))
    out = runner.cancel("k")
    assert out["status"] == "cancelled"
    assert json.loads((runs_dir / "k" / "j1" / "meta.json").read_text())["status"] == "cancelled"


# --- latest / settle / reap_dead -------------------------------------------


def test_latest_none_when_nothing(runs_dir):
    assert runner.latest("ghost") is None
    (runs_dir / "empty").mkdir(parents=True)
    assert runner.latest("empty") is None


def test_latest_picks_newest_by_job_id(runs_dir):
    write_meta(runs_dir, "k", "20260101T000000-write", status="done", pid=None)
    write_meta(runs_dir, "k", "20260301T000000-write", status="done", pid=None)
    write_meta(runs_dir, "k", "20260201T000000-write", status="done", pid=None)
    assert runner.latest("k")["job_id"] == "20260301T000000-write"


def test_latest_settles_a_running_job_whose_process_died(runs_dir):
    d = write_meta(runs_dir, "k", "j1")
    m = runner.latest("k")
    assert m["status"] == "failed" and m["died"] is True
    assert m["exit_code"] is None and m["finished"]
    assert json.loads((d / "meta.json").read_text())["status"] == "failed"
    st = runner.status("k")
    assert st["state"] == "failed" and "killed under it" in st["error"]


def test_settle_leaves_a_live_job_alone(runs_dir):
    write_meta(runs_dir, "k", "j1", pid=os.getpid())
    assert runner.latest("k")["status"] == "running"


def test_died_job_error_prefers_log_tail(runs_dir):
    d = write_meta(runs_dir, "k", "j1")
    (d / "output.log").write_text("Traceback...\nValueError: nope\n")
    assert runner.status("k")["error"].endswith("ValueError: nope")


def test_reap_dead_with_no_runs_dir(runs_dir):
    assert runner.reap_dead() == []


def test_reap_dead_settles_only_dead_running_jobs(runs_dir):
    write_meta(runs_dir, "a", "j1")                               # dead, running
    write_meta(runs_dir, "b", "j1", pid=os.getpid())              # alive, running
    write_meta(runs_dir, "c", "j1", status="done", pid=None)      # not running
    settled = runner.reap_dead()
    assert [m["slug"] for m in settled] == ["a"]
    assert settled[0]["status"] == "failed"
    assert runner.reap_dead() == []  # idempotent
    assert json.loads((runs_dir / "b" / "j1" / "meta.json").read_text())["status"] == "running"
    assert json.loads((runs_dir / "c" / "j1" / "meta.json").read_text())["status"] == "done"


def test_reap_dead_covers_underscore_keys_too(runs_dir):
    write_meta(runs_dir, "_bible-x", "j1", stage="promote-preview")
    write_meta(runs_dir, "_casting-arc", "j1", stage="audition")
    assert {m["slug"] for m in runner.reap_dead()} == {"_bible-x", "_casting-arc"}


# --- start (story stages) with the child launch mocked ---------------------


@pytest.fixture
def launches(monkeypatch):
    calls = []

    def fake_launch(d, argv, meta):
        calls.append({"dir": d, "argv": argv, "meta": meta})
        return meta
    monkeypatch.setattr(runner, "_launch", fake_launch)
    return calls


def test_start_rejects_unknown_stage(cfg, runs_dir, real_story, launches):
    with pytest.raises(ValueError, match="unknown stage: ideate"):
        runner.start(cfg, "the-cracked-beam", "ideate")
    assert launches == []


def test_start_unknown_slug_raises_and_creates_nothing(cfg, runs_dir, launches):
    with pytest.raises(FileNotFoundError):
        runner.start(cfg, "ghost", "write")
    assert not (runs_dir / "ghost").exists()


def test_start_bad_slug_refused(cfg, runs_dir, launches):
    with pytest.raises(ValueError, match="not a story slug"):
        runner.start(cfg, "../..", "write")


@pytest.mark.parametrize("stage,kw,extra", [
    ("write", {}, []),
    ("write", {"force": True}, ["--force"]),
    ("write", {"restart": True}, ["--restart"]),
    ("write", {"force": True, "restart": True}, ["--force", "--restart"]),
    ("record", {}, []),
    ("record", {"force": True}, ["--force"]),
    ("direct", {"force": True}, ["--force"]),
    ("direct", {"restart": True}, []),
    ("design", {}, []),
    ("design", {"replan": True}, ["--replan"]),
    ("design", {"force": True}, []),
])
def test_start_builds_cli_argv(cfg, runs_dir, real_story, launches, stage, kw, extra):
    runner.start(cfg, "the-cracked-beam", stage, **kw)
    argv = launches[0]["argv"]
    assert argv[:5] == [sys.executable, "-m", "story_pipeline.cli", stage,
                        "stories/amberlight/the-cracked-beam"]
    assert argv[5:] == extra


def test_start_meta_and_job_dir(cfg, runs_dir, real_story, launches):
    meta = runner.start(cfg, "the-cracked-beam", "write", restart=True)
    d = launches[0]["dir"]
    assert d.is_dir() and d.parent == runs_dir / "the-cracked-beam"
    assert meta["job_id"].endswith("-write") and d.name == meta["job_id"]
    assert meta["status"] == "running" and meta["restart"] is True
    assert meta["slug"] == "the-cracked-beam" and meta["kind"] == "write"
    assert meta["finished"] is None and meta["pid"] is None


def test_start_redraft_writes_note_file_and_passes_it(cfg, runs_dir, real_story, launches):
    runner.start(cfg, "the-cracked-beam", "redraft", chapter="3",
                 note="make it\nshorter")
    argv, d = launches[0]["argv"], launches[0]["dir"]
    assert argv[5:7] == ["--chapter", "3"]
    assert argv[7] == "--note-file"
    note = d / "note.txt"
    assert argv[8] == str(note.resolve()) and note.read_text() == "make it\nshorter"


def test_start_redraft_requires_numeric_chapter(cfg, runs_dir, real_story, launches):
    with pytest.raises((TypeError, ValueError)):
        runner.start(cfg, "the-cracked-beam", "redraft", chapter=None, note="x")


def test_start_refuses_when_story_job_running(cfg, runs_dir, real_story):
    runner.start_command("the-cracked-beam", "write",
                         [PY, "-c", "import time;time.sleep(30)"])
    try:
        with pytest.raises(ValueError, match="already has a write running"):
            runner.start(cfg, "the-cracked-beam", "record")
    finally:
        runner.cancel("the-cracked-beam")


def test_start_two_in_same_second_get_distinct_dirs(cfg, runs_dir, real_story,
                                                    launches, monkeypatch):
    monkeypatch.setattr(runner.time, "strftime", lambda fmt: "20260101T000000")
    a = runner.start(cfg, "the-cracked-beam", "record")
    b = runner.start(cfg, "the-cracked-beam", "record")
    assert a["job_id"] == "20260101T000000-record"
    assert b["job_id"] == "20260101T000000-record-2"
