"""Config: a YAML file plus environment for credentials.

Precedence is flag > bundle-pinned > config file > default. Anything that would
change how an existing story re-renders gets pinned into the bundle manifest on
first use, so re-running months later produces the same thing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "stories_dir": "stories",
    "bibles_dir": "bibles",
    # Target runtime in minutes. See LENGTHS in agents/text.py for what each
    # setting implies; --length on `new` overrides it per story, and whichever
    # wins is pinned into the manifest.
    "length_min": 15,
    # Credited in bible changelog entries. None falls back to `git config
    # user.name`, then the login name. See changelog.resolve_author.
    "author": None,
    # --- text models ---
    "models": {
        "ideator": "claude-sonnet-5",
        "writer": "claude-opus-5",
        "editor": "claude-sonnet-5",
        "designer": "claude-sonnet-5",
    },
    # Reply ceilings, per agent. A ceiling is not a charge — you pay for the
    # tokens produced, so these are only a stop on a runaway reply. They live
    # here rather than at the call sites because hitting one fails a whole
    # stage, and that should be fixable in config.yaml rather than in source.
    #
    # The ideator and editor are the two that need room: an outline carries
    # every chapter's beats, and a verdict carries a quote and a replacement
    # for each finding. The writer's is per chapter and comfortable at 8,000.
    "max_tokens": {
        "ideator": 16000,
        "writer": 8000,
        "editor": 16000,
        "designer": 8000,
    },
    "max_edit_passes": 2,
    # How far a chapter may drift from its word target before it is sent back
    # to be cut. Read by the writer's prompt and by the check that enforces it,
    # so the instruction and the rule cannot disagree. Raise it to let chapters
    # run long; a revision costs one writer call plus one editor call.
    "length_tolerance": 0.20,
    # Fallback art direction, used only when a bundle has nothing pinned. Every
    # story pins one at ideate from its bible or its genre, so this stays None
    # and the designer raises a readable error rather than a KeyError if it is
    # ever reached. Set a string here to give styleless bundles a house look.
    "art_style": None,
    # How many of a genre's tropes the ideator sees per run. Showing the whole
    # list every time makes it reach for the same three; a rotating subset is the
    # cheapest defence against a channel of stories that all rhyme.
    "trope_sample": 8,
    # --- narration ---
    "tts_provider": "elevenlabs",  # elevenlabs | polly
    # Provider-independent casting. Genre files override narrator and
    # character_voices; those names resolve through the provider's voice library.
    "casting": {
        "narrator": "Charlotte",
        "character_voices": {
            "female": ["Alice", "Lily", "Matilda"],
            "male": ["Daniel", "George", "Brian"],
        },
        # solo: one voice reads everything, the way an audiobook is read. It is
        # the default because a cast pays for itself in seams — every change of
        # speaker is a separate generation with its own start and end, and a
        # chapter of dialogue has dozens. cast: a voice per character, kept for
        # the stories that want it.
        "mode": "solo",
        "solo_voice": None,        # defaults to the narrator
        "max_named_voices": 3,
        "gap_ms": 400,
        # The silence a scene break becomes. Long enough not to be heard as the
        # pause between paragraphs (gap_ms), short enough not to be heard as the
        # end of the chapter.
        "break_ms": 2000,
        # Spoken before each chapter. Generated at record time from this
        # template rather than written by the model, which was inconsistent in
        # a way only the audio revealed: one story announced seven of eight
        # chapters, the next announced none. `{n}` and `{title}` are the
        # available fields; null turns announcements off entirely.
        "chapter_announcement": "Chapter {n}. {title}.",
    },
    "elevenlabs": {
        "model": "eleven_multilingual_v2",
        # 44.1kHz PCM needs a Pro plan; 24kHz doesn't and is ample for narration
        # over stills. PCM rather than mp3 keeps the stitch lossless.
        "output_format": "pcm_24000",
        "text_normalization": "auto",
        "seed": None,          # set an int to make re-records deterministic
        "voice_settings": None,
        "usd_per_1k_credits": 0.22,  # Creator tier at time of writing; verify
        # Names used by genre files and casting, mapped to voice IDs. Run
        # `cli voices` to list your account's library and correct these.
        "voice_library": {
            "Charlotte": "XB0fDUnXU5powFXDhCwa",
            "Alice": "Xb7hH8MzeJPuXlfP81I8",
            "Lily": "pFZP5JQG7iQjIQuC4Bku",
            "Matilda": "XrExE9yKIg1WjnnlVkGX",
            "George": "JBFqnCBsd6RMkjVDRZzb",
            "Daniel": "onwK4e9ZLuTAKqWW03F9",
            "Brian": "nPczCjzI2devNBz1zQrb",
            "Rachel": "21m00Tcm4TlvDq8ikWAM",
        },
    },
    "polly": {
        "engine": "generative",
        "region": "us-east-1",
        "profile": None,
        "sample_rate": 16000,
    },
    # --- images ---
    "gemini": {
        "model": "gemini-3.1-flash-image",
        "aspect_ratio": "16:9",
        # The image API accepts JPEG only; PNG is refused with a 400.
        "image_mime": "image/jpeg",
        # Only for the "this will cost about" line. The ledger prices images
        # from ledger/rates.json, which is the figure that has to be right.
        "usd_per_image": 0.05,
        "image_size": "2K",
        "thinking_level": "high",
        # null derives the count from the story's length, holding the cadence at
        # roughly one image a minute. Set an integer to override that everywhere.
        "scenes_per_chapter": None,
        "max_character_refs": 4,  # 3.1 Flash Image ceiling for character consistency
    },
    # --- video ---
    "video": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "burn_subtitles": False,
        "ken_burns": True,
        "crossfade_ms": 800,
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | Path | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    p = Path(path) if path else Path("config.yaml")
    if p.exists():
        cfg = _deep_merge(cfg, yaml.safe_load(p.read_text()) or {})
    return cfg


def load_env(path: str | Path = ".env") -> list[str]:
    """Read `.env` into the environment. Returns the names it set.

    `.env.example` has always said "copy to .env", and nothing ever read it —
    every command that calls a model worked only if you had sourced the file
    into your shell yourself. That is fine for a script that does the sourcing
    and invisible everywhere else: `cli review` would start happily and then
    fail on the first paid call with the SDK's own "could not resolve
    authentication method", which names nothing you can act on.

    Stdlib only, and deliberately small: KEY=VALUE, `export` allowed, `#`
    comments, optional surrounding quotes. Anything already exported wins, so a
    variable set for one command is not overridden by the file.
    """
    p = Path(path)
    if not p.exists():
        return []
    loaded = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value
        loaded.append(key)
    return loaded


def require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} is not set. See .env.example.")
    return v


def prompt_text(name: str) -> str:
    return (Path(__file__).parent / "prompts" / f"{name}.md").read_text()
