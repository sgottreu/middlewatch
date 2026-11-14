# Ambience pipeline — design

*Written 2026-08-28.*

Long-form soundscapes with looping visuals, in two modes: tied to a finished
story, or free-standing from a prompt. This document is the plan and the argument
for it.

Only the procedural synth is built — see [synth.md](synth.md) for what exists
and [calibration.md](calibration.md) for how settled it is. Everything else here
is design. [index.md](index.md) is the map.

It assumes you have read [the story architecture](../story/architecture.md).
Everything below either mirrors that design or says explicitly why it doesn't.

## Two modes

| Mode | Command | Input |
|---|---|---|
| Story-tied | `cli new --from-story stories/<slug>` | A finished story bundle — its bible, pinned art style, cast portraits |
| One-off | `cli new --scene "rain on a library window, candlelight" [--bible amberlight]` | A prompt, and optionally a series to belong to |

Both write `ambience/<slug>/` and both stop at a review gate before anything is
paid for. The gate is the point of the one-off mode: it prints the proposed
audio layers and waits, so you approve a soundscape rather than discover one.

`--bible` on a one-off is what keeps a free-standing video inside a series. It
pulls the art style and the setting's canon, so *Middle Watch: Amberlight — The
Library, 3 Hours* looks like Amberlight even though no story generated it. That
is the cross-promotion mechanism, and it costs one flag.

---

## The dial

`--hours {1,3,8}`, pinned at creation, exactly as `--length` is pinned on a
story. One table drives everything downstream:

```python
HOURS: dict[int, dict[str, Any]] = {
    1: {"seconds": 3600,  "images": 1, "loop_s": 40, "accent_density_scale": 1.0},
    3: {"seconds": 10800, "images": 4, "loop_s": 40, "accent_density_scale": 0.8},
    8: {"seconds": 28800, "images": 6, "loop_s": 40, "accent_density_scale": 0.5},
}
```

Accent density falls as duration rises. A one-hour fireside video can afford a
log settling every four minutes; the same rate across eight hours is eight
hundred events at somebody's bedside. The 8h tier is sleep content and should be
close to featureless.

Unknown values raise rather than falling back, for the same reason
`length_spec()` does — a typo'd duration that quietly rendered one hour would
not be visible until after the SFX calls were billed.

---

## Stages

```
STAGES = ["concept", "sound", "design", "direct"]
```

| Command | Stage | Writes | Costs |
|---|---|---|---|
| `new` | concept | `concept.json`, `ambience.md` | one Anthropic call |
| `revise <a> "…"` | concept | rewrites `concept.json` | one Anthropic call |
| `approve <a>` | — | flips `concept` to `approved` | free |
| `sound <a> [--dry-run]` | sound | `audio/stems/*.wav`, `audio/mix.flac` | SFX calls, cache misses only |
| `design <a>` | design | `visual/images/*.png`, `visual/loops/*.mp4` | Gemini images |
| `direct <a> [--force]` | direct | `render/final.mp4` | free |
| `status <a>` | — | — | free |
| `all` | — | everything, no gate | — |

Same shape as stories: separate stages, each skipping work already on disk, so a
failure at `direct` costs an ffmpeg run and not an audio bill. `sound --dry-run`
is the analogue of `record --dry-run` — it resolves every layer, reports which
clips are cache hits and which will be generated, and synthesizes nothing.

Four commands cost nothing and need no credentials: `approve`, `status`,
`direct`, and `sound --dry-run`. `direct` being free is a real difference from
the story pipeline and worth exploiting — re-render encodes freely.

`sound` and `design` are independent and could run concurrently. Don't build
that until there is a reason; a serial run is easier to resume.

---

## The bundle

```
ambience/<slug>/
  manifest.json          stage state, spend, pinned decisions, RNG seed
  concept.json           the soundscape plan — the file you hand-edit at the gate
  ambience.md            human-readable view; regenerated, never a source
  audio/
    stems/<id>.wav       one full-length rendered layer each
    mix.flac
  visual/
    images/01.png        scene stills
    loops/01.mp4         one seamless motion loop per image
    video.mp4            the macro-loop
  render/
    final.mp4
    thumbnail.png
```

