#!/usr/bin/env python3
"""Compare the repo's elevenlabs.voice_library against the ElevenLabs account,
and optionally render samples to audition voices on Eleven v4.

Stdlib only (PyYAML used if the config is YAML).

Usage:
  export ELEVENLABS_API_KEY=...
  python3 check_voices.py --config config.yaml
  python3 check_voices.py --config config.yaml --sample          # one test line per voice
  python3 check_voices.py --config config.yaml --sample --text "Your own test line."

  # Per-character audition for a story: each speaker's own line, rendered on
  #   current voice @ configured model, current voice @ eleven_v4,
  #   replacement voice @ eleven_v4
  python3 check_voices.py --config config.yaml --sample \
      --story stories/amberlight/the-cracked-beam
  python3 check_voices.py ... --story ... --candidate "Mrs Hale=Sia"   # repeatable, per speaker
  python3 check_voices.py ... --story ... --candidates stories/amberlight/_casting/candidates.json
  python3 check_voices.py ... --story ... --add-missing   # add library voices to My Voices

Default voices (Daniel, Alice, George, Lily, Matilda, Brian, ...) expire on
2026-12-31; their candidates come from ElevenLabs' replacement table below.
Story auditions are recorded per arc in stories/<arc>/_casting/: the arc's
candidates.json is used by default, takes go to _casting/audio/<story>/ (with an
index.html to listen side by side), and every take is logged in auditions.json
and CASTING.md. Record verdicts with `python3 -m story_pipeline.casting`.
Rendered files are cached, so re-running only pays for what's new (--force to
redo). Plain --sample (no --story) still writes scratch audio to voice-samples/.
"""
import argparse
import html
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from story_pipeline import casting

API = "https://api.elevenlabs.io"
DEFAULT_LINE = ("The watch bell rang twice, and somewhere below the lantern "
                "swung against the beam.")
V4 = "eleven_v4"
FALLBACK_BASELINE = "eleven_multilingual_v2"

# ElevenLabs' suggested replacements for Default voices expiring 2026-12-31
# (help.elevenlabs.io "What are Default voices?"). Not 1-to-1 — audition them.
REPLACEMENTS = {
    "Roger": "Darian - Warm Grounded Storyteller",
    "Sarah": "Talia - Warm Soft Guide",
    "Laura": "Elara - Crisp Pro Narrator",
    "Charlie": "Baxter - Dry Calm Aussie",
    "George": "Eldrin - Crisp British Baritone",
    "Callum": "Kellan - Casual Friendly Speaker",
    "River": "Elowen - Upbeat Modern Narrator",
    "Harry": "Kaelen - Amateur Warrior",
    "Liam": "Lawrence - Bright and Informative",
    "Alice": "Alicia - Polished Global Anchor",
    "Matilda": "Maisie - Friendly Casual Neighbor",
    "Will": "Warren - Effortless and Cool",
    "Jessica": "Jade - Upbeat and Natural",
    "Eric": "Eddie - Helpful and Comforting",
    "Chris": "Caleb - Trusted Guide",
    "Brian": "Sawyer - Midnight Storyteller",
    "Daniel": "Finley - Articulate Anchor",
    "Lily": "Florence - Atmospheric Storyteller",
    "Bill": "Wyatt - Seasoned Mentor",
}


def call(method, path, key, params=None, body=None, raw=False):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
            return resp.status, payload if raw else json.loads(payload)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def load_config(path):
    p = pathlib.Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml  # PyYAML, already in the pipeline venv if config is YAML
        return yaml.safe_load(text)
    if p.suffix == ".toml":
        import tomllib
        return tomllib.loads(text)
    return json.loads(text)


def voice_library(cfg):
    lib = (cfg.get("elevenlabs") or {}).get("voice_library")
    if lib is None:
        sys.exit("No elevenlabs.voice_library found in that config.")
    items = lib.items() if isinstance(lib, dict) else ((v.get("name"), v) for v in lib)
    out = {}
    for name, v in items:
        out[name] = v if isinstance(v, str) else (v.get("voice_id") or v.get("id"))
    return out


def account_voices(key):
    voices, token = [], None
    while True:
        params = {"page_size": 100}
        if token:
            params["next_page_token"] = token
        status, data = call("GET", "/v2/voices", key, params)
        if status != 200:
            sys.exit(f"/v2/voices failed: {status} {data}")
        voices += data.get("voices", [])
        if not data.get("has_more"):
            return voices
        token = data.get("next_page_token")


