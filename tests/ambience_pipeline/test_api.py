"""The ambience plugin through the real review server."""
from __future__ import annotations

import json
import time

import pytest

from ambience_pipeline import bundle as ab
from ambience_pipeline.bundle import AmbienceBundle
from story_pipeline.review import ambience_api, server

NOT_FOUND = {"error": "not found"}


def test_registered_as_a_plugin_with_a_releaser():
    assert ambience_api in server.PLUGINS
    assert server.RELEASERS["ambience"] is ambience_api.release


def test_page_is_served(client):
    r = client.get("/ambience")
    assert r.status == 200 and "text/html" in r.header("Content-Type")
    assert 'MW.nav($("pages"), "Ambience")' in r.text


def test_index_lists_recipes_stories_and_bibles(client, fake_story, real_bibles):
    j = client.get("/api/ambience").json
    assert j["ambiences"] == [] and j["hours"] == [1, 3, 8]
    assert {r["name"] for r in j["recipes"]} >= {"rain", "wind", "fire", "ocean", "room_tone"}
    assert [s["slug"] for s in j["stories"]] == ["the-salt-house"]
    assert j["bibles"] == ["amberlight"]
    assert client.get("/api/ambience/recipes").json["recipes"] == j["recipes"]
    assert client.get("/api/ambience/stories").json["stories"] == j["stories"]


def _new(client, **body):
    r = client.post("/api/ambience/new", {"scene": "rain on a window", "hours": 1, **body})
    assert r.status == 200, r.text
    return r.json["slug"]


def test_new_from_scene_and_story(client, fake_story, repo_root):
    slug = _new(client)
    d = client.get(f"/api/ambience/{slug}").json
    assert d["stages"]["concept"] == "awaiting_review" and d["problems"] == []
    assert d["concept"]["layers"][0]["recipe"]["type"] == "rain"
    r = client.post("/api/ambience/new", {"from_story": "the-salt-house", "hours": 3})
    assert r.status == 200 and r.json["ambience"]["source_story"].endswith("the-salt-house")
    assert len(client.get("/api/ambience").json["ambiences"]) == 2


@pytest.mark.parametrize("body, expect", [
    ({}, "either a scene or a story"),
    ({"scene": "x", "from_story": "y"}, "either a scene or a story"),
    ({"from_story": "nope"}, "No story called"),
    ({"scene": "x", "hours": 2}, "hours must be one of"),
    ({"scene": "x", "bible": "nope"}, "would not load"),
])
def test_new_rejects(client, body, expect):
    r = client.post("/api/ambience/new", body)
    assert r.status == 400 and expect in r.json["error"]


def test_concept_edit_validate_approve(client):
    slug = _new(client)
    c = client.get(f"/api/ambience/{slug}/concept").json
    assert c["problems"] == [] and not c["approved"]
    bad = [dict(c["concept"]["layers"][0], gain_db=3)]
    r = client.post(f"/api/ambience/{slug}/concept", {"layers": bad})
    assert r.status == 400 and any("outside" in p for p in r.json["problems"])
    r = client.post(f"/api/ambience/{slug}/sound", {})
    assert r.status == 400 and "Approve the concept first" in r.json["error"]

    good = [dict(c["concept"]["layers"][0], gain_db=-19.0)]
    r = client.post(f"/api/ambience/{slug}/concept", {"layers": good})
    assert r.status == 200 and r.json["concept"]["layers"] == good
    assert r.json["concept"]["seed"] == c["concept"]["seed"]
    assert client.post(f"/api/ambience/{slug}/approve", {}).json["ambience"]["approved"]

    # Editing after approval lapses it.
    r = client.post(f"/api/ambience/{slug}/concept", {"layers": c["concept"]["layers"]})
    assert r.json["lapsed"] is True and r.json["stage"] == "awaiting_review"
    assert client.post(f"/api/ambience/{slug}/concept", {}).status == 400


def test_sound_starts_a_job(client, started):
    slug = _new(client)
    client.post(f"/api/ambience/{slug}/approve", {})
    r = client.post(f"/api/ambience/{slug}/sound", {"seconds": 60})
    assert r.status == 200, r.text
    (call,) = started
    assert call["key"] == f"ambience-{slug}" and call["stage"] == "sound"
    assert call["argv"][1:4] == ["-m", "ambience_pipeline.cli", "sound"]
    assert call["argv"][-2:] == ["--seconds", "60"]
    assert call["extra"]["pipeline"] == "ambience" and call["extra"]["ambience"] == slug
    assert client.post(f"/api/ambience/{slug}/sound", {"seconds": 9999}).status == 400


def test_job_log_and_cancel_with_nothing_running(client):
    slug = _new(client)
    assert client.get(f"/api/ambience/{slug}/job").json["state"] == "none"
    assert client.get(f"/api/ambience/{slug}/log?from=0").json["text"] == ""
    r = client.post(f"/api/ambience/{slug}/cancel", {})
    assert r.status == 400 and "nothing is running" in r.json["error"]
    assert client.get("/api/ambience/nope/job").status == 404
    assert client.get("/api/ambience/audition/job").json["state"] == "none"


