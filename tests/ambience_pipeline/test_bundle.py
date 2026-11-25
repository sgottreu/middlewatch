"""The ambience bundle, the hours dial, and the concept validator."""
from __future__ import annotations

import json

import pytest

from ambience_pipeline import bundle as ab
from ambience_pipeline.bundle import AmbienceBundle

from .conftest import layer


def make(repo_root, concept=None, slug="rain-room", hours=1):
    b = AmbienceBundle.create(repo_root / "ambience", slug, hours, 42, {"scene": "rain"})
    if concept is not None:
        b.write_concept(concept)
    return b


def test_create_writes_an_ambience_manifest(repo_root):
    b = make(repo_root)
    m = b.manifest()
    assert m["pipeline"] == "ambience" and m["slug"] == "rain-room"
    assert m["stages"] == {s: "pending" for s in ab.STAGES}
    assert m["pinned"] == {"hours": 1, "seed": 42}
    assert b.seconds == 3600 and (b.root / "audio").is_dir()
    with pytest.raises(ValueError, match="already exists"):
        make(repo_root)


def test_hours_are_pinned_and_unknown_values_raise(repo_root):
    assert make(repo_root, slug="eight", hours=8).seconds == 28800
    for bad in (2, "x", None, 45):
        with pytest.raises(ValueError, match="hours must be one of"):
            ab.hours_spec(bad)
    with pytest.raises(ValueError):
        AmbienceBundle.create(repo_root / "ambience", "two", 2, 1, {})


def test_ledger_story_is_ambience(repo_root):
    b = make(repo_root)
    b.pin("title", "Rain Room")
    b.pin("series", "Middle Watch: Amberlight")
    s = b.ledger_story()
    assert s["pipeline"] == "ambience" and s["slug"] == "rain-room"
    assert s["title"] == "Rain Room" and s["genre"] == "" and s["flavor"] == ""
    from ledger import ledger
    assert "ambience" in ledger.PIPELINES


def test_add_spend_and_open_refuses_a_story(repo_root):
    b = make(repo_root)
    b.add_spend("procedural_seconds", 2.5)
    b.add_spend("procedural_seconds", 2.5)
    assert b.manifest()["spend"] == {"procedural_seconds": 5.0}
    story = repo_root / "stories" / "x" / "y"
    story.mkdir(parents=True)
    (story / "manifest.json").write_text(json.dumps({"pipeline": "story"}))
    with pytest.raises(ValueError, match="not an ambience"):
        AmbienceBundle.open(story)


def test_slugs_are_confined(repo_root):
    make(repo_root)
    assert ab.find(repo_root / "ambience", "rain-room").name == "rain-room"
    for bad in ("../stories", "..", "a/b", "", "_auditions", "Rain", "audition", "new"):
        with pytest.raises((ValueError, FileNotFoundError)):
            ab.find(repo_root / "ambience", bad)
    assert ab.unique_slug(repo_root / "ambience", "Rain Room") == "rain-room-2"
    assert ab.unique_slug(repo_root / "ambience", "new") == "new-ambience"


def test_approval_lapses_when_the_layers_change(repo_root, concept):
    b = make(repo_root, concept)
    assert b.stage_status("concept") == "awaiting_review"
    b.approve()
    assert b.concept_approved() and b.stage_status("concept") == "approved"
    retitled = dict(concept, title="Another name")
    assert b.write_concept(retitled) is False and b.concept_approved()
    louder = dict(concept, layers=[dict(concept["layers"][0], gain_db=-18.0)])
    assert b.write_concept(louder) is True
    assert b.stage_status("concept") == "awaiting_review" and not b.concept_approved()
    assert "rain" in b.view_path.read_text()


def test_approve_refuses_an_invalid_concept(repo_root):
    b = make(repo_root, {"layers": [layer(gain=5.0)]})
    with pytest.raises(ValueError, match="gain_db 5.0 outside"):
        b.approve()


def test_a_valid_concept_has_no_problems(concept):
    concept["layers"].append(layer("gust", "accent", "wind", -26.0, density_per_min=0.5,
                                   event_s=6, params={"strength": 0.7}))
    concept["layers"].append(layer("drone", "tonal", "room_tone", -36.0, pan=0.2,
                                   hpf_hz=40, lpf_hz=400,
                                   movement={"type": "lfo", "depth_db": 2, "period_s": 60}))
    assert ab.validate_concept(concept) == []


@pytest.mark.parametrize("mutate, expect", [
    (lambda c: c.update(layers=[]), "non-empty"),
    (lambda c: c["layers"][0].update(role="drone"), "role must be one of"),
    (lambda c: c["layers"][0].update(source="generated"), "not built yet"),
    (lambda c: c["layers"][0].update(source="library"), "source must be"),
    (lambda c: c["layers"][0].pop("gain_db"), "gain_db is required"),
    (lambda c: c["layers"][0].update(gain_db=-80), "outside"),
    (lambda c: c["layers"][0].update(recipe={"type": "thunder"}), "no recipe 'thunder'"),
    (lambda c: c["layers"][0]["recipe"].update(colour=1), "rain has no parameter 'colour'"),
    (lambda c: c["layers"][0]["recipe"].update(surface="tin"), "unknown surface"),
    (lambda c: c["layers"][0]["recipe"].update(intensity=-1), "non-negative"),
    (lambda c: c["layers"][0]["recipe"].update(intensity="lots"), "could not convert"),
    (lambda c: c["layers"][0]["recipe"].update(gain_db=-3), "on the layer"),
    (lambda c: c["layers"][1].update(id="rain"), "duplicate id"),
    (lambda c: c["layers"][1].update(id="Bad Id"), "short lowercase"),
    (lambda c: c["layers"][1].update(wobble=1), "unknown keys wobble"),
    (lambda c: c["layers"][1].update(density_per_min=1), "only applies to accents"),
    (lambda c: c["layers"][1].update(role="accent"), "need density_per_min"),
    (lambda c: c["layers"][1].update(role="accent", density_per_min=1e6), "need density_per_min"),
    (lambda c: c["layers"][1].update(role="accent", density_per_min=1, gain_db=-12),
     "louder than the loudest bed"),
    (lambda c: c["layers"][0].update(role="texture"), "at least one bed"),
    (lambda c: c["layers"].extend([layer("b2"), layer("b3")]), "at most 2 bed"),
    (lambda c: c["layers"][0].update(pan=3), "pan must be"),
    (lambda c: c["layers"][0].update(hpf_hz=900, lpf_hz=200), "hpf_hz must be below"),
    (lambda c: c["layers"][0].update(movement={"type": "lfo", "depth_db": 40, "period_s": 9}),
     "movement must be"),
    (lambda c: c.update(seed=-1), "seed must be"),
    (lambda c: c.update(hours=2), "hours must be"),
    (lambda c: c["layers"].extend([layer(f"t{i}", "texture") for i in range(8)]), "at most 8"),
])
def test_validator_names_each_problem(concept, mutate, expect):
    mutate(concept)
    problems = ab.validate_concept(concept)
    assert any(expect in p for p in problems), problems


def test_validator_handles_junk():
    assert ab.validate_concept([]) == ["concept must be a JSON object"]
    assert any("must be an object" in p for p in ab.validate_concept({"layers": [3]}))
