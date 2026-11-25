"""ledger/ledger.py: schema, validation, pricing, CSV writes."""

from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import pytest

from ledger import ledger as L


def rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------- schema pins


def test_pipelines_pinned():
    assert L.PIPELINES == ("story", "ambience", "novella")


def test_cost_fields_pinned():
    assert L.COST_FIELDS == [
        "event_id", "timestamp", "run_id", "pipeline", "story_slug", "story_title",
        "series", "genre", "flavor", "arc_slug", "volume", "episode", "stage",
        "provider", "model", "unit_type", "units", "rate_per_unit", "cost_usd", "notes",
    ]
    assert len(set(L.COST_FIELDS)) == len(L.COST_FIELDS)


def test_revenue_fields_pinned():
    assert L.REVENUE_FIELDS == ["story_slug", "platform", "period", "video_id",
                                "views", "watch_hours", "revenue_usd",
                                "imported_at", "source"]


def test_default_paths_relative_to_cwd():
    assert L.COSTS_CSV == L.LEDGER_DIR / "costs.csv"
    assert L.REVENUE_CSV == L.LEDGER_DIR / "revenue.csv"
    assert L.RATES_JSON.name == "rates.json" and L.RATES_JSON.exists()


# ------------------------------------------------------------- price()


def test_price_per_million_scale():
    rate, cost = L.price("anthropic", "claude-sonnet-5", "input_tokens", 1_000_000)
    assert rate == pytest.approx(2.0e-6)
    assert cost == pytest.approx(2.0)


def test_price_scale_one_for_gemini_images():
    rate, cost = L.price("gemini", "gemini-3.1-flash-image", "images", 10)
    assert rate == pytest.approx(0.05) and cost == pytest.approx(0.5)


def test_price_elevenlabs_credits_per_thousand():
    rate, cost = L.price("elevenlabs", "sound_effects", "credits", 200)
    assert rate == pytest.approx(0.22 / 1000)
    assert cost == pytest.approx(0.044)


def test_price_zero_units_is_zero():
    assert L.price("polly", "neural", "characters", 0)[1] == 0


def test_price_custom_rates_dict():
    rates = {"providers": {"x": {"unit_scale": 10, "models": {"m": {"u": 5.0}}}}}
    assert L.price("x", "m", "u", 4, rates) == (0.5, 2.0)


def test_price_missing_unit_scale_defaults_to_one():
    rates = {"providers": {"x": {"models": {"m": {"u": 5.0}}}}}
    assert L.price("x", "m", "u", 2, rates) == (5.0, 10.0)


def test_price_unknown_provider_raises():
    with pytest.raises(KeyError, match="No rates for provider"):
        L.price("nope", "m", "u", 1)


def test_price_unknown_model_raises_and_lists_known():
    with pytest.raises(KeyError, match="No rates for model 'claude-nope'") as e:
        L.price("anthropic", "claude-nope", "input_tokens", 1)
    assert "claude-sonnet-5" in str(e.value)


def test_price_unknown_unit_raises():
    with pytest.raises(KeyError, match="No rate for unit"):
        L.price("anthropic", "claude-sonnet-5", "bananas", 1)


def test_load_rates_custom_path(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"providers": {}}))
    assert L.load_rates(p) == {"providers": {}}


# ------------------------------------------------------------- record_cost


def test_record_cost_writes_header_and_row(ldir, story):
    path = ldir / "costs.csv"
    row = L.record_cost(story=story, stage="write", provider="anthropic",
                        model="claude-sonnet-5", unit_type="input_tokens",
                        units=500_000, run_id="r1", notes="n", path=path)
    out = rows(path)
    assert len(out) == 1
    assert list(out[0].keys()) == L.COST_FIELDS
    r = out[0]
    assert r["pipeline"] == "story"
    assert r["story_slug"] == "amberlight-01"
    assert r["story_title"] == "The Curate"
    assert (r["series"], r["genre"], r["flavor"]) == ("Amberlight", "regency", "comedy")
    assert (r["arc_slug"], r["volume"], r["episode"]) == ("amberlight-vol-1", "1", "2")
    assert (r["stage"], r["provider"], r["model"]) == ("write", "anthropic", "claude-sonnet-5")
    assert r["unit_type"] == "input_tokens" and r["run_id"] == "r1" and r["notes"] == "n"
    assert float(r["cost_usd"]) == pytest.approx(1.0)
    assert r["cost_usd"] == "1.000000"
    assert r["rate_per_unit"] == "0.0000020000"
    assert row["cost_usd"] == "1.000000"


