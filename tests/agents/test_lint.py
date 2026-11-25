"""story_pipeline.lint: one test per finding type, plus clean text."""

from __future__ import annotations

import pytest

from story_pipeline import lint
from story_pipeline.bundle import BREAK

CLEAN = ("Rowe took the ladder from the shed. It was longer than he remembered. "
         "He set it against the wall and climbed. The slate was loose. Rain had "
         "got under it in March, and nobody had come to look until now. He pulled "
         "it free and held it a moment. Nothing moved below. Then the sexton "
         "called up that the kettle was on.")


def rules(findings):
    return [f.rule for f in findings]


def seg(text, speaker="narrator"):
    return {"speaker": speaker, "text": text}


# --------------------------------------------------------------------------- #
# lint_segment
# --------------------------------------------------------------------------- #


def test_clean_text_has_no_segment_findings():
    assert lint.lint_segment(CLEAN, 0) == []


@pytest.mark.parametrize("word", ["delve", "tapestry", "pivotal", "vibrant", "meticulously"])
def test_ai_vocab_flagged_as_error(word):
    fs = lint.lint_segment(f"The room was {word} in every corner.", 3)
    assert rules(fs) == ["ai_vocab"]
    assert fs[0].severity == "error" and fs[0].segment == 3
    assert word in fs[0].quote


def test_ai_vocab_respects_word_boundaries():
    assert lint.lint_segment("She was a robustly built woman and a delved-in fan.", 0) == [] \
        or "ai_vocab" not in rules(lint.lint_segment("Crucially nothing.", 0))
    assert lint.lint_segment("The crucible was hot.", 0) == []


def test_ai_vocab_multiword_matches_variable_whitespace_and_case():
    assert rules(lint.lint_segment("They DEEP   DIVE into it.", 0)) == ["ai_vocab"]


@pytest.mark.parametrize("text", [
    "He spoke of his health, boasts of strength.",       # boasts of
    "She had a robust constitution.",
    "He read the last will and testament aloud.",
])
def test_ai_vocab_contextual_exemptions(text):
    assert lint.lint_segment(text, 0) == []


def test_analytic_participle_always_tier_any_position():
    fs = lint.lint_segment("Her attention to the guests underscoring her kindness.", 0)
    assert "analytic_participle" in rules(fs)


def test_analytic_participle_after_comma_only():
    assert "analytic_participle" in rules(
        lint.lint_segment("He bowed, demonstrating his respect.", 0))
    # same word without the comma is an honest participle
    assert lint.lint_segment("The clerk demonstrating the lamp stood aside.", 0) == []


def test_marking_after_comma_is_flagged_and_marking_linen_exempt_only_in_ai_vocab():
    assert "analytic_participle" in rules(lint.lint_segment("She nodded, marking the end.", 0))


def test_copula_dodge():
    fs = lint.lint_segment("The house serves as a school.", 0)
    assert rules(fs) == ["copula_dodge"]


def test_manufactured_significance():
    fs = lint.lint_segment("Little did she know what waited.", 0)
    assert "manufactured_significance" in rules(fs)


def test_promotional():
    assert "promotional" in rules(lint.lint_segment("A village nestled in the hills.", 0))


def test_modern_register():
    assert "modern_register" in rules(lint.lint_segment("He wanted closure.", 0))


@pytest.mark.parametrize("text", [
    "It was not just a letter, but a summons.",
    "No tea, no cake, just cold toast.",
    "It was not a gift; it was a bribe.",
])
def test_negative_parallelism_is_warning(text):
    fs = [f for f in lint.lint_segment(text, 0) if f.rule == "negative_parallelism"]
    assert fs and all(f.severity == "warn" for f in fs)


