"""The novella outliner: prompt, validator, retry, applying an outline to the
bundle, versions and spend. story_pipeline.llm.complete is faked (FakeLLM);
complete_json runs for real on top of it, so replies are JSON text."""
import copy
import csv
import json

import pytest

from novella_pipeline import outliner
from novella_pipeline.bundle import Bundle, NovellaError
from novella_pipeline.workflow import Stage

from .conftest import BIBLE


def make_outline(n=12, words=2500, beats=3, cast=None):
    return {
        "title": "Glasswood",
        "logline": "A warden must walk the road that remembers him.",
        "pov": "close third, past tense",
        "cast": cast if cast is not None else [
            {"name": "Garret Vane", "role": "protagonist", "recurring": True,
             "note": "hears the road"},
            {"name": "Mira Thorn", "role": "ally", "recurring": False, "note": "a ferrier"}],
        "chapters": [{"title": f"Chapter {i}", "target_words": words,
                      "beats": [f"beat {i}.{j}" for j in range(1, beats + 1)]}
                     for i in range(1, n + 1)],
    }


def reply(o):
    return json.dumps(o)


@pytest.fixture
def ideas(book, repo_root):
    """The fake bible with a Story Ideas section (ideator-only)."""
    p = repo_root / "bibles" / "glass" / "glass.md"
    p.write_text(BIBLE + "\n## Story Ideas\n\n- IDEA-MARKER: a bridge that sings.\n")
    return p


def ledger_rows(repo_root):
    p = repo_root / "ledger" / "costs.csv"
    return list(csv.DictReader(p.open())) if p.exists() else []


# --------------------------------------------------------------- prompt

def test_prompt_has_brief_bible_ideas_and_target(cfg, book, ideas, fake_llm):
    fake_llm.replies = [reply(make_outline())]
    outliner.outline(cfg, book, note="NOTE-MARKER")
    call = fake_llm.calls[0]
    assert call["model"] == cfg["models"]["novella_outliner"]
    assert call["max_tokens"] == cfg["max_tokens"]["novella_outliner"]
    u = call["user"]
    assert "BRIEF-MARKER" in u and "OUTLINE-MARKER" in u and "NOTE-MARKER" in u
    assert "Garret Vane" in u and "glass road" in u
    assert "IDEA-MARKER" in u                       # Story Ideas reach the outliner
    assert "SECRET-WORKING-NOTE" not in u           # working-only sections never do
    assert "30,000 words" in u and "10 to 16 chapters" in u
    s = call["system"]
    assert "Novella outliner" in s and "{genre_guide}" not in s and "{structure}" not in s


def test_template_outline_is_not_sent_as_notes(cfg, book, fake_llm):
    book.outline_path.write_text("# Glasswood: Outline\n")
    fake_llm.replies = [reply(make_outline())]
    outliner.outline(cfg, book)
    assert "<outline_notes>" not in fake_llm.calls[0]["user"]


def test_empty_brief_refused_before_spend(cfg, book, fake_llm):
    book.brief_path.write_text("# Glasswood: Story Brief\n\n## Premise\n")
    with pytest.raises(NovellaError, match="brief"):
        outliner.outline(cfg, book)
    assert fake_llm.calls == []


def test_missing_bible_refused_before_spend(cfg, book, fake_llm, repo_root):
    (repo_root / "bibles" / "glass" / "glass.md").rename(repo_root / "gone.md")
    with pytest.raises(Exception):
        outliner.outline(cfg, book)
    assert fake_llm.calls == []


# --------------------------------------------------------------- apply

def test_valid_reply_writes_outline_and_chapters(cfg, book, fake_llm):
    o = make_outline()
    o["chapters"][1]["pov"] = "Mira Thorn"
    fake_llm.replies = [reply(o)]
    res = outliner.outline(cfg, book)
    assert res.attempts == 1 and res.written == list(range(1, 13)) and res.skipped == []
    text = book.outline_path.read_text()
    assert text.startswith("# Glasswood: Outline") and "### 12. Chapter 12" in text
    assert "**Garret Vane** (protagonist, recurring)" in text and "30,000 words" in text
    assert len(book.chapters()) == 12
    assert book.chapter(1)["title"] == "Chapter 1"      # replaced the hand-added title
    assert book.chapter(5)["target_words"] == 2500
    assert book.beats(1) == "- beat 1.1\n- beat 1.2\n- beat 1.3\n"
    assert book.beats(2).startswith("POV: Mira Thorn\n\n- beat 2.1")
    assert all(c["stage"] == "outlined" for c in book.chapters())
    assert book.manifest()["outline_version"] == res.version