Gitignored, like `stories/`. `ambience.md` is the one file that is a *view* —
everything in it derives from the manifest and the concept, so deleting it loses
nothing. Same exception `story.md` is.

**On sharing `Bundle`:** don't import `story_pipeline.bundle` from
`ambience_pipeline`. The manifest half (`set_stage`, `pin`, `add_spend`,
`stage_status`) is about fifty lines and identical; the path half is entirely
different. Copy the fifty lines. Two pipelines is not enough evidence to design a
shared abstraction, and a `common/` package that exists to serve one caller each
is worse than the duplication. Revisit if a third pipeline appears.

**Pin the seed.** Procedural audio is deterministic given a seed, and a re-render
that quietly produced different rain would be the ambience equivalent of an
unpinned art style. It goes in `pinned` alongside everything else.

---

## `concept.json`

The load-bearing artifact. Everything downstream reads it and it is what you
edit at the gate.

Four layer roles:

- **bed** — continuous, always audible. One or two. Rain, wind, surf, room tone.
- **texture** — semi-continuous, slowly modulated. Fire crackle, leaves, traffic.
- **accent** — sparse one-shots, stochastically scheduled. A log settling, a
  clock chime, a gull, a distant carriage.
- **tonal** — optional low drone. Very quiet, usually absent.

```json
{
  "slug": "amberlight-library-rain",
  "title": "Rain on the Library Window",
  "series": "Middle Watch: Amberlight",
  "hours": 3,
  "seed": 8814423,
  "scene": "A Regency library at night, tall rain-streaked windows, low fire.",
  "layers": [
    {
      "id": "rain_on_glass",
      "role": "bed",
      "source": "procedural",
      "recipe": {"type": "rain", "surface": "glass", "intensity": 0.45},
      "gain_db": -14.0, "pan": 0.0, "hpf_hz": 90, "lpf_hz": 9000,
      "movement": {"type": "lfo", "depth_db": 2.0, "period_s": 110}
    },
    {
      "id": "log_settle",
      "role": "accent",
      "source": "generated",
      "prompt": "a burning log shifting and settling in a fireplace, close mic, no music",
      "variants": 4,
      "gain_db": -24.0, "pan_jitter": 0.35,
      "density_per_min": 0.25
    }
  ],
  "visual": {"effects": ["rain_streaks", "firelight_flicker"]}
}
```

The concept agent goes through `llm.complete_json()` like every other thinking
agent, with `models.concept` in config defaulting to `claude-sonnet-5`. It needs
a validator equivalent to `_validate_outline()` — an unbounded `density_per_min`
or a missing `gain_db` should fail at the gate, not eight hours into a render.

---

## Audio

The routing rule: **procedural for anything that is fundamentally filtered noise,
generated for anything with a recognisable identity.** If a listener would name
it, generate it. If they would only describe it, synthesize it.

**The procedural half is not a fallback for lacking a sound library.** Worth
stating, because it looks like one. The beds and textures run continuously for
one to eight hours, and a library clip has to be looped to do that — audible
looping being the characteristic failure of this entire niche. Even with a
perfect library you would still want the beds generated: a three-hour rain bed
from a sample needs granular resynthesis to stop repeating, where generated rain
is non-repeating by construction, costs nothing per render, carries no licensing
question, and reproduces from a seed. A library covers **accents**, which is the
other half of the rule, not a substitute for it.

**Fire sits on the wrong side of this rule, and that is a known risk.** Rain's
bed, wind, surf and room tone are genuinely filtered noise, which is what
synthesis is good at. Fire is mostly *events* — pops with internal structure —
and that is why it has taken four rounds of tuning and is still unsigned. If it
does not convince after one or two more, the correct move is not a fifth tuning
pass: it is to reclassify fire as a generated layer and keep a procedural bed
underneath only for the low roar. The rule is a heuristic about material, not a
commitment, and fire is the case that tests where its boundary actually falls.

