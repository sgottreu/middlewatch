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
import threading
import time
from contextlib import contextmanager
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


MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


# --------------------------------------------------------------------------- #
# Saying what is happening
# --------------------------------------------------------------------------- #
#
# An image is the better part of a minute and a story is nearly forty of them,
# so `design` is half an hour of a terminal doing nothing visible. Without a
# heartbeat there is no way to tell a slow call from a hung one, and the honest
# answer to "is it stuck?" was to wait and find out.


class Progress:
    """Counts images against an estimate, and can say how long is left."""

    def __init__(self, total: int, verbose: bool = True):
        self.total, self.done, self.verbose = total, 0, verbose
        self.started = time.monotonic()

    def tick(self) -> None:
        self.done += 1

    def line(self) -> str:
        elapsed = time.monotonic() - self.started
        if not self.done:
            return f"0/{self.total}"
        each = elapsed / self.done
        left = max(self.total - self.done, 0) * each
        return (f"{self.done}/{self.total} · {each:.0f}s each · "
                + (f"~{left / 60:.0f} min left" if left > 90 else f"~{left:.0f}s left"))

    def say(self, text: str) -> None:
        if self.verbose:
            print(text, flush=True)


@contextmanager
def waiting(p: Progress, every: float = 15.0):
    """Print a heartbeat while a single call is in flight.

    A daemon thread rather than a spinner: this output is read live in a
    terminal and later in a log, and a carriage-return animation is unreadable
    in the second one.
    """
    stop = threading.Event()

    def beat():
        t0 = time.monotonic()
        while not stop.wait(every):
            p.say(f"      ...still generating ({time.monotonic() - t0:.0f}s)")

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    t0 = time.monotonic()
    try:
        yield
    finally:
        stop.set()
        p.last_call = time.monotonic() - t0


