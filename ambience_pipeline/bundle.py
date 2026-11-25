"""The ambience bundle: one directory per soundscape, one manifest for its state.

Copied from `story_pipeline/bundle.py` rather than imported, as
`docs/ambience/pipeline-design.md` argues: the manifest half (`set_stage`,
`pin`, `add_spend`, `stage_status`) is identical and short, the path half is
entirely different, and two pipelines is not enough evidence for a shared base.

Bundles live at `ambience/<slug>/`, resolved against the working directory like
`stories/`, and gitignored. Audition renders from the review page go under
`ambience/_auditions/`, which no slug can name (slugs start with [a-z0-9]).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PIPELINE = "ambience"

STAGES = ["concept", "sound", "design", "direct"]

# The dial, pinned at creation exactly as `--length` is on a story. Unknown
# values raise (see `hours_spec`) rather than falling back to an hour.
HOURS: dict[int, dict[str, Any]] = {
    1: {"seconds": 3600,  "images": 1, "loop_s": 40, "accent_density_scale": 1.0},
    3: {"seconds": 10800, "images": 4, "loop_s": 40, "accent_density_scale": 0.8},
    8: {"seconds": 28800, "images": 6, "loop_s": 40, "accent_density_scale": 0.5},
}

ROLES = ("bed", "texture", "accent", "tonal")

# Bounds the validator enforces. An unbounded density or a missing gain should
# fail at the gate, not eight hours into a render.
GAIN_DB = (-48.0, -10.0)
DENSITY_PER_MIN = (0.01, 6.0)
EVENT_S = (1.0, 60.0)
MAX_LAYERS = 8
MAX_BEDS = 2
LAYER_KEYS = {"id", "role", "source", "recipe", "gain_db", "pan", "hpf_hz",
              "lpf_hz", "movement", "density_per_min", "event_s", "note"}

# Names the API routes use under /api/ambience/<x>; no bundle may take one.
RESERVED = {"audition", "auditions", "new", "recipes", "stories"}
AUDITIONS = "_auditions"

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")


def hours_spec(hours: Any) -> dict[str, Any]:
    try:
        return HOURS[int(hours)]
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"hours must be one of {sorted(HOURS)}, got {hours!r}") from None


def ambience_dir(cfg: dict | None = None) -> Path:
    return Path((cfg or {}).get("ambience_dir") or "ambience")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60].strip("-") or "untitled"


def check_slug(slug: str) -> str:
    if not _SLUG.fullmatch(slug or "") or slug in RESERVED:
        raise ValueError(f"not an ambience slug: {slug!r}")
    return slug


def find(root_dir: str | Path, slug: str) -> Path:
    """The bundle for a slug. The pattern check matters: slugs arrive over
    HTTP, and `../..` must resolve to nothing."""
    p = Path(root_dir) / check_slug(slug)
    if not (p / "manifest.json").is_file():
        raise FileNotFoundError(f"no ambience called {slug} under {root_dir}/")
    return p


def bundle_roots(root_dir: str | Path) -> list[Path]:
    return sorted(p.parent for p in Path(root_dir).glob("*/manifest.json")
                  if _SLUG.fullmatch(p.parent.name))


def unique_slug(root_dir: str | Path, base: str) -> str:
    base = slugify(base)
    if base in RESERVED:
        base = f"{base}-ambience"
    slug, n = base, 2
    while (Path(root_dir) / slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def concept_hash(concept: dict) -> str:
    """What approval is recorded against. Only the layers and the seed — the
    things that change the audio. Retitling is not a reason to re-approve."""
    payload = json.dumps({"layers": concept.get("layers"), "seed": concept.get("seed")},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# concept.json
# --------------------------------------------------------------------------- #


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_concept(concept: Any) -> list[str]:
    """Every problem with a concept, as sentences. Empty means renderable.

    The recipe check is `Recipe.make` itself — the same coercion and the same
    unknown-parameter error the audition CLI gives — plus a build, so a bad
    `surface` fails here rather than at the first block of the render.
    """
    from .synth.recipes import REGISTRY

    if not isinstance(concept, dict):
        return ["concept must be a JSON object"]
    out: list[str] = []
    if "hours" in concept and concept["hours"] not in HOURS:
        out.append(f"hours must be one of {sorted(HOURS)}")
    if "seed" in concept and not (isinstance(concept["seed"], int)
                                  and not isinstance(concept["seed"], bool)
                                  and concept["seed"] >= 0):
        out.append("seed must be a non-negative integer")
    layers = concept.get("layers")
    if not isinstance(layers, list) or not layers:
        return out + ["layers must be a non-empty list"]
    if len(layers) > MAX_LAYERS:
        out.append(f"{len(layers)} layers; at most {MAX_LAYERS}")

    seen: set[str] = set()
    beds: list[float] = []
    for i, L in enumerate(layers):
        where = f"layer {i + 1}"
        if not isinstance(L, dict):
            out.append(f"{where}: must be an object")
            continue
        lid = L.get("id")
        if not isinstance(lid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", lid):
            out.append(f"{where}: id must be a short lowercase name (a-z, 0-9, _ or -)")
        else:
            where = f"layer {lid!r}"
            if lid in seen:
                out.append(f"{where}: duplicate id")
            seen.add(lid)
        extra = sorted(set(L) - LAYER_KEYS)
        if extra:
            out.append(f"{where}: unknown keys {', '.join(extra)}")
        role = L.get("role")
        if role not in ROLES:
            out.append(f"{where}: role must be one of {', '.join(ROLES)}")
        src = L.get("source", "procedural")
        if src == "generated":
            out.append(f"{where}: generated layers are not built yet (blocked on "
                       "ElevenLabs commercial rights) — use a procedural recipe")
        elif src != "procedural":
            out.append(f"{where}: source must be 'procedural'")

        g = L.get("gain_db")
        if not _num(g):
            out.append(f"{where}: gain_db is required and must be a number")
        elif not GAIN_DB[0] <= g <= GAIN_DB[1]:
            out.append(f"{where}: gain_db {g} outside {GAIN_DB[0]:g} to {GAIN_DB[1]:g}")
        elif role == "bed":
            beds.append(float(g))

        rec = L.get("recipe")
        if not isinstance(rec, dict) or not isinstance(rec.get("type"), str):
            out.append(f"{where}: recipe must be an object with a 'type'")
        elif rec["type"] not in REGISTRY:
            out.append(f"{where}: no recipe {rec['type']!r}; have "
                       f"{', '.join(sorted(REGISTRY))}")
        else:
            params = {k: v for k, v in rec.items() if k != "type"}
            if "gain_db" in params:
                out.append(f"{where}: put gain_db on the layer, not inside recipe")
                params.pop("gain_db")
            bad = [k for k, v in params.items()
                   if not (isinstance(v, str) or (_num(v) and v >= 0))]
            if bad:
                out.append(f"{where}: recipe params {', '.join(bad)} must be "
                           "non-negative numbers or strings")
            else:
                try:
                    REGISTRY[rec["type"]].make(0, params)
                except (KeyError, ValueError, TypeError) as e:
                    out.append(f"{where}: {str(e).strip(chr(34) + chr(39))}")

        if "pan" in L and not (_num(L["pan"]) and -1.0 <= L["pan"] <= 1.0):
            out.append(f"{where}: pan must be between -1 and 1")
        for k in ("hpf_hz", "lpf_hz"):
            if k in L and L[k] is not None and not (_num(L[k]) and 20 <= L[k] <= 20000):
                out.append(f"{where}: {k} must be between 20 and 20000")
        if _num(L.get("hpf_hz")) and _num(L.get("lpf_hz")) and L["hpf_hz"] >= L["lpf_hz"]:
            out.append(f"{where}: hpf_hz must be below lpf_hz")
        mv = L.get("movement")
        if mv is not None:
            if not (isinstance(mv, dict) and mv.get("type") == "lfo"
                    and _num(mv.get("depth_db")) and 0 <= mv["depth_db"] <= 12
                    and _num(mv.get("period_s")) and 5 <= mv["period_s"] <= 3600):
                out.append(f"{where}: movement must be {{type: lfo, depth_db 0-12, "
                           "period_s 5-3600}")

        if role == "accent":
            d = L.get("density_per_min")
            if not _num(d) or not DENSITY_PER_MIN[0] <= d <= DENSITY_PER_MIN[1]:
                out.append(f"{where}: accents need density_per_min between "
                           f"{DENSITY_PER_MIN[0]} and {DENSITY_PER_MIN[1]:g}")
            if "event_s" in L and not (_num(L["event_s"])
                                       and EVENT_S[0] <= L["event_s"] <= EVENT_S[1]):
                out.append(f"{where}: event_s must be between {EVENT_S[0]:g} and {EVENT_S[1]:g}")
        else:
            for k in ("density_per_min", "event_s"):
                if k in L:
                    out.append(f"{where}: {k} only applies to accents")

    roles = [L.get("role") for L in layers if isinstance(L, dict)]
    if "bed" not in roles:
        out.append("needs at least one bed layer")
    if roles.count("bed") > MAX_BEDS:
        out.append(f"at most {MAX_BEDS} bed layers")
    if beds:
        loudest = max(beds)
        for L in layers:
            if (isinstance(L, dict) and L.get("role") == "accent" and _num(L.get("gain_db"))
                    and L["gain_db"] > loudest):
                out.append(f"layer {L.get('id')!r}: an accent may not be louder than "
                           f"the loudest bed ({loudest:g} dB)")
    return out


def check_concept(concept: Any) -> dict:
    problems = validate_concept(concept)
    if problems:
        raise ValueError("concept.json is not renderable:\n  - " + "\n  - ".join(problems))
    return concept


# --------------------------------------------------------------------------- #
# The bundle
# --------------------------------------------------------------------------- #


@dataclass
class AmbienceBundle:
    root: Path

    SUBDIRS = ("audio",)

    # --- paths -------------------------------------------------------------

    @property
    def slug(self) -> str:
        return self.root.name

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def concept_path(self) -> Path:
        return self.root / "concept.json"

    @property
    def brief_path(self) -> Path:
        return self.root / "brief.md"

    @property
    def view_path(self) -> Path:
        return self.root / "ambience.md"

    @property
    def mix_path(self) -> Path:
        return self.root / "audio" / "mix.wav"

    @property
    def preview_path(self) -> Path:
        return self.root / "audio" / "preview.wav"

    def rejected_path(self, wav: Path) -> Path:
        """Where a render that failed the loudness gate is kept, to be listened to."""
        return wav.with_name(wav.stem + ".rejected.wav")

    # --- lifecycle ---------------------------------------------------------

    @classmethod
    def create(cls, root_dir: str | Path, slug: str, hours: int, seed: int,
               source: dict[str, Any]) -> "AmbienceBundle":
        hours_spec(hours)
        root = Path(root_dir) / check_slug(slug)
        if root.exists():
            raise ValueError(f"{root} already exists")
        b = cls(root)
        b._ensure_dirs()
        b.write_manifest({
            # Which pipeline made this. The ledger requires it and will not
            # guess: an ambience slug and a story slug look alike.
            "pipeline": PIPELINE,
            "slug": slug,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source,
            "stages": {s: "pending" for s in STAGES},
            "spend": {},
            "pinned": {"hours": int(hours), "seed": int(seed)},
        })
        return b

    @classmethod
    def open(cls, path: str | Path) -> "AmbienceBundle":
        b = cls(Path(path))
        if not b.manifest_path.exists():
            raise FileNotFoundError(f"no manifest at {b.manifest_path}")
        m = b.manifest()
        if m.get("pipeline") != PIPELINE:
            raise ValueError(f"{path} is a {m.get('pipeline') or 'story'} bundle, "
                             "not an ambience")
        b._ensure_dirs()
        return b

    def _ensure_dirs(self) -> None:
        for sub in self.SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # --- manifest (the fifty lines copied from story_pipeline.bundle) ------

    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text())

    def write_manifest(self, data: dict[str, Any]) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(self.manifest_path)

    def set_stage(self, stage: str, status: str, **extra: Any) -> None:
        m = self.manifest()
        m["stages"][stage] = status
        if extra:
            m.setdefault("stage_info", {})[stage] = extra
        self.write_manifest(m)

    def stage_status(self, stage: str) -> str:
        return self.stages().get(stage, "pending")

    def stages(self) -> dict[str, str]:
        stored = self.manifest().get("stages", {})
        return {s: stored.get(s, "pending") for s in STAGES}

    def stage_info(self, stage: str) -> dict[str, Any]:
        return self.manifest().get("stage_info", {}).get(stage, {}) or {}

    def add_spend(self, key: str, amount: float) -> None:
        m = self.manifest()
        m["spend"][key] = round(m["spend"].get(key, 0.0) + amount, 4)
        self.write_manifest(m)

    def pin(self, key: str, value: Any) -> None:
        m = self.manifest()
        m.setdefault("pinned", {})[key] = value
        self.write_manifest(m)

    def pinned(self, key: str, default: Any = None) -> Any:
        return self.manifest().get("pinned", {}).get(key, default)

    def ledger_story(self) -> dict[str, Any]:
        """Dimension labels for a ledger row. Ambience leaves genre and flavor
        blank, which is what they are; `pipeline` is never defaulted."""
        m = self.manifest()
        p = m.get("pinned", {})
        return {
            "pipeline": PIPELINE,
            "slug": m.get("slug", ""),
            "title": p.get("title", ""),
            "series": p.get("series", ""),
            "genre": "",
            "flavor": "",
            "arc_slug": "",
            "volume": "",
            "episode": "",
        }

    # --- hours -------------------------------------------------------------

    @property
    def hours(self) -> int:
        return int(self.pinned("hours"))

    @property
    def seconds(self) -> int:
        return int(hours_spec(self.hours)["seconds"])

    @property
    def seed(self) -> int:
        return int(self.pinned("seed"))

    # --- concept and the gate ---------------------------------------------

    def concept(self) -> dict[str, Any]:
        return json.loads(self.concept_path.read_text())

    def write_concept(self, concept: dict[str, Any]) -> bool:
        """Save the concept. Returns True if an approval lapsed because of it.

        Approval is recorded against a hash of the layers, the way a story's
        chapter approval is. Editing an approved concept puts it back to
        `awaiting_review` rather than letting `sound` render something nobody
        agreed to.
        """
        self.concept_path.write_text(json.dumps(concept, indent=2, ensure_ascii=False) + "\n")
        lapsed = False
        if self.stage_status("concept") == "approved" and not self.concept_approved():
            self.set_stage("concept", "awaiting_review")
            lapsed = True
        elif self.stage_status("concept") == "pending":
            self.set_stage("concept", "awaiting_review")
        self.write_view()
        return lapsed

    def approve(self) -> None:
        concept = check_concept(self.concept())
        m = self.manifest()
        m["stages"]["concept"] = "approved"
        m["concept_approved"] = {"hash": concept_hash(concept),
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.write_manifest(m)
        self.write_view()

    def concept_approved(self) -> bool:
        m = self.manifest()
        rec = (m.get("concept_approved") or {}).get("hash")
        if m.get("stages", {}).get("concept") != "approved" or not rec:
            return False
        try:
            return rec == concept_hash(self.concept())
        except (OSError, json.JSONDecodeError):
            return False

    # --- the view ----------------------------------------------------------

    def write_view(self) -> None:
        """ambience.md — a regenerated view, never a source."""
        m = self.manifest()
        p = m.get("pinned", {})
        try:
            c = self.concept()
        except (OSError, json.JSONDecodeError):
            c = {}
        lines = [f"# {p.get('title') or self.slug}", ""]
        if p.get("series"):
            lines += [f"*{p['series']}*", ""]
        lines += [f"- hours: {p.get('hours')}  seed: {p.get('seed')}",
                  "- stages: " + ", ".join(f"{k} {v}" for k, v in self.stages().items())]
        src = m.get("source") or {}
        if src.get("from_story"):
            lines.append(f"- from story: {src['from_story']}")
        if c.get("scene"):
            lines += ["", c["scene"]]
        lines += ["", "| id | role | recipe | gain dB | notes |", "|---|---|---|---|---|"]
        for L in c.get("layers") or []:
            rec = dict(L.get("recipe") or {})
            kind = rec.pop("type", "?")
            params = " ".join(f"{k}={v}" for k, v in rec.items())
            extra = (f"{L['density_per_min']}/min" if L.get("role") == "accent"
                     and "density_per_min" in L else "")
            lines.append(f"| {L.get('id')} | {L.get('role')} | {kind} {params} | "
                         f"{L.get('gain_db')} | {extra} |")
        self.view_path.write_text("\n".join(lines) + "\n")