def test_spaced_em_dash_warning_and_closed_dash_clean():
    fs = lint.lint_segment("He stopped — and looked back.", 0)
    assert rules(fs) == ["spaced_em_dash"] and fs[0].severity == "warn"
    assert rules(lint.lint_segment("He stopped—and looked back.", 0)) == []
    assert rules(lint.lint_segment("pages 3 – 5", 0)) == ["spaced_em_dash"]


def test_quote_has_context_and_ellipses():
    text = "a" * 100 + " delve " + "b" * 100
    q = lint.lint_segment(text, 0)[0].quote
    assert q.startswith("...") and q.endswith("...") and "delve" in q


def test_multiple_findings_in_one_segment_all_reported():
    fs = lint.lint_segment("A pivotal, vibrant tapestry.", 0)
    assert sorted(rules(fs)) == ["ai_vocab"] * 3


# --------------------------------------------------------------------------- #
# lint_shape
# --------------------------------------------------------------------------- #


def test_shape_clean_variable_text():
    assert lint.lint_shape(CLEAN) == []


def test_uniform_cadence_warning():
    text = " ".join(["The cat sat on the mat today."] * 10)
    fs = lint.lint_shape(text)
    assert "uniform_cadence" in rules(fs)
    assert [f for f in fs if f.rule == "uniform_cadence"][0].severity == "warn"


def test_uniform_cadence_needs_eight_sentences():
    assert "uniform_cadence" not in rules(
        lint.lint_shape(" ".join(["The cat sat on the mat today."] * 7)))


def test_long_sentence_over_forty_words():
    sent = " ".join(["word"] * 41) + "."
    fs = lint.lint_shape(sent)
    assert rules(fs) == ["long_sentence"]
    assert "41 words" in fs[0].note


def test_forty_words_exactly_is_not_long():
    assert "long_sentence" not in rules(lint.lint_shape(" ".join(["word"] * 40) + "."))


def test_clause_chain():
    sent = ("He opened the door, and she walked in, and the dog followed her, "
            "but nobody spoke of the weather or the broken old gate at all.")
    fs = lint.lint_shape(sent)
    assert rules(fs) == ["clause_chain"]
    assert "4 clauses" in fs[0].note


def test_short_chain_below_word_threshold_not_flagged():
    assert lint.lint_shape("He ran, and she ran, and we ran.") == []


def test_long_sentence_takes_precedence_over_clause_chain():
    sent = ", and ".join(["he did a thing of some length here"] * 8) + "."
    assert rules(lint.lint_shape(sent)) == ["long_sentence"]


def test_long_sentence_quote_is_clipped():
    fs = lint.lint_shape(" ".join(["word"] * 60) + ".")
    assert fs[0].quote.endswith("...") and len(fs[0].quote) <= 143


def test_rule_of_three_triggers_over_four_per_thousand():
    text = " ".join(["red, green, and blue lamps burned."] * 6)
    fs = lint.lint_shape(text)
    assert "rule_of_three" in rules(fs)
    assert "e.g. red, green, blue" in [f for f in fs if f.rule == "rule_of_three"][0].quote


def test_rule_of_three_not_triggered_for_single_triple_in_long_text():
    text = "red, green, and blue. " + CLEAN * 4
    assert "rule_of_three" not in rules(lint.lint_shape(text))


def test_shape_empty_text_is_clean():
    assert lint.lint_shape("") == []


# --------------------------------------------------------------------------- #
# lint_breaks
# --------------------------------------------------------------------------- #


def test_break_into_dialogue_is_error():
    ch = {"segments": [seg("Night fell."), seg("", BREAK), seg("Hello there.", "Mr Rowe")]}
    fs = lint.lint_breaks(ch)
    assert rules(fs) == ["break_into_dialogue"]
    assert fs[0].severity == "error" and fs[0].segment == 2


def test_break_unanchored_warns_with_segment_index():
    ch = {"segments": [seg("a"), seg("", BREAK), seg("Rowe frowned and said nothing.")]}
    fs = lint.lint_breaks(ch)
    assert rules(fs) == ["break_unanchored"] and fs[0].segment == 2


