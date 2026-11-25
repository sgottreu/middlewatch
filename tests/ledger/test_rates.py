"""Every model the pipeline actually runs must be priceable (price() raises on a
drifted key). Reads models out of config.py DEFAULTS and config.example.yaml,
never config.yaml."""

from __future__ import annotations

import ast
import json

import pytest
import yaml

from ledger import ledger as L
from tests.conftest import REPO_ROOT

ANTHROPIC_UNITS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")


def _defaults() -> dict:
    """Parse DEFAULTS statically, as the snippet in docs/ambience/pipeline-design.md does."""
    tree = ast.parse((REPO_ROOT / "story_pipeline" / "config.py").read_text())
    return next(ast.literal_eval(n.value) for n in ast.walk(tree)
                if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "DEFAULTS")


def _priced_cases(d: dict):
    for m in sorted(set(d["models"].values())):
        for u in ANTHROPIC_UNITS:
            yield ("anthropic", m, u)
    yield ("gemini", d["gemini"]["model"], "images")
    yield ("elevenlabs", d["elevenlabs"]["model"], "credits")
    yield ("elevenlabs", "sound_effects", "credits")
    yield ("polly", d["polly"]["engine"], "characters")


@pytest.fixture(scope="module")
def defaults():
    return _defaults()


@pytest.fixture(scope="module")
def example_cfg():
    return yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text())


def test_every_default_model_is_priced(defaults):
    cases = list(_priced_cases(defaults))
    assert len(cases) >= 8
    for prov, model, unit in cases:
        rate, cost = L.price(prov, model, unit, 1)
        assert rate > 0 and cost == pytest.approx(rate), (prov, model, unit)


def test_ambience_sfx_model_is_priced():
    assert L.price("elevenlabs", "sound_effects", "credits", 1)[0] > 0


def test_every_polly_engine_priced():
    for engine in ("standard", "neural", "generative"):
        assert L.price("polly", engine, "characters", 1)[0] > 0


def test_example_config_models_are_priced(example_cfg):
    for m in set(example_cfg["models"].values()):
        for u in ANTHROPIC_UNITS:
            L.price("anthropic", m, u, 1)
    L.price("gemini", example_cfg["gemini"]["model"], "images", 1)
    L.price("elevenlabs", example_cfg["elevenlabs"]["model"], "credits", 1)


def test_example_config_matches_default_models(defaults, example_cfg):
    assert example_cfg["models"]["writer"] == defaults["models"]["writer"]
    assert example_cfg["gemini"]["model"] == defaults["gemini"]["model"]


def test_gemini_rate_keyed_to_image_size(defaults, example_cfg):
    """rates.json holds the 2K figure; changing image_size means changing the rate."""
    rate = L.price("gemini", defaults["gemini"]["model"], "images", 1)[1]
    assert defaults["gemini"]["image_size"] == "2K"
    assert example_cfg["gemini"]["image_size"] == "2K"
    assert rate == pytest.approx(0.050)
    # The dry-run estimate and the ledger must agree.
    assert defaults["gemini"]["usd_per_image"] == pytest.approx(rate)
    note = json.loads(L.RATES_JSON.read_text())["providers"]["gemini"]["_comment"]
    assert "2K" in note and "image_size" in note


def test_elevenlabs_rate_agrees_with_config(defaults):
    """Two views of one number (rates.json comment says both must change together)."""
    per_credit = L.price("elevenlabs", "eleven_multilingual_v2", "credits", 1000)[1]
    assert per_credit == pytest.approx(defaults["elevenlabs"]["usd_per_1k_credits"])


def test_every_elevenlabs_model_same_rate():
    models = L.load_rates()["providers"]["elevenlabs"]["models"]
    assert {m["credits"] for m in models.values()} == {0.22}


def test_rates_file_structure():
    r = L.load_rates()
    assert r["rates_verified"]
    assert set(r["providers"]) == {"anthropic", "polly", "gemini", "elevenlabs"}
    for prov in r["providers"].values():
        assert prov["unit_scale"] > 0
        for model_rates in prov["models"].values():
            assert model_rates and all(v > 0 for v in model_rates.values())
    for name, prov in r["providers"].items():
        if name == "anthropic":
            for m in prov["models"].values():
                assert set(m) == set(ANTHROPIC_UNITS)


def test_anthropic_cache_pricing_relationships():
    """5-minute cache write is 1.25x base input; cache read is 0.1x."""
    for name, m in L.load_rates()["providers"]["anthropic"]["models"].items():
        assert m["cache_write_tokens"] == pytest.approx(1.25 * m["input_tokens"]), name
        assert m["cache_read_tokens"] == pytest.approx(0.1 * m["input_tokens"]), name


def test_rates_json_in_scratch_root_matches_real(repo_root):
    assert (repo_root / "ledger" / "rates.json").read_text() == L.RATES_JSON.read_text()


def test_check_rates_draft_imports_harmlessly():
    """Smoke import only: the module is a session draft with no import-time side effects."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_rates_draft", REPO_ROOT / "ledger" / "check_rates-2026-11-14.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.CHECKS
