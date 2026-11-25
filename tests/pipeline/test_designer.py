"""designer.py with Gemini and the LLM mocked."""
import base64
import json
import shutil
from types import SimpleNamespace

import pytest

from story_pipeline.agents import designer
from story_pipeline.bundle import Bundle
from tests.conftest import REPO_ROOT
from tests.pipeline.conftest import make_mini

IMG = b"\xff\xd8fakejpeg"


class FakeInteractions:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kw):
        self.outer.calls.append(kw)
        if self.outer.empty:
            return SimpleNamespace(output_image=None)
        return SimpleNamespace(output_image=SimpleNamespace(data=base64.b64encode(IMG).decode()))


class FakeGemini:
    def __init__(self, empty=False):
        self.calls, self.empty = [], empty
        self.interactions = FakeInteractions(self)


@pytest.fixture
def gem(monkeypatch):
    g = FakeGemini()
    monkeypatch.setattr(designer, "_client", lambda: g)
    return g


def prompt_of(call):
    return call["input"][0]["text"]


def refs_of(call):
    return call["input"][1:]


def styled(b):
    b.pin("art_style", "inkwash")
    return b


# --- Progress / waiting ------------------------------------------------------

def test_progress_line_and_say(capsys, monkeypatch):
    p = designer.Progress(4)
    assert p.line() == "0/4"
    clock = {"t": 100.0}
    monkeypatch.setattr(designer.time, "monotonic", lambda: clock["t"])
    p = designer.Progress(4)                    # started = 100
    clock["t"] = 110.0
    p.tick()
    assert p.line() == "1/4 · 10s each · ~30s left"
    p.say("hello")
    assert capsys.readouterr().out == "hello\n"
    designer.Progress(1, verbose=False).say("quiet")
    assert capsys.readouterr().out == ""


def test_progress_minutes_left(monkeypatch):
    p = designer.Progress(10)
    p.started = designer.time.monotonic() - 60      # 1 done in 60s => 9 left = 540s
    p.tick()
    assert "~9 min left" in p.line()


def test_waiting_records_last_call():
    p = designer.Progress(1, verbose=False)
    with designer.waiting(p, every=0.01):
        pass
    assert p.last_call >= 0


def test_waiting_heartbeat(capsys):
    p = designer.Progress(1)
    with designer.waiting(p, every=0.01):
        designer.time.sleep(0.08)
    assert "still generating" in capsys.readouterr().out


# --- _generate ---------------------------------------------------------------

def test_generate_builds_call_and_decodes(gem, cfg, tmp_path):
    ref1, ref2 = tmp_path / "a.png", tmp_path / "b.jpg"
    ref1.write_bytes(b"png"), ref2.write_bytes(b"jpg")
    out = designer._generate(gem, cfg, "PROMPT", [ref1, ref2])
    assert out == IMG
    c = gem.calls[0]
    assert c["model"] == cfg["gemini"]["model"]
    assert c["input"][0] == {"type": "text", "text": "PROMPT"}
    assert [r["mime_type"] for r in refs_of(c)] == ["image/png", "image/jpeg"]
    assert base64.b64decode(refs_of(c)[0]["data"]) == b"png"
    assert c["response_format"] == {"type": "image", "mime_type": "image/jpeg",
                                    "aspect_ratio": "16:9", "image_size": "2K"}
    assert c["generation_config"] == {"thinking_level": "high"}


def test_generate_caps_refs(gem, cfg, tmp_path):
    refs = []
    for i in range(7):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(b"x")
        refs.append(p)
    designer._generate(gem, cfg, "p", refs)
    assert len(refs_of(gem.calls[0])) == cfg["gemini"]["max_character_refs"] == 4


def test_generate_unknown_suffix_defaults_to_jpeg(gem, cfg, tmp_path):
    p = tmp_path / "x.webp"
    p.write_bytes(b"x")
    designer._generate(gem, cfg, "p", [p])
    assert refs_of(gem.calls[0])[0]["mime_type"] == "image/jpeg"


