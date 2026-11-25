"""The novella bundle: layout, pinned fields, numbered drafts, stage moves."""
import json

import pytest

from novella_pipeline import bundle as nb
from novella_pipeline.bundle import Bundle, NovellaError
from novella_pipeline.workflow import Stage, TransitionError


def _draft(b, n=1, text="### T\n\nProse here.", summary="- s1"):
    return b.save_draft(n, text=text, summary=summary, new_details="- d", flags="")


def test_create_layout_and_manifest(book, repo_root):
    assert book.root.resolve() == repo_root / "novellas" / "glass-roads" / "glasswood"
    m = book.manifest()
    assert m["pipeline"] == "novella" and m["slug"] == "glasswood"
    assert m["pinned"] == {"title": "Glasswood", "series": "Glass Roads",
                           "series_dir": "glass-roads", "genre": "fantasy",
                           "bible": "glass", "target_words": 30000}
    assert (book.root / "chapters").is_dir() and (book.root / "drafts").is_dir()
    assert book.beats(1).startswith("- Garret")
    assert book.stage(1) == Stage.OUTLINED


def test_not_under_stories(book, repo_root):
    from story_pipeline.bundle import bundle_roots
    assert bundle_roots(repo_root / "stories") == []


def test_ledger_story(book):
    assert book.ledger_story() == {
        "pipeline": "novella", "slug": "glasswood", "title": "Glasswood",
        "series": "Glass Roads", "genre": "fantasy", "flavor": "",
        "arc_slug": "glass-roads", "volume": "", "episode": ""}


def test_add_spend(book):
    book.add_spend("anthropic_input_tokens", 10)
    book.add_spend("anthropic_input_tokens", 5)
    assert book.manifest()["spend"] == {"anthropic_input_tokens": 15}


def test_slug_unique_across_series(book, cfg):
    with pytest.raises(NovellaError, match="unique"):
        Bundle.create(cfg["novellas_dir"], "Glasswood", "Other Series")


def test_create_needs_title_and_series(cfg):
    with pytest.raises(NovellaError):
        Bundle.create(cfg["novellas_dir"], "", "S")
    with pytest.raises(NovellaError):
        Bundle.create(cfg["novellas_dir"], "T", "")


@pytest.mark.parametrize("slug", ["../x", "..", "a/b", "", "UPPER", "-x"])
def test_find_bundle_refuses_bad_slugs(book, cfg, slug):
    with pytest.raises((NovellaError, FileNotFoundError)):
        nb.find_bundle(cfg["novellas_dir"], slug)


def test_find_and_resolve(book, cfg):
    assert nb.find_bundle(cfg["novellas_dir"], "glasswood").resolve() == book.root.resolve()
    assert nb.resolve(cfg["novellas_dir"], str(book.root)) == book.root
    assert nb.resolve(cfg["novellas_dir"], "glasswood").resolve() == book.root.resolve()
    with pytest.raises(FileNotFoundError):
        nb.find_bundle(cfg["novellas_dir"], "nope")


def test_open_refuses_a_story_bundle(repo_root):
    from story_pipeline.bundle import Bundle as StoryBundle
    s = StoryBundle.create(repo_root / "stories", "a-story")
    with pytest.raises(NovellaError):
        Bundle.open(s.root)


def test_numbered_drafts(book):
    assert book.current(1) is None and book.edited(1) == ""
    assert _draft(book) == 1
    assert book.draft_path(1, 1).name == "01.1.md"
    assert book.stage(1) == Stage.DRAFTED
    assert book.edited(1) == "### T\n\nProse here.\n"      # seeded from the draft
    assert book.summary(1) == "- s1"
    assert _draft(book, text="Second.") == 2                # redraft from Drafted
    assert book.versions(1) == [1, 2]
    assert book.draft(1, 1).startswith("### T") and book.draft(1) == "Second.\n"
    meta = json.loads(book.writer_path(1, 2).read_text())
    assert meta["summary"] == "- s1" and meta["parsed"] is True


def test_redraft_refused_after_edit_until_sent_back(book):
    _draft(book)
    assert book.save_edit(1, "My edit.") == Stage.AUTHOR_EDITED
    with pytest.raises(TransitionError, match="Send it back"):
        _draft(book)
    book.send_back(1, "outlined")
    assert _draft(book, text="Fresh.") == 2
    assert book.edited(1, 1) == "My edit.\n"                # old edit kept on disk
    assert book.edited(1) == "Fresh.\n"


def test_draft_needs_beats(book):
    book.save_plan(2, "Two")
    with pytest.raises(NovellaError, match="beats"):
        book.check_draft(2)


def test_edit_rules(book):
    with pytest.raises(TransitionError):
        book.save_edit(1, "x")                               # outlined
    _draft(book)
    book.save_edit(1, "a")
    book.save_review(1, "## Minor\n- x\n", {"total": 0})
    assert book.save_edit(1, "b") == Stage.EDITOR_REVIEWED   # no forced re-review


def test_review_rules_and_storage(book):
    _draft(book)
    with pytest.raises(TransitionError, match="Save your edits"):
        book.save_review(1, "x", {})
    book.save_edit(1, "Edited text here.")
    book.save_review(1, "## Must Fix\n- a\n", {"total": 1}, model="m",
                     reviewed_text="Edited text here.")
    r = book.review(1)
    assert r["sections"] == {"Must Fix": "- a"} and r["lint"] == {"total": 1}
    assert r["reviewed_words"] == 3 and book.review_path(1, 1).name == "01.1.review.json"
    assert book.stage(1) == Stage.EDITOR_REVIEWED


def test_approve_rules(book):
    _draft(book)
    with pytest.raises(TransitionError, match="editor reviewed"):
        book.approve(1, "sum")
    book.save_edit(1, "e")
    book.save_review(1, "## Minor\n- x\n", {})
    with pytest.raises(NovellaError, match="summary"):
        book.approve(1, "  ")
    assert book.stage(1) == Stage.EDITOR_REVIEWED
    book.approve(1, "- approved summary", ["Mill Pond"])
    assert book.stage(1) == Stage.APPROVED
    assert book.summary(1) == "- approved summary"
    assert book.details_path(1).read_text() == "- Mill Pond\n"


def test_plan_locked_when_approved(book):
    _draft(book)
    book.save_edit(1, "e")
    book.save_review(1, "x", {})
    book.approve(1, "s")
    with pytest.raises(NovellaError, match="locked"):
        book.save_plan(1, "New title")


def test_earlier_summaries(book):
    for n in (2, 3, 4):
        book.save_plan(n, f"Ch{n}", 3000, f"- b{n}")
    _draft(book, 1, summary="- one happened")
    _draft(book, 3, summary="- three happened")
    got = [(c["n"], s) for c, s in book.earlier_summaries(4)]
    assert got == [(1, "- one happened"), (3, "- three happened")]


def test_send_back_unknown_stage(book):
    with pytest.raises(NovellaError, match="unknown stage"):
        book.send_back(1, "bogus")
    with pytest.raises(TransitionError):
        book.send_back(1, "outlined")
