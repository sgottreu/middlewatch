"""Apply an arc's casting: write the voices you cast everywhere they're decided.

A character's voice is decided in five places, and a recast has to reach all of
them or the old voice comes back somewhere:

    config.yaml          elevenlabs.voice_library (name -> id), casting.mode,
                         elevenlabs.model. Git-ignored: it is per machine.
    bibles/<b>/<b>.md    recurring_cast[].voice, and voices.narrator — what every
                         new story in the series starts from.
    <story>/outline.json cast[].voice — what an unrecorded story will use.
    <story>/manifest.json pinned.voices — what a recorded story re-records with.
                         Changing it marks that narration out of date, which is
                         what puts the story back in the queue for `record`.
    bible changelog      one entry saying what changed and why.

`plan()` reads everything and writes nothing; the page shows it. `apply()`
re-plans, validates the new config and bible before anything is written, writes
it all, logs the change, and commits the tracked files (config.yaml is not one).

Hand-written YAML is edited line by line rather than re-dumped, so comments and
ordering survive; each edit is re-parsed and checked before it is kept.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import bibles, casting, changelog
from . import config as cfgmod

V4 = "eleven_v4"


class ApplyError(RuntimeError):
    pass


# --- yaml line editing ------------------------------------------------------

def _q(s: str) -> str:
    """A YAML scalar: plain when that's safe, quoted otherwise."""
    return s if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._'-]*", s) else json.dumps(s)


def _block(lines: list[str], start: int) -> int:
    """Index just past the indented block that follows lines[start]."""
    base = len(lines[start]) - len(lines[start].lstrip())
    i = start + 1
    while i < len(lines):
        ln = lines[i]
        if ln.strip() and not ln.lstrip().startswith("#") and len(ln) - len(ln.lstrip()) <= base:
            break
        i += 1
    return i


def _find_key(lines, key, start=0, end=None, indent=0) -> int | None:
    pat = re.compile(rf"^{' ' * indent}{re.escape(key)}:(\s|$)")
    for i in range(start, len(lines) if end is None else end):
        if pat.match(lines[i]):
            return i
    return None


def _set_value(line: str, value: str) -> str:
    m = re.match(r"^(\s*[^:#]+:\s*)([^#]*?)(\s*#.*)?$", line)
    if not m:
        raise ApplyError(f"can't edit line: {line!r}")
    comment = m.group(3) or ""
    head = m.group(1) if m.group(1).endswith(" ") else m.group(1) + " "
    if comment and not comment.startswith(" "):
        comment = " " + comment
    return f"{head}{value}{comment}"


def set_child(text: str, parent: str, child: str, value: str) -> str:
    """parent.child = value, for a two-level mapping; adds the child if missing."""
    lines = text.split("\n")
    p = _find_key(lines, parent)
    if p is None:
        raise ApplyError(f"no `{parent}:` section")
    end = _block(lines, p)
    c = _find_key(lines, child, p + 1, end, indent=2)
    if c is None:
        lines.insert(p + 1, f"  {child}: {value}")
    else:
        lines[c] = _set_value(lines[c], value)
    return "\n".join(lines)


def add_library(text: str, additions: dict) -> str:
    """Append name: id lines to elevenlabs.voice_library."""
    if not additions:
        return text
    lines = text.split("\n")
    el = _find_key(lines, "elevenlabs")
    if el is None:
        raise ApplyError("config.yaml has no `elevenlabs:` section")
    lib = _find_key(lines, "voice_library", el + 1, _block(lines, el), indent=2)
    if lib is None:
        raise ApplyError("config.yaml has no elevenlabs.voice_library")
    end = _block(lines, lib)
    while end > lib + 1 and not lines[end - 1].strip():
        end -= 1
    new = [f"    {_q(k)}: {v}" for k, v in additions.items()]
    lines[end:end] = new
    return "\n".join(lines)


