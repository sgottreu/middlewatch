"""Record what an LLM call cost: unit counts into the manifest, rows into the ledger.

One place for it, so every agent records the same way and no call site
hand-builds the ledger's story dict (`Bundle.ledger_story()` does that).
"""

from __future__ import annotations

import sys
from typing import Any

# Manifest keys follow the other stages' pattern (`gemini_images`,
# `elevenlabs_credits`): provider, then unit.
_TOKEN_KEYS = (
    ("input_tokens", "anthropic_input_tokens"),
    ("output_tokens", "anthropic_output_tokens"),
    ("cache_creation_input_tokens", "anthropic_cache_write_tokens"),
    ("cache_read_input_tokens", "anthropic_cache_read_tokens"),
)


def record_llm(b, stage: str, model: str, usage: dict[str, Any] | None,
               notes: str = "") -> None:
    """Count one Anthropic call against bundle `b` and write its ledger rows.

    Never raises. The reply has already been paid for, and losing a finished
    chapter to a bookkeeping error (an unpriced model in rates.json, an old
    manifest with no `pipeline`) would cost the call a second time. A ledger
    failure is printed to stderr instead; the manifest counts are kept either way.
    """
    if not usage:
        return
    try:
        for src, key in _TOKEN_KEYS:
            n = usage.get(src) or 0
            if n:
                b.add_spend(key, n)
    except Exception as e:  # noqa: BLE001 — bookkeeping must not lose the output
        print(f"warning: could not record {stage} tokens in the manifest: {e}",
              file=sys.stderr)
    try:
        from ledger import ledger
        ledger.record_anthropic(story=b.ledger_story(), stage=stage, model=model,
                                usage=usage, notes=notes)
    except Exception as e:  # noqa: BLE001
        print(f"warning: {stage} cost not written to the ledger: {e}", file=sys.stderr)
