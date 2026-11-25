"""Prompt shape, ported from kindle-novellas tests/test_prompts.py, plus the
shared layers and Bible.prompt_block()."""
import pytest

from novella_pipeline import prompts
from novella_pipeline.parsing import EDITOR_SECTIONS, WRITER_SECTIONS
from novella_pipeline.prompts import Prompt, _tag


def test_tag_formatting():
    assert _tag("a", " body \n") == "<a>\nbody\n</a>"
    assert _tag("a", "") == "<a>\n(none)\n</a>"
    assert _tag("a", "  ", n=1, title="T") == '<a n="1" title="T">\n(none)\n</a>'


@pytest.mark.parametrize("kind", ["writer", "editor"])
def test_system_carries_the_shared_layers(book, kind):
    s = prompts.agent_system(kind, book)
    assert s.startswith(f"# Novella {'draft writer' if kind == 'writer' else 'editor'}")
    assert "# House rules" in s                      # house.md
    assert "# What these stories don't do" in s      # boundaries.md
    assert "# Not sounding like a machine" in s      # ai-tells.md
    assert "Scott's Voice" in s                      # authors/scott-voice.md
    assert "# Genre: Fantasy" in s                   # genres/fantasy.md
    assert "2,500 to 4,000 words" in s and "words-per-minute" in s
    for ph in ("{structure}", "{house}", "{boundaries}", "{ai_tells}", "{genre_guide}",
               "{author_voice}", "{genre_label}"):
        assert ph not in s


def test_writer_and_editor_get_identical_shared_text(book):
    w = prompts.agent_system("writer", book)
    e = prompts.agent_system("editor", book)
    tail = w[w.index("## Length"):]
    assert tail == e[e.index("## Length"):]


def test_canon_block_uses_prompt_block(book, cfg):
    t = prompts.canon_block(book, cfg)
    assert t.index("<series_bible>") < t.index("<story_brief>") < t.index("<outline>")
    assert "# Series bible: Glass Roads" in t and "cracks every winter" in t
    assert "Garret Vane" in t
    assert "SECRET-WORKING-NOTE" not in t            # Open Questions are working-only
    assert "BRIEF-MARKER" in t and "OUTLINE-MARKER" in t


def test_canon_block_without_bible(cfg, repo_root):
    from novella_pipeline.bundle import Bundle
    b = Bundle.create(cfg["novellas_dir"], "Solo", "Standalone")
    t = prompts.canon_block(b, cfg)
    assert "<series_bible>" not in t and "<story_brief>" in t and "<outline>" in t
    assert "(No genre guide" in prompts.agent_system("writer", b)


def test_missing_bible_raises(book, cfg, repo_root):
    import shutil
    shutil.rmtree(repo_root / "bibles" / "glass")
    with pytest.raises(ValueError, match="no bible"):
        prompts.writer_prompt(book, cfg, 1)


def test_writer_prompt_shape(book, cfg):
    p = prompts.writer_prompt(book, cfg, 1)
    assert isinstance(p, Prompt) and len(p.content) == 2
    t = p.content[1]
    assert '<target_chapter number="1" title="The Chiming" target_words="2500">' in t
    assert "- Garret hears the road chime" in t and "This is the first chapter." in t
    assert "Write chapter 1" in t and "level-3 heading" in t
    for h in WRITER_SECTIONS:
        assert f"## {h}" in t
    assert t.index("## Chapter Text") < t.index("## Chapter Summary") < t.index("## Flags")
    assert "<author_note>" not in t
    assert p.user == p.content[0] + "\n\n" + p.content[1] == p.as_text()


def test_writer_prompt_note_and_summaries(book, cfg):
    book.save_plan(2, "Two", 3000, "- b2")
    book.save_draft(1, text="x", summary="- one happened", new_details="", flags="")
    t = prompts.writer_prompt(book, cfg, 2, note="More rain.").content[1]
    assert "Chapter 1: The Chiming\n- one happened" in t
    assert "<author_note>\nMore rain.\n</author_note>" in t
    assert "This is the first chapter." not in t


def test_writer_prompt_unknown_chapter(book, cfg):
    with pytest.raises(ValueError):
        prompts.writer_prompt(book, cfg, 9)


def test_editor_prompt_shape(book, cfg):
    book.save_draft(1, text="Draft prose.", summary="", new_details="", flags="")
    book.save_edit(1, "Edited prose here.")
    t = prompts.editor_prompt(book, cfg, 1).content[1]
    assert '<text_to_review number="1">\nEdited prose here.\n</text_to_review>' in t
    assert '<chapter_beats number="1" title="The Chiming" target_words="2500">' in t
    assert "Suggest only; never rewrite" in t and "not yet in the bible" in t
    assert "Draft prose." not in t
    for h in EDITOR_SECTIONS:
        assert f"## {h}" in t


def test_amberlight_bible_in_place(real_bibles, cfg):
    from novella_pipeline.bundle import Bundle
    b = Bundle.create(cfg["novellas_dir"], "A Winter Book", "Amberlight",
                      genre="regency", bible="amberlight")
    b.save_plan(1, "One", 3000, "- beat")
    p = prompts.writer_prompt(b, cfg, 1)
    assert "# Series bible: Middle Watch: Amberlight" in p.user
    assert "# Genre: Regency" in p.system