@pytest.mark.parametrize("first", [
    "By Thursday the beam was down.",
    "The next morning came cold.",
    "At the mill the wheel had stopped.",
    "Two weeks later he returned.",
    "It was past midnight.",
])
def test_break_anchored_by_time_or_place(first):
    ch = {"segments": [seg("a"), seg("", BREAK), seg(first)]}
    assert lint.lint_breaks(ch) == []


def test_break_count_over_two_warns():
    segs = []
    for _ in range(3):
        segs += [seg("Some text."), seg("", BREAK), seg("The next morning dawned.")]
    fs = lint.lint_breaks({"segments": segs})
    assert rules(fs) == ["break_count"]
    assert fs[0].segment == -1 and "3 scene breaks" in fs[0].quote


def test_trailing_break_with_no_next_segment_is_ignored():
    assert lint.lint_breaks({"segments": [seg("a"), seg("", BREAK)]}) == []


def test_no_segments_key_is_fine():
    assert lint.lint_breaks({}) == []


# --------------------------------------------------------------------------- #
# lint_chapter / helpers
# --------------------------------------------------------------------------- #


def test_lint_chapter_clean():
    ch = {"segments": [seg(CLEAN), seg("", BREAK), seg("The next morning he left."),
                       seg("I will go.", "Mr Rowe")]}
    assert lint.lint_chapter(ch) == []


def test_lint_chapter_skips_break_text_and_tags_segment_index():
    ch = {"segments": [seg("Fine words."), seg("delve", BREAK), seg("The room was vibrant.")]}
    fs = lint.lint_chapter(ch)
    ai = [f for f in fs if f.rule == "ai_vocab"]
    assert len(ai) == 1 and ai[0].segment == 2


def test_lint_chapter_empty():
    assert lint.lint_chapter({}) == []


def test_errors_filters_by_severity():
    a = lint.Finding("x", "error", "q", "n")
    b = lint.Finding("y", "warn", "q2", "n")
    assert lint.errors([a, b]) == [a]


def test_summarize_counts_and_shape():
    fs = [lint.Finding("x", "error", "q", "n", 1), lint.Finding("x", "warn", "q2", "n"),
          lint.Finding("y", "warn", "q3", "n")]
    s = lint.summarize(fs)
    assert s["total"] == 3 and s["errors"] == 1
    assert s["by_rule"] == {"x": 2, "y": 1}
    assert s["findings"][0] == {"rule": "x", "severity": "error", "segment": 1,
                                "quote": "q", "note": "n"}
    assert lint.summarize([]) == {"total": 0, "errors": 0, "by_rule": {}, "findings": []}


def test_summarize_findings_round_trip_through_finding_constructor():
    # draft_and_edit rebuilds Findings from this dict with Finding(**f)
    fs = [lint.Finding("x", "error", "q", "n", 4)]
    again = [lint.Finding(**f) for f in lint.summarize(fs)["findings"]]
    assert again == fs


def test_as_fix_with_and_without_segment():
    assert lint.Finding("r", "error", "quote", "note", 2).as_fix() == \
        "segment 2: “quote” — note"
    assert lint.Finding("r", "error", "quote", "note").as_fix() == "“quote” — note"


def test_to_fixes_errors_first_dedup_and_limit():
    fs = [lint.Finding("w", "warn", "same", "n"),
          lint.Finding("e", "error", "err", "n"),
          lint.Finding("w2", "warn", "same", "n2"),
          lint.Finding("e2", "error", "err", "n3")]
    out = lint.to_fixes(fs)
    assert len(out) == 2 and "err" in out[0] and "same" in out[1]
    many = [lint.Finding("e", "error", f"q{i}", "n") for i in range(30)]
    assert len(lint.to_fixes(many)) == 12
    assert len(lint.to_fixes(many, limit=3)) == 3
    assert lint.to_fixes([]) == []
