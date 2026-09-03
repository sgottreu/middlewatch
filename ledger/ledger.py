"""Cost and revenue ledger.

Two files, deliberately separate.

`costs.csv` is append-only. A cost event is a fact about something that already
happened and was already paid for. Nothing ever rewrites a row.

`revenue.csv` is upserted. YouTube revenue for a given video and month is
restated as data settles, and accrues for years after upload. Rows get replaced
in place, keyed on (story_slug, platform, period).

Mixing the two in one file would mean rewriting immutable cost rows every time
revenue is refreshed. Keeping them apart means a corrupted revenue import can
never destroy your spend history.

Stdlib only. Drops into the pipeline with no new dependencies.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

LEDGER_DIR = Path(os.environ.get("MW_LEDGER_DIR", "ledger"))
COSTS_CSV = LEDGER_DIR / "costs.csv"
REVENUE_CSV = LEDGER_DIR / "revenue.csv"
RATES_JSON = Path(__file__).with_name("rates.json")

# Which pipeline produced a cost event. Not derivable after the fact — an
# ambience slug and a story slug look alike — so it is recorded, validated, and
# never defaulted. Adding a pipeline means adding it here.
PIPELINES = ("story", "ambience")

COST_FIELDS = [
    "event_id",
    "timestamp",
    "run_id",
    "pipeline",
    "story_slug",
    "story_title",
    "series",
    "genre",
    "flavor",
    "arc_slug",
    "volume",
    "episode",
    "stage",
    "provider",
    "model",
    "unit_type",
    "units",
    "rate_per_unit",
    "cost_usd",
    "notes",
]

REVENUE_FIELDS = [
    "story_slug",
    "platform",
    "period",
    "video_id",
    "views",
    "watch_hours",
    "revenue_usd",
    "imported_at",
    "source",
]


# ---------------------------------------------------------------- rates


def load_rates(path: Path | None = None) -> dict:
    with open(path or RATES_JSON) as fh:
        return json.load(fh)


def price(provider: str, model: str, unit_type: str, units: float,
          rates: dict | None = None) -> tuple[float, float]:
    """Return (rate_per_unit, cost_usd).

    Raises on an unknown provider/model/unit so a pricing gap fails loudly at
    the call site instead of silently recording zero.
    """
    rates = rates or load_rates()
    try:
        prov = rates["providers"][provider]
    except KeyError:
        raise KeyError(f"No rates for provider {provider!r}. Add it to rates.json.")
    try:
        model_rates = prov["models"][model]
    except KeyError:
        raise KeyError(
            f"No rates for model {model!r} under {provider!r}. "
            f"Known: {sorted(prov['models'])}"
        )
    try:
        per_scale = model_rates[unit_type]
    except KeyError:
        raise KeyError(
            f"No rate for unit {unit_type!r} on {provider}/{model}. "
            f"Known: {sorted(model_rates)}"
        )
    scale = prov.get("unit_scale", 1)
    rate_per_unit = per_scale / scale
    return rate_per_unit, units * rate_per_unit


# ---------------------------------------------------------------- costs


def _ensure(path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="") as fh:
            csv.DictWriter(fh, fieldnames=fields).writeheader()


def record_cost(
    *,
    story: dict,
    stage: str,
    provider: str,
    model: str,
    unit_type: str,
    units: float,
    run_id: str = "",
    notes: str = "",
    rates: dict | None = None,
    path: Path | None = None,
) -> dict:
    """Append one cost event.

    `story` carries the dimensions you want to slice by later. Pull it straight
    from manifest.json so the labels can never drift from the bundle:

        {"pipeline": "story", "slug": "...", "title": "...",
         "series": "Amberlight", "genre": "regency",
         "flavor": "comedy-of-manners", "arc_slug": "amberlight-vol-1",
         "volume": 1, "episode": 2}

    `pipeline` is required and validated. Every other dimension degrades to a
    blank cell, which costs you a row in a report; this one silently merges two
    channels' economics into one number, and nothing downstream could tell.
    `Bundle.create` writes it into every new manifest, so a caller that reads the
    manifest gets it for free.
    """
    pipeline = str(story.get("pipeline") or "")
    if pipeline not in PIPELINES:
        raise ValueError(
            f"story['pipeline'] is {story.get('pipeline')!r}; expected one of "
            f"{list(PIPELINES)}. It comes from manifest.json — an older bundle "
            f"may predate the field, in which case add it there rather than "
            f"passing it at the call site."
        )
    path = path or COSTS_CSV
    _ensure(path, COST_FIELDS)
    rate, cost = price(provider, model, unit_type, units, rates)
    row = {
        "event_id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "pipeline": pipeline,
        "story_slug": story.get("slug", ""),
        "story_title": story.get("title", ""),
        "series": story.get("series", ""),
        "genre": story.get("genre", ""),
        "flavor": story.get("flavor", ""),
        "arc_slug": story.get("arc_slug", ""),
        "volume": story.get("volume", ""),
        "episode": story.get("episode", ""),
        "stage": stage,
        "provider": provider,
        "model": model,
        "unit_type": unit_type,
        "units": units,
        "rate_per_unit": f"{rate:.10f}",
        "cost_usd": f"{cost:.6f}",
        "notes": notes,
    }
    with open(path, "a", newline="") as fh:
        csv.DictWriter(fh, fieldnames=COST_FIELDS).writerow(row)
    return row


def record_anthropic(*, story, stage, model, usage, run_id="", notes="", **kw):
    """Record one Anthropic call from a response `usage` object or dict.

    Writes a separate row per token class, because cached input is priced an
    order of magnitude below fresh input and collapsing them hides your single
    biggest lever on writing cost.
    """
    u = usage if isinstance(usage, dict) else {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }
    mapping = [
        ("input_tokens", u.get("input_tokens", 0)),
        ("output_tokens", u.get("output_tokens", 0)),
        ("cache_write_tokens", u.get("cache_creation_input_tokens", 0)),
        ("cache_read_tokens", u.get("cache_read_input_tokens", 0)),
    ]
    rows = []
    for unit_type, units in mapping:
        if not units:
            continue
        rows.append(record_cost(
            story=story, stage=stage, provider="anthropic", model=model,
            unit_type=unit_type, units=units, run_id=run_id, notes=notes, **kw))
    return rows


def record_polly(*, story, stage, engine, billed_characters, run_id="", notes="", **kw):
    return record_cost(
        story=story, stage=stage, provider="polly", model=engine,
        unit_type="characters", units=billed_characters,
        run_id=run_id, notes=notes, **kw)


def record_elevenlabs(*, story, stage, model, credits, run_id="", notes="", **kw):
    """Record one ElevenLabs call in credits, the unit the account is billed in.

    Narration and sound effects draw on one credit pool and are recorded the same
    way, differing only in `model`: a voice model for speech, `sound_effects` for
    SFX. Converting to dollars at the call site would bake today's plan rate into
    an append-only row; recording credits keeps rates.json the single place a
    plan change has to be reflected.
    """
    return record_cost(
        story=story, stage=stage, provider="elevenlabs", model=model,
        unit_type="credits", units=credits, run_id=run_id, notes=notes, **kw)


def record_gemini(*, story, stage, model, images, run_id="", notes="", **kw):
    return record_cost(
        story=story, stage=stage, provider="gemini", model=model,
        unit_type="images", units=images, run_id=run_id, notes=notes, **kw)


# ---------------------------------------------------------------- revenue


def _revenue_key(row: dict) -> tuple:
    return (row["story_slug"], row["platform"], row["period"])


def upsert_revenue(rows: list[dict], path: Path | None = None) -> tuple[int, int]:
    """Replace-or-insert revenue rows. Returns (updated, inserted).

    Written atomically through a temp file so an interrupted import cannot
    leave a half-written ledger.
    """
    path = path or REVENUE_CSV
    _ensure(path, REVENUE_FIELDS)
    existing: dict[tuple, dict] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            existing[_revenue_key(r)] = r

    updated = inserted = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in rows:
        r = {**{k: "" for k in REVENUE_FIELDS}, **r}
        r["imported_at"] = now
        key = _revenue_key(r)
        if key in existing:
            updated += 1
        else:
            inserted += 1
        existing[key] = r

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REVENUE_FIELDS)
        w.writeheader()
        for key in sorted(existing):
            w.writerow({k: existing[key].get(k, "") for k in REVENUE_FIELDS})
    os.replace(tmp, path)
    return updated, inserted
