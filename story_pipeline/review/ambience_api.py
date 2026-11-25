"""The ambience page: soundscape bundles in ambience/<slug>/, made from a
scene or a story, edited at the concept gate, rendered as jobs.

A plugin for server.py (see the PLUGIN_MODULES comment there): it defines
`handle_get`, `handle_post`, `PIPELINE` and `release`, and must not import the
server. Jobs go through `runner.start_command` keyed `ambience-<slug>` (one per
bundle) and `ambience-_auditions` (one audition at a time), with
`pipeline="ambience"` so a restart hands a dead job to `release` here rather
than to the story releaser.

Everything here is free: procedural layers only, rendered on this machine.
Rendering is still slow — an hour of audio is minutes of CPU — so the page arms
the Sound button.

Routes:
    GET  /ambience                       the page
    GET  /api/ambience                   bundles, recipes, stories, bibles
    GET  /api/ambience/recipes | /stories
    POST /api/ambience/new               {scene | from_story, bible?, hours}
    GET  /api/ambience/<slug>            one bundle
    GET  /api/ambience/<slug>/concept    POST it back to save
    POST /api/ambience/<slug>/approve
    POST /api/ambience/<slug>/sound      {seconds?, dry_run?} — a job
    GET  /api/ambience/<slug>/job, /log?from=
    POST /api/ambience/<slug>/cancel
    POST /api/ambience/audition          {recipe, params, seconds<=60} — a job
    GET  /api/ambience/audition/job, /log?from=;  POST /api/ambience/audition/cancel
    GET  /ambience-audio/<slug>/<name>.wav, /ambience-audio/_auditions/<name>.wav
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import runner
from ..bundle import bundle_roots as story_roots
from ambience_pipeline import bundle as ab
from ambience_pipeline.bundle import AmbienceBundle

PIPELINE = "ambience"
PAGE = Path(__file__).with_name("ambience.html")
AUDITION_KEY = "ambience-_auditions"
AUDITION_MAX_S = 60.0
PREVIEW_MAX_S = 600.0


class _Bad(ValueError):
    """A request the page should be told off for: answered 400, not 500."""

    def __init__(self, msg: str, problems: list[str] | None = None):
        super().__init__(msg)
        self.problems = problems or []


def _root(cfg: dict) -> Path:
    return ab.ambience_dir(cfg)


def _key(slug: str) -> str:
    return f"ambience-{slug}"


def _open(cfg: dict, slug: str) -> AmbienceBundle:
    return AmbienceBundle.open(ab.find(_root(cfg), slug))


def _url(cfg: dict, p: Path) -> str | None:
    try:
        rel = Path(p).resolve().relative_to(_root(cfg).resolve())
    except ValueError:
        return None
    return "/ambience-audio/" + rel.as_posix()


def audio_path(cfg: dict, rel: str) -> Path:
    """A wav under ambience/, and nothing else. `rel` arrives over HTTP."""
    base = _root(cfg).resolve()
    parts = rel.split("/")
    if (len(parts) != 3 and not (len(parts) == 2 and parts[0] == ab.AUDITIONS)) \
            or any(x in ("", ".", "..") for x in parts):
        raise FileNotFoundError(rel)
    if len(parts) == 3:
        ab.check_slug(parts[0])
        if parts[1] != "audio":
            raise FileNotFoundError(rel)
    p = (base / rel).resolve()
    if base not in p.parents or p.suffix.lower() != ".wav" or ".partial" in p.name \
            or not p.is_file():
        raise FileNotFoundError(rel)
    return p


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def recipes() -> list[dict]:
    from ambience_pipeline.synth.recipes import REGISTRY
    return [{"name": n, "summary": r.summary, "params": r.params}
            for n, r in sorted(REGISTRY.items())]


def stories(cfg: dict) -> list[dict]:
    out = []
    for p in story_roots(cfg.get("stories_dir") or "stories"):
        try:
            o = json.loads((p / "outline.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"slug": p.name, "arc": p.parent.name, "path": str(p),
                    "title": o.get("title") or p.name, "series": o.get("series") or ""})
    return out


def _bibles(cfg: dict) -> list[str]:
    from .. import bibles
    try:
        return bibles.available(cfg.get("bibles_dir") or "bibles")
    except Exception:
        return []


def _files(cfg: dict, b: AmbienceBundle) -> dict:
    out = {}
    for name, p in (("mix", b.mix_path), ("preview", b.preview_path),
                    ("mix_rejected", b.rejected_path(b.mix_path)),
                    ("preview_rejected", b.rejected_path(b.preview_path))):
        if p.is_file():
            out[name] = {"url": _url(cfg, p), "mb": round(p.stat().st_size / 1e6, 1),
                         "mtime": p.stat().st_mtime}
    return out


def _reconcile(b: AmbienceBundle) -> None:
    """A sound stage left `running` with no job running is released."""
    if b.stage_status("sound") == "running" and \
            runner.status(_key(b.slug)).get("state") != "running":
        b.set_stage("sound", "pending")


def summary(cfg: dict, b: AmbienceBundle) -> dict:
    _reconcile(b)
    m = b.manifest()
    p = m.get("pinned", {})
    return {"slug": b.slug, "title": p.get("title") or b.slug,
            "series": p.get("series", ""), "hours": p.get("hours"),
            "seed": p.get("seed"), "bible": p.get("bible", ""),
            "source": m.get("source") or {}, "source_story": p.get("source_story", ""),
            "created": m.get("created"), "stages": b.stages(),
            "approved": b.concept_approved(), "files": _files(cfg, b),
            "job": runner.status(_key(b.slug)).get("state", "none")}


def index(cfg: dict) -> dict:
    bundles = []
    for root in ab.bundle_roots(_root(cfg)):
        try:
            bundles.append(summary(cfg, AmbienceBundle.open(root)))
        except Exception:
            continue
    bundles.sort(key=lambda x: x.get("created") or "", reverse=True)
    return {"ambiences": bundles, "recipes": recipes(), "stories": stories(cfg),
            "bibles": _bibles(cfg), "hours": sorted(ab.HOURS), "roles": list(ab.ROLES),
            "gain_db": list(ab.GAIN_DB), "density_per_min": list(ab.DENSITY_PER_MIN)}


def detail(cfg: dict, slug: str) -> dict:
    b = _open(cfg, slug)
    out = summary(cfg, b)
    out["concept"] = b.concept() if b.concept_path.is_file() else None
    out["problems"] = ab.validate_concept(out["concept"]) if out["concept"] else []
    out["brief"] = b.brief_path.read_text() if b.brief_path.is_file() else ""
    out["sound_info"] = b.stage_info("sound")
    out["preview_info"] = b.manifest().get("stage_info", {}).get("preview", {})
    return out


def concept_view(cfg: dict, slug: str) -> dict:
    b = _open(cfg, slug)
    c = b.concept()
    return {"concept": c, "problems": ab.validate_concept(c),
            "approved": b.concept_approved(), "stage": b.stage_status("concept")}


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _busy(b: AmbienceBundle) -> None:
    if runner.status(_key(b.slug)).get("state") == "running":
        raise _Bad(f"{b.slug} is rendering. Stop it first, or wait.")


def new(cfg: dict, body: dict) -> dict:
    from ambience_pipeline import cli
    scene = (body.get("scene") or "").strip()
    story = (body.get("from_story") or "").strip()
    if bool(scene) == bool(story):
        raise _Bad("Give either a scene or a story, not both.")
    story_path = ""
    if story:
        known = stories(cfg)
        hit = [s for s in known if story in (s["slug"], s["path"])]
        if not hit:
            raise _Bad(f"No story called {story!r}.")
        story_path = hit[0]["path"]
    try:
        hours = int(body.get("hours") or 1)
        ab.hours_spec(hours)
    except ValueError as e:
        raise _Bad(str(e))
    try:
        b = cli.create(cfg, scene=scene, from_story=story_path,
                       bible=(body.get("bible") or "").strip(), hours=hours,
                       title=(body.get("title") or "").strip())
    except ValueError as e:
        raise _Bad(str(e))
    return {"ok": True, "slug": b.slug, "ambience": detail(cfg, b.slug)}


def save_concept(cfg: dict, slug: str, body: dict) -> dict:
    b = _open(cfg, slug)
    _busy(b)
    incoming = body.get("concept") if isinstance(body.get("concept"), dict) else None
    if incoming is None and isinstance(body.get("layers"), list):
        incoming = {"layers": body["layers"]}
    if incoming is None:
        raise _Bad("Send {concept: {...}} or {layers: [...]}.")
    c = {**b.concept(), **incoming}
    c["seed"] = b.seed            # the manifest's pin is what renders
    c["hours"] = b.hours
    c["slug"] = b.slug
    c.pop("starter", None)
    problems = ab.validate_concept(c)
    if problems:
        raise _Bad("The concept has problems: " + "; ".join(problems), problems)
    lapsed = b.write_concept(c)
    return {"ok": True, "lapsed": lapsed, **concept_view(cfg, slug)}


def approve(cfg: dict, slug: str) -> dict:
    b = _open(cfg, slug)
    problems = ab.validate_concept(b.concept())
    if problems:
        raise _Bad("Fix the concept first: " + "; ".join(problems), problems)
    b.approve()
    return {"ok": True, "ambience": detail(cfg, slug)}


def sound(cfg: dict, slug: str, body: dict) -> dict:
    b = _open(cfg, slug)
    if not b.concept_approved():
        raise _Bad("Approve the concept first." if b.stage_status("concept") != "approved"
                   else "The concept changed since it was approved — approve it again.")
    argv = [sys.executable, "-m", "ambience_pipeline.cli", "sound", str(b.root)]
    seconds = body.get("seconds")
    if seconds not in (None, "", 0):
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            raise _Bad("seconds must be a number")
        if not 0 < seconds <= PREVIEW_MAX_S:
            raise _Bad(f"A preview is at most {PREVIEW_MAX_S:g} seconds.")
        argv += ["--seconds", f"{seconds:g}"]
    if body.get("dry_run"):
        argv.append("--dry-run")
    try:
        runner.start_command(_key(slug), "sound", argv,
                             busy=f"{slug} is already rendering.",
                             pipeline=PIPELINE, ambience=slug,
                             preview=bool(seconds), dry_run=bool(body.get("dry_run")))
    except ValueError as e:
        raise _Bad(str(e))
    return {"ok": True, "job": job(cfg, _key(slug))}


def audition(cfg: dict, body: dict) -> dict:
    from ambience_pipeline.synth.recipes import REGISTRY
    name = body.get("recipe") or ""
    if name not in REGISTRY:
        raise _Bad(f"No recipe {name!r}.")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise _Bad("params must be an object")
    params = {k: v for k, v in params.items() if k != "type"}
    if body.get("gain_db") is not None:
        params["gain_db"] = body["gain_db"]
    try:
        seconds = float(body.get("seconds") or 30)
    except (TypeError, ValueError):
        raise _Bad("seconds must be a number")
    if not 0 < seconds <= AUDITION_MAX_S:
        raise _Bad(f"An audition is at most {AUDITION_MAX_S:g} seconds.")
    try:
        REGISTRY[name].make(0, params)
    except (KeyError, ValueError, TypeError) as e:
        raise _Bad(str(e).strip("'\""))
    out_dir = _root(cfg) / ab.AUDITIONS
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{time.strftime('%Y%m%dT%H%M%S')}-{name}.wav"
    argv = [sys.executable, "-m", "ambience_pipeline.cli", "audition", name,
            "--seconds", f"{seconds:g}", "--out", str(out)]
    for k, v in params.items():
        argv += ["-p", f"{k}={v}"]
    try:
        runner.start_command(AUDITION_KEY, "audition", argv,
                             busy="An audition is already rendering. Wait, or stop it.",
                             pipeline=PIPELINE, recipe=name, out=str(out))
    except ValueError as e:
        raise _Bad(str(e))
    return {"ok": True, "url": _url(cfg, out), "job": job(cfg, AUDITION_KEY)}


def job(cfg: dict, key: str) -> dict:
    j = runner.status(key)
    marks = j.pop("progress", []) or []
    plan = next((m for m in reversed(marks) if m.get("event") == "plan"), {})
    block = next((m for m in reversed(marks) if m.get("event") == "block"), {})
    art = next((m for m in reversed(marks) if m.get("event") == "artifact"), None)
    loud = next((m for m in reversed(marks) if m.get("event") == "loudness"), None)
    j["stage"] = j.get("kind")
    j["total"] = plan.get("total")
    j["done"] = block.get("done", 0) if plan else 0
    if art and art.get("path"):
        j["artifact"] = _url(cfg, Path(art["path"]))
    if loud:
        j["lufs"] = loud.get("lufs")
        if not loud.get("ok"):
            j["failed"] = True
    return j


def cancel(cfg: dict, key: str) -> dict:
    try:
        meta = runner.cancel(key)
    except ValueError as e:
        raise _Bad(str(e))
    release(cfg, meta)
    return {"ok": True, "job": job(cfg, key)}


def release(cfg: dict, meta: dict) -> None:
    """Put a sound stage a dead or cancelled job left `running` back to pending."""
    slug = meta.get("ambience")
    if not slug or meta.get("stage") != "sound":
        return
    try:
        b = _open(cfg, slug)
        if b.stage_status("sound") == "running":
            b.set_stage("sound", "pending")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def _offset(handler) -> int:
    q = parse_qs(urlparse(handler.path).query)
    try:
        return int((q.get("from") or ["0"])[0] or 0)
    except ValueError:
        return 0


def _answer(handler, fn) -> bool:
    try:
        handler._json(fn())
    except _Bad as e:
        handler._json({"error": str(e), "problems": e.problems}, 400)
    except FileNotFoundError as e:
        handler._json({"error": str(e) or "no such ambience"}, 404)
    except ValueError as e:
        # check_slug and friends: a malformed slug is a request error.
        handler._json({"error": str(e)}, 400)
    return True


def handle_get(handler, cfg: dict, path: str) -> bool:
    if path in ("/ambience", "/ambience.html"):
        handler._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        return True
    if path.startswith("/ambience-audio/"):
        try:
            handler._file(audio_path(cfg, unquote(path[len("/ambience-audio/"):])),
                          "audio/wav")
        except (FileNotFoundError, ValueError):
            handler._json({"error": "no such audio"}, 404)
        return True
    if path == "/api/ambience":
        return _answer(handler, lambda: index(cfg))
    if not path.startswith("/api/ambience/"):
        return False
    parts = [unquote(x) for x in path[len("/api/ambience/"):].split("/")]
    if parts == ["recipes"]:
        return _answer(handler, lambda: {"recipes": recipes()})
    if parts == ["stories"]:
        return _answer(handler, lambda: {"stories": stories(cfg)})
    if parts[0] == "audition" and len(parts) == 2:
        if parts[1] == "job":
            return _answer(handler, lambda: job(cfg, AUDITION_KEY))
        if parts[1] == "log":
            return _answer(handler, lambda: runner.log_chunk(AUDITION_KEY, _offset(handler)))
        return False
    slug = parts[0]
    if len(parts) == 1:
        return _answer(handler, lambda: detail(cfg, slug))
    if len(parts) == 2:
        what = parts[1]
        if what == "concept":
            return _answer(handler, lambda: concept_view(cfg, slug))
        if what == "job":
            return _answer(handler, lambda: (_open(cfg, slug), job(cfg, _key(slug)))[1])
        if what == "log":
            return _answer(handler, lambda: (_open(cfg, slug),
                                             runner.log_chunk(_key(slug), _offset(handler)))[1])
    return False


def handle_post(handler, cfg: dict, path: str, body: dict) -> bool:
    if not path.startswith("/api/ambience/"):
        return False
    body = body if isinstance(body, dict) else {}
    parts = [unquote(x) for x in path[len("/api/ambience/"):].split("/")]
    if parts == ["new"]:
        return _answer(handler, lambda: new(cfg, body))
    if parts == ["audition"]:
        return _answer(handler, lambda: audition(cfg, body))
    if parts == ["audition", "cancel"]:
        return _answer(handler, lambda: cancel(cfg, AUDITION_KEY))
    if len(parts) != 2:
        return False
    slug, what = parts
    if what == "concept":
        return _answer(handler, lambda: save_concept(cfg, slug, body))
    if what == "approve":
        return _answer(handler, lambda: approve(cfg, slug))
    if what == "sound":
        return _answer(handler, lambda: sound(cfg, slug, body))
    if what == "cancel":
        return _answer(handler, lambda: (_open(cfg, slug), cancel(cfg, _key(slug)))[1])
    return False
