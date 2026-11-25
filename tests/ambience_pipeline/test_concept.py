"""The concept agent: prompt, validation and retry, revise, spend, CLI and API.

`llm.complete_json` is scripted (tests/agents/conftest.py's ScriptedLLM); the
network is blocked anyway. Bibles: amberlight via `real_bibles`, or none."""
from __future__ import annotations

import csv
import json

import pytest

from ambience_pipeline import bundle as ab
from ambience_pipeline import cli
from ambience_pipeline import concept as ca
from ambience_pipeline.bundle import AmbienceBundle
from ambience_pipeline.synth.recipes import REGISTRY
from story_pipeline import llm
from tests.agents.conftest import ScriptedLLM

from .conftest import layer

CFG = {"models": {"concept": "claude-sonnet-5"}, "max_tokens": {"concept": 1234},
       "bibles_dir": "bibles", "stories_dir": "stories"}


def good_reply(**over):
    r = {"scene": "Rain on a tall library window at night, a low fire.",
         "layers": [layer("rain_glass", "bed", "rain", -20.0, params={"surface": "glass"}),
                    layer("hearth", "texture", "fire", -27.0, params={"crackle": 0.8}),
                    layer("gust", "accent", "wind", -30.0, density_per_min=0.3)],
         "notes": "no clock: no recipe makes one"}
    r.update(over)
    return r


@pytest.fixture
def scripted(monkeypatch):
    s = ScriptedLLM()
    monkeypatch.setattr(llm, "complete_json", s.complete_json)
    monkeypatch.setattr(llm, "complete", s.complete)
    return s


@pytest.fixture
def bundle(repo_root):
    return cli.create(CFG, scene="rain on a library window, a low fire", seed=5)


def ledger_rows(repo_root):
    path = repo_root / "ledger" / "costs.csv"
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #


def test_prompt_lists_every_recipe_with_params_defaults_and_ranges():
    sp = ca.system_prompt()
    assert not any(f"{{{k}}}" in sp for k in ("recipes", "roles", "conventions"))
    for name, r in REGISTRY.items():
        assert f"**{name}**" in sp and r.summary in sp
        for k, default in r.params.items():
            if k == "gain_db":
                continue
            assert f"`{k}`: default {ca._fmt(default)}" in sp, (name, k)
            rng = r.ranges[k]
            if isinstance(rng[0], str):
                assert all(f'"{x}"' in sp for x in rng)
            else:
                assert f"{ca._fmt(rng[0])} to {ca._fmt(rng[1])}" in sp
    for role in ab.ROLES:
        assert f"**{role}**" in sp
    assert "Between -48 and -10" in sp and "blocked on commercial rights" in sp
    assert f"At most {ab.MAX_LAYERS} layers" in sp


def test_every_recipe_param_has_a_range():
    for name, r in REGISTRY.items():
        assert set(r.ranges) == set(r.params) - {"gain_db"}, name


def test_user_message_carries_brief_hours_and_bible(repo_root, real_bibles):
    b = cli.create(CFG, scene="rain on the library window", bible="amberlight", hours=3, seed=1)
    msg = ca.user_message(b, CFG)
    assert "Length: 3 hours" in msg and "×0.8" in msg
    assert "rain on the library window" in msg and "## Series bible" in msg


def test_a_bible_that_will_not_load_is_skipped(bundle, capsys):
    bundle.pin("bible", "no-such-bible")
    assert "## Series bible" not in ca.user_message(bundle, CFG)
    assert "skipping bible 'no-such-bible'" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Drafting, validation, retry
# --------------------------------------------------------------------------- #


