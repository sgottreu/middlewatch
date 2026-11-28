#!/usr/bin/env python3
"""Write docs/story/voices.md: every voice on the ElevenLabs account, with
ElevenLabs' description and labels, the voices.yaml name, and who it's cast as.

    source .venv/bin/activate
    python3 scripts/voices-doc.py                     # ask ElevenLabs (free), write the doc
    python3 scripts/voices-doc.py --save-json voices-account.json
    python3 scripts/voices-doc.py --from-json voices-account.json   # no API call

Run from the repo root. The logic is in story_pipeline/voice_catalog.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from story_pipeline import config as cfgmod  # noqa: E402
from story_pipeline import voice_catalog as vc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-json", type=Path, help="use a saved /v1/voices listing instead of the API")
    ap.add_argument("--save-json", type=Path, help="also save the raw listing here")
    ap.add_argument("--out", type=Path, default=Path("docs/story/voices.md"))
    args = ap.parse_args()

    cfg = cfgmod.load()
    library = (cfg.get("elevenlabs") or {}).get("voice_library") or {}
    if args.from_json:
        rows = vc.load_rows(args.from_json)
    else:
        from story_pipeline import tts

        cfgmod.load_env()
        provider = tts.build(cfg)
        if not hasattr(provider, "list_voices"):
            sys.exit(f"{provider.name} cannot list voices")
        rows = provider.list_voices()
    if args.save_json:
        args.save_json.write_text(json.dumps({"voices": rows}, indent=2, ensure_ascii=False) + "\n")

    es = vc.entries(rows, library)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(vc.render(es, vc.cast_in(), library, date.today().isoformat()))
    print(f"Wrote {args.out}: {len(es)} voices.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
