"""llm.complete / complete_json with an on_text callback (streamed text)."""

from __future__ import annotations

from story_pipeline import llm

from .conftest import block, message


def test_on_text_receives_pieces_and_return_is_unchanged(fake_client):
    fake_client(message("Hello there, chapter one."))
    pieces: list[str] = []
    text, usage = llm.complete("claude-opus-5", "s", "u", on_text=pieces.append)
    assert len(pieces) >= 2 and "".join(pieces) == "Hello there, chapter one."
    assert text == "Hello there, chapter one."
    assert usage["output_tokens"] == 7 and usage["stop_reason"] == "end_turn"


def test_on_text_skips_non_text_blocks(fake_client):
    fake_client(message(blocks=[block("thinking"), block("text", "kept")]))
    pieces: list[str] = []
    text, _ = llm.complete("claude-opus-5", "s", "u", on_text=pieces.append)
    assert "".join(pieces) == "kept" and text == "kept"


def test_without_on_text_the_stream_is_not_iterated(fake_client):
    fake_client(message("plain"))
    assert llm.complete("claude-opus-5", "s", "u")[0] == "plain"


def test_complete_json_passes_on_text_through(fake_client):
    fake_client(message('{"a": 1}'))
    pieces: list[str] = []
    obj, _ = llm.complete_json("claude-opus-5", "s", "u", on_text=pieces.append)
    assert obj == {"a": 1} and "".join(pieces) == '{"a": 1}'


def test_on_text_still_raises_truncated_after_streaming(fake_client):
    import pytest
    fake_client(message("cut off mid", stop_reason="max_tokens"))
    pieces: list[str] = []
    with pytest.raises(llm.TruncatedReply):
        llm.complete("claude-opus-5", "s", "u", on_text=pieces.append)
    assert "".join(pieces) == "cut off mid"
