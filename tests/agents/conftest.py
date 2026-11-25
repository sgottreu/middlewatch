"""Fixtures for the LLM-layer / text-agent tests: a fake Anthropic client and a
scripted `llm.complete_json` / `llm.complete`."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from story_pipeline import llm


def block(type_: str, text: str | None = None):
    b = SimpleNamespace(type=type_)
    if text is not None:
        b.text = text
    return b


def message(text="ok", stop_reason="end_turn", in_tok=11, out_tok=7, blocks=None):
    return SimpleNamespace(
        content=blocks if blocks is not None else [block("text", text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


def bad_request(msg: str) -> anthropic.BadRequestError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req)
    return anthropic.BadRequestError(msg, response=resp, body=None)


class FakeClient:
    """Plays back `script` (messages or exceptions) one per stream() call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        outer = self

        class _Ctx:
            def __enter__(self_inner):
                if isinstance(item, Exception):
                    raise item
                return self_inner

            def __exit__(self_inner, *a):
                return False

            @property
            def text_stream(self_inner):
                # Each text block arrives in two pieces, as a real stream splits it.
                for b in item.content:
                    if b.type == "text" and b.text:
                        mid = len(b.text) // 2
                        yield from (s for s in (b.text[:mid], b.text[mid:]) if s)

            def get_final_message(self_inner):
                return item

        return _Ctx()


@pytest.fixture
def fake_client(monkeypatch):
    """Install a FakeClient; returns a factory taking the script."""
    monkeypatch.setattr(llm, "_NO_PREFILL", set(llm._NO_PREFILL))

    def install(*script):
        fc = FakeClient(script)
        monkeypatch.setattr(llm, "_client", fc)
        return fc

    return install


# --------------------------------------------------------------------------- #
# Scripted llm.complete_json / llm.complete
# --------------------------------------------------------------------------- #


class ScriptedLLM:
    """Replaces llm.complete_json and llm.complete. Each call pops the next item
    from the matching queue (an Exception is raised, anything else returned) and
    records (model, system, user, max_tokens)."""

    def __init__(self):
        self.json_queue: list = []
        self.text_queue: list = []
        self.json_calls: list[dict] = []
        self.text_calls: list[dict] = []
        self.usage = {"input_tokens": 100, "output_tokens": 50, "stop_reason": "end_turn"}

    def complete_json(self, model, system, user, max_tokens=8000):
        self.json_calls.append(dict(model=model, system=system, user=user,
                                    max_tokens=max_tokens))
        if not self.json_queue:
            raise AssertionError("unexpected llm.complete_json call")
        item = self.json_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, dict(self.usage)

    def complete(self, model, system, user, max_tokens=8000, prefill=None):
        self.text_calls.append(dict(model=model, system=system, user=user,
                                    max_tokens=max_tokens))
        if not self.text_queue:
            raise AssertionError("unexpected llm.complete call")
        item = self.text_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, dict(self.usage)


@pytest.fixture
def scripted(monkeypatch):
    s = ScriptedLLM()
    monkeypatch.setattr(llm, "complete_json", s.complete_json)
    monkeypatch.setattr(llm, "complete", s.complete)
    return s
