"""server.PLUGINS: other pipelines' modules answer their own routes."""

from __future__ import annotations

import types

import pytest

from story_pipeline.review import server

from .test_server_http import assert_not_found


def _plugin(**kw):
    return types.SimpleNamespace(**kw)


def test_plugin_get_and_post_routes(client, monkeypatch):
    def get(h, cfg, path):
        if path == "/api/demo":
            h._json({"ok": "get"})
            return True
        return False

    def post(h, cfg, path, body):
        if path == "/api/demo":
            h._json({"ok": "post", "body": body})
            return True
        return False

    monkeypatch.setattr(server, "PLUGINS", [_plugin(handle_get=get, handle_post=post)])
    assert client.get("/api/demo").json == {"ok": "get"}
    assert client.post("/api/demo", {"x": 1}).json == {"ok": "post", "body": {"x": 1}}
    assert_not_found(client.get("/api/other"))
    assert_not_found(client.post("/api/other", {}))


def test_plugin_error_is_a_json_500(client, monkeypatch):
    def get(h, cfg, path):
        raise ValueError("bad thing")
    monkeypatch.setattr(server, "PLUGINS", [_plugin(handle_get=get)])
    r = client.get("/api/whatever")
    assert r.status == 500 and r.json["error"] == "ValueError: bad thing"


def test_existing_routes_win_over_plugins(client, monkeypatch):
    called = []
    monkeypatch.setattr(server, "PLUGINS", [_plugin(
        handle_get=lambda h, c, p: called.append(p) or False)])
    assert client.get("/common.css").status == 200
    assert called == []


def test_missing_plugin_module_is_skipped(monkeypatch):
    monkeypatch.setattr(server, "PLUGIN_MODULES", ("no_such_plugin_xyz",))
    assert server._load_plugins() == []


def test_broken_plugin_module_raises(monkeypatch, tmp_path):
    import sys
    pkg = server.__package__
    mod = types.ModuleType(f"{pkg}.broken_demo")
    monkeypatch.setattr(server, "PLUGIN_MODULES", ("broken_demo",))

    import importlib
    real = importlib.import_module

    def fake(name, *a, **k):
        if name == f"{pkg}.broken_demo":
            raise ModuleNotFoundError("No module named 'numpyy'", name="numpyy")
        return real(name, *a, **k)
    monkeypatch.setattr(importlib, "import_module", fake)
    with pytest.raises(ModuleNotFoundError):
        server._load_plugins()


def test_plugin_releaser_is_registered(monkeypatch):
    import importlib, sys
    pkg = server.__package__
    mod = types.ModuleType(f"{pkg}.demo_plugin")
    mod.PIPELINE = "demo"
    mod.release = lambda cfg, meta: None
    monkeypatch.setitem(sys.modules, f"{pkg}.demo_plugin", mod)
    monkeypatch.setattr(server, "PLUGIN_MODULES", ("demo_plugin",))
    monkeypatch.setattr(server, "RELEASERS", dict(server.RELEASERS))
    assert server._load_plugins() == [mod]
    assert server.RELEASERS["demo"] is mod.release
