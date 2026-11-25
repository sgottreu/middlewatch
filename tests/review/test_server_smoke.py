"""The fixture server itself: binds loopback on an ephemeral port and answers."""
from __future__ import annotations

import socket

import pytest

from story_pipeline.review import server


def test_server_binds_loopback_and_answers(client):
    assert client.port > 0
    assert client.get("/manifest.webmanifest").status == 200


def test_server_is_loopback_only(client):
    s = socket.socket()
    try:
        s.settimeout(2)
        s.connect(("127.0.0.1", client.port))
    finally:
        s.close()


def test_serve_binds_to_localhost_not_all_interfaces(cfg, monkeypatch):
    seen = {}

    class Boom(Exception):
        pass

    class Fake:
        def __init__(self, addr, handler):
            seen["addr"] = addr
            raise Boom
    monkeypatch.setattr(server, "ThreadingHTTPServer", Fake)
    with pytest.raises(Boom):
        server.serve(cfg, port=1234, open_browser=False)
    assert seen["addr"] == ("127.0.0.1", 1234)
