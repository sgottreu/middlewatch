#!/usr/bin/env python3
"""Put your own ElevenLabs voice ids into config.yaml.

    python3 scripts/sync-voices.py              # update config.yaml, keep a .bak
    python3 scripts/sync-voices.py --dry-run    # show the diff, write nothing
    python3 scripts/sync-voices.py --all        # also add every other account voice
    python3 scripts/sync-voices.py --from-json voices.json    # no API call

Run from the repo root, in the venv, like everything else:

    source .venv/bin/activate
    python3 scripts/sync-voices.py

`elevenlabs.voice_library` ships the public library ids, which only work for an
account that has added those voices. On any other key the first `record` dies
with `404 voice_not_found` — and `cli voices` tells you the right ids but leaves
you to copy seven of them across by hand, which is the kind of transcription
nobody does twice without a typo.

WHICH NAMES MATTER. Not whatever is in the library today: the names the pipeline
can actually ask for. That is the narrator and character pools from config, the
`genre_voices` each genre file pins, and — because those are pinned per story at
creation and outlive a config change — the ones already recorded in existing
manifests and outline cast entries. All four are gathered here, so a name a
story will reach for cannot be quietly left unmapped.

NAMES ARE MATCHED LOOSELY. The library voices arrive from ElevenLabs named
`Alice - Clear, Engaging Educator`, not `Alice`, so an exact match finds nothing
and reports an account full of the right voices as empty. Everything before the
dash is the name; the rest is marketing.

WHEN A NAME IS GENUINELY ABSENT, `--map` is the way out, because
`voice_library` maps a *name the pipeline uses* to an id — the name is a label,
not a claim about who ElevenLabs think the voice is:

    python3 scripts/sync-voices.py --map Charlotte=Sarah
    python3 scripts/sync-voices.py --set Charlotte=wScwPA1qCkWo5R2dmlS8

That points the narrator at Sarah's id and leaves every story's pinned casting
working. The alternative is adding a voice called Charlotte to your account in
the dashboard, which is better only if you want the label to stay honest.

The file is edited line by line rather than loaded and dumped: config.yaml is
commented throughout, and a round trip through PyYAML would throw every comment
away to change seven values.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from story_pipeline import config as cfgmod  # noqa: E402


def wanted_names(cfg: dict, root: Path) -> dict[str, list[str]]:
    """Every voice name the pipeline can ask for, and where each came from."""
    want: dict[str, list[str]] = {}

    def add(name, source):
        if isinstance(name, str) and name.strip():
            want.setdefault(name, [])
            if source not in want[name]:
                want[name].append(source)

    def pools(casting: dict, source: str):
        add(casting.get("narrator"), source)
        pool = casting.get("character_voices") or {}
        groups = pool.values() if isinstance(pool, dict) else [pool]
        for group in groups:
            for v in group or []:
                add(v, source)

    pools(cfg.get("casting", {}), "config.yaml")

    # Genre files pin their own casting in YAML frontmatter.
    for g in sorted((root / "story_pipeline" / "prompts" / "genres").glob("*.md")):
        text = g.read_text()
        if not text.startswith("---"):
            continue
        front = text.split("---", 2)[1]
        try:
            import yaml
            meta = yaml.safe_load(front) or {}
        except Exception:
            continue
        voices = meta.get("genre_voices") or meta.get("casting") or {}
        if voices:
            pools(voices, f"genres/{g.stem}")

    # Stories pin casting at creation, so an old story can still want a name the
    # config no longer mentions.
    for man in sorted((root / Path(cfg.get("stories_dir", "stories"))).glob("*/manifest.json")):
        try:
            m = json.loads(man.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        pinned = m.get("pinned", {})
        pools(pinned.get("genre_voices") or {}, man.parent.name)
        for v in (pinned.get("voices") or {}).values():
            add(v, man.parent.name)
        outline = man.parent / "outline.json"
        if outline.exists():
            try:
                for c in json.loads(outline.read_text()).get("cast", []):
                    add(c.get("voice"), man.parent.name)
            except (OSError, json.JSONDecodeError):
                pass
    return want


def bare(name: str) -> str:
    """`Alice - Clear, Engaging Educator` -> `alice`.

    ElevenLabs ships library voices with a descriptive tail, and a user can
    rename a voice to anything. The leading word is the part everybody, including
    `config.yaml`, actually calls it.
    """
    head = re.split(r"\s+[-\u2013\u2014]\s+", name.strip(), maxsplit=1)[0]
    return head.strip().casefold()


def resolve(name: str, have: dict[str, str]) -> tuple[str | None, str | None, list[str]]:
    """Find one account voice for `name`: (id, matched display name, ambiguities)."""
    if name in have:
        return have[name], name, []
    hits = [disp for disp in have if bare(disp) == bare(name)]
    if len(hits) == 1:
        return have[hits[0]], hits[0], []
    if len(hits) > 1:
        return None, None, hits
    return None, None, []


def account_voices(cfg: dict, from_json: Path | None) -> dict[str, str]:
    """name -> voice_id, from the account (or a saved listing)."""
    if from_json:
        raw = json.loads(from_json.read_text())
        rows = raw.get("voices", raw) if isinstance(raw, dict) else raw
    else:
        from story_pipeline import tts

        cfgmod.load_env()
        provider = tts.build(cfg)
        if not hasattr(provider, "list_voices"):
            sys.exit(f"{provider.name} cannot list voices; nothing to sync")
        rows = provider.list_voices()

    out: dict[str, str] = {}
    for v in rows:
        name = (v.get("name") or "").strip()
        if not name:
            continue
        if name in out:
            print(f"  ! two account voices are called {name!r}; keeping {out[name]}")
            continue
        out[name] = v["voice_id"]
    return out


LIB_KEY = re.compile(r"^(\s*)voice_library:\s*(#.*)?$")
ENTRY = re.compile(r"^(\s*)([A-Za-z][\w .'-]*)\s*:\s*(\S+)\s*(#.*)?$")


def patch(text: str, ids: dict[str, str], extra: dict[str, str]) -> tuple[str, list]:
    """Rewrite the ids inside voice_library, leaving every comment alone."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if LIB_KEY.match(l)), None)
    if start is None:
        sys.exit("no voice_library: block in config.yaml — add one and rerun")
    indent = len(LIB_KEY.match(lines[start]).group(1))

    # `last` rather than "the first line that ends the block": a blank line
    # inside the block would otherwise put the new entries after it, against
    # whatever key comes next. It parses, and it reads like a mistake.
    changes, seen, last = [], set(), start
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        m = ENTRY.match(line)
        if not m:
            continue
        pad, name, old, comment = m.groups()
        seen.add(name)
        last = i
        new = ids.get(name)
        if new is None:
            changes.append((name, old, None, "not in your account"))
        elif new == old:
            changes.append((name, old, new, "already correct"))
        else:
            lines[i] = f"{pad}{name}: {new}" + (f"  {comment}" if comment else "") + "\n"
            changes.append((name, old, new, "updated"))

    add = {n: v for n, v in {**ids, **extra}.items() if n not in seen}
    if add:
        pad = " " * (indent + 2)
        block = "".join(f"{pad}{n}: {v}\n" for n, v in sorted(add.items()))
        lines[last + 1:last + 1] = [block]
        changes += [(n, None, v, "added") for n, v in sorted(add.items())]
    return "".join(lines), changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the changes, write nothing")
    ap.add_argument("--all", action="store_true",
                    help="also add every other voice in the account, so the pools "
                         "have something to widen into")
    ap.add_argument("--from-json", type=Path, metavar="FILE",
                    help="read a saved /v1/voices listing instead of calling the API")
    ap.add_argument("--map", action="append", default=[], metavar="NAME=VOICE",
                    help="point a name at a different account voice, e.g. "
                         "--map Charlotte=Sarah. Repeatable.")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=ID",
                    help="write an id you already have, e.g. "
                         "--set Charlotte=wScwPA1qCkWo5R2dmlS8. Wins over anything "
                         "matched from the account. Repeatable.")
    ap.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    if not args.config.exists():
        sys.exit(f"{args.config} not found — run this from the repo root")

    cfg = cfgmod.load(args.config)
    want = wanted_names(cfg, root)
    have = account_voices(cfg, args.from_json)
    print(f"{len(have)} voices in the account, {len(want)} names the pipeline can ask for\n")

    aliases = {}
    for pair in args.map:
        if "=" not in pair:
            sys.exit(f"--map wants NAME=VOICE, got {pair!r}")
        k, v = (x.strip() for x in pair.split("=", 1))
        aliases[k] = v

    literal = {}
    for pair in args.set:
        if "=" not in pair:
            sys.exit(f"--set wants NAME=ID, got {pair!r}")
        k, v = (x.strip() for x in pair.split("=", 1))
        literal[k] = v

    ids, matched, ambiguous = {}, {}, {}
    for name in want:
        target = aliases.get(name, name)
        vid, disp, hits = resolve(target, have)
        if hits:
            ambiguous[name] = hits
        elif vid:
            ids[name] = vid
            if disp != name:
                matched[name] = disp
    for name, target in aliases.items():
        if name not in want:                 # mapping something config never asks for
            vid, disp, hits = resolve(target, have)
            if vid:
                ids[name] = vid
                matched[name] = disp

    # An id given by hand outranks anything matched by name: you are holding the
    # dashboard open and the script is guessing from a string.
    for name, vid in literal.items():
        ids[name] = vid
        matched[name] = next((d for d, v in have.items() if v == vid), "given by hand")
        ambiguous.pop(name, None)

    extra = {n: v for n, v in have.items() if bare(n) not in {bare(x) for x in ids}} \
        if args.all else {}

    text = args.config.read_text()
    new_text, changes = patch(text, ids, extra)

    width = max((len(c[0]) for c in changes), default=10)
    for name, old, new, what in changes:
        mark = {"updated": "~", "added": "+", "already correct": " ",
                "not in your account": "!"}[what]
        detail = f"{old} -> {new}" if what == "updated" else (new or old or "")
        via = f'   as "{matched[name]}"' if name in matched else ""
        print(f" {mark} {name:<{width}}  {detail}    {what}{via}")

    for name, hits in ambiguous.items():
        print(f"\n ? {name}: {len(hits)} account voices answer to that name — "
              f"{', '.join(hits)}.\n   Pick one with --map {name}=<the full name>.")

    missing = [c[0] for c in changes if c[3] == "not in your account"]
    if missing:
        print("\nStill unmapped, and every one of them is a name a story can ask for:")
        for n in missing:
            print(f"  {n:<{width}}  wanted by: {', '.join(want.get(n, ['config']))}")
        spare = sorted(n for n in have if bare(n) not in {bare(x) for x in ids})
        print("\nIn the account and unused"
              + (f": {', '.join(spare)}" if spare else ": nothing"))
        print(f"\nEither add a voice under that name in the ElevenLabs dashboard, or "
              f"point\nthe name at one you have — the name is only a label:\n"
              f"  python3 scripts/sync-voices.py --map {missing[0]}=<account voice>")

    if new_text == text:
        print("\nconfig.yaml already matches your account — nothing written.")
        return 1 if missing else 0
    if args.dry_run:
        print("\nDry run. config.yaml not written.")
        return 0

    backup = args.config.with_suffix(args.config.suffix + ".bak")
    shutil.copy2(args.config, backup)
    args.config.write_text(new_text)
    print(f"\nWrote {args.config} (previous version kept as {backup.name})")
    print("Check it with:  python3 -m story_pipeline.cli record <story> --dry-run")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
