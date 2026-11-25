"""lint.py's checks run over Markdown prose."""
from novella_pipeline.lint_md import lint_markdown, to_chapter
from story_pipeline.bundle import BREAK

TEXT = """### The Chiming

Garret *listened* to the road.
It hummed.

* * *

It was a crucial moment, underscoring everything.
"""


def test_to_chapter_paragraphs_breaks_headings():
    segs = to_chapter(TEXT)["segments"]
    assert [s["speaker"] for s in segs] == ["narrator", BREAK, "narrator"]
    assert segs[0]["text"] == "Garret listened to the road. It hummed."


def test_lint_markdown_findings_by_paragraph():
    out = lint_markdown(TEXT)
    rules = {f["rule"]: f for f in out["findings"]}
    assert rules["ai_vocab"]["paragraph"] == 1 and rules["ai_vocab"]["severity"] == "error"
    assert "analytic_participle" in rules
    assert out["errors"] >= 2 and out["paragraphs"] == 2
    assert all("segment" not in f for f in out["findings"])


def test_clean_text_has_no_errors():
    assert lint_markdown("She shut the gate.\n\nThe dog barked twice.")["errors"] == 0
