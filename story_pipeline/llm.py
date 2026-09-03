"""Thin Anthropic wrapper. Every agent that thinks goes through here."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import anthropic

_client: anthropic.Anthropic | None = None


# Models that reject an assistant prefill. Seeded with the generations that
# removed it — Sonnet 4.6 and 5, Opus 4.6 and 5, Haiku 4.5 — and added to at
# runtime when a model refuses, so a model renamed in config.yaml costs one
# wasted call rather than failing outright.
_NO_PREFILL: set[str] = {
    "claude-opus-5", "claude-sonnet-5", "claude-haiku-5",
    "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
}


class TruncatedReply(RuntimeError):
    """The reply hit max_tokens and stopped mid-structure. Not a model failure and
    not malformed output — the ceiling was too low."""


class ConfigurationError(RuntimeError):
    """Credentials or account setup are wrong. Retrying will not help, and every
    subsequent call would fail the same way — callers stop rather than continue."""

WORKSPACE_HELP = """Your ANTHROPIC_API_KEY is identity-linked (a personal or service
account key) and is not scoped to a single workspace, so every request has to say
which workspace it acts in.

Two ways to fix it, either is fine:

  1. Set ANTHROPIC_WORKSPACE_ID in .env. Find the id in the ID column of
     Settings -> Workspaces in the Claude Console. It looks like
     wrkspc_01JwQvzr7rXLA5AGx3HKfFUJ.

  2. Create a new key scoped to one workspace (Settings -> API keys -> Create
     key, and set a workspace). A scoped key carries the workspace with it and
     needs no header at all.

