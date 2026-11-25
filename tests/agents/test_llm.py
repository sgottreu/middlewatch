"""story_pipeline.llm: client construction, complete(), complete_json(), _parse_json."""

from __future__ import annotations

import json

import anthropic
import pytest

from story_pipeline import llm

from .conftest import bad_request, block, message


# --------------------------------------------------------------------------- #
# client()
# --------------------------------------------------------------------------- #


class _Recorder:
    def __init__(self, **kw):
        self.kw = kw


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(anthropic, "Anthropic", _Recorder)


def test_client_missing_key_raises_configuration_error(monkeypatch, recorder):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(llm.ConfigurationError, match="ANTHROPIC_API_KEY is not set"):
        llm.client()


def test_client_blank_key_counts_as_missing(monkeypatch, recorder):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    with pytest.raises(llm.ConfigurationError):
        llm.client()


def test_client_without_workspace_sends_no_headers(recorder):
    c = llm.client()
    assert c.kw == {"default_headers": None}


def test_client_workspace_id_becomes_header(monkeypatch, recorder):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "  wrkspc_123  ")
    assert llm.client().kw["default_headers"] == {"anthropic-workspace-id": "wrkspc_123"}


def test_client_is_cached(recorder):
    assert llm.client() is llm.client()


def test_configuration_error_is_runtime_error_and_truncated_is_distinct():
    assert issubclass(llm.ConfigurationError, RuntimeError)
    assert issubclass(llm.TruncatedReply, RuntimeError)
    assert not issubclass(llm.TruncatedReply, llm.ConfigurationError)


# --------------------------------------------------------------------------- #
# complete()
# --------------------------------------------------------------------------- #


def test_complete_returns_text_and_usage_shape(fake_client):
    fc = fake_client(message("hello", in_tok=5, out_tok=9))
    text, usage = llm.complete("claude-sonnet-5", "SYS", "USER", max_tokens=123)
    assert text == "hello"
    assert usage == {"input_tokens": 5, "output_tokens": 9, "stop_reason": "end_turn"}
    call = fc.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["system"] == "SYS"
    assert call["max_tokens"] == 123
    assert call["messages"] == [{"role": "user", "content": "USER"}]


def test_complete_joins_only_text_blocks(fake_client):
    fake_client(message(blocks=[block("thinking"), block("text", "a"), block("text", "b")]))
    text, _ = llm.complete("claude-sonnet-5", "s", "u")
    assert text == "ab"


def test_complete_default_max_tokens_is_8000(fake_client):
    fc = fake_client(message())
    llm.complete("claude-sonnet-5", "s", "u")
    assert fc.calls[0]["max_tokens"] == 8000


def test_prefill_sent_and_prepended_for_model_that_accepts_it(fake_client):
    fc = fake_client(message('"a": 1}'))
    text, _ = llm.complete("claude-3-old", "s", "u", prefill="{")
    assert fc.calls[0]["messages"][-1] == {"role": "assistant", "content": "{"}
    assert text == '{"a": 1}'


@pytest.mark.parametrize("model", sorted(llm._NO_PREFILL))
def test_no_prefill_models_never_get_a_prefill(fake_client, model):
    fc = fake_client(message("plain"))
    text, _ = llm.complete(model, "s", "u", prefill="{")
    assert len(fc.calls[0]["messages"]) == 1
    assert text == "plain"          # not prefixed either


def test_empty_prefill_is_not_sent(fake_client):
    fc = fake_client(message("x"))
    llm.complete("claude-3-old", "s", "u", prefill="")
    assert len(fc.calls[0]["messages"]) == 1


def test_prefill_refusal_retries_without_and_remembers_model(fake_client):
    fc = fake_client(bad_request("assistant message prefill is not supported"),
                     message("clean"), message("clean2"))
    text, _ = llm.complete("claude-newmodel", "s", "u", prefill="{")
    assert text == "clean"
    assert len(fc.calls) == 2
    assert len(fc.calls[0]["messages"]) == 2 and len(fc.calls[1]["messages"]) == 1
    assert "claude-newmodel" in llm._NO_PREFILL
    # second call to the same model pays no wasted request
    llm.complete("claude-newmodel", "s", "u", prefill="{")
    assert len(fc.calls) == 3 and len(fc.calls[2]["messages"]) == 1


def test_unrelated_bad_request_is_reraised(fake_client):
    fake_client(bad_request("max_tokens too large"))
    with pytest.raises(anthropic.BadRequestError):
        llm.complete("claude-3-old", "s", "u", prefill="{")


def test_bad_request_on_prefill_free_call_with_prefill_word_is_reraised(fake_client):
    # use_prefill False -> no retry even if the message mentions prefill
    fake_client(bad_request("prefill whatever"))
    with pytest.raises(anthropic.BadRequestError):
        llm.complete("claude-sonnet-5", "s", "u")


def test_workspace_error_becomes_configuration_error_with_help(fake_client):
    fake_client(bad_request("anthropic-workspace-id header is required"))
    with pytest.raises(llm.ConfigurationError) as ei:
        llm.complete("claude-sonnet-5", "s", "u")
    assert "ANTHROPIC_WORKSPACE_ID" in str(ei.value)


def test_workspace_error_wins_even_when_prefill_attempted(fake_client):
    fake_client(bad_request("anthropic-workspace-id missing; prefill"))
    with pytest.raises(llm.ConfigurationError):
        llm.complete("claude-3-old", "s", "u", prefill="{")