def _front(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        raise ApplyError("bible has no front matter")
    close = text.index("\n---", 3)
    return text[: close + 1], text[close + 1:]


def bible_set_voices(text: str, voices: dict[str, str], narrator: str | None) -> str:
    """recurring_cast[name].voice = voice; voices.narrator = narrator."""
    front, rest = _front(text)
    lines = front.split("\n")
    rc = _find_key(lines, "recurring_cast")
    if voices:
        if rc is None:
            raise ApplyError("bible has no recurring_cast")
        i, end = rc + 1, _block(lines, rc)
        while i < end:
            m = re.match(r"^(\s*)- name:\s*(.+?)\s*$", lines[i])
            if m:
                name = m.group(2).strip().strip("\"'")
                ind = " " * (len(m.group(1)) + 2)
                j = i + 1
                while j < end and not re.match(r"^\s*- name:", lines[j]) and \
                        (not lines[j].strip() or lines[j].startswith(ind)):
                    j += 1
                if name in voices:
                    v = next((k for k in range(i + 1, j)
                              if re.match(rf"^{ind}voice:", lines[k])), None)
                    if v is None:
                        lines.insert(i + 1, f"{ind}voice: {_q(voices[name])}")
                        end += 1
                        j += 1
                    else:
                        lines[v] = _set_value(lines[v], _q(voices[name]))
                i = j
            else:
                i += 1
    if narrator:
        vb = _find_key(lines, "voices")
        if vb is None:
            g = _find_key(lines, "genre")
            at = (g + 1) if g is not None else 1
            lines[at:at] = ["voices:", f"  narrator: {_q(narrator)}"]
        else:
            n = _find_key(lines, "narrator", vb + 1, _block(lines, vb), indent=2)
            if n is None:
                lines.insert(vb + 1, f"  narrator: {_q(narrator)}")
            else:
                lines[n] = _set_value(lines[n], _q(narrator))
    return "\n".join(lines) + rest


# --- planning -----------------------------------------------------------------

def _library(cfg: dict) -> dict:
    lib = (cfg.get("elevenlabs") or {}).get("voice_library") or {}
    return {k: (v if isinstance(v, str) else v.get("voice_id")) for k, v in lib.items()}


def _key_for(vid: str, name: str, library: dict, taken: dict) -> str:
    """The voice_library name for a voice id: an existing one, or a new one."""
    for k, v in {**library, **taken}.items():
        if v == vid:
            return k
    base = re.sub(r"[^A-Za-z0-9 ._'-]", "", name.split(" - ")[0]).strip() or vid
    key, n = base, 2
    while key in library or key in taken:
        key = f"{base} {vid[:4]}" if n == 2 else f"{base} {vid[:4]}{n}"
        n += 1
    return key


def _speakers_in(story_dir: Path) -> set[str]:
    try:
        _, lines = casting.story_lines(story_dir)
        return {s for s in lines if s}
    except Exception:
        return set()


def _busy(arc: str, slugs: list[str]) -> list[str]:
    try:
        from .review import runner
    except Exception:
        return []
    out = []
    for key in [f"_casting-{arc}", *slugs]:
        m = runner.latest(key)
        if m and m.get("status") == "running":
            out.append(f"{key} ({m.get('stage')})")
    return out


def plan(cfg: dict, arc: str, *, use_v4: bool = True, cast_mode: bool = True) -> dict:
    stories = Path(cfg.get("stories_dir") or "stories")
    record = casting.load(arc, stories)
    cast = casting.cast_map(record)                     # speaker -> (vid, voice)
    if not cast:
        raise ApplyError("Nobody is cast yet. Mark a voice Cast for at least one character.")
    library = _library(cfg)
    new_keys: dict = {}
    voice_of = {}                                       # speaker -> library key
    for sp, (vid, v) in sorted(cast.items()):
        key = _key_for(vid, v["name"], library, new_keys)
        if key not in library:
            new_keys[key] = vid
        voice_of[sp] = key

    el, ca = cfg.get("elevenlabs") or {}, cfg.get("casting") or {}
    config_changes = {"library_add": new_keys}
    if cast_mode and ca.get("mode", "solo") != "cast":
        config_changes["mode"] = [ca.get("mode", "solo"), "cast"]
    if cast_mode:
        n = len([s for s in cast if s != "narrator"])
        if n > int(ca.get("max_named_voices") or 0):
            config_changes["max_named_voices"] = [ca.get("max_named_voices"), n]
    if use_v4 and el.get("model") != V4:
        config_changes["model"] = [el.get("model"), V4]

    # The bible: the one most of this arc's stories name.
    story_dirs = sorted(m.parent for m in (stories / arc).glob("*/manifest.json"))
    def _bible_of(d):
        m = json.loads((d / "manifest.json").read_text())
        return m.get("bible") or (m.get("pinned") or {}).get("bible")
    bible_names = [_bible_of(d) for d in story_dirs]
    bible_name = max(set(filter(None, bible_names)) or {None}, key=bible_names.count)
    bible_changes, bible_path, narrator_change, bible_notes = [], None, None, []
    if bible_name:
        bible_path = bibles.path_for(bible_name, cfg.get("bibles_dir") or "bibles")
        b = bibles.load(bible_name, cfg.get("bibles_dir") or "bibles")
        for member in b.meta.get("recurring_cast") or []:
            nm = member.get("name")
            if nm in voice_of and member.get("voice") != voice_of[nm]:
                bible_changes.append({"character": nm, "from": member.get("voice"),
                                      "to": voice_of[nm]})
        cur_narr = (b.meta.get("voices") or {}).get("narrator")
        if cur_narr is None:
            # No narrator in the bible means the genre's default is in force.
            try:
                from . import genres
                gv = genres.load(b.meta.get("genre", ""))
                gv = gv.voices() if callable(gv.voices) else gv.voices
                genre_narr = (gv or {}).get("narrator")
            except Exception:
                genre_narr = None
        if "narrator" in voice_of and (cur_narr or genre_narr) != voice_of["narrator"]:
            narrator_change = {"from": cur_narr, "to": voice_of["narrator"]}
        old = {c["from"] for c in bible_changes if c["from"]}
        prose = _front(bible_path.read_text())[1]
        mentioned = sorted(v for v in old if re.search(rf"\b{re.escape(v)}\b", prose))
        if mentioned:
            bible_notes.append(f"The bible's prose still mentions {', '.join(mentioned)} "
                               "(e.g. a voice casting note). That's yours to rewrite.")

    stories_out = []
    for d in story_dirs:
        m = json.loads((d / "manifest.json").read_text())
        pinned = (m.get("pinned") or {}).get("voices")
        speakers = _speakers_in(d)
        outline = json.loads((d / "outline.json").read_text()) if (d / "outline.json").exists() else {}
        oc = [{"character": c["name"], "from": c.get("voice"), "to": voice_of[c["name"]]}
              for c in outline.get("cast", []) if c.get("name") in voice_of
              and c.get("voice") != voice_of[c["name"]]]
        pc = []
        if pinned is not None:
            for sp in sorted(speakers | set(pinned)):
                if sp in voice_of and pinned.get(sp) != voice_of[sp]:
                    pc.append({"character": sp, "from": pinned.get(sp), "to": voice_of[sp]})
        gv = (m.get("pinned") or {}).get("genre_voices") or {}
        narr = "narrator" in voice_of and gv and gv.get("narrator") != voice_of["narrator"]
        recorded = any((d / "audio").glob("*.mp3")) if (d / "audio").is_dir() else False
        uncast = sorted(s for s in speakers if s not in voice_of)
        if oc or pc or narr or uncast:
            stories_out.append({
                "slug": d.name, "title": m.get("title") or outline.get("title") or d.name,
                "outline": oc, "pinned": pc, "genre_narrator": bool(narr),
                "rerecord": bool(recorded and pc), "uncast": uncast})

    warnings = []
    pool = []
    try:
        from . import genres
        g = genres.load(json.loads((story_dirs[0] / "manifest.json").read_text()).get("genre", ""))
        gv_ = g.voices() if callable(g.voices) else g.voices
        cv = (gv_ or {}).get("character_voices") or {}
        pool = sorted({v for vs in cv.values() for v in (vs or [])})
    except Exception:
        pass
    defaults = {"Roger", "Sarah", "Laura", "Charlie", "George", "Callum", "River", "Harry", "Liam",
                "Alice", "Matilda", "Will", "Jessica", "Eric", "Chris", "Brian", "Daniel", "Lily", "Bill"}
    expiring = [v for v in pool if v in defaults]
    if expiring:
        warnings.append(f"The genre's pool for unnamed characters still uses Default voices that "
                        f"expire on 2026-12-31: {', '.join(expiring)}. Minor characters in new "
                        f"stories will get them.")
    warnings.append("config.yaml isn't in git, so its changes apply to this machine only. "
                    "Copy the new voice_library lines to any other machine that narrates.")

    return {
        "arc": arc, "cast": [{"speaker": s, "voice": voice_of[s], "id": cast[s][0],
                              "name": cast[s][1]["name"]} for s in sorted(cast)],
        "config": config_changes,
        "bible": {"name": bible_name, "path": str(bible_path) if bible_path else None,
                  "changes": bible_changes, "narrator": narrator_change, "notes": bible_notes},
        "stories": stories_out, "warnings": warnings,
        "busy": _busy(arc, [d.name for d in story_dirs]),
    }


# --- applying -------------------------------------------------------------------

def apply(cfg: dict, arc: str, *, use_v4: bool = True, cast_mode: bool = True,
          commit: bool = True, push: bool = True, config_path: str = "config.yaml") -> dict:
    p = plan(cfg, arc, use_v4=use_v4, cast_mode=cast_mode)
    c = p["config"]
    if not (c["library_add"] or len(c) > 1 or p["bible"]["changes"] or p["bible"]["narrator"]
            or any(s["outline"] or s["pinned"] or s["genre_narrator"] for s in p["stories"])):
        raise ApplyError("Nothing to change: this cast is already applied everywhere.")
    if p["busy"]:
        raise ApplyError("Wait for these to finish first: " + ", ".join(p["busy"]))
    stories = Path(cfg.get("stories_dir") or "stories")
    voice_of = {c["speaker"]: c["voice"] for c in p["cast"]}
    touched: list[Path] = []

    # 1. config.yaml — built and checked in memory first.
    cpath = Path(config_path)
    ctext = cpath.read_text()
    new = add_library(ctext, p["config"]["library_add"])
    if "mode" in p["config"]:
        new = set_child(new, "casting", "mode", "cast")
    if "max_named_voices" in p["config"]:
        new = set_child(new, "casting", "max_named_voices", str(p["config"]["max_named_voices"][1]))
    if "model" in p["config"]:
        new = set_child(new, "elevenlabs", "model", V4)
    parsed = yaml.safe_load(new)
    lib = parsed["elevenlabs"]["voice_library"]
    for k, vid in p["config"]["library_add"].items():
        if lib.get(k) != vid:
            raise ApplyError(f"config edit check failed for voice_library.{k}")
    if "mode" in p["config"] and parsed["casting"]["mode"] != "cast":
        raise ApplyError("config edit check failed for casting.mode")
    if "model" in p["config"] and parsed["elevenlabs"]["model"] != V4:
        raise ApplyError("config edit check failed for elevenlabs.model")

    # 2. The bible — edited, re-parsed and validated against the new config.
    bpath = Path(p["bible"]["path"]) if p["bible"]["path"] else None
    btext = bnew = None
    if bpath and (p["bible"]["changes"] or p["bible"]["narrator"]):
        btext = bpath.read_text()
        bnew = bible_set_voices(btext, {c["character"]: c["to"] for c in p["bible"]["changes"]},
                                p["bible"]["narrator"]["to"] if p["bible"]["narrator"] else None)
        meta = yaml.safe_load(_front(bnew)[0].strip().strip("-"))
        for c in p["bible"]["changes"]:
            got = next((m.get("voice") for m in meta.get("recurring_cast", [])
                        if m.get("name") == c["character"]), None)
            if got != c["to"]:
                raise ApplyError(f"bible edit check failed for {c['character']}")

    # Nothing has been written yet. From here on, write everything.
    stamp = time.strftime("%Y%m%dT%H%M%S")
    shutil.copy2(cpath, cpath.with_name(f"{cpath.name}.{stamp}.bak"))
    cpath.write_text(new)
    if bnew is not None:
        merged = {**cfg, **parsed}
        # Only problems this edit introduced: an unrelated, pre-existing error in
        # the bible is not this button's to refuse over.
        before = {str(x) for x in bibles.fatal(bibles.validate(bpath, merged))}
        bpath.write_text(bnew)
        fatal = [x for x in bibles.fatal(bibles.validate(bpath, merged)) if str(x) not in before]
        if fatal:
            bpath.write_text(btext)
            cpath.write_text(ctext)
            raise ApplyError("The edited bible didn't validate, so nothing was changed:\n"
                             + "\n".join(str(f) for f in fatal))
        touched.append(bpath)

    for s in p["stories"]:
        d = stories / arc / s["slug"]
        if s["outline"]:
            o = json.loads((d / "outline.json").read_text())
            for c in o.get("cast", []):
                if c.get("name") in voice_of:
                    c["voice"] = voice_of[c["name"]]
            (d / "outline.json").write_text(json.dumps(o, indent=2) + "\n")
            touched.append(d / "outline.json")
        if s["pinned"] or s["genre_narrator"]:
            m = json.loads((d / "manifest.json").read_text())
            pin = m.setdefault("pinned", {})
            if s["pinned"]:
                for c in s["pinned"]:
                    pin["voices"][c["character"]] = c["to"]
            if s["genre_narrator"]:
                pin["genre_voices"]["narrator"] = voice_of["narrator"]
            (d / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
            touched.append(d / "manifest.json")

    # 3. Changelog and the casting record.
    lines = [f"- {c['speaker']}: {c['name']} (`{c['id']}`) as `{c['voice']}`" for c in p["cast"]]
    if "model" in p["config"]:
        lines.append(f"- Narration model: {p['config']['model'][0]} → {V4}")
    summary = f"Voices recast from the casting page ({arc}).\n\n" + "\n".join(lines)
    if bpath:
        changelog.append(bpath, summary, changelog.resolve_author(cfg=cfg), kind="voices")
        touched.append(changelog.path_for(bpath))
    rec = casting.load(arc, stories)
    rec.setdefault("applied", []).append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cast": {c["speaker"]: {"id": c["id"], "voice": c["voice"]} for c in p["cast"]},
        "model": V4 if "model" in p["config"] else (cfg.get("elevenlabs") or {}).get("model"),
        "mode": "cast" if cast_mode else (cfg.get("casting") or {}).get("mode"),
    })
    casting.save(rec, stories)
    cd = casting.casting_dir(arc, stories)
    touched += [cd / "auditions.json", cd / "CASTING.md"]
    if (cd / "candidates.json").exists():
        touched.append(cd / "candidates.json")

    # 4. The running server keeps its config in memory; refresh it in place.
    keep = {k: v for k, v in cfg.items() if k.startswith("_")}
    cfg.clear()
    cfg.update(cfgmod.load(cpath))
    cfg.update(keep)

    result = {"ok": True, "plan": p, "files": [str(x) for x in touched],
              "rerecord": [s["slug"] for s in p["stories"] if s["rerecord"]]}
    if commit:
        result["git"] = _commit(touched, arc, p, push)
    return result


