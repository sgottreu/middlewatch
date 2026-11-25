"""ledger/migrate.py: reconcile old-schema CSVs to COST_FIELDS."""

from __future__ import annotations

import csv

import pytest

from ledger import ledger as L
from ledger import migrate as M

OLD_COST_FIELDS = [f for f in L.COST_FIELDS
                   if f not in ("pipeline", "genre", "flavor", "story_title")]


@pytest.fixture(autouse=True)
def _targets(ldir, monkeypatch):
    """migrate binds its paths at import; point TARGETS at the tmp ledger."""
    monkeypatch.setattr(M, "TARGETS", [
        ("costs", ldir / "costs.csv", L.COST_FIELDS),
        ("revenue", ldir / "revenue.csv", L.REVENUE_FIELDS),
    ])


def write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def read(path):
    with open(path, newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames), list(r)


def old_row(i):
    return {k: f"{k}{i}" for k in OLD_COST_FIELDS}


@pytest.fixture
def old_costs(ldir):
    p = ldir / "costs.csv"
    write_csv(p, OLD_COST_FIELDS, [old_row(1), old_row(2)])
    return p


def backups(ldir):
    return sorted(ldir.glob("*.bak.csv"))


def test_check_flags_old_schema(old_costs, capsys):
    before = old_costs.read_text()
    assert M.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "Out of step: costs" in out and "pipeline" in out
    assert old_costs.read_text() == before and not backups(old_costs.parent)


def test_check_ok_when_missing_files(capsys):
    assert M.main(["--check"]) == 0
    assert "does not exist yet" in capsys.readouterr().out


