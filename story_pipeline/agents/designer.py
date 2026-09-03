"""The designer. Two phases, and the order is the whole trick.

Phase one generates a reference portrait per cast member. Phase two passes those
portraits back in as character references on every scene call. Nano Banana 2
accepts up to four character reference images and holds faces across them, so
Elizabeth looks like the same woman in chapter five as in chapter one. Skip phase
one and the visual identity drifts by the third chapter, which is the single most
common way this kind of video looks cheap.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

from google import genai

from . import text as textagent
from .. import bibles, changelog, genres, llm
from ..bundle import Bundle
from ..config import prompt_text

NEGATIVE = (
    " No text, no lettering, no captions, no speech bubbles, no watermark, "
    "no signature, no frame or border."
)


def _client():
    return genai.Client()  # reads GEMINI_API_KEY


def _generate(client, cfg: dict, prompt: str, refs: list[Path]) -> bytes:
    g = cfg["gemini"]
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for ref in refs[: g["max_character_refs"]]:
        parts.append(
            {
                "type": "image",
                "data": base64.b64encode(ref.read_bytes()).decode(),
                "mime_type": "image/png",
            }
        )

    interaction = client.interactions.create(
        model=g["model"],
        input=parts,
        response_format={
            "type": "image",
            "mime_type": "image/png",
            "aspect_ratio": g["aspect_ratio"],
            "image_size": g["image_size"],
        },
        generation_config={"thinking_level": g["thinking_level"]},
    )
    image = interaction.output_image
    if image is None:
        raise RuntimeError(f"no image returned for prompt: {prompt[:120]}...")
    return base64.b64decode(image.data)


def _art_style(cfg: dict, b: Bundle) -> str:
    """The style pinned into this bundle, or the config fallback.

    `ideate` pins this from the series bible or the genre frontmatter, so a story
    bundle that reached the designer normally always carries one. A bundle that
    doesn't — hand-assembled, or made by a pipeline with no genre behind it —
    used to raise KeyError against a config key that has never existed in
    DEFAULTS. Fail with a sentence that says what to do instead.
    """
    style = b.pinned("art_style") or cfg.get("art_style")
    if not style:
        raise RuntimeError(
            f"No art style for {b.root}: nothing pinned in manifest.json and no "
            "`art_style` in config.yaml. `ideate` pins one from the series bible "
            "or the genre; pin it by hand, or set a config fallback."
        )
    return style


# --------------------------------------------------------------------------- #
# Phase one: the cast sheet
# --------------------------------------------------------------------------- #


def build_cast_sheet(cfg: dict, b: Bundle, verbose: bool = True) -> dict[str, Path]:
    client = _client()
    outline = b.outline()
    style = _art_style(cfg, b)
    bible = bibles.load(outline["bible"], cfg["bibles_dir"]) if outline.get("bible") else None
    sheet: dict[str, Path] = {}

    for member in outline["cast"]:
        path = b.cast_image(member["name"])
        sheet[member["name"]] = path

        if path.exists():
            if verbose:
                print(f"  cast: {member['name']} exists, skipping")
            continue

        # A recurring character already has a face somewhere in the series. Reuse
        # it rather than generating a new one: two portraits from the same
        # description are not the same person, and across a playlist that reads
        # as recasting the role between episodes.
        if bible:
            existing = bible.portrait(member["name"])
            if existing:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(existing, path)
                if verbose:
                    print(f"  cast: {member['name']} reused from {bible.series}")
                continue

        prompt = (
            f"Character reference portrait. {member['name']}, "
            f"age {member.get('age', 'about thirty')}. "
            f"{member['appearance']} Wearing: {member['dress']} "
            f"Three-quarter view from the waist up, neutral expression, "
            f"plain pale background, even light, full face clearly visible. "
            f"Style: {style}{NEGATIVE}"
        )
        if verbose:
            print(f"  cast: generating {member['name']}...")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_generate(client, cfg, prompt, refs=[]))
        b.add_spend("gemini_images", 1)

        # Promote it into the series so the next story inherits the same face.
        # This mutates the bible's cast directory, so it goes in the changelog —
        # it is the one change to a series that nobody makes deliberately, and
        # the one most likely to be puzzled over later.
        if bible and bible.member(member["name"]):
            if bible.save_portrait(member["name"], path):
                changelog.append(
                    bible.path,
                    f"Promoted portrait for {member['name']} into "
                    f"`{bible.cast_dir.name}/`. Every later story in this series "
                    f"reuses this face; delete the png to regenerate it.",
                    author=f"pipeline ({b.root.name})",
                    kind="portrait",
                    series=bible.series,
                )
                if verbose:
                    print(f"  cast: {member['name']} saved to {bible.series}")

    return sheet


# --------------------------------------------------------------------------- #
# Phase two: the shots
# --------------------------------------------------------------------------- #


def plan_shots(cfg: dict, b: Bundle, n: int) -> dict:
    """Ask the model which moments to illustrate, anchored to timeline sentences."""
    timeline = b.timeline(n)
    numbered = "\n".join(
        f"[{i}] ({s['start_ms']}ms) {s['speaker']}: {s['text']}"
        for i, s in enumerate(timeline["sentences"])
    )
    # Pinned at ideate time so re-rendering an old story keeps its image count
    # even after the config or the length table changes underneath it.
    lspec = textagent._length_of(b)
    k = b.pinned("scenes_per_chapter") or textagent._scenes_per_chapter(cfg, lspec)

    outline = b.outline()
    genre = genres.load(outline["genre"])
    bible = bibles.load(outline["bible"], cfg["bibles_dir"]) if outline.get("bible") else None
    system = (
        prompt_text("designer")
        # The designer assembles its own prompt rather than going through
        # text._render, so it has to fill {structure} itself. house.md carries
        # that placeholder now, and an unsubstituted one would reach the model.
        .replace("{house}", prompt_text("house"))
        .replace("{structure}", textagent._structure_block(lspec))
        .replace("{boundaries}", prompt_text("boundaries"))
        .replace("{bible_block}", bible.prompt_block() if bible else "")
        .replace("{genre_guide}", genre.guide)
        .replace("{genre_label}", genre.meta.get("label", genre.name))
        .replace("{n}", str(n))
        .replace("{k}", str(k))
    )
    ask = (
        f"# Outline\n\n{json.dumps(b.outline(), indent=2)}\n\n"
        f"# Chapter {n}, numbered sentences with timings\n\n{numbered}\n\n"
        f"Choose {k} shots."
    )

    plan, _ = llm.complete_json(cfg["models"]["designer"], system, ask,
                                max_tokens=textagent._ceiling(cfg, "designer"))
    plan["n"] = n

    # Anchor each shot to a real timestamp and clamp bad indices.
    last = len(timeline["sentences"]) - 1
    for shot in plan["shots"]:
        idx = max(0, min(int(shot.get("sentence_index", 0)), last))
        shot["sentence_index"] = idx
        shot["at_ms"] = timeline["sentences"][idx]["start_ms"]
    plan["shots"].sort(key=lambda s: s["at_ms"])
    return plan


def render_chapter(cfg: dict, b: Bundle, n: int, sheet: dict[str, Path], verbose=True) -> dict:
    client = _client()
    style = _art_style(cfg, b)
    plan = plan_shots(cfg, b, n)

    for i, shot in enumerate(plan["shots"]):
        path = b.scene_image(n, i)
        shot["image"] = str(path.relative_to(b.root))
        if path.exists():
            continue

        refs = [sheet[c] for c in shot.get("characters", []) if c in sheet and sheet[c].exists()]
        who = ""
        if shot.get("characters"):
            who = (
                " The people shown are, in the order of the reference images provided: "
                + ", ".join(shot["characters"])
                + ". Match their faces, hair and clothing to the references exactly."
            )

        prompt = f"{shot['prompt']}{who} Style: {style}{NEGATIVE}"
        if verbose:
            print(f"  chapter {n} shot {i} at {shot['at_ms'] / 1000:.0f}s...")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_generate(client, cfg, prompt, refs))
        b.add_spend("gemini_images", 1)

    return plan


def design_all(cfg: dict, b: Bundle, verbose: bool = True) -> None:
    b.set_stage("design", "running")
    sheet = build_cast_sheet(cfg, b, verbose)

    scenes = {}
    if b.scenes_path.exists():
        scenes = json.loads(b.scenes_path.read_text())

    for n in b.chapter_numbers():
        if not b.timeline_path(n).exists():
            raise RuntimeError(f"chapter {n} has no timeline; run the record stage first")
        scenes[str(n)] = render_chapter(cfg, b, n, sheet, verbose)
        b.scenes_path.write_text(json.dumps(scenes, indent=2) + "\n")

    b.set_stage("design", "done")