def _generate(client, cfg: dict, prompt: str, refs: list[Path]) -> bytes:
    g = cfg["gemini"]
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for ref in refs[: g["max_character_refs"]]:
        parts.append(
            {
                "type": "image",
                "data": base64.b64encode(ref.read_bytes()).decode(),
                # Whatever that portrait actually is. References come off disk
                # and predate this — a .png labelled image/jpeg is rejected.
                "mime_type": MIME.get(ref.suffix.lower(), "image/jpeg"),
            }
        )

    interaction = client.interactions.create(
        model=g["model"],
        input=parts,
        response_format={
            "type": "image",
            # The API supports JPEG only — asking for PNG is a 400 before a
            # single image is generated. Config carries it so a later format
            # is a config change rather than a code change.
            "mime_type": g.get("image_mime", "image/jpeg"),
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


def build_cast_sheet(cfg: dict, b: Bundle, verbose: bool = True,
                     p: Progress | None = None) -> dict[str, Path]:
    client = _client()
    p = p or Progress(len(b.outline().get("cast", [])), verbose)
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
        p.say(f"  cast: {member['name']}... [{p.line()}]")
        path.parent.mkdir(parents=True, exist_ok=True)
        with waiting(p):
            data = _generate(client, cfg, prompt, refs=[])
        path.write_bytes(data)
        b.add_spend("gemini_images", 1)
        p.tick()
        p.say(f"    wrote {path.name} ({len(data) / 1e6:.1f} MB) "
              f"in {getattr(p, 'last_call', 0):.0f}s")

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
                p.say(f"    promoted into {bible.series} — every later story "
                      f"reuses this face")
        elif bible:
            # Not in the bible's recurring cast, so the face belongs to this
            # story alone. Said out loud because the silence here looked exactly
            # like a portrait that failed to save.
            p.say(f"    guest of this story — not added to {bible.series}")

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


def render_chapter(cfg: dict, b: Bundle, n: int, sheet: dict[str, Path], verbose=True,
                   p: Progress | None = None, cached: dict | None = None,
                   replan: bool = False, save=None) -> dict:
    client = _client()
    style = _art_style(cfg, b)
    p = p or Progress(0, verbose)

    # Re-use the plan rather than paying for a new one. Planning is a model call,
    # so a resumed run was buying a fresh set of shots for chapters whose images
    # already existed — and a new plan can choose different moments, which would
    # leave `scenes.json` describing pictures that were never drawn. The plan is
    # tied to the narration it was built from: re-record a chapter and it is
    # planned again, because the moments it picked are gone.
    stamp = b.timeline(n).get("chapter_hash")
    # A plan written before stamping existed carries no hash and is trusted
    # rather than bought again — the same fallback the timeline and the editor's
    # verdict already use, for the same reason: it was right when it was made.
    keep = cached and not replan and cached.get("chapter_hash", stamp) == stamp
    if keep:
        plan = cached
        plan["chapter_hash"] = stamp
        p.say(f"  chapter {n}: keeping the shot plan already on disk")
    else:
        drawn = [b.scene_image(n, i).name for i in range(12) if b.scene_image(n, i).exists()]
        if drawn and not replan:
            # Images without the plan that produced them. They keep their
            # numbers, so a fresh plan would quietly adopt them for moments they
            # were never drawn for. Say it plainly rather than shipping a
            # picture that does not match its sentence.
            p.say(f"  chapter {n}: !! {len(drawn)} image(s) on disk with no plan "
                  f"to go with them — {', '.join(drawn)}")
            p.say(f"     They were drawn from a plan lost when a run stopped "
                  f"part-way. A new plan may choose different moments, and they "
                  f"would be reused for the wrong ones.")
            p.say(f"     Delete them and re-run to redraw at "
                  f"${len(drawn) * cfg.get('gemini', {}).get('usd_per_image', 0.05):.2f}, "
                  f"or keep them knowing the mismatch.")
        with waiting(p):
            plan = plan_shots(cfg, b, n)
        plan["chapter_hash"] = stamp

    # On disk before a single image is paid for. The plan is the cheap half and
    # the recoverable half: losing it is what stranded chapter two's images with
    # nothing to describe them.
    if save:
        save(plan)

    for i, shot in enumerate(plan["shots"]):
        path = b.scene_image(n, i)
        shot["image"] = str(path.relative_to(b.root))
        if path.exists():
            p.say(f"  chapter {n} shot {i + 1}/{len(plan['shots'])}: "
                  f"{path.name} exists, skipping")
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
        p.say(f"  chapter {n} shot {i + 1}/{len(plan['shots'])} "
              f"at {shot['at_ms'] / 1000:.0f}s... [{p.line()}]")
        path.parent.mkdir(parents=True, exist_ok=True)
        with waiting(p):
            data = _generate(client, cfg, prompt, refs)
        # Written before the line that announces it. The old order printed the
        # shot it was about to start, so the last line on screen named a file
        # that did not exist yet — which reads as a lost image rather than as
        # one in flight.
        path.write_bytes(data)
        b.add_spend("gemini_images", 1)
        p.tick()
        p.say(f"    wrote {path.name} ({len(data) / 1e6:.1f} MB) "
              f"in {getattr(p, 'last_call', 0):.0f}s")

    return plan


def design_all(cfg: dict, b: Bundle, verbose: bool = True, replan: bool = False) -> None:
    b.set_stage("design", "running")

    # What is left to draw, so the count and the estimate mean something on a
    # resumed run as well as a fresh one.
    outline = b.outline()
    lspec = textagent._length_of(b)
    per_chapter = b.pinned("scenes_per_chapter") or textagent._scenes_per_chapter(cfg, lspec)
    todo = sum(1 for m in outline.get("cast", []) if not b.cast_image(m["name"]).exists())
    for n in b.chapter_numbers():
        todo += sum(1 for i in range(per_chapter) if not b.scene_image(n, i).exists())
    p = Progress(todo, verbose)
    rate = cfg.get("gemini", {}).get("usd_per_image", 0.05)
    p.say(f"  {todo} image(s) to generate, about ${todo * rate:.2f}")

    sheet = build_cast_sheet(cfg, b, verbose, p)

    scenes = {}
    if b.scenes_path.exists():
        scenes = json.loads(b.scenes_path.read_text())

    for n in b.chapter_numbers():
        if not b.timeline_path(n).exists():
            raise RuntimeError(f"chapter {n} has no timeline; run the record stage first")
        def save(plan, _n=n):
            scenes[str(_n)] = plan
            b.scenes_path.parent.mkdir(parents=True, exist_ok=True)
            b.scenes_path.write_text(json.dumps(scenes, indent=2) + "\n")

        scenes[str(n)] = render_chapter(cfg, b, n, sheet, verbose, p,
                                        cached=scenes.get(str(n)), replan=replan,
                                        save=save)
        save(scenes[str(n)])

    b.set_stage("design", "done")
    p.say(f"  done — {p.done} image(s) in "
          f"{(time.monotonic() - p.started) / 60:.1f} min")