**Procedural** — numpy and scipy, no API, no cost, non-repeating by construction:

| Layer | Recipe |
|---|---|
| Rain | White noise → bandpass 500–8k, plus Poisson-scheduled drop transients → small reverb. `surface` shifts the band and the transient decay |
| Wind | Brown noise → resonant bandpass, slow LFOs on centre frequency and Q → amplitude LFO |
| Fire | Brown noise bed + Poisson crackle bursts, exponential decay, bandpass 1–4k, ~4/s |
| Ocean | Pink noise → randomised swell envelope 0.08–0.15 Hz → lowpass 4k |
| Room tone | Filtered brown noise + two very quiet low sine partials |
| Distant traffic | Brown noise → lowpass 400 Hz → slow amplitude drift |
| Thunder | Low-band burst → long decay convolution |

**Generated** — birds, crickets, tavern murmur, carriage and hooves, bells,
doors, page turns, footsteps, mechanical and sci-fi textures.

### The provider is already in `.env`

ElevenLabs sound effects, on the key `ELEVENLABS_API_KEY` the actor already
uses. No new account, no new credential, and the same
`usd_per_1k_credits` conversion.

That inherits the licensing problem the story pipeline already has, and it is
worse here. **ElevenLabs grants no commercial rights on its free tier**
([setup](../story/setup.md)). For a story that affects the narration; for an
ambience video the generated audio *is* the video. Confirm the plan tier before
the cache fills with clips that cannot be monetised, because the cache is
designed to be permanent.

### The cache

`assets/sfx_cache/<sha256(prompt+params)>.wav`, shared across every project. The
directory already exists. Over time this converges on a curated library and the
marginal cost of a new video trends toward zero — it is the reason hybrid beats
pure generation, and it is the reason the licensing question has to be settled
first rather than later.

The cache is **gitignored**, decided deliberately rather than by default. The
argument for committing it is the one already made for `bibles/` — generated
assets reused across every project are content, not build output. The argument
against won: it is unbounded binary that git cannot forget once committed, and
every entry is regenerable from the prompt in `concept.json`. Ignoring is
reversible in one line; committing is not. Regenerating costs money, so the cache
wants a backup that isn't git.

### Looping is the hard problem

Generated clips are 5–30 seconds and you need up to eight hours. Verbatim
looping is the characteristic failure of this niche — a listener consciously
notices a repeat within about three cycles, and for sleep content that is fatal.

Three techniques, by role:

- **Granular resynthesis** for beds and textures. Chop the source into 0.5–2s
  grains, scatter with randomised ordering, ±2% pitch jitter, equal-power
  crossfades. A statistically identical, indefinitely non-repeating stream from
  one clip. This is the core trick and everything else is detail.
- **Variant stacking**. 3–5 variants per generated bed, rotated on a schedule
  whose period is not an integer multiple of anything else.
- **Poisson scheduling** for accents. Inter-onset intervals drawn from an
  exponential distribution at `density_per_min`, with per-instance pan and gain
  jitter. No rhythm ever emerges.

Procedural layers need none of this.

### Render in blocks

A three-hour stereo float32 buffer is about 2 GB and an eight-hour one is 5.5.
Render in 60-second blocks, streaming each to disk, carrying LFO phase and grain
state across boundaries. Makes the 8h tier feasible at all and gives a progress
line for free — which the story pipeline's stages already set the precedent for.

### Loudness

- Integrated **−20 to −23 LUFS**, true peak **−3 dBTP**. YouTube normalises to
  −14, so anything hotter is turned down anyway, and ambient content wants to sit
  quiet.
- Narrow dynamic range. Accents capped at −24 dB relative to the bed, rolled off
  above 10 kHz, hard-limited at the sum. Nothing should wake anyone.