def _git(*args, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    if check and r.returncode != 0:
        raise ApplyError((r.stderr or r.stdout).strip() or f"git {args[0]} failed")
    return r


def _commit(files: list[Path], arc: str, p: dict, push: bool) -> dict:
    """Commit exactly these files — nothing else that happens to be staged — and
    push without force. Mirrors mw-commit-stories: a failed push leaves the
    commit safe on disk and says so, rather than rebasing anything."""
    try:
        _git("rev-parse", "--is-inside-work-tree")
    except Exception as e:
        return {"committed": False, "message": f"not a git checkout: {e}"}
    paths = sorted({str(f) for f in files if Path(f).exists()})
    for attempt in range(5):
        if not Path(".git/index.lock").exists():
            break
        time.sleep(1)
    _git("add", "--", *paths)
    if _git("diff", "--cached", "--quiet", "--", *paths, check=False).returncode == 0:
        return {"committed": False, "message": "nothing to commit — already up to date"}
    who = []
    if not _git("config", "user.name", check=False).stdout.strip():
        who = ["-c", "user.name=Middle Watch review", "-c", "user.email=sgottreu@gmail.com"]
    msg = f"casting: apply {arc} voices\n\n" + "\n".join(
        f"- {c['speaker']}: {c['name']} ({c['id']})" for c in p["cast"])
    r = subprocess.run(["git", *who, "commit", "-q", "-m", msg, "--", *paths],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ApplyError((r.stderr or r.stdout).strip() or "git commit failed")
    sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
    out = {"committed": True, "sha": sha, "files": paths, "pushed": False}
    if push:
        pr = _git("push", "-q", check=False)
        if pr.returncode == 0:
            out["pushed"] = True
        else:
            out["push_error"] = (pr.stderr or pr.stdout).strip()
    return out
