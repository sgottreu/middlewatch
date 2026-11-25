"""Fixtures for the review UI tests: a real server on a loopback port, a
config, and a tiny HTTP client. The autouse fixtures in tests/conftest.py already
block the network, fake the keys and chdir into a scratch repo root."""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from story_pipeline import config
from story_pipeline.review import server


@pytest.fixture
def cfg(repo_root):
    return config.load()


@pytest.fixture
def runs_dir(repo_root, monkeypatch):
    d = repo_root / "runs"
    monkeypatch.setenv("MW_RUNS_DIR", str(d))
    return d


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            hdrs = dict(headers or {})
            data = raw
            if body is not None:
                data = json.dumps(body).encode()
                hdrs.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=data, headers=hdrs)
            r = conn.getresponse()
            payload = r.read()
            return Response(r.status, dict(r.getheaders()), payload)
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body={} if body is None else body, **kw)


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    @property
    def json(self):
        return json.loads(self.body)

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def header(self, name):
        for k, v in self.headers.items():
            if k.lower() == name.lower():
                return v
        return None


@pytest.fixture
def client(cfg, runs_dir):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server._handler(cfg))
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01},
                         daemon=True)
    t.start()
    try:
        yield Client(httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


@pytest.fixture(autouse=True)
def _no_real_git(monkeypatch):
    """Nothing in this folder may run git against a real repository. Tests that
    want git behaviour replace these themselves."""
    from story_pipeline import bible_promote, casting_apply

    def refuse(*a, **k):
        raise bible_promote.PromoteError("git is disabled in tests")
    monkeypatch.setattr(bible_promote, "_git", refuse)
    monkeypatch.setattr(casting_apply, "_git", refuse)


@pytest.fixture
def started(monkeypatch):
    """Capture runner.start_command calls instead of launching anything."""
    from story_pipeline.review import runner
    calls = []

    def fake(key, stage, argv, busy=None, **extra):
        calls.append({"key": key, "stage": stage, "argv": argv, "busy": busy,
                      "extra": extra})
        return {"job_id": "j", "slug": key, "stage": stage}
    monkeypatch.setattr(runner, "start_command", fake)
    return calls


@pytest.fixture
def real_bibles(repo_root):
    """Same as tests.conftest.real_bibles (amberlight only), minus the images (about
    10 MB per test, and nothing in the review UI reads them)."""
    import shutil
    from tests.conftest import REPO_ROOT
    shutil.copytree(REPO_ROOT / "bibles" / "amberlight",
                    repo_root / "bibles" / "amberlight", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.png", "*.jpg", "*.jpeg", "*.webp"))
    return repo_root / "bibles"