- 10s fade in, 20s fade out.
- Decorrelate stacked noise layers with short delays or independent seeds, or
  they will partially cancel.

Gate the render on `ffmpeg -af ebur128` and fail loudly if it lands outside the
window, the way `price()` raises on an unknown model rather than recording zero.

---

## Visual

### One short loop, streamed

Never encode hours of video. Encode a seamless 40-second loop, then
`-stream_loop -1` it against the long audio with `-c:v copy`. An eight-hour
render becomes an audio encode plus a forty-second video encode. The director
already mixes with `-c:v copy` when subtitles aren't burned in, so the pattern is
established.

The constraint this imposes is absolute: **every overlay effect must be exactly
periodic with the loop length.** Frame N must equal frame 0.

| Effect | Approach | Seamless because |
|---|---|---|
| Rain streaks | Tiled translucent sprite, scrolled vertically | Tile height divides total scroll |
| Snow, embers | Particle system with wrap-around | Positions wrap at the period |
| Firelight flicker | Brightness and colour-temp modulation over a mask | Driven by looped smooth noise |
| Fog, cloud | Scrolling tiled noise at low opacity | Tiling |
| Water shimmer | Sine displacement over a mask | Integer cycles per loop |

Write this as a small numpy/PIL compositor emitting PNG frames, not as an ffmpeg
filter graph. The periodicity constraint is checkable in code — assert frame N
equals frame 0 within tolerance — and unreadable as a filter string.

### Multiple images

At 3 hours and above the dial calls for 4–6 images. Each gets its own motion
loop; the macro-loop holds each for 4–6 minutes and crossfades 10s into the next,
cycling once through the set. Stream-loop *that*, so a 4-image 3h video encodes
20 minutes of video instead of 180.

**Reuse `_stitch()` from `agents/director.py`.** It already does progressive
`xfade` with the offset accumulation correct, and the comment above the `extra`
calculation in `direct()` records the bug worth not rediscovering: an xfade eats
its duration from the *start* of the incoming clip, so every clip but the first
needs those frames back. That applies here identically. `_run()`, `_stitch()` and
the padding logic want extracting into a shared ffmpeg module rather than
reimplementing — this is the one place where sharing code across the two
pipelines is clearly right, because the alternative is reintroducing a fixed bug.

Ken Burns is wrong here and `video.ken_burns` should not be inherited. A slow
push over forty seconds either reads as drift or has to loop, and a zoom cannot
loop seamlessly.

---

## Story-tied mode

Reads the story bundle directly:

| Reads | For |
|---|---|
| `manifest.json` → `pinned.art_style` | Visual continuity with the story video |
| `outline.json` → `setting`, `title`, `series`, `bible` | Scene material and series identity |
| `images/scenes.json` | Existing shot prompts, as candidate scenes |
| `images/cast/*.png` | Character refs, if a figure appears in frame |

The art style is **inherited, not regenerated**. Two style strings from one
description are not the same style, which is the same argument
`build_cast_sheet()` already makes about reusing a recurring character's
portrait rather than generating a second one.

The manifest records `source_story`. Write the reciprocal link into the story's
manifest too, so the relationship is discoverable from either end without a scan.

One-off mode has no story and no genre, so it has no art style to inherit. The
concept agent proposes one and pins it — see the defect below, which this hits
first.

---

## The ledger

Both pipelines record spend to one `ledger/`, and the schema is now ready for the
second one. `COST_FIELDS` carries a `pipeline` column, `report --by pipeline`
groups on it, and `record_cost()` validates it against `PIPELINES` rather than
defaulting. Ambience fills `pipeline`, `slug`, `title` and `series` and leaves
`genre` and `flavor` blank, which is what they are.

The validation is deliberate asymmetry. A missing `genre` costs one line in one
report; a missing `pipeline` merges two channels' economics into a single number
that nothing downstream could tell was wrong. So it raises.

