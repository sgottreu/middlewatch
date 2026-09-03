# Middle Watch Ambience — Pipeline Design

Design doc for review. Written without repo access, so anything marked **[assumed]** needs a check against the actual Stories pipeline before implementation.

---

## 1. Goals

Two entry modes, one pipeline:

| Mode | Command | Input |
|---|---|---|
| Story-tied | `ambience from-story <slug>` | `stories/<slug>/` — manifest, scenes, cast sheet, pinned art style |
| One-off | `ambience new "rain on a library window, candlelight"` | Free-text prompt |

The one-off mode's defining feature is a **proposal gate**: the pipeline emits a human-readable soundscape plan and stops, so you approve or edit the audio layers before anything renders.

Design constraints inherited from the Stories pipeline:

- File-based handoffs, never context-passing. Every stage writes to disk.
- `manifest.json` tracks stage state, spend, and pinned decisions.
- Any stage independently resumable.
- Reuse the fixed `xfade` crossfade helper — the padding bug applies here too.

---

## 2. Stage graph

```
concept  →  [PROPOSAL GATE]  →  sound  →  mix  ─┐
                             ↘  visual → motion ─┴→  render  →  publish
```

| Stage | Writes | Notes |
|---|---|---|
| `concept` | `concept.json` | Agent. Scene, mood, duration, art style, full layer plan |
| gate | — | One-off mode only; `--yes` skips. Story mode auto-approves |
| `sound` | `audio/layers/<id>/*.wav` | Resolve each layer to source clips (procedural or generated) |
| `mix` | `audio/mix.flac` | Extend to full duration, level, pan, filter, limit |
| `visual` | `visual/images/*.png` | Gemini, pinned art style |
| `motion` | `visual/loops/*.mp4` | Seamless short loops with overlay effects |
| `render` | `render/final.mp4`, `thumbnail.png` | ffmpeg assembly |
| `publish` | `publish/metadata.json` | Title, description, chapters, tags |

`sound`/`mix` and `visual`/`motion` are independent branches and can run concurrently.

---

## 3. The soundscape plan (`concept.json`)

This is the load-bearing artifact. Everything downstream reads it, and it's the file you hand-edit at the gate.

Four layer roles:

- **bed** — continuous, always audible. 1–2 max. Rain, wind, surf, room tone.
- **texture** — semi-continuous, slowly modulated. Fire crackle, leaves, distant traffic.
- **accent** — sparse one-shots, stochastically scheduled. A log settling, a clock chime, a gull, a distant carriage.
- **tonal** — optional low drone or pad. Very quiet, often absent.

```json
{
  "slug": "amberlight-library-rain",
  "title": "Rain on the Library Window",
  "duration_s": 10800,
  "seed": 8814423,
  "art_style": "<pinned from story manifest or generated>",
  "scene": "A Regency library at night, tall rain-streaked windows, low fire.",
  "layers": [
    {
      "id": "rain_on_glass",
      "role": "bed",
      "source": "procedural",
      "recipe": {"type": "rain", "surface": "glass", "intensity": 0.45},
      "gain_db": -14.0,
      "pan": 0.0,
      "hpf_hz": 90, "lpf_hz": 9000,
      "movement": {"type": "lfo", "depth_db": 2.0, "period_s": 110}
    },
    {
      "id": "log_settle",
      "role": "accent",
      "source": "generated",
      "prompt": "a burning log shifting and settling in a fireplace, close mic, no music",
      "variants": 4,
      "gain_db": -24.0,
      "pan_jitter": 0.35,
      "density_per_min": 0.25
    }
  ],
  "visual": {"image_count": 4, "effects": ["rain_streaks", "firelight_flicker"]}
}
```

`seed` is pinned so a re-render is bit-identical. That matters more than it sounds — you will re-render for encode tweaks and you don't want the audio to change underneath you.

---

## 4. Audio: procedural vs. generated

The routing rule: **procedural for anything that is fundamentally filtered noise; generated for anything with recognizable events or identity.**

**Procedural (numpy + scipy, $0, infinite, deterministic)**