def test_record_cost_event_id_and_timestamp(ldir, story):
    path = ldir / "costs.csv"
    for _ in range(3):
        L.record_cost(story=story, stage="s", provider="polly", model="neural",
                      unit_type="characters", units=10, path=path)
    out = rows(path)
    ids = {r["event_id"] for r in out}
    assert len(ids) == 3 and all(len(i) == 12 for i in ids)
    assert out[0]["timestamp"].endswith("+00:00")


def test_record_cost_appends_never_rewrites(ldir, story):
    path = ldir / "costs.csv"
    kw = dict(story=story, stage="s", provider="polly", model="neural",
              unit_type="characters", path=path)
    L.record_cost(units=1, **kw)
    first = path.read_text()
    L.record_cost(units=2, **kw)
    assert path.read_text().startswith(first)
    assert len(rows(path)) == 2


@pytest.mark.parametrize("bad", [None, "", "podcast", "Story", "STORY"])
def test_record_cost_rejects_bad_pipeline(ldir, story, bad):
    path = ldir / "costs.csv"
    s = {**story, "pipeline": bad}
    with pytest.raises(ValueError, match="pipeline"):
        L.record_cost(story=s, stage="s", provider="polly", model="neural",
                      unit_type="characters", units=1, path=path)
    assert not path.exists()  # validation happens before anything is created


def test_record_cost_pipeline_never_defaulted(ldir, story):
    s = {k: v for k, v in story.items() if k != "pipeline"}
    with pytest.raises(ValueError, match="pipeline"):
        L.record_cost(story=s, stage="s", provider="polly", model="neural",
                      unit_type="characters", units=1, path=ldir / "c.csv")


@pytest.mark.parametrize("p", list(L.PIPELINES))
def test_record_cost_accepts_each_pipeline(ldir, story, p):
    row = L.record_cost(story={**story, "pipeline": p}, stage="s", provider="polly",
                        model="neural", unit_type="characters", units=1,
                        path=ldir / "c.csv")
    assert row["pipeline"] == p


def test_record_cost_other_dimensions_degrade_to_blank(ldir):
    path = ldir / "c.csv"
    L.record_cost(story={"pipeline": "ambience"}, stage="s", provider="polly",
                  model="neural", unit_type="characters", units=1, path=path)
    r = rows(path)[0]
    for k in ("story_slug", "story_title", "series", "genre", "flavor",
              "arc_slug", "volume", "episode"):
        assert r[k] == ""
    assert r["pipeline"] == "ambience"


def test_record_cost_unknown_model_raises_and_writes_no_row(ldir, story):
    path = ldir / "c.csv"
    with pytest.raises(KeyError):
        L.record_cost(story=story, stage="s", provider="anthropic", model="claude-x",
                      unit_type="input_tokens", units=1, path=path)
    assert not path.exists() or rows(path) == []


def test_record_cost_uses_module_default_path(ldir, story, monkeypatch, repo_root):
    target = repo_root / "elsewhere" / "costs.csv"
    monkeypatch.setattr(L, "COSTS_CSV", target)
    L.record_cost(story=story, stage="s", provider="polly", model="neural",
                  unit_type="characters", units=1)
    assert target.exists() and len(rows(target)) == 1


def test_record_cost_default_path_is_cwd_relative(story, repo_root):
    L.record_cost(story=story, stage="s", provider="polly", model="neural",
                  unit_type="characters", units=1)
    assert (repo_root / "ledger" / "costs.csv").exists()


def test_record_cost_custom_rates(ldir, story):
    rates = {"providers": {"x": {"models": {"m": {"u": 0.5}}}}}
    row = L.record_cost(story=story, stage="s", provider="x", model="m",
                        unit_type="u", units=4, rates=rates, path=ldir / "c.csv")
    assert row["cost_usd"] == "2.000000"


def test_record_cost_csv_quoting_of_notes(ldir, story):
    path = ldir / "c.csv"
    L.record_cost(story=story, stage="s", provider="polly", model="neural",
                  unit_type="characters", units=1, notes='a, "b"\nc', path=path)
    assert rows(path)[0]["notes"] == 'a, "b"\nc'


# ------------------------------------------------------------- helpers


def test_record_anthropic_one_row_per_token_class(ldir, story):
    path = ldir / "c.csv"
    out = L.record_anthropic(
        story=story, stage="write", model="claude-opus-5", path=path,
        usage={"input_tokens": 100, "output_tokens": 200,
               "cache_creation_input_tokens": 300, "cache_read_input_tokens": 400})
    assert [r["unit_type"] for r in out] == [
        "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"]
    assert [r["unit_type"] for r in rows(path)] == [r["unit_type"] for r in out]
    assert all(r["provider"] == "anthropic" for r in out)