def test_generate_no_image_raises(cfg):
    with pytest.raises(RuntimeError, match="no image returned for prompt: PROMPT"):
        designer._generate(FakeGemini(empty=True), cfg, "PROMPT", [])


# --- _art_style --------------------------------------------------------------

def test_art_style_precedence_and_error(mini, cfg):
    with pytest.raises(RuntimeError, match="No art style"):
        designer._art_style(cfg, mini)
    cfg["art_style"] = "fallback"
    assert designer._art_style(cfg, mini) == "fallback"
    mini.pin("art_style", "pinned")
    assert designer._art_style(cfg, mini) == "pinned"


# --- build_cast_sheet --------------------------------------------------------

def test_cast_sheet_generates_each_member_and_bills(mini, cfg, gem):
    styled(mini)
    sheet = designer.build_cast_sheet(cfg, mini, verbose=False)
    assert list(sheet) == ["Ann Lee", "Bob Ray"]
    assert sheet["Ann Lee"] == mini.root / "images" / "cast" / "ann-lee.jpg"
    assert sheet["Ann Lee"].read_bytes() == IMG
    assert mini.manifest()["spend"]["gemini_images"] == 2
    p = prompt_of(gem.calls[0])
    assert p.startswith("Character reference portrait. Ann Lee, age 30. tall. Wearing: grey.")
    assert "Style: inkwash" in p and p.endswith(designer.NEGATIVE)
    assert refs_of(gem.calls[0]) == []


def test_cast_sheet_skips_existing(mini, cfg, gem, capsys):
    styled(mini)
    path = mini.cast_image("Ann Lee")
    path.write_bytes(b"old")
    designer.build_cast_sheet(cfg, mini)
    assert path.read_bytes() == b"old" and len(gem.calls) == 1
    assert "Ann Lee exists, skipping" in capsys.readouterr().out


def test_cast_sheet_existing_png_counts_as_existing(mini, cfg, gem):
    styled(mini)
    png = mini.root / "images" / "cast" / "ann-lee.png"
    png.write_bytes(b"png")
    sheet = designer.build_cast_sheet(cfg, mini, verbose=False)
    assert sheet["Ann Lee"] == png and len(gem.calls) == 1


def test_cast_sheet_requires_style_before_any_call(mini, cfg, gem):
    with pytest.raises(RuntimeError, match="No art style"):
        designer.build_cast_sheet(cfg, mini)
    assert gem.calls == []


def test_cast_sheet_with_real_bible_reuses_portraits_and_promotes_new_ones(story, cfg, gem, real_bibles):
    sheet = designer.build_cast_sheet(cfg, story, verbose=False)
    assert len(sheet) == 6
    # three recurring characters have portraits in the bible -> copied, not generated
    assert len(gem.calls) == 3
    for name in ("Mr Silas Rowe", "Miss Judith Vane", "Mrs Honoria Pike"):
        assert sheet[name].exists()
        assert sheet[name].read_bytes() == (REPO_ROOT / "bibles/amberlight/amberlight.cast" /
                                            f"{name.lower().replace(' ', '-')}.jpg").read_bytes()
    assert story.manifest()["spend"]["gemini_images"] == 41 + 3      # 41 already on the real manifest
    # the other three are in the bible's recurring cast but have no face yet, so the
    # new portraits are promoted into the series
    cast_dir = real_bibles / "amberlight" / "amberlight.cast"
    assert sorted(p.name for p in cast_dir.iterdir()) == [
        "miss-judith-vane.jpg", "miss-louisa-marchmont.jpg", "mr-owen-vane.jpg",
        "mr-silas-rowe.jpg", "mrs-hale.jpg", "mrs-honoria-pike.jpg"]
    log = (real_bibles / "amberlight" / "amberlight.changelog.md").read_text()
    assert log.count("Promoted portrait for") >= 3