Ambience needs its own `ledger_story()` on its own bundle, returning
`{"pipeline": "ambience", ...}`. That is the whole of the ledger work.

`record_elevenlabs()` now exists, recording credits rather than dollars so that
an append-only row never bakes in today's plan rate. Narration and SFX use it
identically, differing only in `model`.

---

## Defects found and fixed, 2026-08-28

Four, found while reading the pipeline against this design. Three were latent for
stories and immediate for ambience. All are fixed; the verification is in the
last section.

**1. `rates.json` priced models the pipeline does not run — in every provider.**
`CLAUDE.md` recorded half of it: rates priced `claude-opus-4-6` /
`claude-sonnet-4-6` while config runs `claude-opus-5` / `claude-sonnet-5`. The
same drift existed for images and was recorded nowhere — rates priced the
marketing name `nano-banana-2`, config runs the API id `gemini-3.1-flash-image`.
`price()` raises on an unknown model by design, so `record_gemini()` would have
failed on the first image ever recorded. There was no `elevenlabs` provider at
all, so narration was unpriceable too.

Two further errors surfaced while correcting it, both understating cost:

- The Anthropic figures were a *generation* stale, not just misnamed —
  `$15/$75` per MTok is Opus 4.1 pricing. Opus 5 is `$5/$25`, Sonnet 5 `$2/$10`.
- Gemini images were priced at `$0.04`. At `image_size: 2K`, which is what
  `config.yaml` sets, 3.1 Flash Image is **$0.050** — images are billed by output
  tokens, so the price moves with the resolution setting. Roughly 25% under per
  image, on the stage that mints 15–32 of them per story.

Google's own page contradicts itself here: the table states `$30/1M` output
tokens and quotes `$0.050` for a 2K image, while a footnote below it quotes
`$60/1M` and `$0.101`. The table is internally consistent (1,680 tokens ×
$30/1M = $0.0504) and is what `rates.json` now uses, with the discrepancy
recorded in the file. Check it against a real invoice.

Not fixed, and noted in `rates.json`: character reference images are billed as
*input* at $0.25/1M and nothing records them. At up to four refs per scene call
that is a small, systematic undercount.

**2. `cfg["art_style"]` had no default.** `designer.py` read
`b.pinned("art_style", cfg["art_style"])` in two places, but `art_style` was
absent from `DEFAULTS` and from `config.example.yaml`. Any bundle reaching the
designer without a pinned style raised `KeyError` against a config key that had
never existed. Stories are protected in practice — `text.py` pins one at ideate
from the bible or the genre — but a one-off ambience has no genre behind it, so
this was the first thing that would have broken.

Now a single `_art_style(cfg, b)` helper, used by both call sites: pinned value
wins, config fallback second, and a `RuntimeError` naming the bundle and saying
what to do if neither exists. `art_style: null` is in `DEFAULTS` and
`config.example.yaml` as a documented escape hatch — null still errors rather
than passing `None` into an image prompt.

**3. `assets/sfx_cache/` was neither ignored nor deliberately committed.** Now
gitignored, with a `.gitkeep` so the directory survives, and the reasoning in the
file. See the cache section above.

**4. `cleanup.sh` hardcoded an absolute `ROOT`.** It resolves from
`BASH_SOURCE` now. The old path meant the script's own guard clause — which
exists precisely to catch a wrong working directory — would have aborted every
run on any other machine while looking somewhere the repo was never going to be.

### Also done, same pass

The ledger schema, which was the one change with a closing window. `pipeline` is
on `COST_FIELDS`, validated rather than defaulted; `genre`, `flavor`, `title` and
`series` are pinned at ideate and read back through `Bundle.ledger_story()`;
`report --by pipeline` groups on it. Schema changes from here go through
`python3 -m ledger.migrate`, which reconciles the CSV to `COST_FIELDS` —
additively, atomically, and without dropping a column that holds data.

### Still outstanding