@pytest.mark.parametrize("body, expect", [
    ({"recipe": "thunder"}, "No recipe"),
    ({"recipe": "rain", "seconds": 61}, "at most 60"),
    ({"recipe": "rain", "params": {"colour": 1}}, "no parameter"),
    ({"recipe": "rain", "params": {"surface": "tin"}}, "unknown surface"),
])
def test_audition_rejects(client, body, expect):
    r = client.post("/api/ambience/audition", body)
    assert r.status == 400 and expect in r.json["error"], r.text


def test_audition_starts_a_job_under_auditions(client, started):
    r = client.post("/api/ambience/audition", {"recipe": "rain", "params": {"surface": "roof"},
                                               "gain_db": -18, "seconds": 30})
    assert r.status == 200, r.text
    (call,) = started
    assert call["key"] == "ambience-_auditions"
    out = call["argv"][call["argv"].index("--out") + 1]
    assert out.startswith("ambience/_auditions/") and out.endswith("-rain.wav")
    assert "surface=roof" in call["argv"] and "gain_db=-18" in call["argv"]
    assert r.json["url"] == "/ambience-audio/_auditions/" + out.rsplit("/", 1)[1]


def test_a_real_audition_job_runs_and_its_wav_plays(client, monkeypatch):
    # The child runs in the scratch root, as the server runs in the repo root.
    from tests.conftest import REPO_ROOT
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    r = client.post("/api/ambience/audition", {"recipe": "room_tone", "seconds": 1})
    assert r.status == 200, r.text
    for _ in range(200):
        j = client.get("/api/ambience/audition/job").json
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "done", client.get("/api/ambience/audition/log").json
    assert j["artifact"] == r.json["url"] and j["total"] == 1
    a = client.get(j["artifact"], headers={"Range": "bytes=0-99"})
    assert a.status == 206 and len(a.body) == 100 and a.body[:4] == b"RIFF"
    assert "wrote" in client.get("/api/ambience/audition/log?from=0").json["text"]


def test_bundle_audio_is_served_with_range(client, repo_root):
    slug = _new(client)
    wav = repo_root / "ambience" / slug / "audio" / "preview.wav"
    wav.write_bytes(b"RIFF" + b"\0" * 996)
    r = client.get(f"/ambience-audio/{slug}/audio/preview.wav", headers={"Range": "bytes=10-"})
    assert r.status == 206 and len(r.body) == 990
    files = client.get(f"/api/ambience/{slug}").json["files"]
    assert files["preview"]["url"] == f"/ambience-audio/{slug}/audio/preview.wav"


@pytest.mark.parametrize("path", [
    "/ambience-audio/../config.yaml",
    "/ambience-audio/%2e%2e/config.yaml",
    "/ambience-audio/{slug}/audio/../../../secret.wav",
    "/ambience-audio/{slug}/audio/%2e%2e%2f%2e%2e%2f..%2fsecret.wav",
    "/ambience-audio/{slug}/manifest.json",
    "/ambience-audio/{slug}/audio/mix.partial.wav",
    "/ambience-audio/_auditions/../{slug}/audio/preview.wav",
    "/ambience-audio/_auditions/x/../../secret.wav",
    "/ambience-audio//etc/passwd",
    "/ambience-audio/{slug}/audio/",
    "/ambience-audio/..%2fsecret.wav",
])
def test_audio_paths_are_confined(client, repo_root, path):
    slug = _new(client)
    (repo_root / "secret.wav").write_bytes(b"RIFFsecret")
    (repo_root / "ambience" / slug / "audio" / "mix.partial.wav").write_bytes(b"RIFF")
    (repo_root / "ambience" / slug / "audio" / "preview.wav").write_bytes(b"RIFF")
    r = client.get(path.replace("{slug}", slug))
    assert r.status in (400, 404) and b"secret" not in r.body


@pytest.mark.parametrize("path", ["/api/ambience/..%2f..%2fstories", "/api/ambience/Bad",
                                  "/api/ambience/_auditions", "/api/ambience/x/concept"])
def test_bad_slugs_are_request_errors_not_500s(client, path):
    r = client.get(path)
    assert r.status in (400, 404) and r.json != NOT_FOUND


def test_release_puts_a_dead_render_back(repo_root, cfg):
    b = AmbienceBundle.create(repo_root / "ambience", "dead", 1, 1, {"scene": "x"})
    b.set_stage("sound", "running")
    ambience_api.release(cfg, {"ambience": "dead", "stage": "sound", "pipeline": "ambience"})
    assert b.stage_status("sound") == "pending"
    ambience_api.release(cfg, {"ambience": "../x", "stage": "sound"})   # harmless


def test_a_stuck_running_stage_is_reconciled_on_read(client, repo_root):
    slug = _new(client)
    AmbienceBundle.open(repo_root / "ambience" / slug).set_stage("sound", "running")
    assert client.get(f"/api/ambience/{slug}").json["stages"]["sound"] == "pending"


def test_recover_hands_dead_ambience_jobs_to_the_releaser(cfg, runs_dir, repo_root):
    b = AmbienceBundle.create(repo_root / "ambience", "gone", 1, 1, {"scene": "x"})
    b.set_stage("sound", "running")
    d = runs_dir / "ambience-gone" / "20260101T000000-sound"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({
        "job_id": d.name, "slug": "ambience-gone", "stage": "sound", "status": "running",
        "pid": 999999999, "pipeline": "ambience", "ambience": "gone"}))
    server.recover(cfg)
    assert b.stage_status("sound") == "pending"