def test_cast_sheet_promotes_new_recurring_face_and_logs(story, cfg, gem, real_bibles):
    portrait = real_bibles / "amberlight" / "amberlight.cast" / "mrs-honoria-pike.jpg"
    portrait.unlink()
    log = real_bibles / "amberlight" / "amberlight.changelog.md"
    before = log.read_text()
    designer.build_cast_sheet(cfg, story, verbose=False)
    assert portrait.exists() and portrait.read_bytes() == IMG           # promoted into the series
    after = log.read_text()
    assert after != before and "Promoted portrait for Mrs Honoria Pike" in after
    assert "pipeline (the-cracked-beam)" in after


# --- plan_shots --------------------------------------------------------------

@pytest.fixture
def llm_plan(monkeypatch):
    seen = {}

    def fake(model, system, ask, max_tokens=None, **kw):
        seen.update(model=model, system=system, ask=ask, max_tokens=max_tokens)
        return seen["reply"], {}
    monkeypatch.setattr(designer.llm, "complete_json", fake)
    return seen


def test_plan_shots_anchors_clamps_and_sorts(story, cfg, llm_plan, real_bibles):
    n_sent = len(story.timeline(1)["sentences"])
    llm_plan["reply"] = {"shots": [
        {"sentence_index": 9999, "prompt": "late"},       # clamped to last
        {"sentence_index": -5, "prompt": "early"},        # clamped to 0
        {"sentence_index": "3", "prompt": "mid"},         # string index coerced
        {"prompt": "no index"},                           # defaults to 0
    ]}
    plan = designer.plan_shots(cfg, story, 1)
    tl = story.timeline(1)["sentences"]
    assert plan["n"] == 1
    assert [s["prompt"] for s in plan["shots"]][-1] == "late"
    assert [s["at_ms"] for s in plan["shots"]] == sorted(s["at_ms"] for s in plan["shots"])
    by = {s["prompt"]: s for s in plan["shots"]}
    assert by["late"]["sentence_index"] == n_sent - 1 and by["late"]["at_ms"] == tl[-1]["start_ms"]
    assert by["early"]["sentence_index"] == 0 and by["early"]["at_ms"] == 0
    assert by["mid"]["sentence_index"] == 3 and by["mid"]["at_ms"] == tl[3]["start_ms"]
    assert by["no index"]["sentence_index"] == 0


def test_plan_shots_prompt_assembly(story, cfg, llm_plan, real_bibles):
    llm_plan["reply"] = {"shots": []}
    designer.plan_shots(cfg, story, 2)
    system, ask = llm_plan["system"], llm_plan["ask"]
    assert llm_plan["model"] == cfg["models"]["designer"] and llm_plan["max_tokens"] == 8000
    for placeholder in ("{house}", "{structure}", "{boundaries}", "{bible_block}",
                        "{genre_guide}", "{genre_label}", "{n}", "{k}"):
        assert placeholder not in system
    assert "Choose 4 shots." in ask                       # pinned scenes_per_chapter=4
    assert "# Chapter 2, numbered sentences with timings" in ask
    first = story.timeline(2)["sentences"][0]
    assert f"[0] ({first['start_ms']}ms) {first['speaker']}: {first['text']}" in ask
    assert '"title": "The Cracked Beam"' in ask


def test_plan_shots_k_falls_back_to_config_then_length(story, mini, cfg, llm_plan, real_bibles):
    llm_plan["reply"] = {"shots": []}
    manifest = story.manifest()
    del manifest["pinned"]["scenes_per_chapter"]
    story.write_manifest(manifest)
    designer.plan_shots(cfg, story, 1)
    assert "Choose 4 shots." in llm_plan["ask"]            # 30-minute spec
    cfg["gemini"]["scenes_per_chapter"] = 2
    designer.plan_shots(cfg, story, 1)
    assert "Choose 2 shots." in llm_plan["ask"]


def test_plan_shots_without_bible(mini, cfg, llm_plan):
    llm_plan["reply"] = {"shots": [{"sentence_index": 0, "prompt": "p"}]}
    mini.timeline_path(1).write_text(json.dumps({"duration_ms": 900, "sentences": [
        {"start_ms": 0, "end_ms": 400, "text": "a.", "speaker": "narrator"},
        {"start_ms": 500, "end_ms": 900, "text": "b.", "speaker": "narrator"}]}))
    plan = designer.plan_shots(cfg, mini, 1)
    assert plan["shots"][0]["at_ms"] == 0
    assert "Choose 3 shots." in llm_plan["ask"]            # 15-minute default


