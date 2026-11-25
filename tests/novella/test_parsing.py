"""Reply parsing, ported from kindle-novellas tests/test_parsing.py."""
import pytest

from novella_pipeline.parsing import (EDITOR_SECTIONS, WRITER_SECTIONS, bullets, is_empty,
                                      parse_review, parse_writer, split_sections)

from .conftest import REVIEW_REPLY, WRITER_REPLY

W = WRITER_SECTIONS


def test_section_constants():
    assert WRITER_SECTIONS == ("Chapter Text", "Chapter Summary", "New Details", "Flags")
    assert EDITOR_SECTIONS == ("Must Fix", "Canon", "Should Consider", "Minor", "What's Working")


def test_split_numbering_bold_case_colon():
    text = "## 1. **Chapter Text**\nbody\n## chapter summary:\n- s\n## 3) New Details\nx\n## FLAGS\nNone.\n"
    assert split_sections(text, W) == {"Chapter Text": "body", "Chapter Summary": "- s",
                                       "New Details": "x", "Flags": "None."}


def test_unknown_and_level3_headings_are_not_boundaries():
    s = split_sections("Sure.\n\n## Chapter Text\nA\n## Author Notes\nB\n### Flags\nC\n", W)
    assert s == {"Chapter Text": "A\n## Author Notes\nB\n### Flags\nC"}


def test_curly_apostrophe_and_underscores():
    s = split_sections("## What’s Working\n- ok\n## _Must Fix_\n- x\n", EDITOR_SECTIONS)
    assert s == {"What's Working": "- ok", "Must Fix": "- x"}


def test_duplicate_last_wins_and_empties():
    assert split_sections("## Flags\none\n## Flags\ntwo\n", W) == {"Flags": "two"}
    assert split_sections("", W) == {} and split_sections("##Flags\nx\n# Flags\n", W) == {}


@pytest.mark.parametrize("body,expected", [("", True), ("None", True), ("none.", True),
                                           ("N/A", True), ("-", True), ("None here", False)])
def test_is_empty(body, expected):
    assert is_empty(body) is expected


def test_bullets():
    assert bullets("- one\n  wrapped\n* two\n- None.\nstray\n") == ["one wrapped", "two"]


def test_parse_writer_full_reply():
    r = parse_writer(WRITER_REPLY)
    assert r.parsed and r.text.startswith("### The Chiming")
    assert r.summary == "- Garret hears the road chime."
    assert r.new_details == "- **Hollin Ferry:** a river crossing."
    assert r.flags == ""


def test_parse_writer_unparsed_keeps_whole_reply():
    r = parse_writer("  Here is a chapter.\n\n## Chapter Summary\n- s\n")
    assert not r.parsed and r.text == "Here is a chapter.\n\n## Chapter Summary\n- s"
    assert "did not follow" in r.flags


def test_parse_review():
    r = parse_review(REVIEW_REPLY)
    assert list(r) == list(EDITOR_SECTIONS)
    assert parse_review("free text") == {"Review": "free text"}