def name_matches(name, voices):
    n = name.lower()
    return [v for v in voices if v["name"].lower().split(" - ")[0].strip() == n
            or v["name"].lower().startswith(n)]


def library_matches(name, key):
    status, data = call("GET", "/v1/shared-voices", key,
                        {"search": name, "page_size": 30})
    if status != 200:
        return []
    return [v for v in data.get("voices", [])
            if v.get("name", "").lower().startswith(name.lower())]


def v4_flag(voice):
    hq = voice.get("high_quality_base_model_ids")
    if hq is None:
        return ""
    return "  v4-optimized" if V4 in hq else "  (not listed as v4-optimized)"


# --- story auditions -------------------------------------------------------

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# Story parsing lives in story_pipeline.casting so the review page reads lines
# exactly the way this script renders them.
find_cast, story_lines, pick_line = casting.find_cast, casting.story_lines, casting.pick_line


def progress(**event):
    """One line for the review page's progress bar; silent from a terminal."""
    if os.environ.get("MW_PROGRESS"):
        print("::progress " + json.dumps(event), flush=True)


def resolve(pick, voices, by_id, key, add_missing):
    """A pick -> (voice_id, display name, note). Account first, then library.

    A pick is a voice name, a voice id, or {"id": ..., "name": ...}. With an id,
    only that exact voice is accepted; the name is how the library is searched,
    since adding a library voice needs its owner, which only a search returns.
    """
    if isinstance(pick, dict):
        vid, name = pick.get("id"), pick.get("name") or pick.get("id")
    elif re.fullmatch(r"[A-Za-z0-9]{20}", pick):
        vid, name = pick, pick
    else:
        vid, name = None, pick
    if vid and vid in by_id:
        return vid, by_id[vid]["name"], v4_flag(by_id[vid])
    full = name.lower()
    first = full.split(" - ")[0].strip()
    if not vid:
        acct = [v for v in voices if v["name"].lower() == full] or name_matches(first, voices)
        if acct:
            return acct[0]["voice_id"], acct[0]["name"], v4_flag(acct[0])
    lib = library_matches(first, key) if name != vid else []
    if vid:
        hit = next((v for v in lib if v["voice_id"] == vid), None)
        if not hit:
            return None, name, (f"{vid} not in account and not found by searching "
                                f"the Voice Library for '{name}' — add it on elevenlabs.io")
    else:
        hit = ([v for v in lib if v["name"].lower() == full] or lib or [None])[0]
    if not hit:
        return None, name, "not found in account or Voice Library"
    if not add_missing:
        return None, hit["name"], (f"in Voice Library only ({hit['voice_id']}); "
                                   "rerun with --add-missing to add it to My Voices")
    status, data = call("POST", f"/v1/voices/add/{hit['public_owner_id']}/{hit['voice_id']}",
                        key, body={"new_name": hit["name"]})
    if status != 200:
        return None, hit["name"], f"add to My Voices failed: {status} {str(data)[:120]}"
    new_id = data.get("voice_id", hit["voice_id"])
    added = {"voice_id": new_id, "name": hit["name"], "category": "professional",
             "labels": casting.labels_of(hit)}
    voices.append(added)       # so a voice picked for two speakers is added once
    by_id[new_id] = added
    if vid and vid != new_id:
        by_id[vid] = added
    return new_id, hit["name"], "  added to My Voices"


