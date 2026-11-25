"""The real bibles, copied into the tmp root: nothing here reads or writes the
originals, so Scott's uncommitted draft edits are safe."""

from __future__ import annotations

import yaml

import pytest

from story_pipeline import bibles
from tests.conftest import REPO_ROOT


def _voice_cfg():
    lib = yaml.safe_load((REPO_ROOT / "voices.yaml").read_text())["voice_library"]
    return {"elevenlabs": {"voice_library": lib}}


def _all_md(root):
    return sorted(p for p in (root).rglob("*.md") if not p.name.endswith(".changelog.md"))


def test_amberlight_loads_and_validates(real_bibles):
    path = bibles.path_for("amberlight", "bibles")
    b = bibles.load("amberlight")
    assert b.series and b.genre == "regency" and b.anchor_year == 1817
    assert len(b.recurring_cast) >= 3 and b.canon
    assert not bibles.fatal(bibles.validate(path, {}))
    assert not bibles.fatal(bibles.validate(path, _voice_cfg()))
    assert "amberlight" in bibles.available()


def test_amberlight_cast_and_portraits(real_bibles):
    b = bibles.load("amberlight")
    for m in b.recurring_cast:
        assert b.member(m["name"]) is m
        got = b.portrait(m["name"])      # portraits are generated lazily: may be absent
        assert got is None or got.exists()
    shipped = {p.name for p in b.cast_dir.glob("*.jpg")} | {p.name for p in b.cast_dir.glob("*.png")}
    found = {b.portrait(m["name"]).name for m in b.recurring_cast if b.portrait(m["name"])}
    assert shipped and found == shipped        # every portrait on disk belongs to someone


def test_amberlight_prompt_block_shape(real_bibles):
    b = bibles.load("amberlight")
    out = b.prompt_block()
    assert out.startswith(f"# Series bible: {b.series}")
    assert "## Established facts" in out and "## Recurring cast" in out
    for m in b.recurring_cast:
        assert f"**{m['name']}**" in out
    assert "Ages are as of 1817" in out
    # the image-only fields never reach a text agent
    for m in b.recurring_cast:
        for hidden in ("appearance", "dress"):
            if m.get(hidden):
                assert str(m[hidden]) not in out


def test_every_real_bible_file_loads_or_fails_with_documented_error(real_bibles):
    files = _all_md(real_bibles)
    assert files
    for path in files:
        try:
            b = bibles._parse(path)
        except ValueError as e:
            assert "has no frontmatter block" in str(e), (path, e)
        else:
            assert isinstance(b.meta, dict) and isinstance(b.guide, str)


def test_validate_never_raises_on_any_real_file(real_bibles):
    for path in _all_md(real_bibles):
        out = bibles.validate(path, {})
        assert all(isinstance(p, bibles.Problem) for p in out)


def test_available_lists_only_published_real_bibles(real_bibles):
    names = bibles.available()
    for n in names:
        assert bibles.path_for(n, "bibles").exists()
        bibles.load(n)                      # every listed bible is loadable
    assert not any(n.endswith(".draft") for n in names)


def test_prompt_block_never_leaks_working_only_sections(real_bibles, sample_draft):
    checked = 0
    for path in _all_md(real_bibles):
        text = path.read_text()
        try:
            b = bibles._parse(path)
        except ValueError:
            # A draft has no frontmatter: treat the whole file as the guide, the
            # worst case for leaks.
            b = bibles.Bible(name=path.stem, meta={}, guide=text.strip(), path=path)
        for ideator in (False, True):
            block = b.prompt_block(ideator=ideator)
            for title, _ in bibles.split_sections(block):
                if title is None:
                    continue
                key = bibles.heading_key(title)
                assert key not in bibles.WORKING_ONLY, (path.name, title)
                if not ideator:
                    assert key not in bibles.IDEATOR_ONLY, (path.name, title)
            checked += 1
    assert checked >= 4   # amberlight + the synthetic draft, two views each


def test_working_only_bodies_do_not_appear_in_any_block(real_bibles, sample_draft):
    for path in _all_md(real_bibles):
        guide = path.read_text()
        for title, chunk in bibles.split_sections(guide):
            if title is None or bibles.heading_key(title) not in bibles.WORKING_ONLY:
                continue
            body = bibles.section_body(guide, bibles.heading_key(title)) or ""
            others = "\n".join(c for t, c in bibles.split_sections(guide)
                               if t is not None and bibles.heading_key(t) not in bibles.WORKING_ONLY)
            b = bibles.Bible(name="x", meta={}, guide=guide, path=path)
            for line in (ln.strip() for ln in body.splitlines()):
                if len(line) > 25 and line not in others:      # distinctive lines only
                    assert line not in b.prompt_block(ideator=True), (path.name, line)
