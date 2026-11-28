"""Every voice on the ElevenLabs account, written up as docs/story/voices.md.

    python3 scripts/voices-doc.py                    # ask ElevenLabs, write the doc
    python3 scripts/voices-doc.py --save-json v.json # also keep the raw listing
    python3 scripts/voices-doc.py --from-json v.json # no API call

Each voice gets ElevenLabs' own description and labels (gender, age, accent,
character, use case), its id, its category, the names voices.yaml knows it by,
and who it has been cast as in any arc's _casting/auditions.json. Free: listing
voices costs no credits. Stdlib only, apart from the API call in tts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

GENDER_ORDER = {"female": 0, "male": 1}


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _words(s) -> str:
    return _clean(str(s or "").replace("_", " "))


def load_rows(path: Path) -> list[dict]:
    raw = json.loads(Path(path).read_text())
    return raw.get("voices", raw) if isinstance(raw, dict) else raw


def entries(rows: list[dict], library: dict) -> list[dict]:
    """One tidy record per account voice, female first, then by name."""
    by_id: dict[str, list[str]] = {}
    for name, vid in library.items():
        by_id.setdefault(vid, []).append(name)
    out = []
    for v in rows:
        lb = v.get("labels") or {}
        name = _clean(v.get("name")) or v.get("voice_id", "")
        out.append({
            "id": v.get("voice_id", ""),
            "name": name,
            "short": name.split(" - ")[0].strip(),
            "category": v.get("category") or "",
            "gender": _words(lb.get("gender")),
            "age": _words(lb.get("age")),
            "traits": " · ".join(_words(lb.get(k)) for k in ("gender", "age", "accent", "descriptive")
                                 if lb.get(k)),
            "use_case": _words(lb.get("use_case")),
            "description": _clean(v.get("description")),
            "preview": v.get("preview_url") or "",
            "pipeline_names": sorted(by_id.get(v.get("voice_id", ""), [])),
        })
    out.sort(key=lambda e: (GENDER_ORDER.get(e["gender"], 2), e["short"].lower()))
    return out


def cast_in(stories: Path = Path("stories")) -> dict[str, list[str]]:
    """voice id -> ["Mrs Hale (amberlight)", ...] from every arc's casting record."""
    out: dict[str, list[str]] = {}
    for p in sorted(Path(stories).glob("*/_casting/auditions.json")):
        arc = p.parent.parent.name
        for vid, v in (json.loads(p.read_text()).get("voices") or {}).items():
            for speaker, ver in (v.get("verdicts") or {}).items():
                if (ver or {}).get("verdict") == "cast":
                    out.setdefault(vid, []).append(f"{speaker} ({arc})")
    return out


def _cell(s: str) -> str:
    return (s or "—").replace("|", "\\|")


def render(es: list[dict], cast: dict, library: dict, generated: str) -> str:
    out = [
        "# Voices on the ElevenLabs account", "",
        f"{len(es)} voices, listed {generated} by `python3 scripts/voices-doc.py`. "
        "Rerun it after adding or removing voices; don't edit by hand.", "",
        "Descriptions and labels are ElevenLabs' own. **In voices.yaml** is the name the "
        "pipeline uses for the voice (blank means it isn't in `voice_library` yet). "
        "**Cast as** comes from each arc's `_casting/auditions.json`.", "",
        "| Voice | Gender, age, accent, character | Use case | Category | In voices.yaml | Cast as |",
        "|---|---|---|---|---|---|",
    ]
    for e in es:
        out.append(f"| {_cell(e['short'])} | {_cell(e['traits'])} | {_cell(e['use_case'])} | "
                   f"{_cell(e['category'])} | {_cell(', '.join(e['pipeline_names']))} | "
                   f"{_cell(', '.join(cast.get(e['id'], [])))} |")
    out += ["", "## Each voice", ""]
    for e in es:
        out += [f"### {e['short']}", ""]
        if e["name"] != e["short"]:
            out += [f"*{e['name']}*", ""]
        out += [e["description"] or "No description on ElevenLabs.", ""]
        facts = [f"ID: `{e['id']}`"]
        if e["traits"]:
            facts.append(e["traits"])
        if e["use_case"]:
            facts.append(f"Use case: {e['use_case']}")
        if e["category"]:
            facts.append(f"Category: {e['category']}")
        if e["pipeline_names"]:
            facts.append("voices.yaml: " + ", ".join(e["pipeline_names"]))
        if cast.get(e["id"]):
            facts.append("Cast as: " + ", ".join(cast[e["id"]]))
        if e["preview"]:
            facts.append(f"[preview]({e['preview']})")
        out += ["- " + f for f in facts] + [""]
    on_account = {e["id"] for e in es}
    missing = sorted(n for n, vid in library.items() if vid not in on_account)
    out += ["## Not on the account", ""]
    if missing:
        out += ["Names in `voices.yaml` whose id isn't on the account. Recording with them "
                "fails with `voice_not_found`:", ""]
        out += [f"- {n} (`{library[n]}`)" for n in missing]
    else:
        out.append("None: every name in `voices.yaml` is on the account.")
    out.append("")
    return "\n".join(out)
