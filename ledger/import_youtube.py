"""Import a YouTube Analytics CSV export into revenue.csv.

There is no free API for revenue figures, so this step is manual and always
will be. In YouTube Studio: Analytics, Advanced mode, set the date range to a
single month, pick the Video dimension, add the Revenue columns, export as CSV.

You also need `ledger/videos.csv` mapping story slugs to video IDs. Fill it in
when you publish. Two columns:

    story_slug,video_id
    amberlight-01-the-curate,dQw4w9WgXcQ

Usage:
    python3 -m ledger.import_youtube export.csv --period 2026-09
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .ledger import LEDGER_DIR, upsert_revenue

VIDEOS_CSV = LEDGER_DIR / "videos.csv"

# YouTube renames these columns periodically. Add aliases rather than editing.
ALIASES = {
    "video_id": ["Video", "Content", "Video ID"],
    "views": ["Views"],
    "watch_hours": ["Watch time (hours)", "Watch time (hours) ", "Watch time"],
    "revenue_usd": [
        "Your estimated revenue (USD)",
        "Estimated revenue (USD)",
        "Your estimated revenue",
    ],
}


def _find(header: list[str], names: list[str]) -> str | None:
    lookup = {h.strip().lower(): h for h in header}
    for n in names:
        if n.strip().lower() in lookup:
            return lookup[n.strip().lower()]
    return None


def load_video_map(path: Path = VIDEOS_CSV) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="") as fh:
        return {r["video_id"].strip(): r["story_slug"].strip()
                for r in csv.DictReader(fh) if r.get("video_id")}


def parse_export(csv_path: Path, period: str) -> tuple[list[dict], list[str]]:
    vid_map = load_video_map()
    rows, unmapped = [], []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        cols = {k: _find(header, v) for k, v in ALIASES.items()}
        if not cols["video_id"]:
            raise SystemExit(
                f"No video ID column found. Header was: {header}\n"
                "Export with the Video dimension, not Geography or Traffic source."
            )
        for r in reader:
            vid = (r.get(cols["video_id"]) or "").strip()
            # YouTube puts a "Total" summary row in every export.
            if not vid or vid.lower() in {"total", "totals"}:
                continue
            slug = vid_map.get(vid)
            if not slug:
                unmapped.append(vid)
                continue

            def num(key):
                c = cols.get(key)
                if not c:
                    return ""
                raw = (r.get(c) or "").replace(",", "").replace("$", "").strip()
                return raw or ""

            rows.append({
                "story_slug": slug,
                "platform": "youtube",
                "period": period,
                "video_id": vid,
                "views": num("views"),
                "watch_hours": num("watch_hours"),
                "revenue_usd": num("revenue_usd"),
                "source": csv_path.name,
            })
    return rows, unmapped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", type=Path)
    ap.add_argument("--period", required=True, help="YYYY-MM")
    args = ap.parse_args()

    rows, unmapped = parse_export(args.csv_path, args.period)
    updated, inserted = upsert_revenue(rows)
    print(f"{inserted} inserted, {updated} updated for {args.period}")
    if unmapped:
        print(f"\n{len(unmapped)} video(s) not in videos.csv, skipped:")
        for v in sorted(set(unmapped)):
            print(f"  {v}")
        print(f"\nAdd them to {VIDEOS_CSV} and re-run. Re-running is safe.")


if __name__ == "__main__":
    main()
