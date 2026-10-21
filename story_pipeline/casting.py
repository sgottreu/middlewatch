"""Per-arc casting record: every voice auditioned, for whom, and the verdicts.

Lives at stories/<arc>/_casting/ so it travels with the arc:

    candidates.json   voices to audition, per speaker (you edit this)
    auditions.json    the record — written by check_voices.py, verdicts by this module
    CASTING.md        human-readable summary, regenerated on every write
    audio/<story>/    the rendered takes plus index.html (git-ignored; S3 via
                      `mw-s3-archive push-casting`)

`_casting` has no manifest.json, so nothing that lists story bundles sees it,
and mw-commit-stories commits its text along with the rest of stories/.

    python3 -m story_pipeline.casting show amberlight
    python3 -m story_pipeline.casting verdict amberlight "Mrs Hale" Elizabeth cast
    python3 -m story_pipeline.casting verdict amberlight "Mrs Hale" Verity no --note "too young"
    python3 -m story_pipeline.casting pool amberlight --gender female

Verdicts: cast (this voice plays this character), maybe, no. Casting a voice
here does not change the bible or config.yaml — copy it there when you're sure.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERDICTS = ("cast", "maybe", "no")
LABEL_KEYS = ("gender", "age", "accent", "descriptive", "use_case")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def casting_dir(arc: str, stories: Path = Path("stories")) -> Path:
    return stories / arc / "_casting"


def arc_of(story_dir: str | Path) -> str:
    return Path(story_dir).resolve().parent.name


def load(arc: str, stories: Path = Path("stories")) -> dict:
    p = casting_dir(arc, stories) / "auditions.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"arc": arc, "updated": None, "voices": {}, "unavailable": []}


def save(record: dict, stories: Path = Path("stories")) -> Path:
    d = casting_dir(record["arc"], stories)
    d.mkdir(parents=True, exist_ok=True)
    record["updated"] = now()
    p = d / "auditions.json"
    p.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    (d / "CASTING.md").write_text(render(record))
    return p


# --- stories, lines, candidates -------------------------------------------

def find_cast(obj):
    """The manifest's speaker -> voice-name map (the dict that has 'narrator')."""
    if isinstance(obj, dict):
        v = obj.get("voices")
        if isinstance(v, dict) and "narrator" in v:
            return v
        for x in obj.values():
            r = find_cast(x)
            if r:
                return r
    elif isinstance(obj, list):
        for x in obj:
            r = find_cast(x)
            if r:
                return r
    return None


def story_lines(story_dir) -> tuple[dict, dict]:
    """(speaker -> current voice name, speaker -> every line they speak)."""
    d = Path(story_dir)
    manifest = json.loads((d / "manifest.json").read_text())
    cast = find_cast(manifest)
    if not cast:
        raise ValueError(f"No voices map (with a narrator) in {d / 'manifest.json'}.")
    lines: dict = {}
    for f in sorted((d / "chapters").glob("*.json")):
        for seg in json.loads(f.read_text()).get("segments", []):
            t = (seg.get("text") or "").strip()
            if t:
                lines.setdefault(seg.get("speaker"), []).append(t)
    return cast, lines


def pick_line(texts, target=240, cap=400):
    """The line nearest `target` chars, trimmed at a sentence end under `cap`."""
    if not texts:
        return None
    t = min(texts, key=lambda s: abs(len(s) - target))
    if len(t) > cap:
        cut = max(t.rfind(p, 0, cap) for p in (". ", "? ", "! ", ".\u201d ", "\u201d "))
        t = t[: cut + 1].strip() if cut > 80 else t[:cap].rsplit(" ", 1)[0] + "\u2026"
    return t


def arc_stories(arc: str, stories: Path = Path("stories")) -> list[Path]:
    """Story bundles in the arc that have written chapters, newest name order."""
    return sorted(m.parent for m in (stories / arc).glob("*/manifest.json")
                  if any((m.parent / "chapters").glob("*.json")))


def arcs(stories: Path = Path("stories")) -> list[str]:
    return sorted(d.name for d in stories.iterdir()
                  if d.is_dir() and not d.name.startswith((".", "_"))
                  and any(d.glob("*/manifest.json")))


def load_candidates(arc: str, stories: Path = Path("stories")) -> dict:
    p = casting_dir(arc, stories) / "candidates.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_candidates(arc: str, data: dict, stories: Path = Path("stories")) -> None:
    d = casting_dir(arc, stories)
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidates.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _pick_id(p) -> str | None:
    return p.get("id") if isinstance(p, dict) else (p if re.fullmatch(r"[A-Za-z0-9]{20}", p) else None)


def _pick_name(p) -> str:
    return (p.get("name") or p.get("id")) if isinstance(p, dict) else p