def build_auditions(args, cfg, configured, voices, by_id, key, baseline):
    cast, lines = story_lines(args.story)
    overrides = load_candidates(args)
    narrator_voice = cast.get("narrator")
    jobs, rows = [], []

    print(f"\nAudition cast for {args.story}:")
    shared = {}
    for speaker, vname in cast.items():
        shared.setdefault(vname, []).append(speaker)
    for vname, who in shared.items():
        if len(who) > 1:
            print(f"  note: {', '.join(who)} share {vname} — give each a voice of their own.")

    only_speakers = set(args.speaker or [])
    only_voices = {v.lower() for v in (args.voice or [])}

    def wanted(vid, name):
        if not only_voices:
            return True
        return (vid or "").lower() in only_voices or (name or "").lower().split(" - ")[0] in only_voices

    for speaker, vname in cast.items():
        if only_speakers and speaker not in only_speakers:
            continue
        text = pick_line(lines.get(speaker, []))
        if not text:
            print(f"  {speaker:<22} no lines in this story — skipped")
            continue
        cur_id = configured.get(vname)
        cur = by_id.get(cur_id) if cur_id else None
        is_default = (cur or {}).get("category") == "premade" or vname in REPLACEMENTS

        if speaker in overrides:
            picks, why = overrides[speaker], "your pick"
        elif vname in REPLACEMENTS:
            picks, why = [REPLACEMENTS[vname]], f"replaces Default '{vname}'"
        elif speaker != "narrator" and vname == narrator_voice:
            picks, why = [], "shares the narrator — pass --candidate to audition one"
        else:
            picks, why = [], "not a Default; kept"

        tag = " [Default, expires 2026-12-31]" if is_default else ""
        print(f"  {speaker:<22} now {vname}{tag}")
        if not picks:
            print(f"  {'':<22} try -  ({why})")

        row = {"speaker": speaker, "text": text, "cells": []}
        base = f"{slug(speaker)}"
        if cur_id and wanted(cur_id, vname):
            for model, label in ((baseline, f"{vname} @ {baseline}"), (V4, f"{vname} @ {V4}")):
                jobs.append((row, label, cur_id, model, text, f"{base}__now__{model}.mp3",
                             {"speaker": speaker, "source": "current"}))
        elif not cur_id:
            print(f"  {'':<22} ! {vname} isn't in elevenlabs.voice_library — current takes skipped")

        seen = {cur_id}
        for pick in picks:
            pid = pick.get("id") if isinstance(pick, dict) else pick
            pname = pick.get("name") if isinstance(pick, dict) else pick
            if not wanted(pid, pname):
                continue
            cid, cdisplay, note = resolve(pick, voices, by_id, key, args.add_missing)
            if not cid:
                print(f"  {'':<22} try {cdisplay}  ({why} — {note.strip()})")
                jobs.append((row, f"{cdisplay} @ {V4}", None, note.strip(), "", None,
                             {"speaker": speaker, "pick": pick}))
                continue
            print(f"  {'':<22} try {cdisplay}  ({why}{note})")
            if cid in seen:
                continue
            seen.add(cid)
            jobs.append((row, f"{cdisplay} @ {V4}", cid, V4, text,
                         f"{base}__try-{slug(cdisplay.split(' - ')[0])}-{cid[:6].lower()}__{V4}.mp3",
                         {"speaker": speaker, "source": why}))
        rows.append(row)

    return rows, jobs


def record_auditions(arc, story, jobs, by_id, out, stories_root=pathlib.Path("stories")):
    """Log every take that exists on disk, and every pick that couldn't be found."""
    record = casting.load(arc, stories_root)
    cdir = casting.casting_dir(arc, stories_root).resolve()
    for row, label, vid, model, text, fname, meta in jobs:
        if vid is None:
            pick = meta.get("pick")
            pick = pick.get("name") or pick.get("id") if isinstance(pick, dict) else pick
            if isinstance(meta.get("pick"), dict) and meta["pick"].get("id"):
                pick = f"{pick} ({meta['pick']['id']})"
            casting.add_unavailable(record, speaker=meta["speaker"], pick=pick, note=model)
            continue
        if not meta.get("rendered"):
            continue
        v = by_id.get(vid) or {}
        casting.note_voice(record, vid, v.get("name") or label.split(" @ ")[0],
                           casting.labels_of(v), meta.get("source"))
        casting.add_try(record, vid, story=story, speaker=meta["speaker"], model=model,
                        line=text, file=_rel(out / fname, cdir))
        name = record["voices"][vid]["name"].lower()
        record["unavailable"] = [
            u for u in record["unavailable"]
            if not (u["speaker"] == meta["speaker"]
                    and (vid in u["pick"] or u["pick"].lower().split(" (")[0] in name))]
    return casting.save(record, stories_root)


def _rel(path, base):
    """Take paths are stored relative to _casting/, so the record survives a move."""
    try:
        return str(pathlib.Path(path).resolve().relative_to(base))
    except ValueError:
        return str(path)


