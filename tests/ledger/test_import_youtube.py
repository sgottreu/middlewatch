"""ledger/import_youtube.py: parsing a YouTube Analytics export. No HTTP is
involved anywhere in this module; the network guard in conftest backs that up."""

from __future__ import annotations

import csv
import sys

import pytest

from ledger import import_youtube as IY
from ledger import ledger as L


@pytest.fixture(autouse=True)
def _videos(ldir, monkeypatch):
    """The default VIDEOS_CSV is bound at import; read the tmp ledger's instead."""
    orig = IY.load_video_map
    monkeypatch.setattr(IY, "load_video_map", lambda path=ldir / "videos.csv": orig(path))
    (ldir / "videos.csv").write_text(
        "story_slug,video_id\nstory-a,vidA\nstory-b, vidB \n,vidX\n")


def export(tmp_path, header, rows, bom=False, name="export.csv"):
    p = tmp_path / name
    with open(p, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return p


HDR = ["Video", "Views", "Watch time (hours)", "Your estimated revenue (USD)"]


def test_load_video_map_strips_whitespace(ldir):
    m = IY.load_video_map(ldir / "videos.csv")
    assert m["vidA"] == "story-a" and m["vidB"] == "story-b"
    assert m["vidX"] == ""  # blank slug is kept as ''; parse_export treats it as unmapped


def test_blank_slug_counts_as_unmapped(tmp_path):
    p = export(tmp_path, HDR, [["vidX", "1", "1", "1"]])
    assert IY.parse_export(p, "2026-09") == ([], ["vidX"])


def test_load_video_map_missing_file(tmp_path):
    assert IY.load_video_map(tmp_path / "none.csv") == {}


def test_parse_basic(tmp_path):
    p = export(tmp_path, HDR, [["Total", "10", "1", "5"], ["vidA", "1,234", "56.7", "$12.34"],
                               ["vidB", "10", "0.5", "0"]])
    rows, unmapped = IY.parse_export(p, "2026-09")
    assert unmapped == []
    assert rows[0] == {"story_slug": "story-a", "platform": "youtube", "period": "2026-09",
                       "video_id": "vidA", "views": "1234", "watch_hours": "56.7",
                       "revenue_usd": "12.34", "source": "export.csv"}
    assert rows[1]["story_slug"] == "story-b" and rows[1]["revenue_usd"] == "0"
    assert len(rows) == 2  # Total skipped


def test_parse_skips_total_variants_and_blank(tmp_path):
    p = export(tmp_path, HDR, [["TOTALS", "1", "1", "1"], ["total", "1", "1", "1"],
                               ["", "1", "1", "1"], ["  ", "1", "1", "1"]])
    assert IY.parse_export(p, "2026-09") == ([], [])


def test_parse_reports_unmapped(tmp_path):
    p = export(tmp_path, HDR, [["vidA", "1", "1", "1"], ["nope", "1", "1", "1"],
                               ["nope", "2", "2", "2"]])
    rows, unmapped = IY.parse_export(p, "2026-09")
    assert len(rows) == 1 and unmapped == ["nope", "nope"]


def test_parse_header_aliases_case_and_whitespace(tmp_path):
    p = export(tmp_path, ["video id", "VIEWS", "Watch time", "estimated revenue (usd)"],
               [["vidA", "5", "2", "1.5"]])
    rows, _ = IY.parse_export(p, "2026-09")
    assert (rows[0]["views"], rows[0]["watch_hours"], rows[0]["revenue_usd"]) == ("5", "2", "1.5")


def test_parse_utf8_bom(tmp_path):
    p = export(tmp_path, HDR, [["vidA", "1", "2", "3"]], bom=True)
    assert len(IY.parse_export(p, "2026-09")[0]) == 1


def test_parse_missing_numeric_columns_blank(tmp_path):
    p = export(tmp_path, ["Content"], [["vidA"]])
    rows, _ = IY.parse_export(p, "2026-09")
    assert (rows[0]["views"], rows[0]["watch_hours"], rows[0]["revenue_usd"]) == ("", "", "")


def test_parse_empty_cells_blank(tmp_path):
    p = export(tmp_path, HDR, [["vidA", "", "", ""]])
    rows, _ = IY.parse_export(p, "2026-09")
    assert rows[0]["views"] == "" and rows[0]["revenue_usd"] == ""


def test_parse_no_video_column_exits(tmp_path):
    p = export(tmp_path, ["Country", "Views"], [["US", "1"]])
    with pytest.raises(SystemExit, match="No video ID column"):
        IY.parse_export(p, "2026-09")


def test_find_returns_original_header_spelling():
    assert IY._find(["  Views "], ["views"]) == "  Views "
    assert IY._find(["x"], ["views"]) is None


def test_main_end_to_end_and_rerun_safe(tmp_path, ldir, monkeypatch, capsys):
    p = export(tmp_path, HDR, [["vidA", "100", "5", "2.50"], ["ghost", "1", "1", "1"]])
    monkeypatch.setattr(sys, "argv", ["iy", str(p), "--period", "2026-09"])
    IY.main()
    out = capsys.readouterr().out
    assert "1 inserted, 0 updated for 2026-09" in out
    assert "1 video(s) not in videos.csv" in out and "ghost" in out
    IY.main()
    assert "0 inserted, 1 updated" in capsys.readouterr().out
    with open(ldir / "revenue.csv", newline="") as fh:
        got = list(csv.DictReader(fh))
    assert len(got) == 1 and got[0]["revenue_usd"] == "2.50" and got[0]["source"] == "export.csv"


def test_main_requires_period(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["iy", str(tmp_path / "x.csv")])
    with pytest.raises(SystemExit):
        IY.main()


def test_main_writes_to_cwd_ledger_not_real_one(tmp_path, repo_root, monkeypatch):
    p = export(tmp_path, HDR, [["vidA", "1", "1", "1"]])
    monkeypatch.setattr(sys, "argv", ["iy", str(p), "--period", "2026-09"])
    IY.main()
    assert (repo_root / "ledger" / "revenue.csv").exists()
