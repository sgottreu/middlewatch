"""scripts/deploy/mw-commit-stories and mw-write-running."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

DEPLOY = REPO_ROOT / "scripts" / "deploy"


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def box(tmp_path):
    """A server checkout with a bare origin, and a fake mw-write-running on PATH."""
    origin, repo, bin_ = tmp_path / "origin.git", tmp_path / "srv", tmp_path / "bin"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(repo))
    for k, v in (("user.name", "T"), ("user.email", "t@example.invalid")):
        git(repo, "config", k, v)
    (repo / "stories").mkdir()
    (repo / "stories" / "a.md").write_text("a\n")
    (repo / "other.txt").write_text("x\n")
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "init"); git(repo, "push", "-q", "origin", "HEAD:main")
    bin_.mkdir()
    fake = bin_ / "mw-write-running"
    fake.write_text("#!/usr/bin/env bash\ncat \"$BUSY_FILE\" 2>/dev/null || true\n")
    fake.chmod(0o755)
    env = {**os.environ, "MW_REPO": str(repo), "PATH": f"{bin_}:{os.environ['PATH']}",
           "BUSY_FILE": str(tmp_path / "busy")}
    return repo, origin, env, tmp_path / "busy"


def run(env):
    return subprocess.run(["bash", str(DEPLOY / "mw-commit-stories")], env=env,
                          capture_output=True, text=True)


def test_commits_stories_and_novellas_only(box):
    repo, origin, env, _ = box
    (repo / "stories" / "a.md").write_text("changed\n")
    (repo / "novellas" / "s" / "n").mkdir(parents=True)
    (repo / "novellas" / "s" / "n" / "manifest.json").write_text("{}\n")
    (repo / "other.txt").write_text("not mine\n")
    git(repo, "add", "other.txt")          # staged elsewhere: must not be swept in
    r = run(env)
    assert r.returncode == 0, r.stderr
    files = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == ["novellas/s/n/manifest.json", "stories/a.md"]
    assert "other.txt" in git(repo, "diff", "--cached", "--name-only")
    assert git(origin, "log", "--oneline", "main").count("\n") == 2


def test_no_novellas_dir_is_fine(box):
    repo, _, env, _ = box
    (repo / "stories" / "a.md").write_text("changed\n")
    r = run(env)
    assert r.returncode == 0, r.stderr
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["stories/a.md"]


def test_nothing_changed_commits_nothing(box):
    repo, _, env, _ = box
    before = git(repo, "rev-parse", "HEAD")
    assert run(env).returncode == 0
    assert git(repo, "rev-parse", "HEAD") == before


def test_skips_while_busy(box):
    repo, _, env, busy = box
    (repo / "novellas").mkdir()
    (repo / "novellas" / "x.md").write_text("x\n")
    busy.write_text("novella x (draft, chapter 1)\n")
    before = git(repo, "rev-parse", "HEAD")
    r = run(env)
    assert r.returncode == 0 and "skipping" in r.stdout
    assert git(repo, "rev-parse", "HEAD") == before


# --- mw-write-running against a fake review server ---------------------------

def _serve(routes):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = routes.get(self.path)
            if body is None:
                self.send_response(404); self.end_headers(); self.wfile.write(b'{"error":"not found"}')
                return
            data = json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(data)
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def write_running(port):
    return subprocess.run([sys.executable, str(DEPLOY / "mw-write-running")],
                          env={**os.environ, "MW_REVIEW_PORT": str(port)},
                          capture_output=True, text=True, timeout=30)


def test_reports_running_novella_job():
    s = _serve({
        "/api/queue": {"groups": {}},
        "/api/novella": {"series": [{"novellas": [{"slug": "glass"}, {"slug": "idle"}]}]},
        "/api/novella/glass/job": {"state": "running", "kind": "draft", "chapter": 3},
        "/api/novella/idle/job": {"state": "done"},
    })
    try:
        r = write_running(s.server_address[1])
    finally:
        s.shutdown()
    assert r.returncode == 0 and r.stdout.strip() == "novella glass (draft, chapter 3)"


def test_server_without_novella_plugin_is_idle():
    s = _serve({"/api/queue": {"groups": {}}})
    try:
        r = write_running(s.server_address[1])
    finally:
        s.shutdown()
    assert r.returncode == 0 and r.stdout == ""
