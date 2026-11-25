"""ledger/report.py against a synthetic costs.csv / revenue.csv."""

from __future__ import annotations

import csv
import sys
from datetime import date, timedelta

import pytest

from ledger import ledger as L
from ledger import report as R


def add(ldir, slug, cost_units, *, pipeline="story", stage="write", genre="regency",
        flavor="comedy", title=None, series="S", episode=1, model="neural"):
    # polly neural: $16 / 1M chars -> units * 16e-6
    return L.record_cost(
        story={"pipeline": pipeline, "slug": slug, "title": title or slug.upper(),
               "series": series, "genre": genre, "flavor": flavor, "episode": episode},
        stage=stage, provider="polly", model=model, unit_type="characters",
        units=cost_units, path=ldir / "costs.csv")


@pytest.fixture
def populated(ldir):
    add(ldir, "a", 1_000_000, stage="write")          # $16
    add(ldir, "a", 500_000, stage="narrate")          # $8   -> a: $24
    add(ldir, "b", 250_000, genre="gothic", flavor="dread", episode=2)  # $4
    add(ldir, "amb", 125_000, pipeline="ambience", genre="", flavor="")  # $2
    L.upsert_revenue([
        {"story_slug": "a", "platform": "youtube", "period": "2026-09",
         "views": "1000", "watch_hours": "10", "revenue_usd": "30.00"},
        {"story_slug": "a", "platform": "youtube", "period": "2026-10",
         "views": "500", "watch_hours": "5", "revenue_usd": "10.00"},
        {"story_slug": "b", "platform": "youtube", "period": "2026-09",
         "views": "100", "watch_hours": "1", "revenue_usd": "1.00"},
        {"story_slug": "ghost", "platform": "youtube", "period": "2026-09",
         "views": "9", "watch_hours": "9", "revenue_usd": "9.00"},
    ], ldir / "revenue.csv")
    return ldir


def test_read_missing_files_empty(ldir):
    assert R.read_costs(ldir / "nope.csv") == []
    assert R.read_revenue(ldir / "nope.csv") == []


def test_f_parses_or_zero():
    assert R._f("1.5") == 1.5
    assert R._f("") == 0.0 and R._f(None) == 0.0 and R._f("x") == 0.0


def test_build_totals(populated):
    s = R.build(R.read_costs(populated / "costs.csv"), R.read_revenue(populated / "revenue.csv"))
    assert set(s) == {"a", "b", "amb"}          # revenue for unknown slug ignored
    assert s["a"]["cost"] == pytest.approx(24.0)
    assert s["a"]["revenue"] == pytest.approx(40.0)
    assert s["a"]["views"] == 1500 and s["a"]["watch_hours"] == 15
    assert s["a"]["months"] == 2 and s["b"]["months"] == 1 and s["amb"]["months"] == 0
    assert s["a"]["by_stage"] == {"write": pytest.approx(16.0), "narrate": pytest.approx(8.0)}
    assert s["a"]["by_provider"]["polly/neural"] == pytest.approx(24.0)
    assert s["a"]["title"] == "A" and s["a"]["series"] == "S"
    assert s["amb"]["pipeline"] == "ambience" and s["a"]["pipeline"] == "story"


def test_build_later_label_wins_blank_does_not_clobber():
    base = dict(story_slug="x", story_title="", series="", genre="", flavor="",
                arc_slug="", episode="", pipeline="story", stage="s", provider="p",
                model="m", cost_usd="1")
    costs = [{**base, "timestamp": "2026-02-01", "genre": "old"},
             {**base, "timestamp": "2026-01-01", "genre": "new"},
             {**base, "timestamp": "2026-03-01", "genre": ""}]
    s = R.build(costs, [])["x"]
    assert s["genre"] == "new"             # later *row* wins; blank never overwrites
    assert s["first_cost"] == "2026-01-01"
    assert s["cost"] == 3.0


def test_build_empty():
    assert R.build([], []) == {}


def test_print_stories_empty(capsys):
    R.print_stories({})
    assert "No cost events recorded yet." in capsys.readouterr().out


def test_print_stories_statuses(populated, capsys):
    s = R.build(R.read_costs(populated / "costs.csv"), R.read_revenue(populated / "revenue.csv"))
    R.print_stories(s)
    out = capsys.readouterr().out
    assert "paid back (2mo)" in out                    # a: 40 >= 24
    assert "25% recovered" in out                      # b: 1 / 4
    assert "unpublished or pre-monetization" in out    # amb
    assert "TOTAL" in out and "$30.00" in out          # cost 24+4+2
    assert "1 of 3 stories have paid back" in out
    assert "Blended RPM" in out