def test_dry_run_writes_nothing(old_costs, capsys):
    before = old_costs.read_text()
    assert M.main(["--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert old_costs.read_text() == before and not backups(old_costs.parent)


def test_migrate_adds_columns_in_schema_order_and_keeps_data(old_costs):
    original = old_costs.read_text()
    assert M.main([]) == 0
    header, rows = read(old_costs)
    assert header == L.COST_FIELDS
    assert len(rows) == 2
    assert rows[0]["event_id"] == "event_id1" and rows[1]["notes"] == "notes2"
    for new in ("pipeline", "genre", "flavor", "story_title"):
        assert rows[0][new] == ""
    bak = backups(old_costs.parent)
    assert len(bak) == 1 and bak[0].read_text() == original


def test_migrate_is_idempotent(old_costs, capsys):
    M.main([])
    after_first = old_costs.read_text()
    nbak = len(backups(old_costs.parent))
    capsys.readouterr()
    assert M.main([]) == 0
    assert "Nothing to do." in capsys.readouterr().out
    assert old_costs.read_text() == after_first
    assert len(backups(old_costs.parent)) == nbak
    assert M.main(["--check"]) == 0


def test_check_passes_after_migrate(old_costs):
    M.main([])
    assert M.main(["--check"]) == 0


def test_current_schema_file_untouched(ldir, story):
    L.record_cost(story=story, stage="s", provider="polly", model="neural",
                  unit_type="characters", units=1, path=ldir / "costs.csv")
    before = (ldir / "costs.csv").read_text()
    assert M.main([]) == 0
    assert (ldir / "costs.csv").read_text() == before and not backups(ldir)


def test_reorders_columns(ldir):
    p = ldir / "costs.csv"
    hdr = list(reversed(L.COST_FIELDS))
    write_csv(p, hdr, [{k: k for k in hdr}])
    assert M.main(["--check"]) == 1
    M.main([])
    header, rows = read(p)
    assert header == L.COST_FIELDS and rows[0]["stage"] == "stage"


def test_unknown_empty_column_dropped(ldir):
    p = ldir / "costs.csv"
    hdr = L.COST_FIELDS + ["junk"]
    write_csv(p, hdr, [{k: "" for k in hdr}])
    M.main([])
    assert read(p)[0] == L.COST_FIELDS


def test_unknown_populated_column_kept_at_end(ldir):
    p = ldir / "costs.csv"
    hdr = OLD_COST_FIELDS + ["legacy"]
    write_csv(p, hdr, [{**old_row(1), "legacy": "keep me"}])
    M.main([])
    header, rows = read(p)
    assert header == L.COST_FIELDS + ["legacy"]
    assert rows[0]["legacy"] == "keep me"


def test_populated_extra_only_is_not_a_change(ldir):
    p = ldir / "costs.csv"
    hdr = L.COST_FIELDS + ["legacy"]
    write_csv(p, hdr, [{**{k: "" for k in L.COST_FIELDS}, "legacy": "x"}])
    assert M.main(["--check"]) == 0


def test_backfill_fills_only_empty_cells(old_costs, ldir):
    M.main([])
    header, rows = read(old_costs)
    rows[0]["pipeline"] = "ambience"
    write_csv(old_costs, header, rows)
    assert M.main(["--backfill", "pipeline=story"]) == 0
    _, rows = read(old_costs)
    assert [r["pipeline"] for r in rows] == ["ambience", "story"]
    assert len(backups(ldir)) >= 1


def test_backfill_on_old_schema_in_one_step(old_costs):
    M.main(["--backfill", "pipeline=story"])
    header, rows = read(old_costs)
    assert header == L.COST_FIELDS
    assert {r["pipeline"] for r in rows} == {"story"}


def test_backfill_rerun_is_noop(old_costs, capsys):
    M.main(["--backfill", "pipeline=story"])
    snap = old_costs.read_text()
    capsys.readouterr()
    M.main(["--backfill", "pipeline=story"])
    assert "Nothing to do." in capsys.readouterr().out
    assert old_costs.read_text() == snap


def test_backfill_unknown_column_exits(old_costs):
    with pytest.raises(SystemExit, match="not a column"):
        M.main(["--backfill", "nonsense=1"])


def test_backfill_bad_format_exits():
    with pytest.raises(SystemExit, match="column=value"):
        M.main(["--backfill", "pipeline"])


def test_parse_backfill():
    assert M.parse_backfill(["a=1", " b = x=y "]) == {"a": "1", "b": "x=y"}
    assert M.parse_backfill(None) == {}


def test_only_limits_to_one_file(ldir, old_costs):
    write_csv(ldir / "revenue.csv", ["story_slug", "platform"], [{"story_slug": "a", "platform": "y"}])
    M.main(["--only", "revenue"])
    assert read(ldir / "revenue.csv")[0] == L.REVENUE_FIELDS
    assert read(old_costs)[0] == OLD_COST_FIELDS  # costs untouched


def test_revenue_migration_preserves_values(ldir):
    p = ldir / "revenue.csv"
    write_csv(p, ["period", "story_slug", "platform"],
              [{"period": "2026-09", "story_slug": "a", "platform": "youtube"}])
    M.main([])
    header, rows = read(p)
    assert header == L.REVENUE_FIELDS
    assert rows[0]["period"] == "2026-09" and rows[0]["story_slug"] == "a"


def test_no_tmp_files_left(old_costs):
    M.main([])
    assert not list(old_costs.parent.glob("*.tmp"))


def test_failed_write_leaves_original_and_cleans_tmp(old_costs, monkeypatch):
    original = old_costs.read_text()
    plan = M.Plan("costs", old_costs, L.COST_FIELDS)
    monkeypatch.setattr(M.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        M.write(plan)
    assert old_costs.read_text() == original
    assert not list(old_costs.parent.glob("*.tmp"))


def test_plan_describe_variants(old_costs, ldir):
    p = M.Plan("costs", old_costs, L.COST_FIELDS)
    assert p.changed and set(p.missing) == {"pipeline", "story_title", "genre", "flavor"}
    assert any("add columns" in l for l in p.describe())
    missing = M.Plan("x", ldir / "none.csv", L.COST_FIELDS)
    assert not missing.changed and "does not exist" in missing.describe()[0]