def test_a_valid_reply_becomes_concept_json(bundle, scripted):
    scripted.json_queue = [good_reply()]
    c = ca.draft(bundle, CFG)
    (call,) = scripted.json_calls
    assert call["model"] == "claude-sonnet-5" and call["max_tokens"] == 1234
    assert "rain on a library window" in call["user"]
    assert c == bundle.concept()
    assert ab.validate_concept(c) == [] and "starter" not in c
    assert c["seed"] == 5 and c["slug"] == bundle.slug and c["hours"] == 1
    assert [L["id"] for L in c["layers"]] == ["rain_glass", "hearth", "gust"]
    assert c["notes"].startswith("no clock")
    assert bundle.stage_status("concept") == "awaiting_review"
    # The starter it replaced is kept.
    assert [p.name for p in ca.history(bundle)] == ["concept.1.json"]
    assert json.loads((bundle.root / "concept.1.json").read_text())["starter"] is True


def test_the_model_cannot_overwrite_the_pins(bundle, scripted):
    scripted.json_queue = [good_reply(seed=99, hours=8, slug="other", title="X")]
    c = ca.draft(bundle, CFG)
    assert (c["seed"], c["hours"], c["slug"]) == (5, 1, bundle.slug)
    assert c["title"] == bundle.pinned("title")


def test_an_invalid_reply_is_retried_once_with_its_problems(bundle, scripted, capsys):
    bad = good_reply(layers=[layer("gust", "accent", "wind", -30.0)])   # no bed, no density
    scripted.json_queue = [bad, good_reply()]
    c = ca.draft(bundle, CFG)
    assert len(scripted.json_calls) == 2
    retry = scripted.json_calls[1]["user"]
    assert "It was rejected for these problems" in retry
    assert "needs at least one bed layer" in retry and "density_per_min" in retry
    assert len(c["layers"]) == 3
    assert "asking once more" in capsys.readouterr().out


def test_an_out_of_range_param_is_a_problem_for_the_agent(bundle, scripted):
    loud = good_reply()
    loud["layers"][0]["recipe"]["intensity"] = 4.0
    scripted.json_queue = [loud, good_reply()]
    ca.draft(bundle, CFG)
    assert "rain.intensity 4 is outside 0.05 to 1" in scripted.json_calls[1]["user"]


def test_generated_layers_are_refused(bundle, scripted):
    gen = good_reply()
    gen["layers"].append({"id": "chime", "role": "accent", "source": "generated",
                          "prompt": "a clock chime", "gain_db": -30, "density_per_min": 0.1})
    scripted.json_queue = [gen, gen]
    with pytest.raises(ca.ConceptError, match="generated layers are not built"):
        ca.draft(bundle, CFG)


def test_two_bad_replies_fail_clearly_and_change_nothing(bundle, scripted, repo_root):
    before = bundle.concept_path.read_text()
    scripted.json_queue = [{"layers": []}, {"nonsense": True}]
    with pytest.raises(ca.ConceptError) as e:
        ca.draft(bundle, CFG)
    assert "still not renderable after one retry" in str(e.value)
    assert "layers must be a non-empty list" in e.value.problems
    assert bundle.concept_path.read_text() == before
    assert ca.history(bundle) == []
    assert bundle.stage_status("concept") == "awaiting_review"
    # Both calls were paid for, so both are recorded.
    assert bundle.manifest()["spend"]["anthropic_output_tokens"] == 100
    assert len(ledger_rows(repo_root)) == 4


def test_an_llm_error_puts_the_stage_back(bundle, scripted):
    scripted.json_queue = [ValueError("no JSON object in model reply")]
    with pytest.raises(ValueError):
        ca.draft(bundle, CFG)
    assert bundle.stage_status("concept") == "awaiting_review"


# --------------------------------------------------------------------------- #
# Spend
# --------------------------------------------------------------------------- #


def test_spend_goes_to_the_manifest_and_the_ledger(bundle, scripted, repo_root):
    scripted.json_queue = [good_reply()]
    ca.draft(bundle, CFG)
    spend = bundle.manifest()["spend"]
    assert spend["anthropic_input_tokens"] == 100 and spend["anthropic_output_tokens"] == 50
    rows = ledger_rows(repo_root)
    assert {(r["pipeline"], r["stage"], r["model"], r["unit_type"]) for r in rows} == {
        ("ambience", "concept", "claude-sonnet-5", "input_tokens"),
        ("ambience", "concept", "claude-sonnet-5", "output_tokens")}
    assert all(r["story_slug"] == bundle.slug and r["genre"] == "" for r in rows)
    assert float(rows[0]["cost_usd"]) > 0