| Layer | Recipe sketch |
|---|---|
| Rain | White noise → bandpass 500–8k, plus Poisson-scheduled drop transients (short filtered impulses) → small reverb. `surface` param shifts the bandpass and transient decay |
| Wind | Brown noise → resonant bandpass with slow LFOs on both center frequency and Q → amplitude LFO |
| Fire | Brown noise bed + Poisson crackle bursts (exponential-decay noise, bandpass 1–4k, density ~4/s) |
| Ocean | Pink noise → slow randomized swell envelope (0.08–0.15 Hz) → lowpass ~4k |
| Room tone / hum | Filtered brown noise + two very quiet low sine partials |
| Distant traffic | Brown noise → lowpass 400 Hz → very slow amplitude drift |
| Thunder | Low-band noise burst → long decay convolution |
| Ship creak | Low-rate resonant noise bursts with pitch drift |

**Generated (AI SFX API)**

Birds, crickets, tavern murmur, carriage and hooves, church bells, doors, page turns, animal calls, mechanical/sci-fi textures, footsteps. Anything a listener would name.

**Shared cache.** Generated clips go in `assets/sfx_cache/<sha256(prompt+params)>.wav`, outside any single project. Over time this converges on a curated library and marginal cost per video trends to zero. The cache is the reason the hybrid approach beats pure generation.

### 4.1 The looping problem

Generated clips are 5–30s. You need 1–8 hours. Verbatim looping is the single most common failure in this niche — listeners consciously notice a repeat within about three cycles, and it's fatal for sleep content.

Three techniques, applied by role:

- **Granular resynthesis** (beds and textures). Chop the source into 0.5–2s grains, scatter them with randomized ordering, ±2% pitch jitter, and equal-power crossfades. Produces a statistically identical but non-repeating stream indefinitely. This is the core trick.
- **Variant stacking**. Generate 3–5 variants per generated bed and rotate between them on a slow, non-integer-related schedule.
- **Poisson scheduling** (accents). Draw inter-onset intervals from an exponential distribution at `density_per_min`. No rhythmic pattern ever emerges. Add per-instance pan and gain jitter.

Procedural layers are non-repeating by construction — no special handling.

### 4.2 Render strategy

A 3-hour stereo float32 buffer is ~2 GB. Don't hold it in memory. Render in **60-second blocks**, streaming each block to disk, carrying LFO phase and grain state across block boundaries. Makes 8-hour renders trivially feasible and gives you a progress bar for free.

### 4.3 Loudness

- Integrated **−20 to −23 LUFS**, true peak **−3 dBTP**. YouTube normalizes to −14 so anything hotter just gets turned down; ambient content wants to sit quiet anyway.
- Narrow dynamic range. Nothing should wake anyone: cap accents at −24 dB relative to the bed, roll off above 10 kHz, hard-limit the sum.
- Fade in over 10s, fade out over 20s.
- Check for phase cancellation when stacking correlated noise layers — decorrelate with short delays or independent seeds.

Verify with `ffmpeg -af ebur128` as an automated gate before render completes.

---

## 5. Video track

### 5.1 The loop trick

Never encode hours of video. Encode one **seamless 30–60s loop**, then `-stream_loop -1` it against the long audio and copy the video stream. A 3-hour render becomes an audio encode plus a 30-second video encode.

The hard constraint this imposes: **every overlay effect must be exactly periodic with period == loop length.** Frame N must equal frame 0.

### 5.2 Effects (default mode)

Still base image plus animated overlays:

| Effect | Approach | Seamless because |
|---|---|---|
| Rain streaks | Tiled translucent sprite sheet, scrolled vertically | Tile height divides total scroll distance |
| Snow / embers | Particle system with wrap-around | Particles wrap position at period boundary |
| Firelight flicker | Brightness + color-temp modulation over a masked region | Driven by looped smooth noise |
| Fog / clouds | Scrolling tiled noise texture at low opacity | Tiling, same as rain |
| Water shimmer | Sine displacement on a masked region | Integer number of cycles per loop |

Implement as a small Python compositor writing PNG frames (numpy/PIL), then encode. Simpler to reason about than a stack of ffmpeg filter graphs, and the periodicity constraint is easier to enforce in code.

### 5.3 Multi-image mode

Your rule: default to animated overlays, escalate to multi-image if there's enough material.

```
if len(images) >= 3:  multi-image crossfade + overlays
else:                 single image + overlays
```

Each image gets its own animated loop. Long video is built by holding each loop for 3–8 minutes and crossfading 8–15s into the next, cycling through the set. Note this forfeits the `-stream_loop` shortcut — mitigate by building one **macro-loop** covering the full cycle through all images (e.g. 4 images × 5 min = 20 min), then stream-looping *that*.