def test_print_stories_no_revenue_no_rpm_line(ldir, capsys):
    add(ldir, "a", 1000)
    R.print_stories(R.build(R.read_costs(ldir / "costs.csv"), []))
    out = capsys.readouterr().out
    assert "0 of 1 stories" in out and "Blended RPM" not in out


def test_print_group_by_genre(populated, capsys):
    s = R.build(R.read_costs(populated / "costs.csv"), R.read_revenue(populated / "revenue.csv"))
    R.print_group(s, "genre")
    out = capsys.readouterr().out
    assert "regency" in out and "gothic" in out and "(unset)" in out
    assert "Small n." in out


def test_print_group_by_pipeline(populated, capsys):
    s = R.build(R.read_costs(populated / "costs.csv"), R.read_revenue(populated / "revenue.csv"))
    R.print_group(s, "pipeline")
    out = capsys.readouterr().out
    assert "story" in out and "ambience" in out


def test_print_stages(populated, capsys):
    s = R.build(R.read_costs(populated / "costs.csv"), R.read_revenue(populated / "revenue.csv"))
    R.print_stages(s)
    out = capsys.readouterr().out
    assert "Cost by stage" in out and "write" in out and "narrate" in out
    assert "polly/neural" in out
    assert "Mean cost per story: $10.00 across 3 stories." in out


def test_write_csv(populated, tmp_path, capsys):
    s = R.build(R.read_costs(populated / "costs.csv"), R.read_revenue(populated / "revenue.csv"))
    out = tmp_path / "summary.csv"
    R.write_csv(s, out)
    got = {r["slug"]: r for r in csv.DictReader(open(out, newline=""))}
    assert list(got) == ["a", "amb", "b"]
    assert got["a"]["cost_usd"] == "24.0" and got["a"]["revenue_usd"] == "40.0"
    assert got["a"]["net_usd"] == "16.0" and got["a"]["recovered_pct"] == "166.7"
    assert got["a"]["months_reported"] == "2" and got["a"]["views"] == "1500"
    assert got["amb"]["recovered_pct"] == "0.0"
    assert "Wrote" in capsys.readouterr().out


def test_write_csv_zero_cost_blank_pct(tmp_path):
    s = {"x": dict(slug="x", title="", series="", genre="", flavor="", arc_slug="",
                   episode="", cost=0.0, revenue=0.0, views=0.0, watch_hours=0.0, months=0)}
    R.write_csv(s, tmp_path / "o.csv")
    assert list(csv.DictReader(open(tmp_path / "o.csv")))[0]["recovered_pct"] == ""


def _run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["report", *argv])
    R.main()


def test_main_default_reads_cwd_ledger(populated, monkeypatch, capsys):
    _run_main(monkeypatch)
    assert "TOTAL" in capsys.readouterr().out


def test_main_no_ledger(monkeypatch, capsys):
    _run_main(monkeypatch)
    assert "No cost events recorded yet." in capsys.readouterr().out


def test_main_by_and_stages_and_csv(populated, monkeypatch, capsys, tmp_path):
    _run_main(monkeypatch, "--by", "flavor")
    assert "flavor" in capsys.readouterr().out
    _run_main(monkeypatch, "--stages")
    assert "Cost by stage" in capsys.readouterr().out
    _run_main(monkeypatch, "--csv", str(tmp_path / "x.csv"))
    assert (tmp_path / "x.csv").exists()


def test_main_rejects_bad_by(monkeypatch):
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, "--by", "nonsense")


def test_stale_rate_warning(monkeypatch, capsys):
    old = (date.today() - timedelta(days=R.RATE_STALE_DAYS + 5)).isoformat()
    monkeypatch.setattr(R, "load_rates", lambda: {"rates_verified": old})
    R.check_rates_age()
    assert "last verified" in capsys.readouterr().out


def test_fresh_rate_no_warning(monkeypatch, capsys):
    monkeypatch.setattr(R, "load_rates", lambda: {"rates_verified": date.today().isoformat()})
    R.check_rates_age()
    assert capsys.readouterr().out == ""


def test_rate_age_swallows_errors(monkeypatch, capsys):
    monkeypatch.setattr(R, "load_rates", lambda: {})
    R.check_rates_age()
    assert capsys.readouterr().out == ""