# --- render_chapter ----------------------------------------------------------

def stub_plan(monkeypatch, shots, calls=None):
    def fake(cfg, b, n):
        if calls is not None:
            calls.append(n)
        return {"n": n, "shots": [dict(s) for s in shots]}
    monkeypatch.setattr(designer, "plan_shots", fake)


def mini_narrated(b):
    for n in b.chapter_numbers():
        b.timeline_path(n).write_text(json.dumps(
            {"chapter_hash": b.chapter_hash(n), "duration_ms": 1000,
             "sentences": [{"start_ms": 0, "end_ms": 1000, "text": "x.", "speaker": "narrator"}]}))


SHOTS = [{"prompt": "A harbour.", "characters": ["Ann Lee"], "at_ms": 0},
         {"prompt": "A door.", "characters": ["Nobody", "Bob Ray"], "at_ms": 500}]


def test_render_chapter_plans_draws_and_bills(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    planned = []
    stub_plan(monkeypatch, SHOTS, planned)
    sheet = {"Ann Lee": mini.cast_image("Ann Lee"), "Bob Ray": mini.cast_image("Bob Ray")}
    sheet["Ann Lee"].write_bytes(b"A")                      # Bob has no portrait on disk
    saved = []
    plan = designer.render_chapter(cfg, mini, 1, sheet, verbose=False, save=saved.append)
    assert planned == [1] and len(saved) == 1 and saved[0] is plan
    assert plan["chapter_hash"] == mini.chapter_hash(1)
    assert [s["image"] for s in plan["shots"]] == ["images/01-00.jpg", "images/01-01.jpg"]
    assert all(mini.scene_image(1, i).read_bytes() == IMG for i in (0, 1))
    assert mini.manifest()["spend"]["gemini_images"] == 2
    c0, c1 = gem.calls
    assert prompt_of(c0).startswith("A harbour. The people shown are, in the order of the reference "
                                    "images provided: Ann Lee. Match their faces")
    assert prompt_of(c0).endswith("Style: inkwash" + designer.NEGATIVE)
    assert len(refs_of(c0)) == 1
    assert refs_of(c1) == []                                # unknown + portrait-less names dropped


def test_render_chapter_no_characters_has_no_who_clause(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    stub_plan(monkeypatch, [{"prompt": "Sky.", "at_ms": 0}])
    designer.render_chapter(cfg, mini, 1, {}, verbose=False)
    assert prompt_of(gem.calls[0]) == "Sky. Style: inkwash" + designer.NEGATIVE


def test_render_chapter_skips_existing_images(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    stub_plan(monkeypatch, SHOTS)
    mini.scene_image(1, 0).write_bytes(b"keep")
    designer.render_chapter(cfg, mini, 1, {}, verbose=False)
    assert mini.scene_image(1, 0).read_bytes() == b"keep" and len(gem.calls) == 1
    assert mini.manifest()["spend"]["gemini_images"] == 1


def test_render_chapter_keeps_cached_plan(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    planned = []
    stub_plan(monkeypatch, [], planned)
    cached = {"n": 1, "chapter_hash": mini.chapter_hash(1), "shots": [dict(SHOTS[1])]}
    plan = designer.render_chapter(cfg, mini, 1, {}, verbose=False, cached=cached)
    assert planned == [] and plan is cached and len(gem.calls) == 1


def test_render_chapter_legacy_cached_plan_without_hash_is_trusted(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    planned = []
    stub_plan(monkeypatch, [], planned)
    cached = {"n": 1, "shots": [dict(SHOTS[0])]}
    plan = designer.render_chapter(cfg, mini, 1, {}, verbose=False, cached=cached)
    assert planned == [] and plan["chapter_hash"] == mini.chapter_hash(1)


def test_render_chapter_stale_plan_is_replanned(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    planned = []
    stub_plan(monkeypatch, [SHOTS[0]], planned)
    cached = {"n": 1, "chapter_hash": "different", "shots": [dict(SHOTS[1])]}
    plan = designer.render_chapter(cfg, mini, 1, {}, verbose=False, cached=cached)
    assert planned == [1] and plan["shots"][0]["prompt"] == "A harbour."


def test_render_chapter_replan_flag_forces_new_plan(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    planned = []
    stub_plan(monkeypatch, [SHOTS[0]], planned)
    cached = {"n": 1, "chapter_hash": mini.chapter_hash(1), "shots": [dict(SHOTS[1])]}
    designer.render_chapter(cfg, mini, 1, {}, verbose=False, cached=cached, replan=True)
    assert planned == [1]


def test_render_chapter_warns_about_orphan_images(mini, cfg, gem, monkeypatch, capsys):
    styled(mini)
    mini_narrated(mini)
    stub_plan(monkeypatch, [SHOTS[0]])
    mini.scene_image(1, 0).write_bytes(b"orphan")
    designer.render_chapter(cfg, mini, 1, {})
    out = capsys.readouterr().out
    assert "!! 1 image(s) on disk with no plan" in out and "01-00.jpg" in out
    assert mini.scene_image(1, 0).read_bytes() == b"orphan"          # still reused


def test_render_chapter_saves_plan_before_paying_for_images(mini, cfg, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    stub_plan(monkeypatch, [SHOTS[0]])
    order = []
    monkeypatch.setattr(designer, "_client", lambda: FakeGemini(empty=True))
    with pytest.raises(RuntimeError, match="no image returned"):
        designer.render_chapter(cfg, mini, 1, {}, verbose=False, save=lambda p: order.append("saved"))
    assert order == ["saved"]
    assert mini.manifest()["spend"] == {}                             # failed call not billed


# --- design_all --------------------------------------------------------------

def test_design_all_requires_timeline(mini, cfg, gem):
    styled(mini)
    with pytest.raises(RuntimeError, match="chapter 1 has no timeline; run the record stage first"):
        designer.design_all(cfg, mini, verbose=False)
    assert mini.stage_status("design") == "running"      # as it stands: left running on failure


def test_design_all_mini_end_to_end(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    stub_plan(monkeypatch, SHOTS)
    designer.design_all(cfg, mini, verbose=False)
    assert mini.stage_status("design") == "done"
    scenes = json.loads(mini.scenes_path.read_text())
    assert sorted(scenes) == ["1", "2"]
    assert scenes["2"]["shots"][0]["image"] == "images/02-00.jpg"
    assert mini.manifest()["spend"]["gemini_images"] == 2 + 4        # 2 portraits + 2 chapters x 2 shots


def test_design_all_resume_keeps_scenes_and_images(mini, cfg, gem, monkeypatch):
    styled(mini)
    mini_narrated(mini)
    planned = []
    stub_plan(monkeypatch, SHOTS, planned)
    designer.design_all(cfg, mini, verbose=False)
    n_calls = len(gem.calls)
    designer.design_all(cfg, mini, verbose=False)
    assert len(gem.calls) == n_calls and planned == [1, 2]


def test_design_all_real_story_uses_cached_plan(story, cfg, gem, real_bibles, monkeypatch):
    def no_plan(*a, **k):
        raise AssertionError("plan_shots must not run: scenes.json already has every plan")
    monkeypatch.setattr(designer, "plan_shots", no_plan)
    designer.design_all(cfg, story, verbose=False)
    assert story.stage_status("design") == "done"
    # 3 new portraits (the other 3 faces come from the bible) + 8 chapters x 4 shots
    assert len(gem.calls) == 3 + 32
    assert all(story.scene_image(n, i).exists() for n in range(1, 9) for i in range(4))
    scenes = json.loads(story.scenes_path.read_text())
    assert sorted(scenes, key=int) == [str(i) for i in range(1, 9)]
    assert scenes["3"]["chapter_hash"] == story.chapter_hash(3)