def test_estimate_is_a_small_positive_number():
    usd = ca.estimate_usd(CFG)
    assert usd is not None and 0 < usd < 0.5
    assert ca.estimate_usd({"models": {"concept": "no-such-model"}}) is None


# --------------------------------------------------------------------------- #
# Revise
# --------------------------------------------------------------------------- #


def test_revise_keeps_history_and_unapproves(bundle, scripted):
    scripted.json_queue = [good_reply()]
    ca.draft(bundle, CFG)
    bundle.approve()
    assert bundle.concept_approved()
    first = bundle.concept()

    windier = good_reply()
    windier["layers"][2]["density_per_min"] = 0.8
    scripted.json_queue = [windier]
    ca.draft(bundle, CFG, note="more wind", revise=True)
    user = scripted.json_calls[-1]["user"]
    assert "## Revise it" in user and "more wind" in user and '"rain_glass"' in user
    assert [p.name for p in ca.history(bundle)] == ["concept.1.json", "concept.2.json"]
    assert json.loads((bundle.root / "concept.2.json").read_text()) == first
    assert bundle.concept()["layers"][2]["density_per_min"] == 0.8
    assert bundle.stage_status("concept") == "awaiting_review"
    assert not bundle.concept_approved() and "concept_approved" not in bundle.manifest()
    assert bundle.stage_info("concept")["note"] == "more wind"


def test_revise_needs_a_note(bundle, scripted):
    with pytest.raises(ca.ConceptError, match="needs a note"):
        ca.draft(bundle, CFG, note="  ", revise=True)
    assert scripted.json_calls == []


def test_even_an_identical_revision_needs_approving_again(bundle, scripted):
    scripted.json_queue = [good_reply(), good_reply()]
    ca.draft(bundle, CFG)
    bundle.approve()
    ca.draft(bundle, CFG, note="keep it", revise=True)
    assert not bundle.concept_approved()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def run(capsys, *argv):
    rc = cli.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out + out.err


def only_bundle(repo_root):
    (root,) = ab.bundle_roots(repo_root / "ambience")
    return AmbienceBundle.open(root)


def test_cli_new_runs_the_agent_by_default(repo_root, scripted, capsys, monkeypatch):
    monkeypatch.setenv("MW_PROGRESS", "1")
    scripted.json_queue = [good_reply()]
    rc, out = run(capsys, "new", "--scene", "rain on a library window", "--seed", "4",
                  "--no-stream")
    assert rc == 0, out
    assert len(scripted.json_calls) == 1
    b = only_bundle(repo_root)
    assert "starter" not in b.concept() and "drafted" in out
    assert "rain_glass" in out and "hearth" in out and "notes: no clock" in out
    marks = [json.loads(l.split(" ", 1)[1]) for l in out.splitlines()
             if l.startswith("::progress ")]
    assert [m["event"] for m in marks] == ["created", "plan", "block", "done"]


def test_cli_new_starter_makes_no_call(repo_root, scripted, capsys):
    rc, out = run(capsys, "new", "--scene", "rain on a library window", "--starter")
    assert rc == 0 and scripted.json_calls == [] and "no call" in out
    assert only_bundle(repo_root).concept()["starter"] is True


def test_cli_new_keeps_the_starter_when_the_agent_fails(repo_root, scripted, capsys):
    scripted.json_queue = [{"layers": []}, {"layers": []}]
    rc, out = run(capsys, "new", "--scene", "rain on a library window")
    assert rc == 1 and "not renderable after one retry" in out
    assert "starter layers are still in" in out and "cli draft" in out
    b = only_bundle(repo_root)
    assert b.concept()["starter"] is True and ab.validate_concept(b.concept()) == []