def set_candidate(arc: str, speaker: str, voice_id: str, name: str = "", note: str = "",
                  remove: bool = False, stories: Path = Path("stories")) -> dict:
    """Add (or drop) one voice on a speaker's audition list. Ids are required:
    names alone are ambiguous in a 17,500-voice library."""
    voice_id = (voice_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{20}", voice_id):
        raise ValueError("A voice id is 20 letters and digits, e.g. AXdMgz6evoL7OPd7eU12.")
    data = load_candidates(arc, stories)
    picks = data.setdefault(speaker, [])
    picks[:] = [p for p in picks if _pick_id(p) != voice_id]
    if not remove:
        picks.append({"id": voice_id, "name": name.strip() or voice_id,
                      "note": note.strip()})
    if not picks:
        data.pop(speaker)
    save_candidates(arc, data, stories)
    return data


def labels_of(voice: dict) -> dict:
    """Gender/age/accent from an account voice (labels{}) or a library hit (top level)."""
    src = dict(voice.get("labels") or {})
    for k in LABEL_KEYS:
        if voice.get(k) and not src.get(k):
            src[k] = voice[k]
    return {k: src[k] for k in LABEL_KEYS if src.get(k)}


def note_voice(record: dict, voice_id: str, name: str, labels: dict | None = None,
               source: str | None = None) -> dict:
    v = record["voices"].setdefault(voice_id, {
        "name": name, "labels": {}, "first_tried": now(), "tries": [], "verdicts": {}})
    v["name"] = name or v["name"]
    if labels:
        v["labels"].update({k: x for k, x in labels.items() if x})
    if source:
        v.setdefault("sources", [])
        if source not in v["sources"]:
            v["sources"].append(source)
    return v


def add_try(record: dict, voice_id: str, *, story: str, speaker: str, model: str,
            line: str, file: str) -> None:
    """One take. Re-running the same take updates it rather than adding another."""
    v = record["voices"][voice_id]
    for t in v["tries"]:
        if (t["story"], t["speaker"], t["model"]) == (story, speaker, model):
            t.update(line=line, file=file, date=now())
            return
    v["tries"].append({"story": story, "speaker": speaker, "model": model,
                       "line": line, "file": file, "date": now()})


def add_unavailable(record: dict, *, speaker: str, pick: str, note: str) -> None:
    for u in record["unavailable"]:
        if (u["speaker"], u["pick"]) == (speaker, pick):
            u.update(note=note, date=now())
            return
    record["unavailable"].append({"speaker": speaker, "pick": pick, "note": note, "date": now()})


def find(record: dict, voice: str) -> tuple[str, dict]:
    if voice in record["voices"]:
        return voice, record["voices"][voice]
    q = voice.lower()
    hits = [(k, v) for k, v in record["voices"].items()
            if v["name"].lower() == q or v["name"].lower().split(" - ")[0].strip() == q]
    hits = hits or [(k, v) for k, v in record["voices"].items() if v["name"].lower().startswith(q)]
    if len(hits) != 1:
        names = ", ".join(f"{v['name']} ({k})" for k, v in hits) or "none"
        sys.exit(f"'{voice}' matches {len(hits)} auditioned voices: {names}. Use the id.")
    return hits[0]


def set_verdict(record: dict, speaker: str, voice: str, verdict: str, note: str | None) -> str:
    vid, v = find(record, voice)
    if verdict == "cast":
        # One voice per character: an earlier cast for this speaker becomes "maybe".
        for oid, o in record["voices"].items():
            prev = o["verdicts"].get(speaker)
            if oid != vid and prev and prev["verdict"] == "cast":
                prev.update(verdict="maybe", note=(prev.get("note") or "") + " (recast)",
                            date=now())
    v["verdicts"][speaker] = {"verdict": verdict, "note": note, "date": now()}
    return vid


def clear_verdict(record: dict, speaker: str, voice: str) -> str:
    vid, v = find(record, voice)
    v["verdicts"].pop(speaker, None)
    return vid


def cast_map(record: dict) -> dict[str, tuple[str, dict]]:
    out = {}
    for vid, v in record["voices"].items():
        for speaker, verdict in v["verdicts"].items():
            if verdict["verdict"] == "cast":
                out[speaker] = (vid, v)
    return out


def pool(record: dict) -> list[tuple[str, dict]]:
    """Voices auditioned in this arc that aren't cast as anyone: free for new characters."""
    cast_ids = {vid for vid, _ in cast_map(record).values()}
    return [(vid, v) for vid, v in record["voices"].items() if vid not in cast_ids]


def _labels(v: dict) -> str:
    lb = v.get("labels") or {}
    return ", ".join(str(lb[k]).replace("_", " ") for k in ("gender", "age", "accent", "descriptive")
                     if lb.get(k)) or "—"


def _speakers(record: dict) -> list[str]:
    seen = []
    for v in record["voices"].values():
        for t in v["tries"]:
            if t["speaker"] not in seen:
                seen.append(t["speaker"])
    return seen


def render(record: dict) -> str:
    arc = record["arc"]
    out = [f"# Casting — {arc}", "",
           "Generated from `auditions.json`; edit verdicts with "
           f"`python3 -m story_pipeline.casting verdict {arc} \"<speaker>\" <voice> cast|maybe|no`, "
           "not by hand.", "", f"Updated {record.get('updated') or now()}.", ""]

    cast = cast_map(record)
    out += ["## Cast", ""]
    if cast:
        out += ["| Character | Voice | ID |", "|---|---|---|"]
        out += [f"| {s} | {v['name']} | `{vid}` |" for s, (vid, v) in sorted(cast.items())]
    else:
        out.append("Nobody cast yet.")
    out.append("")

    out += ["## Auditions by character", ""]
    for speaker in _speakers(record):
        out += [f"### {speaker}", "", "| Voice | ID | Verdict | Note | Heard in |", "|---|---|---|---|---|"]
        for vid, v in record["voices"].items():
            tries = [t for t in v["tries"] if t["speaker"] == speaker]
            if not tries:
                continue
            vd = v["verdicts"].get(speaker) or {}
            heard = ", ".join(sorted({f"{t['story']} ({t['model']})" for t in tries}))
            out.append(f"| {v['name']} | `{vid}` | {vd.get('verdict', '—')} | "
                       f"{(vd.get('note') or '').replace('|', '/')} | {heard} |")
        out.append("")

    out += ["## Voice pool", "",
            "Auditioned in this arc and not cast as anyone — the place to start for a new character.", ""]
    free = pool(record)
    if free:
        out += ["| Voice | ID | Gender, age, accent | Tried for |", "|---|---|---|---|"]
        for vid, v in sorted(free, key=lambda x: x[1]["name"].lower()):
            tried = sorted({t["speaker"] for t in v["tries"]})
            marks = [f"{s} ({v['verdicts'][s]['verdict']})" if s in v["verdicts"] else s for s in tried]
            out.append(f"| {v['name']} | `{vid}` | {_labels(v)} | {', '.join(marks)} |")
    else:
        out.append("Empty.")
    out.append("")

    if record.get("unavailable"):
        out += ["## Couldn't audition", "", "| Character | Pick | Why |", "|---|---|---|"]
        out += [f"| {u['speaker']} | {u['pick']} | {u['note'].replace('|', '/')} |"
                for u in record["unavailable"]]
        out.append("")
    return "\n".join(out)


def arc_view(arc: str, story: str | None = None, stories: Path = Path("stories")) -> dict:
    """Everything the casting page shows for one arc, read off disk. Free.

    Speakers come from the chosen story (whose lines are auditioned) plus anyone
    on the candidate list or in the record, so nobody auditioned in another
    story drops off the page.
    """
    record = load(arc, stories)
    cands = load_candidates(arc, stories)
    story_dirs = arc_stories(arc, stories)
    names = [d.name for d in story_dirs]
    story = story if story in names else (names[0] if names else None)

    current, lines = {}, {}
    if story:
        current, lines = story_lines(stories / arc / story)
    speakers_in = {}
    for d in story_dirs:
        try:
            _, ls = story_lines(d)
        except ValueError:
            continue
        for sp in ls:
            if sp:
                speakers_in.setdefault(sp, []).append(d.name)

    order = list(current) + [s for s in cands if not s.startswith("_") and s not in current]
    order += [s for s in _speakers(record) if s not in order]
    cast = cast_map(record)

    def takes(vid, speaker):
        v = record["voices"].get(vid) or {}
        return [{"story": t["story"], "model": t["model"], "line": t["line"],
                 "url": f"/casting-audio/{arc}/{t['file']}", "date": t["date"]}
                for t in v.get("tries", []) if t["speaker"] == speaker]

    out_speakers = []
    for sp in order:
        line = pick_line(lines.get(sp, []))
        entries, seen = [], set()
        cur_name = current.get(sp)
        cur = [(vid, v) for vid, v in record["voices"].items()
               if cur_name and (v["name"] == cur_name or v["name"].split(" - ")[0] == cur_name)
               and any(t["speaker"] == sp for t in v["tries"])]
        for vid, v in cur:
            seen.add(vid)
            entries.append(_entry(vid, v, sp, takes(vid, sp), role="current"))
        for p in cands.get(sp, []) if not sp.startswith("_") else []:
            vid = _pick_id(p)
            if vid and vid in seen:
                continue
            v = record["voices"].get(vid) if vid else None
            if v is None and not vid:
                hit = [(k, x) for k, x in record["voices"].items()
                       if x["name"].lower().split(" - ")[0] == _pick_name(p).lower()]
                if hit:
                    vid, v = hit[0]
            e = _entry(vid, v, sp, takes(vid, sp) if v else [], role="candidate",
                       name=_pick_name(p), note=p.get("note", "") if isinstance(p, dict) else "")
            e["unavailable"] = next((u["note"] for u in record.get("unavailable", [])
                                     if u["speaker"] == sp and (vid and vid in u["pick"]
                                     or _pick_name(p).lower() in u["pick"].lower())), None)
            entries.append(e)
            if vid:
                seen.add(vid)
        # Anything auditioned or judged for this speaker that is no longer listed.
        for vid, v in record["voices"].items():
            if vid in seen:
                continue
            if any(t["speaker"] == sp for t in v["tries"]) or sp in v["verdicts"]:
                entries.append(_entry(vid, v, sp, takes(vid, sp), role="other"))
                seen.add(vid)
        out_speakers.append({
            "speaker": sp, "line": line, "current": cur_name,
            "in_stories": speakers_in.get(sp, []),
            "cast": {"id": cast[sp][0], "name": cast[sp][1]["name"]} if sp in cast else None,
            "entries": entries,
        })

    return {
        "arc": arc, "story": story, "stories": names, "speakers": out_speakers,
        "pool": [{"id": vid, "name": v["name"], "labels": v.get("labels", {}),
                  "tried_for": sorted({t["speaker"] for t in v["tries"]}),
                  "verdicts": {s: x["verdict"] for s, x in v["verdicts"].items()},
                  "sample": next((f"/casting-audio/{arc}/{t['file']}" for t in v["tries"]), None)}
                 for vid, v in sorted(pool(record), key=lambda x: x[1]["name"].lower())],
        "updated": record.get("updated"),
    }


def _entry(vid, v, speaker, takes, role, name=None, note=""):
    v = v or {}
    verdict = (v.get("verdicts") or {}).get(speaker) or {}
    return {"id": vid, "name": v.get("name") or name or vid, "labels": v.get("labels") or {},
            "role": role, "note": note, "takes": takes,
            "verdict": verdict.get("verdict"), "verdict_note": verdict.get("note") or "",
            "auditioned": bool(takes)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python3 -m story_pipeline.casting",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--stories", default="stories")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="regenerate CASTING.md and print it")
    s.add_argument("arc")

    v = sub.add_parser("verdict", help="record cast / maybe / no for a voice and character")
    v.add_argument("arc")
    v.add_argument("speaker")
    v.add_argument("voice", help="voice name or id, as auditioned")
    v.add_argument("verdict", choices=VERDICTS)
    v.add_argument("--note")

    p = sub.add_parser("pool", help="voices auditioned in the arc and not cast")
    p.add_argument("arc")
    p.add_argument("--gender")
    p.add_argument("--accent")

    args = ap.parse_args(argv)
    stories = Path(args.stories)
    record = load(args.arc, stories)
    if not record["voices"] and args.cmd != "show":
        sys.exit(f"No auditions recorded for {args.arc} yet — run scripts/audition-cast.sh first.")

    if args.cmd == "show":
        save(record, stories)
        print(render(record))
    elif args.cmd == "verdict":
        vid = set_verdict(record, args.speaker, args.voice, args.verdict, args.note)
        if not any(t["speaker"] == args.speaker for t in record["voices"][vid]["tries"]):
            heard = ", ".join(sorted({t["speaker"] for t in record["voices"][vid]["tries"]}))
            print(f"Note: {record['voices'][vid]['name']} hasn't been auditioned as "
                  f"{args.speaker} (heard as: {heard}). Recorded anyway.")
        path = save(record, stories)
        print(f"{args.speaker}: {record['voices'][vid]['name']} ({vid}) -> {args.verdict}. "
              f"Updated {path.parent / 'CASTING.md'}.")
        if args.verdict == "cast":
            print("Not applied to the bible or config.yaml — copy it there when you're sure.")
    elif args.cmd == "pool":
        for vid, v in sorted(pool(record), key=lambda x: x[1]["name"].lower()):
            lb = v.get("labels") or {}
            if args.gender and (lb.get("gender") or "").lower() != args.gender.lower():
                continue
            if args.accent and args.accent.lower() not in (lb.get("accent") or "").lower():
                continue
            tried = ", ".join(sorted({t["speaker"] for t in v["tries"]}))
            print(f"{vid}  {v['name']:<36} {_labels(v):<40} tried for: {tried}")


if __name__ == "__main__":
    main()
