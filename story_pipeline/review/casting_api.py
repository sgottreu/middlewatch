"""The casting page: voice auditions per arc, run as jobs, judged in the browser.

Reads and writes stories/<arc>/_casting/ through story_pipeline.casting — the
same record the command line keeps — and runs `check_voices.py` as a detached
job keyed `_casting-<arc>` under runs/, so an audition outlives a restart and
can be cancelled like any other paid stage.

Everything here is free except /api/casting/run, which renders audio and
spends ElevenLabs credits. The page shows the estimate before it asks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import runner
from .. import bibles, casting

V4 = "eleven_v4"


def _stories(cfg: dict) -> Path:
    return Path(cfg.get("stories_dir") or "stories")


def _key(arc: str) -> str:
    return f"_casting-{arc}"


def _arc(cfg: dict, arc: str) -> str:
    if arc not in casting.arcs(_stories(cfg)):
        raise ValueError(f"no arc called {arc!r} under {_stories(cfg)}/")
    return arc


def _voice_ids(cfg: dict) -> dict:
    lib = (cfg.get("elevenlabs") or {}).get("voice_library") or {}
    return {k: (v if isinstance(v, str) else v.get("voice_id")) for k, v in lib.items()}


def index(cfg: dict) -> dict:
    out = []
    for arc in casting.arcs(_stories(cfg)):
        rec = casting.load(arc, _stories(cfg))
        cands = casting.load_candidates(arc, _stories(cfg))
        out.append({"arc": arc,
                    "stories": len(casting.arc_stories(arc, _stories(cfg))),
                    "candidates": sum(len(v) for k, v in cands.items() if not k.startswith("_")),
                    "auditioned": len(rec["voices"]),
                    "cast": len(casting.cast_map(rec))})
    return {"arcs": out}


def estimate(cfg: dict, view: dict) -> dict:
    """Takes not yet in the record, for the chosen story, and roughly what they cost.

    An upper bound: it can't know which library voices will turn out to be
    missing until the run looks them up, and those cost nothing.

    A voice that couldn't be auditioned last time and has no take is counted
    again (`retry`): it may have been added to My Voices since, or be found by
    id now. Without that, a run that leaves one unavailable voice switches
    every Run button off for good.
    """
    stories = _stories(cfg)
    rec = casting.load(view["arc"], stories)
    story = view["story"]
    model = (cfg.get("elevenlabs") or {}).get("model") or "eleven_multilingual_v2"
    ids = _voice_ids(cfg)
    rate = (cfg.get("elevenlabs") or {}).get("usd_per_1k_credits") or 0

    def have(vid, speaker, m):
        v = rec["voices"].get(vid) or {}
        return any((t["story"], t["speaker"], t["model"]) == (story, speaker, m)
                   for t in v.get("tries", []))

    total_takes, total_chars, total_retry, per = 0, 0, 0, {}
    for sp in view["speakers"]:
        line = sp.get("line")
        if not line:
            continue
        n = retry = 0
        cur = ids.get(sp.get("current") or "")
        if cur:
            n += sum(1 for m in dict.fromkeys([model, V4]) if not have(cur, sp["speaker"], m))
        for e in sp["entries"]:
            if e["role"] == "candidate" and e["id"] and e["id"] != cur:
                missing = not have(e["id"], sp["speaker"], V4)
                e["pending"] = missing
                n += int(missing)
                retry += int(missing and bool(e["unavailable"]))
        per[sp["speaker"]] = {"takes": n, "retry": retry, "chars": n * len(line),
                              "usd": round(n * len(line) / 1000 * rate, 2)}
        total_takes += n
        total_retry += retry
        total_chars += n * len(line)
    return {"takes": total_takes, "retry": total_retry, "chars": total_chars,
            "usd": round(total_chars / 1000 * rate, 2), "per_speaker": per,
            "baseline_model": model}


def view(cfg: dict, arc: str, story: str | None = None) -> dict:
    v = casting.arc_view(_arc(cfg, arc), story, _stories(cfg))
    v["estimate"] = estimate(cfg, v)
    v["job"] = job(cfg, arc)
    _attach_bible(cfg, v)
    return v


# What the page shows of a character, in this order. Bible entries carry
# `note`; a story's own cast carries `want` and `voice_note` instead.
_PROFILE = ("age", "gender", "station", "appearance", "dress", "note", "want", "voice_note")


def _attach_bible(cfg: dict, v: dict) -> None:
    """Give each speaker the character entry to cast against: the series
    bible's recurring_cast first, else the chosen story's outline cast (a
    one-story character has no bible entry). Missing files mean no entry, not
    an error — the page is still usable without them."""
    outline = {}
    if v.get("story"):
        p = _stories(cfg) / v["arc"] / v["story"] / "outline.json"
        try:
            outline = json.loads(p.read_text())
        except (OSError, ValueError):
            pass
    try:
        bible = bibles.load(outline.get("bible") or v["arc"], cfg.get("bibles_dir") or "bibles")
    except Exception:
        bible = None
    story_cast = {c.get("name", "").lower(): c for c in outline.get("cast") or []}
    for sp in v["speakers"]:
        name = sp["speaker"].lower()
        hit = bible and bible.member(sp["speaker"])
        src = "bible" if hit else "story"
        hit = hit or story_cast.get(name)
        if hit and src == "bible" and name in story_cast:
            # The bible is canon; the story adds what this character wants here.
            hit = {**{k: story_cast[name].get(k) for k in ("want", "voice_note")}, **hit}
        sp["bible"] = ({"source": src, **{k: hit[k] for k in _PROFILE if hit.get(k)}}
                       if hit else None)


def job(cfg: dict, arc: str) -> dict:
    j = runner.status(_key(arc))
    marks = j.pop("progress", []) or []
    plan = next((m for m in reversed(marks) if m.get("event") == "plan"), {})
    takes = [m for m in marks if m.get("event") == "take"]
    j.update(total=plan.get("total"), done=len(takes),
             failed=sum(1 for m in takes if not m.get("ok")))
    # Whose take is in flight (or was last), so the page can put the progress
    # panel above that character. Before the first take, the first speaker the
    # run was started for; a run of everyone has none until it starts.
    said = [m.get("speaker") for m in marks if m.get("event") in ("start", "take")]
    asked = (runner.latest(_key(arc)) or {}).get("speakers") or [None]
    j["speaker"] = next((s for s in reversed(said) if s), asked[0])
    return j


def log(cfg: dict, arc: str, offset: int = 0) -> dict:
    return runner.log_chunk(_key(arc), offset)


def run(cfg: dict, arc: str, story: str, speakers=None, voices=None,
        force: bool = False) -> dict:
    """Render auditions. **This spends ElevenLabs credits.**"""
    _arc(cfg, arc)
    story_dir = _stories(cfg) / arc / story
    if story not in [d.name for d in casting.arc_stories(arc, _stories(cfg))]:
        raise ValueError(f"{story} isn't a written story in {arc}.")
    script = Path("check_voices.py")
    if not script.exists():
        raise ValueError("check_voices.py isn't in the working directory — start "
                         "the review server from the repo root.")
    argv = [sys.executable, str(script), "--config", cfg.get("_config_path", "config.yaml"),
            "--sample", "--story", str(story_dir), "--add-missing", "--yes"]
    for sp in speakers or []:
        argv += ["--speaker", sp]
    for v in voices or []:
        argv += ["--voice", v]
    if force:
        argv.append("--force")
    runner.start_command(_key(arc), "audition", argv,
                         busy=f"Auditions are already running for {arc}. Wait for "
                              "them to finish, or stop them first.",
                         arc=arc, story=story,
                         speakers=list(speakers or []), voices=list(voices or []))
    return {"ok": True, "job": job(cfg, arc)}


def cancel(cfg: dict, arc: str) -> dict:
    runner.cancel(_key(arc))
    return {"ok": True, "job": job(cfg, arc)}


def verdict(cfg: dict, arc: str, speaker: str, voice_id: str, verdict: str | None,
            note: str | None = None) -> dict:
    stories = _stories(cfg)
    rec = casting.load(_arc(cfg, arc), stories)
    if verdict in (None, "", "clear"):
        casting.clear_verdict(rec, speaker, voice_id)
    elif verdict in casting.VERDICTS:
        casting.set_verdict(rec, speaker, voice_id, verdict, (note or "").strip() or None)
    else:
        raise ValueError(f"verdict must be one of {', '.join(casting.VERDICTS)}, or clear")
    casting.save(rec, stories)
    return {"ok": True}


def candidate(cfg: dict, arc: str, speaker: str, voice_id: str, name: str = "",
              note: str = "", remove: bool = False) -> dict:
    if not (speaker or "").strip():
        raise ValueError("Which character is this voice for?")
    casting.set_candidate(_arc(cfg, arc), speaker.strip(), voice_id, name, note,
                          remove=remove, stories=_stories(cfg))
    return {"ok": True}


def audio_path(cfg: dict, arc: str, rel: str) -> Path:
    """A take on disk, confined to stories/<arc>/_casting/audio/."""
    base = (casting.casting_dir(_arc(cfg, arc), _stories(cfg)) / "audio").resolve()
    p = (casting.casting_dir(arc, _stories(cfg)) / rel).resolve()
    if base not in p.parents or p.suffix.lower() != ".mp3" or not p.is_file():
        raise FileNotFoundError(rel)
    return p


def apply_plan(cfg: dict, arc: str, use_v4: bool = True, cast_mode: bool = True) -> dict:
    """What Apply would change. Free; writes nothing."""
    from .. import casting_apply
    return casting_apply.plan(cfg, _arc(cfg, arc), use_v4=use_v4, cast_mode=cast_mode)


def apply(cfg: dict, arc: str, use_v4: bool = True, cast_mode: bool = True,
          commit: bool = True, push: bool = True) -> dict:
    """Write the cast into voices.yaml, config.yaml, the bible and the arc's stories, then
    commit. Free in itself; it is the re-recording it queues up that costs."""
    from .. import casting_apply
    return casting_apply.apply(cfg, _arc(cfg, arc), use_v4=use_v4, cast_mode=cast_mode,
                               commit=commit, push=push,
                               config_path=cfg.get("_config_path", "config.yaml"))
