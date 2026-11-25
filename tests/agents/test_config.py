"""story_pipeline.config: defaults merge, voices.yaml, .env, prompt_text."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
import yaml

from story_pipeline import config
from tests.conftest import REPO_ROOT

PROMPTS = REPO_ROOT / "story_pipeline" / "prompts"
PROMPT_NAMES = sorted(p.stem for p in PROMPTS.glob("*.md"))


# --------------------------------------------------------------------------- #
# load()
# --------------------------------------------------------------------------- #


def test_load_without_config_file_returns_defaults(repo_root):
    cfg = config.load()
    assert cfg["stories_dir"] == "stories"
    assert cfg["bibles_dir"] == "bibles"
    assert cfg["length_min"] == 15
    assert cfg["models"]["writer"] == config.DEFAULTS["models"]["writer"]
    assert cfg["max_edit_passes"] == 2
    assert cfg["casting"]["mode"] == "solo"


def test_load_shallow_override_and_deep_merge(repo_root):
    (repo_root / "config.yaml").write_text(yaml.safe_dump({
        "length_min": 30,
        "models": {"writer": "my-writer"},
        "casting": {"gap_ms": 111},
    }))
    cfg = config.load()
    assert cfg["length_min"] == 30
    assert cfg["models"]["writer"] == "my-writer"
    # siblings in the nested dict survive
    assert cfg["models"]["ideator"] == config.DEFAULTS["models"]["ideator"]
    assert cfg["casting"]["gap_ms"] == 111
    assert cfg["casting"]["break_ms"] == config.DEFAULTS["casting"]["break_ms"]
    assert cfg["casting"]["character_voices"]["male"] == ["Daniel", "George", "Brian"]


def test_load_explicit_path(repo_root):
    p = repo_root / "other.yaml"
    p.write_text("trope_sample: 3\n")
    assert config.load(p)["trope_sample"] == 3
    assert config.load(str(p))["trope_sample"] == 3


def test_load_empty_yaml_file_is_ok(repo_root):
    (repo_root / "config.yaml").write_text("")
    assert config.load()["length_min"] == 15


def test_load_null_override_replaces_value(repo_root):
    (repo_root / "config.yaml").write_text("casting:\n  solo_voice: Rachel\n")
    assert config.load()["casting"]["solo_voice"] == "Rachel"


def test_load_does_not_mutate_defaults(repo_root):
    before = copy.deepcopy(config.DEFAULTS)
    (repo_root / "config.yaml").write_text("models:\n  writer: x\n")
    config.load()
    assert config.DEFAULTS == before


def test_load_returns_independent_nested_dicts(repo_root):
    original = config.DEFAULTS["models"]["writer"]
    try:
        a = config.load()
        a["models"]["writer"] = "mutated"
        assert config.load()["models"]["writer"] == original
    finally:
        config.DEFAULTS["models"]["writer"] = original


# --------------------------------------------------------------------------- #
# voices.yaml
# --------------------------------------------------------------------------- #


def test_voices_yaml_is_merged_into_elevenlabs_library(repo_root):
    (repo_root / "voices.yaml").write_text(yaml.safe_dump(
        {"voice_library": {"Zed": "id-zed", "Charlotte": "override-id"}}))
    lib = config.load()["elevenlabs"]["voice_library"]
    assert lib["Zed"] == "id-zed"
    assert lib["Charlotte"] == "override-id"        # voices.yaml beats DEFAULTS fallback
    assert lib["Rachel"] == config.DEFAULTS["elevenlabs"]["voice_library"]["Rachel"]


def test_real_voices_yaml_loads_and_covers_default_casting(repo_root):
    cfg = config.load()
    lib = cfg["elevenlabs"]["voice_library"]
    assert lib and all(isinstance(v, str) and v for v in lib.values())
    assert cfg["casting"]["narrator"] in lib


def test_missing_voices_file_falls_back_to_defaults(repo_root):
    (repo_root / "voices.yaml").unlink()
    cfg = config.load()
    assert cfg["elevenlabs"]["voice_library"] == config.DEFAULTS["elevenlabs"]["voice_library"]


def test_voices_yaml_without_mapping_raises(repo_root):
    (repo_root / "voices.yaml").write_text("something_else: 1\n")
    with pytest.raises(RuntimeError, match="no `voice_library:` mapping"):
        config.load()


def test_voices_yaml_with_list_library_raises(repo_root):
    (repo_root / "voices.yaml").write_text("voice_library: [a, b]\n")
    with pytest.raises(RuntimeError, match="voice_library"):
        config.load()


def test_empty_voices_yaml_raises(repo_root):
    (repo_root / "voices.yaml").write_text("")
    with pytest.raises(RuntimeError):
        config.load()


def test_config_yaml_must_not_also_hold_voice_library(repo_root):
    (repo_root / "config.yaml").write_text(
        "elevenlabs:\n  voice_library:\n    Zed: id\n")
    with pytest.raises(RuntimeError, match="in both"):
        config.load()


def test_config_voice_library_ok_when_no_voices_yaml(repo_root):
    (repo_root / "voices.yaml").unlink()
    (repo_root / "config.yaml").write_text(
        "elevenlabs:\n  voice_library:\n    Zed: id\n")
    lib = config.load()["elevenlabs"]["voice_library"]
    assert lib["Zed"] == "id"
    assert lib["Charlotte"] == config.DEFAULTS["elevenlabs"]["voice_library"]["Charlotte"]


def test_voices_file_key_redirects_library(repo_root):
    (repo_root / "alt-voices.yaml").write_text(
        yaml.safe_dump({"voice_library": {"Only": "x"}}))
    (repo_root / "config.yaml").write_text("voices_file: alt-voices.yaml\n")
    assert config.load()["elevenlabs"]["voice_library"]["Only"] == "x"


def test_voices_path_default_and_override():
    assert config.voices_path() == Path("voices.yaml")
    assert config.voices_path({"voices_file": "x.yaml"}) == Path("x.yaml")
    assert config.voices_path({"voices_file": None}) == Path("voices.yaml")


# --------------------------------------------------------------------------- #
# load_env / require_env
# --------------------------------------------------------------------------- #


def test_load_env_missing_file_returns_empty(repo_root):
    assert config.load_env() == []


def test_load_env_parses_forms(repo_root, monkeypatch):
    for k in ("MW_A", "MW_B", "MW_C", "MW_D", "MW_E", "MW_F"):
        monkeypatch.delenv(k, raising=False)
    (repo_root / ".env").write_text(
        "# comment\n"
        "\n"
        "MW_A=plain\n"
        "export MW_B=exported\n"
        'MW_C="double quoted"\n'
        "MW_D='single quoted'\n"
        "  MW_E = spaced \n"
        "not a pair\n"
        "MW_F=a=b=c\n"
    )
    loaded = config.load_env()
    assert loaded == ["MW_A", "MW_B", "MW_C", "MW_D", "MW_E", "MW_F"]
    assert os.environ["MW_A"] == "plain"
    assert os.environ["MW_B"] == "exported"
    assert os.environ["MW_C"] == "double quoted"
    assert os.environ["MW_D"] == "single quoted"
    assert os.environ["MW_E"] == "spaced"
    assert os.environ["MW_F"] == "a=b=c"
    for k in loaded:
        monkeypatch.delenv(k)


def test_load_env_does_not_override_exported(repo_root, monkeypatch):
    monkeypatch.setenv("MW_X", "shell")
    (repo_root / ".env").write_text("MW_X=file\nMW_Y=file\n")
    monkeypatch.delenv("MW_Y", raising=False)
    assert config.load_env() == ["MW_Y"]
    assert os.environ["MW_X"] == "shell"
    monkeypatch.delenv("MW_Y")


def test_load_env_mismatched_quotes_kept_and_empty_key_skipped(repo_root, monkeypatch):
    monkeypatch.delenv("MW_Q", raising=False)
    (repo_root / ".env").write_text("MW_Q=\"oops'\n=novalue\n")
    assert config.load_env() == ["MW_Q"]
    assert os.environ["MW_Q"] == "\"oops'"
    monkeypatch.delenv("MW_Q")


def test_load_env_custom_path(repo_root, monkeypatch):
    monkeypatch.delenv("MW_P", raising=False)
    (repo_root / "custom.env").write_text("MW_P=1\n")
    assert config.load_env("custom.env") == ["MW_P"]
    monkeypatch.delenv("MW_P")


def test_require_env_returns_value_or_raises(monkeypatch):
    monkeypatch.setenv("MW_REQ", "v")
    assert config.require_env("MW_REQ") == "v"
    monkeypatch.delenv("MW_REQ")
    with pytest.raises(RuntimeError, match="MW_REQ is not set"):
        config.require_env("MW_REQ")
    monkeypatch.setenv("MW_REQ", "")
    with pytest.raises(RuntimeError):
        config.require_env("MW_REQ")


# --------------------------------------------------------------------------- #
# prompt_text
# --------------------------------------------------------------------------- #


def test_prompt_dir_is_not_empty():
    assert {"house", "ideator", "writer", "editor", "designer", "boundaries",
            "ai-tells", "arc"} <= set(PROMPT_NAMES)


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_every_prompt_loads_non_empty(name):
    assert config.prompt_text(name).strip()


def test_prompt_text_unknown_raises():
    with pytest.raises(FileNotFoundError):
        config.prompt_text("no-such-prompt")


def test_prompt_text_resolves_independent_of_cwd(repo_root):
    # repo_root is an empty tmp dir with no prompts; the package's are used
    assert not (repo_root / "prompts").exists()
    assert "{structure}" in config.prompt_text("house")


def test_defaults_models_and_ceilings_cover_same_agents():
    assert set(config.DEFAULTS["models"]) == set(config.DEFAULTS["max_tokens"])
