"""ledger accepts the novella pipeline; config prices the novella models."""
import json

from ledger import ledger as L
from story_pipeline import config


def test_novella_is_a_pipeline():
    assert "novella" in L.PIPELINES


def test_novella_models_are_priced():
    rates = json.loads(L.RATES_JSON.read_text())["providers"]["anthropic"]["models"]
    for role in ("novella_writer", "novella_editor"):
        assert config.DEFAULTS["models"][role] in rates
        assert config.DEFAULTS["max_tokens"][role] > 0