def test_record_anthropic_skips_zero_classes(ldir, story):
    out = L.record_anthropic(story=story, stage="s", model="claude-opus-5",
                             path=ldir / "c.csv",
                             usage={"input_tokens": 5, "output_tokens": 0})
    assert [r["unit_type"] for r in out] == ["input_tokens"]


def test_record_anthropic_all_zero_writes_nothing(ldir, story):
    path = ldir / "c.csv"
    assert L.record_anthropic(story=story, stage="s", model="claude-opus-5",
                              usage={}, path=path) == []
    assert not path.exists()


def test_record_anthropic_accepts_sdk_object_with_none_cache(ldir, story):
    u = SimpleNamespace(input_tokens=10, output_tokens=20,
                        cache_creation_input_tokens=None, cache_read_input_tokens=None)
    out = L.record_anthropic(story=story, stage="s", model="claude-opus-5",
                             usage=u, path=ldir / "c.csv")
    assert [r["unit_type"] for r in out] == ["input_tokens", "output_tokens"]


def test_record_polly(ldir, story):
    r = L.record_polly(story=story, stage="narrate", engine="neural",
                       billed_characters=1_000_000, path=ldir / "c.csv")
    assert (r["provider"], r["model"], r["unit_type"]) == ("polly", "neural", "characters")
    assert float(r["cost_usd"]) == pytest.approx(16.0)


def test_record_elevenlabs(ldir, story):
    r = L.record_elevenlabs(story=story, stage="narrate", model="eleven_multilingual_v2",
                            credits=1000, path=ldir / "c.csv")
    assert r["unit_type"] == "credits" and float(r["cost_usd"]) == pytest.approx(0.22)


def test_record_gemini(ldir, story):
    r = L.record_gemini(story=story, stage="art", model="gemini-3.1-flash-image",
                        images=20, path=ldir / "c.csv")
    assert r["unit_type"] == "images" and float(r["cost_usd"]) == pytest.approx(1.0)


def test_helpers_validate_pipeline(ldir):
    with pytest.raises(ValueError):
        L.record_gemini(story={}, stage="s", model="gemini-3.1-flash-image",
                        images=1, path=ldir / "c.csv")


# ------------------------------------------------------------- revenue


def rev(slug="a", period="2026-09", **kw):
    return {"story_slug": slug, "platform": "youtube", "period": period,
            "revenue_usd": "1.00", **kw}


def test_upsert_revenue_insert_then_update(ldir):
    path = ldir / "revenue.csv"
    assert L.upsert_revenue([rev(), rev("b")], path) == (0, 2)
    assert L.upsert_revenue([rev(revenue_usd="9.99")], path) == (1, 0)
    out = {r["story_slug"]: r for r in rows(path)}
    assert out["a"]["revenue_usd"] == "9.99" and out["b"]["revenue_usd"] == "1.00"
    assert len(out) == 2
    assert out["a"]["imported_at"]


def test_upsert_revenue_key_includes_period_and_platform(ldir):
    path = ldir / "revenue.csv"
    L.upsert_revenue([rev(period="2026-08"), rev(period="2026-09"),
                      {**rev(), "platform": "other"}], path)
    assert len(rows(path)) == 3


def test_upsert_revenue_sorted_and_header(ldir):
    path = ldir / "revenue.csv"
    L.upsert_revenue([rev("z"), rev("a")], path)
    assert [r["story_slug"] for r in rows(path)] == ["a", "z"]
    assert list(rows(path)[0].keys()) == L.REVENUE_FIELDS


def test_upsert_revenue_leaves_no_tmp_files(ldir):
    L.upsert_revenue([rev()], ldir / "revenue.csv")
    assert not list(ldir.glob("*.tmp"))


def test_upsert_revenue_does_not_touch_costs(ldir, story):
    L.record_cost(story=story, stage="s", provider="polly", model="neural",
                  unit_type="characters", units=1, path=ldir / "costs.csv")
    before = (ldir / "costs.csv").read_text()
    L.upsert_revenue([rev()], ldir / "revenue.csv")
    assert (ldir / "costs.csv").read_text() == before


def test_upsert_revenue_empty_list(ldir):
    assert L.upsert_revenue([], ldir / "revenue.csv") == (0, 0)
    assert rows(ldir / "revenue.csv") == []