def load_candidates(args):
    """Speaker -> [voice name or id], from --candidates FILE and --candidate flags.

    The file is JSON: {"Speaker": ["Voice or id", ...], ...}. Keys starting
    with "_" are ignored, so the file can carry notes.
    """
    out = {}
    if not args.candidates and not args.candidate and getattr(args, "story", None):
        default = (casting.casting_dir(casting.arc_of(args.story),
                                       pathlib.Path(args.story).resolve().parent.parent)
                   / "candidates.json")
        if default.exists():
            args.candidates = str(default)
            print(f"Candidates: {default}")
    if args.candidates:
        data = json.loads(pathlib.Path(args.candidates).read_text())
        for speaker, picks in data.items():
            if speaker.startswith("_"):
                continue
            picks = [picks] if isinstance(picks, str) else picks
            out.setdefault(speaker, []).extend(picks)
    for c in args.candidate:
        speaker, pick = c.split("=", 1)
        out.setdefault(speaker.strip(), []).append(pick.strip())
    return out


def write_index(out, title, rows):
    def cell(c):
        if c.get("file"):
            return (f'<td><div class="lbl">{html.escape(c["label"])}</div>'
                    f'<audio controls preload="none" src="{html.escape(c["file"])}"></audio></td>')
        return f'<td><div class="lbl">{html.escape(c["label"])}</div><em>{html.escape(c["error"])}</em></td>'
    body = "\n".join(
        f'<tr><th>{html.escape(r["speaker"])}<p>{html.escape(r["text"])}</p></th>'
        + "".join(cell(c) for c in r["cells"]) + "</tr>" for r in rows)
    (out / "index.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>body{{font:15px/1.4 system-ui;margin:24px;background:#131e2b;color:#e8e2d4}}
table{{border-collapse:collapse;width:100%}}td,th{{border-top:1px solid #2c3b4f;padding:12px;vertical-align:top;text-align:left}}
th{{width:34%}}th p{{font-weight:400;color:#b9b2a3;font-size:13px}}.lbl{{color:#e8a44c;font-size:13px;margin-bottom:6px}}
audio{{width:100%}}em{{color:#d77}}</style>
<h1>{html.escape(title)}</h1><table>{body}</table>""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--key-env", default="ELEVENLABS_API_KEY")
    ap.add_argument("--sample", action="store_true",
                    help="render samples (costs credits)")
    ap.add_argument("--text", default=DEFAULT_LINE)
    ap.add_argument("--story", help="story folder: audition each speaker with their own line")
    ap.add_argument("--candidate", action="append", default=[], metavar="SPEAKER=VOICE",
                    help="audition this voice (name or id) for a speaker; repeatable")
    ap.add_argument("--candidates", metavar="FILE",
                    help='JSON {"Speaker": ["voice name or id", ...]} of voices to audition')
    ap.add_argument("--speaker", action="append", metavar="NAME",
                    help="only audition this speaker; repeatable")
    ap.add_argument("--voice", action="append", metavar="ID_OR_NAME",
                    help="only render takes for this voice; repeatable")
    ap.add_argument("--add-missing", action="store_true",
                    help="add candidate voices found only in the Voice Library to My Voices")
    ap.add_argument("--force", action="store_true", help="re-render files that already exist")
    ap.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    ap.add_argument("--out", help="where takes go (default: stories/<arc>/_casting/audio/ "
                    "with --story, else voice-samples/)")
    args = ap.parse_args()

    key = os.environ.get(args.key_env)
    if not key:
        try:
            from story_pipeline.config import load_env
            load_env()
            key = os.environ.get(args.key_env)
        except Exception:
            pass
    if not key:
        sys.exit(f"Set {args.key_env} first.")

    cfg = load_config(args.config)
    configured = voice_library(cfg)
    el = cfg.get("elevenlabs") or {}
    baseline = el.get("model") or FALLBACK_BASELINE
    models_to_compare = list(dict.fromkeys([baseline, V4]))
    voices = account_voices(key)
    by_id = {v["voice_id"]: v for v in voices}

    status, models = call("GET", "/v1/models", key)
    model_ids = {m["model_id"] for m in models} if status == 200 else set()

    print(f"\nAccount has {len(voices)} voices; repo configures {len(configured)}.\n")
    missing, ok = [], []
    for name, vid in configured.items():
        acct = by_id.get(vid)
        if acct:
            ok.append((name, vid))
            dflt = "  [Default, expires 2026-12-31]" if acct.get("category") == "premade" else ""
            print(f"  OK       {name:<18} {vid}  -> {acct['name']} [{acct.get('category')}]"
                  f"{v4_flag(acct)}{dflt}")
        else:
            missing.append((name, vid))
            print(f"  MISSING  {name:<18} {vid}")

    for name, vid in missing:
        print(f"\n  {name}: not in account.")
        local = name_matches(name, voices)
        for v in local:
            print(f"    in account under a different id: {v['voice_id']}  {v['name']}")
        if not local:
            lib = library_matches(name, key)
            for v in lib:
                print(f"    in Voice Library (add to My Voices first): "
                      f"{v['voice_id']}  {v['name']}  owner={v.get('public_owner_id')}")
            if not lib:
                print("    no match in account or Voice Library — recast.")

    unused = [v for v in voices if v["voice_id"] not in set(configured.values())]
    if unused:
        print(f"\nIn the account but not in the repo ({len(unused)}):")
        for v in unused:
            print(f"  {v['voice_id']}  {v['name']} [{v.get('category')}]")

    print("\nModels:")
    for m in models_to_compare:
        print(f"  {m:<24} {'available' if m in model_ids else 'NOT available to this key'}")

    if not args.sample:
        return

    if args.story:
        arc = casting.arc_of(args.story)
        stories_root = pathlib.Path(args.story).resolve().parent.parent
        rows, jobs = build_auditions(args, cfg, configured, voices, by_id, key, baseline)
        base_out = pathlib.Path(args.out) if args.out else casting.casting_dir(arc, stories_root) / "audio"
        out = base_out / pathlib.Path(args.story).name
        title = f"Voice auditions — {pathlib.Path(args.story).name}"
    else:
        rows, jobs = [], []
        for name, vid in ok:
            row = {"speaker": name, "text": args.text, "cells": []}
            for model in models_to_compare:
                jobs.append((row, f"{name} @ {model}", vid, model, args.text,
                             f"{slug(name)}__{model}.mp3", {}))
            rows.append(row)
        title = "Voice samples"
        out = pathlib.Path(args.out or "voice-samples")
    out.mkdir(parents=True, exist_ok=True)

    todo = [j for j in jobs if j[2] and j[3] in model_ids
            and (args.force or not (out / j[5]).exists())]
    chars = sum(len(j[4]) for j in todo)
    rate = el.get("usd_per_1k_credits")
    cost = f", roughly ${chars / 1000 * rate:.2f} at 1 credit/char" if rate else ""
    print(f"\n{len(todo)} renders to do ({len(jobs) - len(todo)} cached, unresolved or model unavailable), "
          f"{chars} chars{cost}. Check how v4 bills on your plan.")
    if todo and not args.yes:
        if input("Render? [y/N] ").strip().lower() != "y":
            return
    progress(event="plan", total=len(todo), chars=chars)
    done = 0

    for row, label, vid, model, text, fname, meta in jobs:
        cell = {"label": label}
        if vid is None:
            cell["error"] = model  # unresolved voice; the note rides in the model slot
            row["cells"].append(cell)
            continue
        dest = out / fname
        if model not in model_ids:
            cell["error"] = f"{model} not available to this key"
        elif dest.exists() and not args.force:
            cell["file"] = fname
            meta["rendered"] = True
        else:
            status, audio = call("POST", f"/v1/text-to-speech/{vid}", key,
                                 {"output_format": "mp3_44100_128"},
                                 {"text": text, "model_id": model}, raw=True)
            done += 1
            if status == 200:
                dest.write_bytes(audio)
                cell["file"] = fname
                meta["rendered"] = True
                print(f"  wrote {dest}")
            else:
                cell["error"] = f"{status} {str(audio)[:160]}"
                print(f"  FAILED {label}: {cell['error']}")
            progress(event="take", done=done, total=len(todo), ok=status == 200,
                     speaker=meta.get("speaker"), label=label)
            if args.story and status == 200:
                # After every take, not just at the end: a run that is stopped
                # or dies halfway still has what it paid for on the record.
                record_auditions(arc, pathlib.Path(args.story).name, jobs, by_id, out,
                                 stories_root)
        row["cells"].append(cell)

    write_index(out, title, rows)
    print(f"\nListen: {out / 'index.html'}")
    if args.story:
        path = record_auditions(arc, pathlib.Path(args.story).name, jobs, by_id, out,
                                stories_root)
        print(f"Recorded in {path} and {path.parent / 'CASTING.md'} — commit them.")


if __name__ == "__main__":
    main()