def test_cli_new_without_a_key_says_so(repo_root, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(llm, "_client", None)
    rc, out = run(capsys, "new", "--scene", "rain")
    assert rc == 1 and "ANTHROPIC_API_KEY is not set" in out


def test_cli_revise_and_draft(repo_root, scripted, capsys):
    run(capsys, "new", "--scene", "rain on a library window", "--starter")
    b = only_bundle(repo_root)
    scripted.json_queue = [good_reply()]
    rc, out = run(capsys, "draft", str(b.root))
    assert rc == 0 and "kept the previous as concept.1.json" in out
    rc, out = run(capsys, "approve", str(b.root))
    assert rc == 0 and b.concept_approved()
    scripted.json_queue = [good_reply()]
    rc, out = run(capsys, "revise", str(b.root), "--note", "softer fire")
    assert rc == 0 and "revised" in out and "concept.2.json" in out
    assert not b.concept_approved()
    assert "softer fire" in scripted.json_calls[-1]["user"]
    rc, out = run(capsys, "revise", str(b.root), "--note", " ")
    assert rc == 1 and "--note is required" in out


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_api_new_starts_a_concept_job(client, started):
    r = client.post("/api/ambience/new", {"scene": "rain on a window", "hours": 1})
    assert r.status == 200, r.text
    slug = r.json["slug"]
    (call,) = started
    assert call["key"] == f"ambience-{slug}" and call["stage"] == "concept"
    assert call["argv"][1:4] == ["-m", "ambience_pipeline.cli", "draft"]
    assert call["argv"][4] == f"ambience/{slug}"
    assert call["extra"]["pipeline"] == "ambience" and call["extra"]["ambience"] == slug
    # The starter layers hold the place until the reply lands.
    assert r.json["ambience"]["concept"]["starter"] is True and "job" in r.json


def test_api_new_starter_starts_nothing(client, started):
    r = client.post("/api/ambience/new", {"scene": "rain", "starter": True})
    assert r.status == 200 and started == [] and "job" not in r.json


def test_api_index_prices_the_call(client):
    j = client.get("/api/ambience").json
    assert j["concept_model"] and j["concept_estimate_usd"] > 0
    rain = next(r for r in j["recipes"] if r["name"] == "rain")
    assert rain["ranges"]["surface"] == ["glass", "canvas", "leaves", "roof"]


def test_api_revise_and_draft_start_jobs(client, started):
    slug = client.post("/api/ambience/new", {"scene": "rain", "starter": True}).json["slug"]
    r = client.post(f"/api/ambience/{slug}/revise", {"note": "more wind"})
    assert r.status == 200, r.text
    r = client.post(f"/api/ambience/{slug}/draft", {})
    assert r.status == 200, r.text
    rev, dr = started
    assert rev["argv"][3:] == ["revise", f"ambience/{slug}", "--note", "more wind"]
    assert rev["stage"] == "concept" and rev["extra"]["revise"] is True
    assert dr["argv"][3:] == ["draft", f"ambience/{slug}"]
    r = client.post(f"/api/ambience/{slug}/revise", {"note": "  "})
    assert r.status == 400 and "Say what to change" in r.json["error"]
    assert client.post("/api/ambience/nope/revise", {"note": "x"}).status == 404


def test_release_and_reconcile_put_a_dead_concept_job_back(repo_root, cfg, client):
    b = cli.create(cfg, scene="rain", seed=1)
    b.set_stage("concept", "running")
    from story_pipeline.review import ambience_api
    ambience_api.release(cfg, {"ambience": b.slug, "stage": "concept", "pipeline": "ambience"})
    assert b.stage_status("concept") == "awaiting_review"
    b.set_stage("concept", "running")
    assert client.get(f"/api/ambience/{b.slug}").json["stages"]["concept"] == "awaiting_review"