def test_chapters_past_outlined_untouched(cfg, book, fake_llm):
    book.save_plan(2, "Two", 2500, "- my beats")
    book.save_plan(3, "Three", 2500, "- three")
    book.save_draft(1, text="x", summary="", new_details="", flags="")
    book.save_draft(2, text="y", summary="", new_details="", flags="")
    book.save_edit(2, "y edited")
    before = {n: (book.chapter(n), book.beats(n)) for n in (1, 2)}
    fake_llm.replies = [reply(make_outline())]
    res = outliner.outline(cfg, book)
    assert res.skipped == [1, 2] and 1 not in res.written and 3 in res.written
    for n in (1, 2):
        assert (book.chapter(n), book.beats(n)) == before[n]
    assert book.chapter(3)["title"] == "Chapter 3"


def test_extra_chapters_reported_not_deleted(cfg, book, fake_llm):
    book.save_plan(15, "Fifteen", 2500, "- keep")
    fake_llm.replies = [reply(make_outline(n=12))]
    res = outliner.outline(cfg, book)
    assert res.extra == [15] and book.beats(15) == "- keep\n"


def test_previous_outline_versioned(cfg, book, fake_llm):
    fake_llm.replies = [reply(make_outline()), reply(make_outline(n=10, words=3000))]
    r1 = outliner.outline(cfg, book)
    # The hand-written outline.md was kept first, then the generated one.
    vs = book.outline_versions()
    assert [v["source"] for v in vs] == ["edited", "outliner"] and r1.version == 2
    assert "OUTLINE-MARKER" in book.outline_version(1)
    first = book.outline_path.read_text()
    r2 = outliner.outline(cfg, book, note="shorter")
    # Unchanged since v2, so not saved again; v3 is the new outline.
    assert r2.version == 3 and [v["version"] for v in book.outline_versions()] == [1, 2, 3]
    assert book.outline_version(2) == first
    assert book.outline_versions()[-1]["note"] == "shorter"
    assert book.outline_versions()[-1]["chapters"] == 10
    # Scott edits outline.md; the next run keeps his edit as its own version.
    book.outline_path.write_text(first + "\nMY EDIT\n")
    fake_llm.replies = [reply(make_outline())]
    r3 = outliner.outline(cfg, book)
    assert r3.version == 5 and "MY EDIT" in book.outline_version(4)


def test_spend_recorded(cfg, book, fake_llm, repo_root):
    fake_llm.replies = [reply(make_outline())]
    outliner.outline(cfg, book)
    sp = book.manifest()["spend"]
    assert sp["anthropic_input_tokens"] == 1000 and sp["anthropic_output_tokens"] == 500
    rows = ledger_rows(repo_root)
    assert rows
    for row in rows:
        assert row["pipeline"] == "novella" and row["stage"] == "outline"
        assert row["story_slug"] == "glasswood" and row["model"] == cfg["models"]["novella_outliner"]
        assert row["notes"] == "attempt 1"


def test_streams_on_text(cfg, book, fake_llm):
    fake_llm.replies = [reply(make_outline())]
    got = []
    outliner.outline(cfg, book, on_text=got.append)
    assert json.loads("".join(got))["title"] == "Glasswood"


# --------------------------------------------------------------- retry

def test_retry_then_success(cfg, book, fake_llm, repo_root):
    fake_llm.replies = [reply(make_outline(n=4)), reply(make_outline())]
    seen = []
    res = outliner.outline(cfg, book, on_retry=seen.append)
    assert res.attempts == 2 and len(fake_llm.calls) == 2
    assert "4 chapters" in seen[0][0]
    second = fake_llm.calls[1]["user"]
    assert "rejected" in second and "4 chapters; a novella wants 10-16" in second
    # both calls were paid for
    assert {r["notes"] for r in ledger_rows(repo_root)} == {"attempt 1", "attempt 2"}


