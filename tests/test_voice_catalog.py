"""voice_catalog.py: the account voice list rendered as docs/story/voices.md."""

from __future__ import annotations

import json
from pathlib import Path

from story_pipeline import voice_catalog as vc

ROWS = [
    {"voice_id": "wScwPA1qCkWo5R2dmlS8", "name": "Charlotte - Storytelling & Narration",
     "category": "premade", "description": "A calm  Australian voice,\nwarm for narration.",
     "labels": {"gender": "female", "age": "middle_aged", "accent": "australian",
                "descriptive": "calm", "use_case": "narrative_story"},
     "preview_url": "https://example.com/c.mp3"},
    {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel - Steady Broadcaster",
     "category": "premade", "description": None,
     "labels": {"gender": "male", "age": "middle_aged", "accent": "british"}},
    {"voice_id": "AXdMgz6evoL7OPd7eU12", "name": "Elizabeth | pipe",
     "category": "professional", "description": "Older | English lady.", "labels": {}},
]
LIBRARY = {"Charlotte": "wScwPA1qCkWo5R2dmlS8", "Narrator": "wScwPA1qCkWo5R2dmlS8",
           "Ghost": "ZZZZZZZZZZZZZZZZZZZZ"}


def test_entries_merge_library_names_and_labels():
    es = {e["id"]: e for e in vc.entries(ROWS, LIBRARY)}
    c = es["wScwPA1qCkWo5R2dmlS8"]
    assert c["short"] == "Charlotte" and c["pipeline_names"] == ["Charlotte", "Narrator"]
    assert c["traits"] == "female · middle aged · australian · calm"
    assert c["use_case"] == "narrative story"
    assert c["description"] == "A calm Australian voice, warm for narration."   # whitespace folded
    assert es["onwK4e9ZLuTAKqWW03F9"]["description"] == ""
    assert es["AXdMgz6evoL7OPd7eU12"]["pipeline_names"] == []


def test_entries_sorted_by_gender_then_name():
    assert [e["short"] for e in vc.entries(ROWS, LIBRARY)] == ["Charlotte", "Daniel", "Elizabeth | pipe"]


def test_cast_in_reads_every_arcs_verdicts(tmp_path):
    d = tmp_path / "amberlight" / "_casting"
    d.mkdir(parents=True)
    (d / "auditions.json").write_text(json.dumps({"voices": {
        "wScwPA1qCkWo5R2dmlS8": {"verdicts": {"Mrs Hale": {"verdict": "cast"},
                                              "narrator": {"verdict": "maybe"}}}}}))
    (tmp_path / "empty").mkdir()
    assert vc.cast_in(tmp_path) == {"wScwPA1qCkWo5R2dmlS8": ["Mrs Hale (amberlight)"]}


def test_render_has_summary_and_a_section_per_voice():
    md = vc.render(vc.entries(ROWS, LIBRARY), {"wScwPA1qCkWo5R2dmlS8": ["Mrs Hale (amberlight)"]},
                   LIBRARY, generated="2026-11-27")
    assert md.startswith("# Voices on the ElevenLabs account")
    assert "3 voices" in md and "2026-11-27" in md
    # pipes in names and descriptions can't break the summary table
    assert "Elizabeth \\| pipe" in md
    assert "### Charlotte" in md and "A calm Australian voice, warm for narration." in md
    assert "Cast as: Mrs Hale (amberlight)" in md
    assert "[preview](https://example.com/c.mp3)" in md
    assert "`wScwPA1qCkWo5R2dmlS8`" in md
    # a voices.yaml name with no voice on the account is called out
    assert "Ghost" in md.split("## Not on the account")[1]


def test_load_rows_from_json(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"voices": ROWS}))
    assert vc.load_rows(p) == ROWS
    p.write_text(json.dumps(ROWS))
    assert vc.load_rows(p) == ROWS
