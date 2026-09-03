"""Reconcile the ledger CSVs with the schemas declared in `ledger.py`.

    python3 -m ledger.migrate --check      # exit 1 if out of step, change nothing
    python3 -m ledger.migrate --dry-run    # say what would change
    python3 -m ledger.migrate              # rewrite in place, keeping a backup

`costs.csv` is append-only for *rows*. That is not the same as being frozen: the
column list will grow whenever a new dimension is worth slicing by, and every
such change is a rewrite of a file that is otherwise never rewritten. Doing it by
hand once is fine. Doing it by hand the third time, against a year of real spend,
is how a ledger gets a half-shifted column nobody notices until the totals stop
matching an invoice.

So the rewrite is a script, and the script is careful about three things:

**It never drops data.** A column in the file but not in the schema is kept and
moved to the end, not deleted, unless every cell in it is empty. A schema is a
statement about what is written next, not a licence to discard what was written
before.

**It is idempotent.** Running it twice does nothing the second time, which is
what makes `--check` usable in a pre-commit hook or a test.

**It writes atomically, through a temp file and `os.replace`, after taking a
timestamped backup.** An interrupted migration leaves the original intact. The
same reasoning `upsert_revenue` already applies to the revenue file.

Backfilling is separate and explicit:

    python3 -m ledger.migrate --backfill pipeline=story

Only empty cells are filled, so a rerun cannot overwrite a real value. Right now
that flag is exactly how a pre-`pipeline` ledger is brought forward: every row
written before the column existed was necessarily a story row, because the
ambience pipeline did not exist to write any others. That reasoning is true today
and will not be true later — check it before reaching for the flag.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .ledger import COST_FIELDS, COSTS_CSV, REVENUE_FIELDS, REVENUE_CSV

TARGETS = [
    ("costs", COSTS_CSV, COST_FIELDS),
    ("revenue", REVENUE_CSV, REVENUE_FIELDS),
]


class Plan:
    """What one file needs, worked out before anything is written."""

    def __init__(self, name: str, path: Path, fields: list[str]):
        self.name = name
        self.path = path
        self.fields = fields
        self.exists = path.exists()
        self.header: list[str] = []
        self.rows: list[dict] = []
        self.missing: list[str] = []
        self.extra_empty: list[str] = []
        self.extra_used: list[str] = []
        self.reordered = False

        if not self.exists:
            return

        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            self.header = list(reader.fieldnames or [])
            self.rows = [dict(r) for r in reader]

        self.missing = [f for f in fields if f not in self.header]
        for col in self.header:
            if col in fields:
                continue
            if any(str(r.get(col) or "").strip() for r in self.rows):
                self.extra_used.append(col)
            else:
                self.extra_empty.append(col)
        self.reordered = [c for c in self.header if c in fields] != [
            f for f in fields if f in self.header
        ]

    @property
    def target_header(self) -> list[str]:
        """Schema order, then any populated unknown column, kept at the end."""
        return list(self.fields) + self.extra_used

    @property
    def changed(self) -> bool:
        return self.exists and (
            bool(self.missing) or bool(self.extra_empty) or self.reordered
        )

    def describe(self) -> list[str]:
        if not self.exists:
            return [f"  {self.path} does not exist yet — nothing to migrate."]
        if not self.changed:
            return [f"  {self.path} already matches the schema "
                    f"({len(self.rows):,} rows)."]
        out = [f"  {self.path} — {len(self.rows):,} rows"]
        if self.missing:
            out.append(f"    add columns:    {', '.join(self.missing)}")
        if self.extra_empty:
            out.append(f"    drop columns:   {', '.join(self.extra_empty)} "
                       f"(present in the file, not in the schema, no data)")
        if self.extra_used:
            out.append(f"    keep at end:    {', '.join(self.extra_used)} "
                       f"(not in the schema but holds data — kept, never dropped)")
        if self.reordered:
            out.append("    reorder columns to match the schema")
        return out


def apply_backfill(plan: Plan, backfill: dict[str, str]) -> int:
    """Fill empty cells only. Returns the number of cells written."""
    filled = 0
    for col, value in backfill.items():
        if col not in plan.target_header:
            raise SystemExit(
                f"--backfill {col}=... but {col!r} is not a column of "
                f"{plan.name}. Columns: {', '.join(plan.target_header)}"
            )
        for row in plan.rows:
            if not str(row.get(col) or "").strip():
                row[col] = value
                filled += 1
    return filled


def write(plan: Plan) -> Path:
    """Rewrite the file atomically, after a timestamped backup."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = plan.path.with_suffix(f".{stamp}.bak.csv")
    shutil.copy2(plan.path, backup)

    header = plan.target_header
    fd, tmp = tempfile.mkstemp(dir=str(plan.path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=header)
            w.writeheader()
            for row in plan.rows:
                w.writerow({k: row.get(k, "") for k in header})
        os.replace(tmp, plan.path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return backup


def parse_backfill(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--backfill expects column=value, got {pair!r}")
        col, _, value = pair.partition("=")
        out[col.strip()] = value.strip()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="ledger.migrate", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if a file is out of step; write nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="describe the changes without making them")
    ap.add_argument("--backfill", action="append", metavar="COL=VALUE",
                    help="fill empty cells in COL with VALUE; repeatable")
    ap.add_argument("--only", choices=[n for n, _, _ in TARGETS],
                    help="migrate one file rather than both")
    args = ap.parse_args(argv)

    backfill = parse_backfill(args.backfill)
    targets = [t for t in TARGETS if not args.only or t[0] == args.only]
    plans = [Plan(name, path, fields) for name, path, fields in targets]

    print(f"Ledger schema check — {datetime.now():%Y-%m-%d %H:%M}")
    for plan in plans:
        print(f"\n{plan.name}")
        for line in plan.describe():
            print(line)
        if backfill and plan.exists:
            targeted = {k: v for k, v in backfill.items()
                        if k in plan.target_header}
            if targeted:
                print(f"    backfill:       "
                      f"{', '.join(f'{k}={v}' for k, v in targeted.items())} "
                      f"(empty cells only)")

    work = [p for p in plans if p.changed]

    if args.check:
        if work:
            print(f"\nOut of step: {', '.join(p.name for p in work)}. "
                  f"Run `python3 -m ledger.migrate` to reconcile.")
            return 1
        print("\nIn step with the schema.")
        return 0

    if args.dry_run:
        print("\nDry run. Nothing written.")
        return 0

    # Backfill is a change worth making even when the header already matches.
    acted = False
    for plan in plans:
        if not plan.exists:
            continue
        filled = apply_backfill(plan, backfill) if backfill else 0
        if not plan.changed and not filled:
            continue
        backup = write(plan)
        acted = True
        detail = f", {filled:,} cells backfilled" if filled else ""
        print(f"\n{plan.name}: rewrote {plan.path} "
              f"({len(plan.rows):,} rows{detail})")
        print(f"  backup at {backup}")

    if not acted:
        print("\nNothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