**Reuse the fixed crossfade helper.** `xfade` consumes duration from the incoming clip's start; non-first clips need frames padded back. Same bug, same fix, don't reimplement it.

---

## 6. Story-tied mode

Reads `stories/<slug>/`: **[assumed]** manifest for the pinned art style and title, segment files for scene text, cast sheet for character references.

1. Concept agent reads the story and picks the scene with the strongest ambient signature — or takes `--scene N`.
2. Art style is **inherited, not regenerated**, so the ambience video is visually continuous with the story video.
3. Manifest records `source_story: <slug>`; optionally write a back-link into the story manifest so the relationship is discoverable from either side.
4. Publish metadata gets the cross-promotion treatment: series badge, title convention `Middle Watch: Amberlight — The Library, 3 Hours`, description linking the Stories video.

This is the cross-promotion mechanism from the two-channel strategy, made mechanical.

---

## 7. The proposal gate

One-off mode stops after `concept` and prints:

```
Rain on the Library Window · 3h · seed 8814423

  BED      rain on glass          procedural  -14 dB  constant
  BED      room tone, low hearth  procedural  -26 dB  constant
  TEXTURE  fire crackle           procedural  -20 dB  swells / 90s
  TEXTURE  distant thunder        procedural  -30 dB  ~1 / 6 min
  ACCENT   log settling           generated   -24 dB  ~1 / 4 min
  ACCENT   mantel clock           generated   -30 dB  continuous
  ACCENT   page turn              generated   -28 dB  ~1 / 3 min
  TONAL    cello drone, D minor   procedural  -34 dB  constant

  VISUAL   4 images · rain streaks + firelight flicker
  COST     ~$0.60 (3 SFX gens, 1 cached) + 4 images

  Edit ambience/amberlight-library-rain/concept.json, then:
    ambience resume amberlight-library-rain
```

Editing a JSON file and resuming is the same interaction model as the rest of your pipeline — no new concepts to learn.

---

## 8. File layout

```
ambience/<slug>/
  manifest.json            stage state, spend, pinned decisions
  concept.json             editable soundscape plan
  audio/
    layers/<id>/*.wav      source clips (generated only)
    stems/<id>.wav         full-length rendered layer
    mix.flac
  visual/
    images/scene_01.png
    loops/scene_01.mp4
    video.mp4
  render/
    final.mp4
    thumbnail.png
  publish/
    metadata.json

assets/sfx_cache/<hash>.wav   shared across all projects
```

---

## 9. Build order

1. **Procedural synth module** — rain, wind, fire, ocean, room tone. Standalone, testable, no API keys. Ship a CLI that renders 60s of any recipe to a wav so you can tune by ear.
2. **Block-streaming mixer** — layers → full-length stem → mix with EBU R128 gate.
3. **Motion compositor** — single image + rain/flicker, seamless loop verification (assert frame N == frame 0 within tolerance).
4. **Render + publish** — ffmpeg assembly, metadata.
5. **Concept agent + proposal gate** — the LLM piece comes last; by now the schema is proven by hand-written `concept.json` files.
6. **Generated-source adapter + cache** — plug in the SFX provider.
7. **Story-tied mode** — thin layer over everything above.

Steps 1–4 give you a complete, publishable video from a hand-written JSON file with zero API cost. That's the right first milestone.

---

## 10. Open questions

- **SFX provider licensing.** ElevenLabs SFX, Stable Audio, and others differ meaningfully on commercial and monetized-YouTube terms. Worth confirming before the cache fills with clips you can't monetize.
- **Tonal layer and Content ID.** Procedural drones are safe. Anything sampled or model-generated from a music model carries some risk on a channel where a single claim affects hours of content. Recommend procedural-only for tonal until proven.
- **Target durations.** Suggest 1h / 3h / 8h. The 8h sleep tier is where the watch-time is, but also where looping artifacts are least forgivable.
- **Sibling CLI or story pipeline stage?** You chose "read `stories/<slug>/` directly," which works either way. A sibling CLI keeps the story pipeline's manifest clean; a stage makes companion videos automatic. Sibling is my recommendation — ambience has its own cadence and you'll make more one-offs than tie-ins.
- **YouTube reused-content policy.** Long ambient uploads draw scrutiny. Fully original generated audio and visuals is the right side of that line, but it's a reason to lean procedural over stock.
- **[assumed]** structure of `stories/<slug>/` — scene/segment file naming and where the art style string lives in `manifest.json`.
