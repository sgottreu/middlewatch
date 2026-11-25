"""The ambience pipeline's command line. Run from the repo root.

    python3 -m ambience_pipeline.cli new --scene "rain on a library window" [--bible amberlight] [--hours 1]
    python3 -m ambience_pipeline.cli new --from-story stories/<arc>/<slug> [--hours 3]
    python3 -m ambience_pipeline.cli new --scene "…" --starter          # free keyword layers, no call
    python3 -m ambience_pipeline.cli draft ambience/<slug>              # run the concept agent on the brief
    python3 -m ambience_pipeline.cli revise ambience/<slug> --note "less fire, more wind"
    python3 -m ambience_pipeline.cli approve ambience/<slug>
    python3 -m ambience_pipeline.cli sound ambience/<slug> [--dry-run] [--seconds N]
    python3 -m ambience_pipeline.cli status ambience/<slug>
    python3 -m ambience_pipeline.cli audition rain -p surface=roof --seconds 30 --out rain.wav

`new`, `draft` and `revise` make one paid Anthropic call each (the concept
agent, `models.concept`), plus one retry if its first reply does not validate.
`new --starter` skips the call and proposes layers from keywords instead.
Everything else is free and offline: procedural layers only. Edit concept.json,
then `approve`, then `sound`.

Under MW_PROGRESS (set by the review server's runner) each command also prints
`::progress {json}` lines, the same contract story_pipeline.cli keeps.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from . import bundle as ab
from .bundle import AmbienceBundle

AUDITION_MAX_S = 600.0


def _progress(**fields) -> None:
    if os.environ.get("MW_PROGRESS"):
        print("::progress " + json.dumps(fields), flush=True)


# --------------------------------------------------------------------------- #
# new
# --------------------------------------------------------------------------- #

# Keyword -> layer, for the starter concept: the free alternative to the
# concept agent (`new --starter`), and what a bundle holds until the agent's
# reply lands — so a failed call still leaves something renderable.
_KEYWORDS = {
    "rain": ("rain", "drizzle", "storm", "downpour", "shower", "wet", "puddle"),
    "ocean": ("sea", "ocean", "surf", "shore", "wave", "harbour", "harbor", "beach",
              "coast", "tide", "cliff", "pier"),
    "wind": ("wind", "gale", "breeze", "moor", "heath", "gust", "blizzard", "storm",
             "hill", "tower"),
    "fire": ("fire", "hearth", "fireside", "candle", "ember", "log", "stove", "forge",
             "grate", "inglenook", "kitchen"),
}


def _mentions(text: str, kind: str) -> bool:
    import re
    words = set(re.findall(r"[a-z]+", text.lower()))
    return any(k in words or (k + "s") in words for k in _KEYWORDS[kind])


def starter_layers(text: str) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []

    def add(lid, role, kind, gain, **kw):
        layers.append({"id": lid, "role": role, "source": "procedural",
                       "recipe": {"type": kind}, "gain_db": gain, **kw})

    if _mentions(text, "rain"):
        add("rain", "bed", "rain", -20.0)
    if _mentions(text, "ocean"):
        add("surf", "bed" if not layers else "texture", "ocean", -20.0 if not layers else -26.0)
    if _mentions(text, "wind") and len(layers) < 2:
        add("wind", "bed" if not layers else "texture", "wind", -22.0 if not layers else -28.0)
    if _mentions(text, "fire"):
        add("fire", "texture" if layers else "bed", "fire", -24.0 if layers else -20.0)
    if not any(L["role"] == "bed" for L in layers):
        add("room", "bed", "room_tone", -22.0)
    if len(layers) == 1:
        # One texture, so there is something to balance at the gate.
        if layers[0]["recipe"]["type"] == "room_tone":
            add("fire", "texture", "fire", -30.0)
        else:
            add("room", "texture", "room_tone", -30.0)
    return layers


def _load_bible(name: str, bibles_dir: str | Path, required: bool):
    from story_pipeline import bibles
    try:
        return bibles.load(name, bibles_dir)
    except Exception as e:
        if required:
            raise ValueError(f"bible {name!r} would not load: {e}") from None
        print(f"  (skipping bible {name!r}: {e})")
        return None


def _story_material(story: Path) -> dict[str, Any]:
    story = Path(story)
    for need in ("manifest.json", "outline.json"):
        if not (story / need).is_file():
            raise ValueError(f"{story} is not a story bundle (no {need})")
    outline = json.loads((story / "outline.json").read_text())
    manifest = json.loads((story / "manifest.json").read_text())
    shots: list[str] = []
    scenes = story / "images" / "scenes.json"
    if scenes.is_file():
        try:
            data = json.loads(scenes.read_text())
            chapters = data.values() if isinstance(data, dict) else data
            for ch in chapters:
                for s in (ch or {}).get("shots", []) if isinstance(ch, dict) else []:
                    p = (s or {}).get("prompt")
                    if p and p not in shots:
                        shots.append(p)
        except (json.JSONDecodeError, AttributeError):
            pass
    return {"outline": outline, "manifest": manifest, "shots": shots}


def build_brief(title: str, scene: str = "", story: dict | None = None,
                bible=None, art_style: str = "", max_shots: int = 12) -> str:
    lines = [f"# Brief — {title}", ""]
    if story:
        o = story["outline"]
        for k in ("title", "series", "genre", "logline", "setting"):
            if o.get(k):
                lines.append(f"- **{k}:** {o[k]}")
        if story["shots"]:
            lines += ["", "## Shots from the story", ""]
            lines += [f"- {s}" for s in story["shots"][:max_shots]]
            if len(story["shots"]) > max_shots:
                lines.append(f"- … {len(story['shots']) - max_shots} more in images/scenes.json")
    if scene:
        lines += ["", "## Scene", "", scene]
    if bible is not None:
        lines += ["", f"## Bible: {bible.name}", ""]
        if bible.canon:
            lines += [f"- {c}" for c in bible.canon[:12]]
    if art_style:
        lines += ["", "## Art style", "", str(art_style).strip()]
    return "\n".join(lines) + "\n"


def create(cfg: dict, *, scene: str = "", from_story: str = "", bible: str = "",
           hours: int = 1, seed: int | None = None, slug: str = "",
           title: str = "") -> AmbienceBundle:
    """Make a bundle with a starter concept. Free; used by the CLI and the page."""
    hours = int(hours)
    ab.hours_spec(hours)
    scene = (scene or "").strip()
    if bool(scene) == bool(from_story):
        raise ValueError("give exactly one of a scene or a story")
    root_dir = ab.ambience_dir(cfg)
    bibles_dir = cfg.get("bibles_dir") or "bibles"
    seed = int(seed) if seed is not None else secrets.randbelow(2**31)

    story = None
    bible_obj = None
    art_style = ""
    if from_story:
        story_path = Path(from_story)
        story = _story_material(story_path)
        o = story["outline"]
        title = title or f"{o.get('title') or story_path.name} — ambience"
        base = slug or f"{story_path.name}-ambience"
        bname = bible or o.get("bible") or ""
        if bname:
            bible_obj = _load_bible(bname, bibles_dir, required=False)
        # Inherited, not regenerated: the story's pinned style wins.
        art_style = ((story["manifest"].get("pinned") or {}).get("art_style")
                     or (bible_obj.meta.get("art_style") if bible_obj else "") or "")
        text = " ".join([o.get("setting", ""), o.get("logline", "")] + story["shots"])
        source = {"from_story": str(story_path)}
        scene_text = o.get("setting", "")
    else:
        if bible:
            bible_obj = _load_bible(bible, bibles_dir, required=True)
            art_style = bible_obj.meta.get("art_style") or ""
        title = title or scene.split(".")[0][:70]
        base = slug or " ".join(title.split()[:6])
        text = scene
        source = {"scene": scene}
        scene_text = scene

    root_dir.mkdir(parents=True, exist_ok=True)
    slug = ab.unique_slug(root_dir, base) if not slug else ab.check_slug(slug)
    b = AmbienceBundle.create(root_dir, slug, hours, seed, source)
    series = (bible_obj.series if bible_obj else "") or \
        ((story or {}).get("outline", {}).get("series") or "")
    b.pin("title", title)
    b.pin("series", series)
    if bible_obj:
        b.pin("bible", bible_obj.name)
    if art_style:
        b.pin("art_style", art_style)
    if story:
        b.pin("source_story", str(from_story))
        _link_story(Path(from_story), slug)

    b.brief_path.write_text(build_brief(title, scene, story, bible_obj, art_style))
    concept = {"slug": slug, "title": title, "series": series, "hours": hours,
               "seed": seed, "scene": scene_text, "starter": True,
               "layers": starter_layers(text)}
    b.write_concept(concept)
    return b


def _link_story(story: Path, slug: str) -> None:
    """The reciprocal link, so the relationship is visible from the story too."""
    p = story / "manifest.json"
    try:
        m = json.loads(p.read_text())
        links = m.setdefault("ambience", [])
        if slug not in links:
            links.append(slug)
            p.write_text(json.dumps(m, indent=2) + "\n")
    except (OSError, json.JSONDecodeError) as e:
        print(f"  (could not link the story's manifest: {e})")


def _cfg() -> dict:
    """The story config if it loads, else defaults — `new` only needs dirs."""
    try:
        from story_pipeline import config
        return config.load()
    except Exception:
        return {"bibles_dir": "bibles", "stories_dir": "stories"}


def cmd_new(args) -> int:
    cfg = _cfg()
    b = create(cfg, scene=args.scene or "", from_story=args.from_story or "",
               bible=args.bible or "", hours=args.hours, seed=args.seed,
               slug=args.slug or "", title=args.title or "")
    print(f"created {b.root}")
    _progress(event="created", slug=b.slug)
    if args.starter:
        print("starter layers, from keywords (no call):")
        _print_concept(b)
        print(f"\nEdit {b.concept_path}, then:  python3 -m ambience_pipeline.cli approve {b.root}")
        return 0
    return _run_agent(b, cfg, revise=False, note="", stream=args.stream)


def cmd_draft(args) -> int:
    b = _open(args.bundle)
    return _run_agent(b, _cfg(), revise=False, note="", stream=args.stream)


def cmd_revise(args) -> int:
    b = _open(args.bundle)
    if not (args.note or "").strip():
        raise ValueError("--note is required: say what to change")
    return _run_agent(b, _cfg(), revise=True, note=args.note, stream=args.stream)


def _run_agent(b: AmbienceBundle, cfg: dict, *, revise: bool, note: str,
               stream: bool | None) -> int:
    """One concept-agent run, printed. Exit 1 on failure; concept.json is left
    as it was (for `new`, the starter layers)."""
    import anthropic
    from story_pipeline import llm

    from . import concept as ca

    model, _ = ca._model(cfg)
    if stream is None:
        stream = bool(os.environ.get("MW_PROGRESS"))
    what = "revising" if revise else "drafting"
    print(f"{what} the concept with {model} (one paid call, one retry at most)…", flush=True)
    _progress(event="plan", total=1)
    on_text = (lambda piece: print(piece, end="", flush=True)) if stream else None
    try:
        ca.draft(b, cfg, note=note, revise=revise, on_text=on_text)
    except (ca.ConceptError, llm.ConfigurationError, llm.TruncatedReply, ValueError,
            anthropic.APIError) as e:
        if stream:
            print()
        print(f"error: {e}", file=sys.stderr)
        if b.concept_path.is_file() and b.concept().get("starter"):
            print(f"  the starter layers are still in {b.concept_path} — edit those, or "
                  f"retry with:  python3 -m ambience_pipeline.cli draft {b.root}")
        _progress(event="failed", error=str(e).splitlines()[0] if str(e) else "failed")
        return 1
    if stream:
        print()
    spent = b.manifest().get("spend", {})
    print(f"{'revised' if revise else 'drafted'} {b.concept_path}"
          + (f"  (kept the previous as {ca.history(b)[-1].name})" if ca.history(b) else ""))
    print(f"  tokens so far: {spent.get('anthropic_input_tokens', 0):,} in, "
          f"{spent.get('anthropic_output_tokens', 0):,} out")
    notes = b.concept().get("notes")
    if notes:
        print(f"  notes: {notes}")
    _print_concept(b)
    print(f"\nReview {b.concept_path}, then:  python3 -m ambience_pipeline.cli approve {b.root}")
    _progress(event="block", done=1, total=1)
    _progress(event="done")
    return 0


# --------------------------------------------------------------------------- #
# approve, status
# --------------------------------------------------------------------------- #


def _open(path: str) -> AmbienceBundle:
    return AmbienceBundle.open(Path(path))


def _print_concept(b: AmbienceBundle) -> None:
    c = b.concept()
    for L in c.get("layers", []):
        rec = dict(L.get("recipe") or {})
        kind = rec.pop("type", "?")
        params = " ".join(f"{k}={v}" for k, v in rec.items())
        extra = f"  {L.get('density_per_min')}/min" if L.get("role") == "accent" else ""
        print(f"  {L.get('role', '?'):<8} {L.get('id', '?'):<14} {kind:<10} "
              f"{L.get('gain_db', '?'):>6} dB  {params}{extra}")


def cmd_approve(args) -> int:
    b = _open(args.bundle)
    problems = ab.validate_concept(b.concept())
    if problems:
        print("concept.json is not renderable:")
        for p in problems:
            print(f"  - {p}")
        return 1
    b.approve()
    print(f"approved {b.slug}")
    _print_concept(b)
    return 0


def cmd_status(args) -> int:
    b = _open(args.bundle)
    m = b.manifest()
    p = m.get("pinned", {})
    print(f"{p.get('title') or b.slug}  ({b.slug})")
    if p.get("series"):
        print(f"  series {p['series']}")
    print(f"  hours {p.get('hours')}   seed {p.get('seed')}")
    if p.get("source_story"):
        print(f"  from {p['source_story']}")
    for s, v in b.stages().items():
        print(f"  {s:<8} {v}")
    if b.stage_status("concept") == "approved" and not b.concept_approved():
        print("  ! concept.json changed since it was approved")
    for f in (b.mix_path, b.preview_path):
        if f.exists():
            print(f"  {f}  ({f.stat().st_size / 1e6:.1f} MB)")
    info = b.stage_info("sound")
    if info.get("lufs") is not None:
        print(f"  last render {info.get('lufs')} LUFS, true peak {info.get('true_peak')} dBTP")
    print("  layers:")
    _print_concept(b)
    return 0


# --------------------------------------------------------------------------- #
# sound
# --------------------------------------------------------------------------- #


def cmd_sound(args) -> int:
    from . import mix

    b = _open(args.bundle)
    concept = ab.check_concept(b.concept())
    if not b.concept_approved():
        state = b.stage_status("concept")
        raise SystemExit(
            "concept.json changed since it was approved — approve it again first."
            if state == "approved" else
            f"the concept is {state}, not approved. Review concept.json, then "
            f"python3 -m ambience_pipeline.cli approve {b.root}")

    preview = args.seconds is not None
    seconds = float(args.seconds) if preview else float(b.seconds)
    if seconds <= 0:
        raise SystemExit("--seconds must be positive")
    scale = ab.hours_spec(b.hours)["accent_density_scale"]
    out = b.preview_path if preview else b.mix_path
    mb = seconds * mix.dsp.SR * 4 / 1e6

    print(f"{b.slug}: {len(concept['layers'])} procedural layers, "
          f"{seconds:g}s ({seconds / 3600:.2f} h){' preview' if preview else ''}, seed {b.seed}")
    for L in concept["layers"]:
        extra = (f", {L['density_per_min'] * scale:.2f}/min after the "
                 f"{b.hours}h scale" if L["role"] == "accent" else "")
        print(f"  {L['role']:<8} {L['id']:<14} {L['recipe']['type']:<10} "
              f"{L['gain_db']} dB — procedural, rendered locally, free{extra}")
    print(f"  -> {out}  (about {mb:,.0f} MB)")
    if args.dry_run:
        print("dry run: nothing rendered. No cache hits or misses — every layer is procedural.")
        _progress(event="dry_run", layers=len(concept["layers"]), seconds=seconds)
        return 0

    if not preview:
        b.set_stage("sound", "running")
    partial = out.with_name(out.stem + ".partial.wav")
    total_s = int(seconds)
    _progress(event="plan", total=max(total_s, 1))
    last = [-1]
    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        pct = int(100 * done / total)
        if pct != last[0] and (pct % 5 == 0 or done == total):
            last[0] = pct
            print(f"  rendering {pct:3d}%", flush=True)
        _progress(event="block", done=int(done / mix.dsp.SR), total=max(total_s, 1))

    try:
        res = mix.mix_to_wav(concept, partial, seconds, b.seed, accent_scale=scale,
                             progress=progress)
        took = time.monotonic() - started
        print(f"  {took:.1f}s to render — {seconds / took if took else 0:.0f}x realtime")
        print(f"  probe {res.probe_lufs:.1f} LUFS, trim {res.trim_db:+.1f} dB, "
              f"peak {res.peak_dbfs:.1f} dBFS")
        try:
            mix.gate(res)
        except mix.LoudnessError as e:
            rejected = b.rejected_path(out)
            partial.replace(rejected)
            print(f"  ! {e}\n  kept for listening: {rejected}", flush=True)
            _progress(event="loudness", ok=False, lufs=res.lufs)
            if not preview:
                b.set_stage("sound", "failed", error=str(e), lufs=res.lufs)
            return 2
        partial.replace(out)
    except BaseException:
        if not preview:
            b.set_stage("sound", "pending")
        raise
    for w in res.warnings:
        print(f"  ! {w}")
    print(f"  {res.lufs:.1f} LUFS integrated, true peak {res.true_peak} dBTP — inside "
          f"{mix.LUFS_WINDOW[0]:g} to {mix.LUFS_WINDOW[1]:g}")
    print(f"wrote {out}")
    info = {**res.as_dict(), "path": str(out), "preview": preview}
    _progress(event="loudness", ok=True, lufs=res.lufs, true_peak=res.true_peak)
    _progress(event="artifact", kind="audio", path=str(out))
    b.add_spend("procedural_seconds", seconds)
    if preview:
        m = b.manifest()
        m.setdefault("stage_info", {})["preview"] = info
        b.write_manifest(m)
    else:
        b.set_stage("sound", "done", **info)
    _progress(event="done")
    return 0


# --------------------------------------------------------------------------- #
# audition
# --------------------------------------------------------------------------- #


def cmd_audition(args) -> int:
    from .synth import dsp
    from .synth.__main__ import _loudness, _parse_params
    from .synth.recipes import REGISTRY
    from .synth.render import render_to_wav

    if args.recipe not in REGISTRY:
        raise SystemExit(f"no recipe {args.recipe!r}. Available: {', '.join(sorted(REGISTRY))}")
    if not 0 < args.seconds <= AUDITION_MAX_S:
        raise SystemExit(f"--seconds must be between 0 and {AUDITION_MAX_S:g}")
    recipe = REGISTRY[args.recipe]
    overrides = _parse_params(args.param)
    layer = recipe.make(args.seed, overrides)
    out = Path(args.out)
    print(f"audition {recipe.name}: " + "  ".join(
        f"{k}={v}" for k, v in {**recipe.params, **overrides}.items()))
    _progress(event="plan", total=max(int(args.seconds), 1))

    def progress(done: int, total: int) -> None:
        _progress(event="block", done=int(done / dsp.SR), total=max(int(args.seconds), 1))

    w = render_to_wav(layer, out, args.seconds, fade_in=1.0, fade_out=1.5,
                      headroom_db=args.headroom, progress=progress)
    print(f"wrote {out}  peak {dsp.gain_to_db(w.peak) if w.peak else -120:.1f} dBFS "
          f"(written {args.headroom:g} dB down)")
    lu = _loudness(out)
    if lu is not None:
        print(f"  {lu + args.headroom:.1f} LUFS for the layer "
              f"(target {float(overrides.get('gain_db', recipe.params['gain_db'])):.1f})")
    _progress(event="artifact", kind="audio", path=str(out))
    _progress(event="done")
    return 0


# --------------------------------------------------------------------------- #


def _stream_args(sp) -> None:
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--stream", dest="stream", action="store_true", default=None,
                   help="echo the model's reply as it arrives (default: only in a job log)")
    g.add_argument("--no-stream", dest="stream", action="store_false")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ambience_pipeline.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="make a bundle and draft its concept (one paid call)")
    src = n.add_mutually_exclusive_group(required=True)
    src.add_argument("--scene", help="a one-off scene, in words")
    src.add_argument("--from-story", help="a story bundle: stories/<arc>/<slug>")
    n.add_argument("--bible", help="series bible (one-off), or override the story's")
    n.add_argument("--hours", type=int, choices=sorted(ab.HOURS), default=1)
    n.add_argument("--seed", type=int)
    n.add_argument("--slug")
    n.add_argument("--title")
    n.add_argument("--starter", action="store_true",
                   help="free: propose layers from keywords instead of calling the concept agent")
    _stream_args(n)
    n.set_defaults(fn=cmd_new)

    d = sub.add_parser("draft", help="(re)draft concept.json from the brief (one paid call)")
    d.add_argument("bundle")
    _stream_args(d)
    d.set_defaults(fn=cmd_draft)

    r = sub.add_parser("revise", help="revise concept.json from a note (one paid call)")
    r.add_argument("bundle")
    r.add_argument("--note", required=True, help="what to change, in words")
    _stream_args(r)
    r.set_defaults(fn=cmd_revise)

    a = sub.add_parser("approve", help="pass the concept gate")
    a.add_argument("bundle")
    a.set_defaults(fn=cmd_approve)

    s = sub.add_parser("sound", help="render the mix (procedural, free)")
    s.add_argument("bundle")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--seconds", type=float,
                   help="render this many seconds to audio/preview.wav instead of "
                        "the pinned length; the stage is not marked done")
    s.set_defaults(fn=cmd_sound)

    st = sub.add_parser("status")
    st.add_argument("bundle")
    st.set_defaults(fn=cmd_status)

    au = sub.add_parser("audition", help="render one recipe to a wav")
    au.add_argument("recipe")
    au.add_argument("-p", "--param", action="append", metavar="NAME=VALUE")
    au.add_argument("--seconds", type=float, default=30.0)
    au.add_argument("--seed", type=int, default=0)
    au.add_argument("--headroom", type=float, default=6.0)
    au.add_argument("--out", required=True)
    au.set_defaults(fn=cmd_audition)

    args = p.parse_args(argv)
    try:
        # Credentials for the concept agent, as story_pipeline.cli does it.
        from story_pipeline import config
        config.load_env()
    except Exception:  # noqa: BLE001 — the free commands need no credentials
        pass
    try:
        return args.fn(args)
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
