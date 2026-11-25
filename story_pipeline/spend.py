"""Record what a paid call cost: unit counts into the manifest, rows into the ledger.

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


def _ledger(stage: str, fn, **kw) -> None:
    try:
        from ledger import ledger
        getattr(ledger, fn)(stage=stage, **kw)
    except Exception as e:  # noqa: BLE001 — the image or audio is already paid for
        print(f"warning: {stage} cost not written to the ledger: {e}", file=sys.stderr)


def record_images(b, stage: str, model: str, images: int = 1, notes: str = "") -> None:
    """Ledger rows for generated images. The manifest's `gemini_images` count is
    still kept by the caller, as before. Never raises."""
    try:
        story = b.ledger_story()
    except Exception as e:  # noqa: BLE001
        print(f"warning: {stage} cost not written to the ledger: {e}", file=sys.stderr)
        return
    _ledger(stage, "record_gemini", story=story, model=model, images=images, notes=notes)


def record_tts(b, stage: str, provider: str, model: str, units: float,
               notes: str = "") -> None:
    """Ledger rows for narration, in the unit the provider bills: ElevenLabs
    credits, Polly characters. The manifest count is still kept by the caller.
    An unknown provider is skipped with a warning. Never raises."""
    if not units:
        return
    try:
        story = b.ledger_story()
    except Exception as e:  # noqa: BLE001
        print(f"warning: {stage} cost not written to the ledger: {e}", file=sys.stderr)
        return
    if provider == "elevenlabs":
        _ledger(stage, "record_elevenlabs", story=story, model=model, credits=units, notes=notes)
    elif provider == "polly":
        _ledger(stage, "record_polly", story=story, engine=model,
                billed_characters=units, notes=notes)
    else:
        print(f"warning: {stage} cost not written to the ledger: "
              f"no ledger mapping for TTS provider {provider!r}", file=sys.stderr)