def test_truncated_with_text_raises_truncated_reply_with_tail(fake_client):
    fake_client(message("x" * 50 + "THE-END", stop_reason="max_tokens", out_tok=100,
                        blocks=[block("text", "x" * 50 + "THE-END")]))
    with pytest.raises(llm.TruncatedReply) as ei:
        llm.complete("claude-sonnet-5", "s", "u", max_tokens=100)
    msg = str(ei.value)
    assert "100-token ceiling" in msg and "cut off mid-sentence" in msg
    assert "THE-END" in msg and "1x text" in msg


def test_truncated_with_no_text_says_budget_went_elsewhere(fake_client):
    fake_client(message(stop_reason="max_tokens", blocks=[block("thinking")], out_tok=5))
    with pytest.raises(llm.TruncatedReply) as ei:
        llm.complete("claude-sonnet-5", "s", "u", max_tokens=5)
    assert "None of it was text" in str(ei.value)
    assert "1x thinking" in str(ei.value)


def test_truncated_with_prefill_only_still_counts_as_text(fake_client):
    # With a prefill the prefix is prepended, so text is non-empty
    fake_client(message("", stop_reason="max_tokens", blocks=[]))
    with pytest.raises(llm.TruncatedReply) as ei:
        llm.complete("claude-3-old", "s", "u", prefill="{")
    assert "cut off mid-sentence" in str(ei.value)


# --------------------------------------------------------------------------- #
# complete_json()
# --------------------------------------------------------------------------- #


def test_complete_json_parses_plain_object(fake_client):
    fake_client(message('{"a": 1}'))
    obj, usage = llm.complete_json("claude-sonnet-5", "s", "u")
    assert obj == {"a": 1}
    assert usage["output_tokens"] == 7


def test_complete_json_requests_brace_prefill_from_old_models(fake_client):
    fc = fake_client(message('"a": 2}'))
    obj, _ = llm.complete_json("claude-3-old", "s", "u")
    assert fc.calls[0]["messages"][-1] == {"role": "assistant", "content": "{"}
    assert obj == {"a": 2}


def test_complete_json_strips_fences_and_preamble(fake_client):
    fake_client(message('Here you go:\n```json\n{"a": [1, 2]}\n```\nEnjoy'))
    obj, _ = llm.complete_json("claude-sonnet-5", "s", "u")
    assert obj == {"a": [1, 2]}


def test_complete_json_failure_names_model_and_stop_reason(fake_client):
    fake_client(message("no json here at all", out_tok=1234))
    with pytest.raises(ValueError) as ei:
        llm.complete_json("claude-sonnet-5", "s", "u", max_tokens=5000)
    msg = str(ei.value)
    assert "no JSON object" in msg
    assert "claude-sonnet-5" in msg and "stop_reason: end_turn" in msg
    assert "1,234 of 5,000" in msg


def test_complete_json_propagates_truncated_reply(fake_client):
    fake_client(message('{"a": "cut', stop_reason="max_tokens"))
    with pytest.raises(llm.TruncatedReply):
        llm.complete_json("claude-sonnet-5", "s", "u")


# --------------------------------------------------------------------------- #
# _parse_json
# --------------------------------------------------------------------------- #

P = llm._parse_json


def test_parse_plain():
    assert P(' {"a": 1} ') == {"a": 1}


def test_parse_fence_without_language_tag():
    assert P('```\n{"a": 1}\n```') == {"a": 1}


def test_parse_preamble_then_object():
    assert P('Sure! {"a": {"b": 2}} done.') == {"a": {"b": 2}}


def test_parse_braces_inside_strings_do_not_confuse_balance():
    assert P('x {"a": "}{ \\" }", "b": 1} y') == {"a": '}{ " }', "b": 1}


def test_parse_ignores_trailing_text_after_balanced_object():
    assert P('{"a": 1} and {"b": 2}') == {"a": 1}


def test_parse_missing_closing_braces_recovered_after_complete_element():
    got = P('{"segments": [{"speaker": "narrator", "text": "hi"}')
    assert got == {"segments": [{"speaker": "narrator", "text": "hi"}]}


def test_parse_recovers_trailing_comma_before_missing_close():
    got = P('{"a": [{"x": 1},')
    assert got == {"a": [{"x": 1}]}


def test_parse_does_not_recover_when_tail_is_a_string_value():
    # cut at the closing quote of a value: valid-looking but missing fields
    with pytest.raises(ValueError, match="unbalanced JSON"):
        P('{"a": "done"')


def test_parse_does_not_recover_mid_word():
    with pytest.raises(ValueError, match="unbalanced JSON"):
        P('{"a": [{"x": 1}, {"y": "trunc')


def test_parse_unbalanced_message_counts_unclosed_braces():
    with pytest.raises(ValueError, match=r"2 unclosed braces"):
        P('{"a": {"b": 1')


def test_parse_singular_brace_wording():
    with pytest.raises(ValueError, match=r"1 unclosed brace\)"):
        P('{"a": 1,')


def test_parse_no_object_raises():
    with pytest.raises(ValueError, match="no JSON object"):
        P("just words")


def test_parse_top_level_array_is_not_an_object():
    # json.loads accepts a bare array on the fast path, so it passes through
    assert P("[1, 2]") == [1, 2]


def test_parse_mismatched_brackets_raise():
    with pytest.raises(ValueError):
        P('{"a": [1, 2}')
