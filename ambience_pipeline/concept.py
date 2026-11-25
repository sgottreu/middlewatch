"""The concept agent: a scene or a story brief -> `concept.json` layers.

One Anthropic call through `llm.complete_json`, with `models.concept` and
`max_tokens.concept` from config. The system prompt is
`prompts/concept.md` with the recipe catalogue, the roles and the mixer's
conventions filled in from code — `REGISTRY`, `bundle.ROLES` and the
validator's bounds — so the prompt cannot offer a recipe or a parameter the
mixer no longer has.

The reply is checked by the same validator the gate uses
(`bundle.validate_concept`), plus the recipes' advisory ranges, which bind the
agent but not a hand edit. A reply with problems is sent back once with the
problems listed; a second failure raises `ConceptError` and leaves
concept.json as it was. Every call — the retry too — is recorded with
`spend.record_llm(b, "concept", ...)`, so the manifest and the ledger see it
with `pipeline="ambience"`.

Procedural layers only: generated layers are blocked on ElevenLabs commercial
rights, and the validator rejects them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from . import bundle as ab
from .bundle import AmbienceBundle

STAGE = "concept"
PROMPT = Path(__file__).with_name("prompts") / "concept.md"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 8000
# The reply keys kept from the model. Everything else in concept.json — slug,
# title, series, hours, seed — comes from the manifest, never from the model.
REPLY_KEYS = ("scene", "layers", "notes")

ROLE_TEXT = {
    "bed": "continuous and always audible — the floor of the mix. One or two, never "
           "more. Rain, wind, surf, room tone.",
    "texture": "semi-continuous, below the bed, slowly modulated. Fire crackle, a "
               "second rain surface, distant surf behind a room.",
    "accent": "occasional swells of a recipe, scheduled at random — a gust, a flare of "
              "the fire, a wave that breaks harder. Sparse.",
    "tonal": "an optional low, steady layer, very quiet. Usually absent; leave it out "
             "unless the scene asks for a drone.",
}


class ConceptError(RuntimeError):
    """The agent could not produce a renderable concept in two tries."""

    def __init__(self, msg: str, problems: list[str] | None = None):
        super().__init__(msg)
        self.problems = problems or []


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #


def _fmt(v: Any) -> str:
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def recipe_catalogue() -> str:
    """Every recipe with its parameters, defaults and ranges — read from REGISTRY."""
    from .synth.recipes import REGISTRY

    lines = ["### Recipes", ""]
    for name, r in sorted(REGISTRY.items()):
        lines.append(f"- **{name}** — {r.summary}")
        for k, default in r.params.items():
            if k == "gain_db":
                continue
            rng = r.ranges.get(k)
            if isinstance(rng, tuple) and rng and all(isinstance(x, str) for x in rng):
                span = "one of " + ", ".join(f'"{x}"' for x in rng)
            elif isinstance(rng, tuple) and len(rng) == 2:
                span = f"{_fmt(rng[0])} to {_fmt(rng[1])}"
            else:
                span = "no stated range"
            lines.append(f"  - `{k}`: default {_fmt(default)}; {span}")
        lines.append(f"  - its level at defaults is about {_fmt(r.params.get('gain_db'))} "
                     "LUFS alone; the layer's `gain_db` replaces that")
    return "\n".join(lines)


def roles_block() -> str:
    return "\n".join(f"- **{r}** — {ROLE_TEXT.get(r, '')}" for r in ab.ROLES)


def conventions_block() -> str:
    from . import mix

    lo, hi = ab.GAIN_DB
    dlo, dhi = ab.DENSITY_PER_MIN
    elo, ehi = ab.EVENT_S
    scales = ", ".join(f"{h} h ×{spec['accent_density_scale']:g}"
                       for h, spec in sorted(ab.HOURS.items()))
    return "\n".join([
        f"- `gain_db` is the layer's approximate loudness alone, in LUFS, and it sets "
        f"the *balance*. Between {lo:g} and {hi:g}. A bed sits around -20, a texture "
        f"4 to 10 dB under the bed, a tonal layer lower still. The whole mix is then "
        f"trimmed to {mix.TARGET_LUFS:g} LUFS, so only the differences between layers "
        f"matter.",
        "- An accent may not be louder than the loudest bed.",
        f"- Accents need `density_per_min`, {dlo:g} to {dhi:g}: the mean number of "
        f"swells per minute before the length scale ({scales}). 0.1 to 1 is the "
        f"useful range; above that an accent stops being occasional. `event_s` "
        f"({elo:g} to {ehi:g}, default {mix.DEFAULT_EVENT_S:g}) is each swell's length.",
        "- `pan` is -1 (left) to 1 (right). `hpf_hz` / `lpf_hz` are 20 to 20000 and "
        "the high-pass must sit below the low-pass — use them to darken a layer "
        "heard through a wall or a window.",
        "- `movement` is `{\"type\": \"lfo\", \"depth_db\": 0-12, \"period_s\": 5-3600}`: a "
        "slow swell in a layer's level. A few dB over a minute or two keeps a bed alive.",
        f"- At most {ab.MAX_LAYERS} layers and {ab.MAX_BEDS} beds; three to five layers "
        "is typical. Layers of the same recipe are fine — each gets its own seed.",
        "- Nothing repeats: each recipe is generated noise, so an eight-hour render "
        "needs no loop points and no variation tricks.",
    ])


def system_prompt() -> str:
    out = PROMPT.read_text()
    for key, value in (("recipes", recipe_catalogue()), ("roles", roles_block()),
                       ("conventions", conventions_block())):
        out = out.replace("{" + key + "}", value)
    left = re.findall(r"\{(recipes|roles|conventions)\}", out)
    if left:
        raise RuntimeError(f"concept.md placeholders survived: {left}")
    return out


# --------------------------------------------------------------------------- #
# The user message
# --------------------------------------------------------------------------- #


def bible_block(b: AmbienceBundle, cfg: dict) -> str:
    """The pinned bible as the agents see it, or "" if there is none or it will
    not load (a story's bible going missing should not stop its ambience)."""
    name = b.pinned("bible")
    if not name:
        return ""
    try:
        from story_pipeline import bibles
        return bibles.load(name, cfg.get("bibles_dir") or "bibles").prompt_block()
    except Exception as e:  # noqa: BLE001
        print(f"  (skipping bible {name!r} in the concept prompt: {e})")
        return ""


def user_message(b: AmbienceBundle, cfg: dict, *, current: dict | None = None,
                 note: str = "", brief: str | None = None) -> str:
    hours = b.hours
    scale = ab.hours_spec(hours)["accent_density_scale"]
    brief = brief if brief is not None else (
        b.brief_path.read_text() if b.brief_path.is_file() else "")
    parts = [f"Length: {hours} hour{'s' if hours != 1 else ''} "
             f"(accent densities are scaled ×{scale:g} at this length).",
             "", "## Brief", "", brief.strip() or "(no brief)"]
    bb = bible_block(b, cfg)
    if bb:
        parts += ["", "## Series bible", "", bb.strip()]
    if current is not None:
        keep = {k: current[k] for k in REPLY_KEYS if k in current}
        parts += ["", "## The current concept", "",
                  json.dumps(keep, indent=2, ensure_ascii=False),
                  "", "## Revise it", "",
                  note.strip() or "(no note)",
                  "", "Change what the note asks for and keep the rest as it is — same "
                  "ids for layers that stay. Reply with the whole revised concept."]
    else:
        parts += ["", "Design the soundscape for this brief."]
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# Checking the reply
# --------------------------------------------------------------------------- #


def range_problems(concept: dict) -> list[str]:
    """Recipe parameters outside their advisory ranges. Binds the agent only."""
    from .synth.recipes import REGISTRY

    out = []
    for L in concept.get("layers") or []:
        if not isinstance(L, dict) or not isinstance(L.get("recipe"), dict):
            continue
        r = REGISTRY.get(L["recipe"].get("type"))
        if r is None:
            continue
        for k, v in L["recipe"].items():
            rng = r.ranges.get(k)
            if not isinstance(rng, tuple) or len(rng) != 2 or isinstance(rng[0], str):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and not rng[0] <= v <= rng[1]:
                out.append(f"layer {L.get('id')!r}: {r.name}.{k} {v:g} is outside "
                           f"{rng[0]:g} to {rng[1]:g}")
    return out


def assemble(b: AmbienceBundle, reply: Any) -> tuple[dict, list[str]]:
    """The concept the reply describes, with the manifest's pins, and its problems."""
    if not isinstance(reply, dict):
        return {}, ["the reply must be a JSON object with a `layers` list"]
    m = b.manifest()
    p = m.get("pinned", {})
    concept = {"slug": b.slug, "title": p.get("title", ""), "series": p.get("series", ""),
               "hours": b.hours, "seed": b.seed}
    for k in REPLY_KEYS:
        if k in reply:
            concept[k] = reply[k]
    layers = concept.get("layers")
    if isinstance(layers, list):
        # The one field with a single legal value; a missing one is not worth a retry.
        concept["layers"] = [dict(L, source=L.get("source", "procedural"))
                             if isinstance(L, dict) else L for L in layers]
    problems = ab.validate_concept(concept)
    if not problems:
        problems = range_problems(concept)
    return concept, problems


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #


def _model(cfg: dict) -> tuple[str, int]:
    model = (cfg.get("models") or {}).get("concept") or DEFAULT_MODEL
    try:
        from story_pipeline.config import DEFAULTS
        shipped = DEFAULTS.get("max_tokens", {}).get("concept", DEFAULT_MAX_TOKENS)
    except Exception:  # noqa: BLE001
        shipped = DEFAULT_MAX_TOKENS
    return model, int((cfg.get("max_tokens") or {}).get("concept", shipped))


def _archive(b: AmbienceBundle) -> Path | None:
    """Keep the concept about to be replaced as concept.N.json (N = 1, 2, …)."""
    if not b.concept_path.is_file():
        return None
    n = 1
    while (b.root / f"concept.{n}.json").exists():
        n += 1
    dest = b.root / f"concept.{n}.json"
    dest.write_text(b.concept_path.read_text())
    return dest


def history(b: AmbienceBundle) -> list[Path]:
    found = [p for p in b.root.glob("concept.*.json")
             if re.fullmatch(r"concept\.\d+\.json", p.name)]
    return sorted(found, key=lambda p: int(p.name.split(".")[1]))


def draft(b: AmbienceBundle, cfg: dict, *, note: str = "", revise: bool = False,
          on_text: Callable[[str], None] | None = None) -> dict:
    """Run the agent and write concept.json. Returns the new concept.

    `revise` sends the current concept and `note` and asks for a revision;
    otherwise the agent designs from the brief alone. Either way the previous
    concept.json is kept as concept.N.json, and the concept goes back to
    `awaiting_review` — a new concept is never approved by inheritance.
    """
    from story_pipeline import llm, spend

    model, max_tokens = _model(cfg)
    current = None
    if revise:
        if not b.concept_path.is_file():
            raise ConceptError(f"{b.slug} has no concept.json to revise")
        current = b.concept()
        if not note.strip():
            raise ConceptError("a revision needs a note saying what to change")
    system = system_prompt()
    user = user_message(b, cfg, current=current, note=note)
    previous = b.stage_status(STAGE)
    b.set_stage(STAGE, "running")
    problems: list[str] = []
    try:
        for attempt in (1, 2):
            reply, usage = _call(llm, model, system, user, max_tokens, on_text)
            spend.record_llm(b, STAGE, model, usage,
                             notes="revise" if revise else ("retry" if attempt == 2 else ""))
            concept, problems = assemble(b, reply)
            if not problems:
                break
            if attempt == 1:
                print(f"  the concept had {len(problems)} problem"
                      f"{'s' if len(problems) != 1 else ''}; asking once more:", flush=True)
                for p in problems:
                    print(f"    - {p}", flush=True)
                user = (user + "\n## Your previous reply\n\n"
                        + json.dumps(reply, indent=2, ensure_ascii=False)[:6000]
                        + "\n\n## It was rejected for these problems\n\n"
                        + "\n".join(f"- {p}" for p in problems)
                        + "\n\nFix every one and reply with the whole concept again.\n")
        else:
            raise ConceptError(
                "the concept agent's reply was still not renderable after one retry:\n  - "
                + "\n  - ".join(problems) + "\nconcept.json is unchanged.", problems)
    except BaseException:
        b.set_stage(STAGE, previous if previous != "running" else "pending")
        raise

    _archive(b)
    # A new concept is reviewed from scratch: drop the old approval outright
    # rather than relying on the hash to notice.
    m = b.manifest()
    m.pop("concept_approved", None)
    m["stages"][STAGE] = "awaiting_review"
    m.setdefault("stage_info", {})[STAGE] = {"model": model, "revised": bool(revise),
                                             **({"note": note.strip()} if revise else {})}
    b.write_manifest(m)
    b.write_concept(concept)
    return concept


def _call(llm, model, system, user, max_tokens, on_text):
    if on_text is None:
        return llm.complete_json(model, system, user, max_tokens)
    return llm.complete_json(model, system, user, max_tokens, on_text=on_text)


# --------------------------------------------------------------------------- #
# Estimate
# --------------------------------------------------------------------------- #


def estimate_usd(cfg: dict, brief_chars: int = 3000, output_tokens: int = 1500) -> float | None:
    """A rough price for one call: the system prompt plus a typical brief in,
    a five-layer concept out. None if the model is not priced."""
    try:
        from ledger import ledger
        model, _ = _model(cfg)
        tokens_in = (len(system_prompt()) + brief_chars) // 4
        _, cin = ledger.price("anthropic", model, "input_tokens", tokens_in)
        _, cout = ledger.price("anthropic", model, "output_tokens", output_tokens)
        return round(cin + cout, 4)
    except Exception:  # noqa: BLE001
        return None
