"""spend.record_images / record_tts: ledger rows for images and narration."""

from __future__ import annotations

import csv

from story_pipeline import spend


def rows(repo_root):
    p = repo_root / "ledger" / "costs.csv"
    return list(csv.DictReader(open(p))) if p.exists() else []


def test_images_row(mini, repo_root):
    spend.record_images(mini, "design", "gemini-3.1-flash-image", notes="shot 1")
    (r,) = rows(repo_root)
    assert (r["provider"], r["model"], r["unit_type"], r["stage"], r["pipeline"]) == (
        "gemini", "gemini-3.1-flash-image", "images", "design", "story")
    assert float(r["units"]) == 1 and r["story_slug"] == mini.root.name


def test_elevenlabs_credits_row(mini, repo_root):
    spend.record_tts(mini, "record", "elevenlabs", "eleven_multilingual_v2", 1234)
    (r,) = rows(repo_root)
    assert (r["provider"], r["unit_type"], float(r["units"])) == ("elevenlabs", "credits", 1234)


def test_polly_characters_row(mini, repo_root):
    spend.record_tts(mini, "record", "polly", "neural", 900)
    (r,) = rows(repo_root)
    assert (r["provider"], r["model"], r["unit_type"]) == ("polly", "neural", "characters")


def test_unpriced_model_warns_and_does_not_raise(mini, repo_root, capsys):
    spend.record_tts(mini, "record", "elevenlabs", "eleven_v4_unpriced", 10)
    assert "cost not written to the ledger" in capsys.readouterr().err


def test_unknown_provider_and_zero_units(mini, repo_root, capsys):
    spend.record_tts(mini, "record", "fake", "x", 10)
    spend.record_tts(mini, "record", "elevenlabs", "eleven_multilingual_v2", 0)
    assert rows(repo_root) == []
    assert "no ledger mapping for TTS provider 'fake'" in capsys.readouterr().err