def test_retry_then_fail_writes_nothing(cfg, book, fake_llm, repo_root):
    fake_llm.replies = [reply(make_outline(n=4)), reply(make_outline(beats=1))]
    before = book.outline_path.read_text()
    with pytest.raises(outliner.OutlineError) as e:
        outliner.outline(cfg, book)
    assert "at least 3" in str(e.value) and len(fake_llm.calls) == 2
    assert book.outline_path.read_text() == before and book.outline_versions() == []
    assert len(book.chapters()) == 1
    kept = json.loads((book.outlines_dir / "rejected.json").read_text())
    assert kept["problems"] and kept["reply"]["title"] == "Glasswood"
    assert {r["notes"] for r in ledger_rows(repo_root)} == {"attempt 1", "attempt 2"}


def test_unparseable_reply_is_retried(cfg, book, fake_llm):
    fake_llm.replies = ["not json at all", reply(make_outline())]
    res = outliner.outline(cfg, book)
    assert res.attempts == 2 and "not valid JSON" in fake_llm.calls[1]["user"]


# --------------------------------------------------------------- validator

class FakeBible:
    recurring_cast = [{"name": "Garret Vane", "aliases": ["Warden Vane"]}]


def problems(o, total=30000, bible=FakeBible()):
    return outliner.validate(o, total, bible)


def test_valid_outline_passes():
    assert problems(make_outline()) == []


def test_alias_counts_as_recurring():
    o = make_outline(cast=[{"name": "Warden Vane", "role": "lead", "recurring": False}])
    assert problems(o) == [] and o["cast"][0]["recurring"] is True


@pytest.mark.parametrize("mutate, expect", [
    (lambda o: o.update(chapters=o["chapters"][:9]), "9 chapters"),
    (lambda o: o["chapters"].extend(copy.deepcopy(o["chapters"][:5])), "17 chapters"),
    (lambda o: o["chapters"][0]["beats"].pop(), "chapter 1 has 2 beats"),
    (lambda o: o["chapters"][0]["beats"].__setitem__(0, " "), "empty beat"),
    (lambda o: o["chapters"][2].update(title=""), "chapter 3 has no title"),
    (lambda o: o["chapters"][2].pop("beats"), "chapter 3 has no beats"),
    (lambda o: o["chapters"][2].update(target_words="3000"), "whole-number"),
    (lambda o: o["chapters"][2].update(target_words=800), "a chapter runs"),
    (lambda o: [c.update(target_words=3000) for c in o["chapters"]], "sum to 36,000"),
    (lambda o: o.update(title=""), "'title'"),
    (lambda o: o.pop("logline"), "'logline'"),
    (lambda o: o["cast"][1].update(role=""), "Mira Thorn has no role"),
    (lambda o: o["cast"][1].update(name=""), "cast entry 2 has no name"),
    (lambda o: o["cast"][0].update(name="Garret Vain"), "not in the bible"),
    (lambda o: o["cast"][0].update(name="garret vane"), "write the name exactly"),
])
def test_validator_rejects(mutate, expect):
    o = make_outline()
    mutate(o)
    found = problems(o)
    assert any(expect in p for p in found), found


def test_validator_rejects_non_object():
    assert problems([]) == ["the reply is not a JSON object"]


def test_empty_optional_pov_dropped():
    o = make_outline()
    o["pov"] = ""
    o["chapters"][0]["pov"] = " "
    assert problems(o) == [] and "pov" not in o and "pov" not in o["chapters"][0]


def test_no_bible_means_no_recurring_cast():
    o = make_outline()
    assert any("not in the bible" in p for p in problems(o, bible=None))
    o["cast"][0]["recurring"] = False
    assert problems(o, bible=None) == []


def test_outliner_model_is_priced():
    from ledger import ledger as L
    from story_pipeline import config
    rates = json.loads(L.RATES_JSON.read_text())["providers"]["anthropic"]["models"]
    assert config.DEFAULTS["models"]["novella_outliner"] in rates
    assert config.DEFAULTS["max_tokens"]["novella_outliner"] > 0