The Default Workspace's id is not listed in the Console. Read it from the
anthropic-workspace-id response header of any request that runs there, or use
option 2.
"""


def client() -> anthropic.Anthropic:
    """The shared Anthropic client.

    A key scoped to one workspace carries that workspace with it. An
    identity-linked key that can reach several must name one per request, via the
    `anthropic-workspace-id` header — set it once here rather than at each call
    site. Absent the variable, nothing is sent and a scoped key works as before.
    """
    global _client
    if _client is None:
        # The SDK's own failure here is "Could not resolve authentication
        # method", which names no file and no variable. Check first and say
        # where the key is meant to come from.
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is not set, so no text agent can run.\n\n"
                "The CLI reads .env at the repo root. Either it is missing, has "
                "no ANTHROPIC_API_KEY line, or you are running from a different "
                "directory — every path here resolves against the working "
                "directory, so run from the repo root.\n\n"
                "  cp .env.example .env     # then fill in the key\n\n"
                "An already-exported ANTHROPIC_API_KEY takes precedence over the "
                "file, so a stale empty one in your shell would also do this."
            )
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        headers = {"anthropic-workspace-id": workspace} if workspace else None
        _client = anthropic.Anthropic(default_headers=headers)  # reads ANTHROPIC_API_KEY
    return _client


def complete(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 8000,
    prefill: str | None = None,
) -> tuple[str, dict[str, int]]:
    """Return (text, usage).

    `prefill` puts words in the assistant's mouth so its reply continues them,
    which used to be how a bare JSON object with no preamble was guaranteed. The
    4.6 and 5 generations removed it and reject the request outright, so it is
    attempted only for models not already known to refuse, and the refusal is
    remembered so the retry is paid once per model rather than once per call.
    """
    use_prefill = bool(prefill) and model not in _NO_PREFILL

    def _send(with_prefill: bool):
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        if with_prefill:
            messages.append({"role": "assistant", "content": prefill})
        return client().messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages,
        )

    try:
        resp = _send(use_prefill)
    except anthropic.BadRequestError as e:
        message = str(e)
        # This one is a setup problem, not a bad prompt, and the raw API message
        # does not say where to put the workspace id in this repo. Every call
        # would fail identically, so say what to do rather than repeating it.
        if "anthropic-workspace-id" in message:
            raise ConfigurationError(WORKSPACE_HELP) from None
        if use_prefill and "prefill" in message.lower():
            _NO_PREFILL.add(model)
            use_prefill = False
            resp = _send(False)
        else:
            raise

    kinds: dict[str, int] = {}
    for b in resp.content:
        kinds[b.type] = kinds.get(b.type, 0) + 1
    text = "".join(b.text for b in resp.content if b.type == "text")
    if use_prefill:
        text = prefill + text

    # A reply cut off at the ceiling is the most common way JSON comes back
    # unparseable, and it looks nothing like a model failure — it looks like
    # malformed output. Say which it is here, where the stop reason is still in
    # scope, rather than leaving the parser to guess from a truncated string.
    if getattr(resp, "stop_reason", None) == "max_tokens":
        blocks = ", ".join(f"{n}x {k}" for k, n in sorted(kinds.items())) or "none"
        head = (f"{model} hit the {max_tokens:,}-token ceiling.\n\n"
                f"Spent {resp.usage.output_tokens:,} output tokens across: {blocks}\n\n")
        if not text.strip():
            # Reaching the ceiling with no text at all is a different fault from
            # a reply cut off mid-word, and pointing the reader at a raised
            # max_tokens would be wrong: the budget went somewhere other than
            # the answer. `blocks` above says where — thinking blocks consume
            # the same allowance as the reply, so a model that reasons past the
            # ceiling returns nothing to parse.
            raise TruncatedReply(
                head + "None of it was text, so there is nothing to parse. The "
                "budget went to the blocks listed above rather than to the "
                "reply, which raising the ceiling alone may not fix."
            )
        raise TruncatedReply(
            head + "The reply was cut off mid-sentence, so it cannot be parsed.\n\n"
            "Raise max_tokens for this call — it is a ceiling, not a charge, and "
            "you are billed for the tokens actually produced either way. A reply "
            "this long from a short chapter usually means the model is repeating "
            "itself, so check the tail before simply raising it.\n\n"
            f"Reply ended: ...{text[-300:]}"
        )

    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        # Carried so a parse failure downstream can say whether the model chose
        # to stop or was cut off. Without it, an unparseable reply gives no way
        # to tell a truncation from malformed output, and the two want opposite
        # fixes. `_parse_json` never sees the response object itself.
        "stop_reason": getattr(resp, "stop_reason", None),
    }
    return text, usage


def complete_json(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 8000,
) -> tuple[Any, dict[str, int]]:
    """Same, but parses a JSON object out of the reply.

    A '{' prefill used to guarantee no preamble. Current models reject prefill,
    so the guard against a chatty reply is now the SYSTEM instruction plus
    `_parse_json`, which strips code fences and falls back to the outermost
    balanced object. That covers the preamble case the prefill was there for.

    The prefill is still requested: `complete` skips it for models known to
    refuse and drops it automatically for any other model that does, so this
    keeps working if an older model is ever pinned in config.yaml.
    """
    text, usage = complete(model, system, user, max_tokens, prefill="{")
    try:
        return _parse_json(text), usage
    except ValueError as e:
        # Say what the model did. A reply that stopped on its own but will not
        # parse is a different problem from one that ran out of room, and the
        # message alone cannot distinguish them.
        raise ValueError(
            f"{e}\n\n--- {model} ---\n"
            f"stop_reason: {usage.get('stop_reason')}  "
            f"output_tokens: {usage.get('output_tokens'):,} of {max_tokens:,}"
        ) from None


def _parse_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost balanced object.
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in model reply:\n{text[:500]}")
    # A stack rather than a brace counter, so an unclosed *array* is seen too.
    # `{"segments": [{...}` is short a `]` as well as a `}`, and counting only
    # braces would both misreport it and repair it wrongly below.
    stack: list[str] = []
    in_str, esc = False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
            if not stack:
                return json.loads(text[start : i + 1])
    depth = len(stack)
    # A reply short only its closing brackets is recoverable, and worth
    # recovering: the prose is whole, it has been generated and billed, and the
    # alternative is paying for the whole chapter again to get back the same
    # words plus one character.
    #
    # What makes this safe is upstream. `complete()` raises TruncatedReply when
    # `stop_reason` is `max_tokens`, before anything reaches the parser — so a
    # reply that ran out of room never arrives here. Everything that does is a
    # reply the model chose to end, where a missing brace is a formatting slip
    # rather than lost content.
    #
    # The tail condition guards the rest: `}` or `]` means a whole element of a
    # container finished. A tail of `"` is deliberately NOT enough — a cut
    # landing on the closing quote of a value leaves a *valid* object missing
    # its later fields, which parses cleanly and hides the gap. Anything ending
    # mid-sentence, mid-key or mid-number still raises.
    if stack and not in_str:
        tail = text.rstrip().rstrip(",")
        if tail and tail[-1] in "}]":
            try:
                return json.loads(tail[start:] + "".join(reversed(stack)))
            except json.JSONDecodeError:
                pass

    # Show both ends. The head says what the model was doing; the tail is where
    # the object stopped closing, which is the half that identifies the cause —
    # a mid-word ending means truncation, a complete-looking ending means the
    # braces are genuinely mismatched.
    raise ValueError(
        f"unbalanced JSON in model reply ({len(text):,} chars, "
        f"{depth} unclosed brace{'s' if depth != 1 else ''}):\n\n"
        f"--- first 400 ---\n{text[:400]}\n\n"
        f"--- last 400 ---\n{text[-400:]}"
    )