**Nothing calls the ledger.** `add_spend` writes unit counts into the manifest;
no stage writes a costed row. `costs.csv` does not exist and `report` has nothing
to read, so the schema above is correct and entirely unexercised. Ambience should
wire its stages from the first commit rather than inherit the habit, and the
story pipeline needs the one line per call that `ledger/README.md` documents.

---

## Build order

1. **Procedural synth module.** Rain, wind, fire, ocean, room tone. No API keys,
   no ledger, no bundle. A CLI that renders 60s of any recipe to a wav so you can
   tune by ear. This is the piece with the most uncertainty and the least
   coupling — build it first and alone.
2. **Block-streaming mixer.** Layers to full-length stems to a mix, with the
   EBU R128 gate.
3. **Motion compositor.** One image, rain and flicker, with a seamlessness
   assertion in the tests.
4. **Bundle, CLI, `direct`.** Now a hand-written `concept.json` produces a
   finished video for zero API spend.
5. **Concept agent and the gate.** The schema is by now proven by the JSON files
   you wrote by hand, which is the right order — the same reason
   `_validate_outline()` exists.
6. **Generated layers and the cache.** Ledger work lands here.
7. **Story-tied mode.** A thin layer over all of the above.

Steps 1–4 give a complete, publishable video with no API calls at all. That is
the milestone worth aiming at, and it is unusually cheap to reach.

---

## Assumed, not measured

In the spirit of `WPM = 150` — these are conventions chosen to get started, and
every one of them is a guess until a render exists to judge:

- Grain size 0.5–2s and ±2% pitch jitter as the point where granular
  resynthesis stops sounding granular.
- 40 seconds as a loop length long enough not to read as a loop.
- The accent density scaling across the three duration tiers.
- −20 LUFS as the right level for sleep content specifically.
- 4–6 minutes per image before the crossfade.

Write these down in `docs/ambience/calibration.md` when the first video exists,
the way the length dial has one.

## Open questions

- **Thumbnails.** The README lists thumbnail generation as not built. Ambience
  needs it more than stories do — the thumbnail is most of the click on a
  soundscape. Worth building here and sharing back.
- **The tonal layer and Content ID.** Procedural drones are safe. Anything from a
  music model risks a claim against hours of content at once. Recommend
  procedural-only for tonal until there is evidence otherwise. Music as the
  *subject* of a video rather than furniture under one is a separate case with
  its own plan — see [music.md](music.md), which covers the providers, the
  licensing gate, and the four techniques for making a ten-minute generated
  track run for hours.
- **YouTube's reused-content policy.** Long ambient uploads attract scrutiny.
  Fully original generated audio and visuals is the right side of that line, and
  is a further argument against stock loops.
- **Upload.** Still manual for both pipelines. Two channels doubles the argument
  for building it.

---

## Verifying the rate table

The failure mode `price()` is built to prevent is a drifted key, and the check
for it is mechanical: read the models out of `config.py` rather than retyping
them, and price one unit of each. It needs no credentials and no network.

```bash
python3 - <<'PY'
import ast, sys; sys.path.insert(0, ".")
from ledger import ledger
d = next(ast.literal_eval(n.value) for n in ast.walk(ast.parse(open("story_pipeline/config.py").read()))
         if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "DEFAULTS")
for prov, model, unit in (
    *(("anthropic", m, u) for m in set(d["models"].values())
      for u in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")),
    ("gemini", d["gemini"]["model"], "images"),
    ("elevenlabs", d["elevenlabs"]["model"], "credits"),
    ("elevenlabs", "sound_effects", "credits"),
    ("polly", d["polly"]["engine"], "characters"),
):
    print(f"ok {prov}/{model} {unit}", ledger.price(prov, model, unit, 1)[1])
PY
```

Any rename on either side raises `KeyError` here instead of at the first billed
call. Worth running whenever a model is changed in `config.yaml`, and it belongs
in a test once there are tests.

---

[← Root README](../../README.md)  ·  [Story docs](../story/index.md)
