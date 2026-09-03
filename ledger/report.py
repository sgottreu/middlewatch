"""Join costs and revenue. Print per-story break-even and rollups.

    python3 -m ledger.report
    python3 -m ledger.report --by genre
    python3 -m ledger.report --by flavor --csv summary.csv
    python3 -m ledger.report --stages
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from .ledger import COSTS_CSV, REVENUE_CSV, RATES_JSON, load_rates

RATE_STALE_DAYS = 90


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def read_costs(path: Path = COSTS_CSV) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def read_revenue(path: Path = REVENUE_CSV) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def build(costs: list[dict], revenue: list[dict]) -> dict[str, dict]:
    stories: dict[str, dict] = {}
    for c in costs:
        s = stories.setdefault(c["story_slug"], {
            "slug": c["story_slug"], "title": "", "series": "", "genre": "",
            "flavor": "", "arc_slug": "", "episode": "", "pipeline": "",
            "cost": 0.0, "revenue": 0.0, "views": 0.0, "watch_hours": 0.0,
            "by_stage": defaultdict(float), "by_provider": defaultdict(float),
            "first_cost": c["timestamp"], "months": 0,
        })
        # Later rows win for labels, so a corrected manifest propagates.
        for k in ("title", "series", "genre", "flavor", "arc_slug", "episode",
                  "pipeline"):
            key = "story_title" if k == "title" else k
            if c.get(key):
                s[k] = c[key]
        amt = _f(c["cost_usd"])
        s["cost"] += amt
        s["by_stage"][c["stage"]] += amt
        s["by_provider"][f'{c["provider"]}/{c["model"]}'] += amt
        s["first_cost"] = min(s["first_cost"], c["timestamp"])

    periods: dict[str, set] = defaultdict(set)
    for r in revenue:
        s = stories.get(r["story_slug"])
        if s is None:
            continue
        s["revenue"] += _f(r["revenue_usd"])
        s["views"] += _f(r["views"])
        s["watch_hours"] += _f(r["watch_hours"])
        periods[r["story_slug"]].add(r["period"])
    for slug, p in periods.items():
        stories[slug]["months"] = len(p)
    return stories


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def print_stories(stories: dict[str, dict]) -> None:
    if not stories:
        print("No cost events recorded yet.")
        return
    rows = sorted(stories.values(), key=lambda s: (s["series"], s["episode"], s["slug"]))
    hdr = f'{"story":<28} {"genre":<10} {"flavor":<18} {"cost":>8} {"rev":>9} {"net":>9} {"views":>8}  status'
    print(hdr)
    print("-" * len(hdr))
    for s in rows:
        net = s["revenue"] - s["cost"]
        if s["revenue"] == 0:
            status = "unpublished or pre-monetization"
        elif net >= 0:
            months = s["months"]
            status = "paid back" if months == 0 else f"paid back ({months}mo)"
        else:
            pct = 100 * s["revenue"] / s["cost"] if s["cost"] else 0
            status = f"{pct:.0f}% recovered"
        name = s["title"] or s["slug"]
        print(f'{name[:28]:<28} {s["genre"][:10]:<10} {s["flavor"][:18]:<18} '
              f'{_fmt_money(s["cost"]):>8} {_fmt_money(s["revenue"]):>9} '
              f'{_fmt_money(net):>9} {s["views"]:>8,.0f}  {status}')

    tc = sum(s["cost"] for s in rows)
    tr = sum(s["revenue"] for s in rows)
    print("-" * len(hdr))
    print(f'{"TOTAL":<28} {"":<10} {"":<18} {_fmt_money(tc):>8} '
          f'{_fmt_money(tr):>9} {_fmt_money(tr - tc):>9}')
    paid = sum(1 for s in rows if s["revenue"] >= s["cost"] and s["cost"] > 0)
    print(f"\n{paid} of {len(rows)} stories have paid back their API cost.")
    if tr > 0:
        breakeven_views = tc / (tr / sum(s["views"] for s in rows)) if sum(s["views"] for s in rows) else 0
        rpm = 1000 * tr / sum(s["views"] for s in rows) if sum(s["views"] for s in rows) else 0
        print(f"Blended RPM {_fmt_money(rpm)} per 1,000 views. "
              f"At that rate a story pays for itself at "
              f"{1000 * (tc / len(rows)) / rpm:,.0f} views." if rpm else "")


def print_group(stories: dict[str, dict], key: str) -> None:
    g: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "revenue": 0.0,
                                              "views": 0.0, "n": 0})
    for s in stories.values():
        b = g[s.get(key) or "(unset)"]
        b["cost"] += s["cost"]
        b["revenue"] += s["revenue"]
        b["views"] += s["views"]
        b["n"] += 1
    hdr = f'{key:<20} {"n":>3} {"cost":>9} {"rev":>9} {"net":>9} {"rev/story":>10} {"RPM":>8}'
    print(hdr)
    print("-" * len(hdr))
    for name, b in sorted(g.items(), key=lambda kv: -(kv[1]["revenue"] - kv[1]["cost"])):
        rpm = 1000 * b["revenue"] / b["views"] if b["views"] else 0
        print(f'{name[:20]:<20} {b["n"]:>3} {_fmt_money(b["cost"]):>9} '
              f'{_fmt_money(b["revenue"]):>9} {_fmt_money(b["revenue"] - b["cost"]):>9} '
              f'{_fmt_money(b["revenue"] / b["n"]):>10} {_fmt_money(rpm):>8}')
    print("\nSmall n. Treat differences under a dozen stories per bucket as noise.")


def print_stages(stories: dict[str, dict]) -> None:
    stage: dict[str, float] = defaultdict(float)
    prov: dict[str, float] = defaultdict(float)
    for s in stories.values():
        for k, v in s["by_stage"].items():
            stage[k] += v
        for k, v in s["by_provider"].items():
            prov[k] += v
    total = sum(stage.values()) or 1
    print("Cost by stage")
    for k, v in sorted(stage.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<16} {_fmt_money(v):>9}  {100*v/total:5.1f}%")
    print("\nCost by provider and model")
    for k, v in sorted(prov.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<28} {_fmt_money(v):>9}  {100*v/total:5.1f}%")
    n = len(stories) or 1
    print(f"\nMean cost per story: {_fmt_money(total / n)} across {n} stories.")


def write_csv(stories: dict[str, dict], path: Path) -> None:
    fields = ["slug", "title", "series", "genre", "flavor", "arc_slug", "episode",
              "cost_usd", "revenue_usd", "net_usd", "views", "watch_hours",
              "months_reported", "recovered_pct"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for s in sorted(stories.values(), key=lambda x: x["slug"]):
            w.writerow({
                "slug": s["slug"], "title": s["title"], "series": s["series"],
                "genre": s["genre"], "flavor": s["flavor"],
                "arc_slug": s["arc_slug"], "episode": s["episode"],
                "cost_usd": round(s["cost"], 4),
                "revenue_usd": round(s["revenue"], 2),
                "net_usd": round(s["revenue"] - s["cost"], 2),
                "views": int(s["views"]), "watch_hours": round(s["watch_hours"], 1),
                "months_reported": s["months"],
                "recovered_pct": round(100 * s["revenue"] / s["cost"], 1) if s["cost"] else "",
            })
    print(f"Wrote {path}")


def check_rates_age() -> None:
    try:
        verified = load_rates()["rates_verified"]
        age = (date.today() - datetime.fromisoformat(verified).date()).days
        if age > RATE_STALE_DAYS:
            print(f"! Rate card last verified {verified} ({age} days ago). "
                  f"Check {RATES_JSON.name} against the pricing pages.\n")
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--by", choices=["pipeline", "genre", "flavor", "series",
                                     "arc_slug"])
    ap.add_argument("--stages", action="store_true")
    ap.add_argument("--csv", type=Path)
    args = ap.parse_args()

    check_rates_age()
    stories = build(read_costs(), read_revenue())
    if args.stages:
        print_stages(stories)
    elif args.by:
        print_group(stories, args.by)
    else:
        print_stories(stories)
    if args.csv:
        write_csv(stories, args.csv)


if __name__ == "__main__":
    main()
