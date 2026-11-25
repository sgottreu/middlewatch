# Ambience page in the review UI — plan (2026-11-24)

An **Ambience** tab in the review server for making a soundscape either from an
existing story or from a stand-alone prompt. Builds on
[pipeline-design.md](pipeline-design.md); novellas are planned in
[docs/novella/plan-2026-11-24.md](../novella/plan-2026-11-24.md).

## Decisions

- **Own namespace:** bundles in `ambience/<slug>/` (gitignored), jobs in
  `runs/ambience-<slug>/`, ledger rows `pipeline="ambience"`. A story-tied
  ambience records `source_story`. This settles the open question in
  `docs/story/review-ui-roadmap.md` ("share the story slug namespace?": no).
- **Same server, separate page:** `review/ambience.html`, its own API module,
  shared runner. The roadmap's "separate UI, shared library" becomes a separate
  page on the shared server.
- **Procedural layers only** until ElevenLabs commercial rights are settled.

## Back end first (from pipeline-design.md's build order)

1. Mixer: layer recipes by `gain_db` and `density_per_min`; ebur128 gate at
   −20 to −23 LUFS.
2. Bundle (copied, ~50 lines) with `ledger_story()` → `pipeline:"ambience"`.
3. CLI: `python3 -m ambience_pipeline.cli new --scene "…" [--bible amberlight]`,
   `new --from-story stories/<arc>/<slug>`, `approve`, `sound [--dry-run]`,
   `status`; `--hours {1,3,8}` pinned at creation.
4. Concept agent via `llm.complete_json` → `concept.json` layers (bed, texture,
   accent, tonal) with a validator; `models.concept` added to config and
   `rates.json`. Records ledger rows.

## Story-tied brief

Built from `outline.json` (`setting`, `logline`, `genre`),
`images/scenes.json` shot prompts (light, time of day, place), the bible's
`canon`/`art_style`, and `manifest.pinned.art_style`. Shown editable before the
concept agent runs.

## Page and API

`review/ambience.html` + `review/ambience_api.py` (job wrapper like casting's).

- `GET /api/ambience` — list
- `POST /api/ambience/new` — `{scene | from_story, bible?, hours}`
- `GET`/`PUT /api/ambience/<slug>/concept` — edit layers
- `POST /api/ambience/<slug>/approve`, `/sound`
- `POST /api/ambience/audition` — `{recipe, params, seconds: 30}`; free, offline

Layout: Story / Prompt switch → editable layer rows (recipe, params, gain) →
job log. Do roadmap item #1 (artifact links, `emit_artifact()`) here so WAVs
play in the log pane on a phone. `server.recover()` / `runner.reap_dead()` must
skip non-story job keys.

## Built 2026-11-25

Step 5's ambience half: steps 1–3 of "Back end first" plus the page. Step 4
(the concept agent) and generated layers are not built.

```bash
python3 -m ambience_pipeline.cli new --scene "rain on a library window, a low fire" --bible amberlight --hours 1
python3 -m ambience_pipeline.cli new --from-story stories/amberlight/the-cracked-beam --hours 3
python3 -m ambience_pipeline.cli status ambience/<slug>
python3 -m ambience_pipeline.cli approve ambience/<slug>
python3 -m ambience_pipeline.cli sound ambience/<slug> --dry-run
python3 -m ambience_pipeline.cli sound ambience/<slug> --seconds 60   # audio/preview.wav
python3 -m ambience_pipeline.cli sound ambience/<slug>                # audio/mix.wav, full length
python3 -m ambience_pipeline.cli audition rain -p surface=roof --seconds 30 --out rain.wav
python3 -m pytest -q tests/ambience_pipeline
```

The page is `/ambience` on the review server. `MW.PAGES` in `common.js` does
not list it yet; add `["Ambience", "/ambience"]` there.

**What `new` does without the concept agent.** It writes `brief.md` (story:
outline `title`/`series`/`genre`/`logline`/`setting`, up to 12 shot prompts
from `images/scenes.json`, the bible's canon, and the art style), and a
*starter* `concept.json` picked from keywords (rain, sea, wind, fire; room tone
when nothing matches). The concept is `awaiting_review`. A story-tied bundle
records `pinned.source_story` and adds its slug to the story manifest's
`ambience` list (the reciprocal link from pipeline-design.md). The story's
pinned `art_style` wins over the bible's. A story bible that will not load is
skipped with a note; an explicit `--bible` on a scene that will not load is an
error.

**The gate.** `approve` validates, then records a hash of the layers and seed.
Changing either afterwards (CLI, hand edit or page) puts the concept back to
`awaiting_review`, and `sound` refuses until it is approved again. Retitling
does not.

**Interpretations of pipeline-design.md worth checking:**

- **Level.** `gain_db` sets each layer's balance. The sum is then trimmed (by
  up to ±18 dB) to −21.5 LUFS, measured by `dsp.lufs` on the first minute, and
  the file is gated by ffmpeg `ebur128` at −23 to −20. Outside the window the
  render is kept as `*.rejected.wav`, the stage is `failed`, and the CLI exits 2.
  A soft limit at −4 dBFS keeps true peak under −3 dBTP; true peak is reported
  and warned on, not gated.
- **Procedural accents.** The design only defines accents as generated
  one-shots. Here an accent is a recipe gated by Poisson swells at
  `density_per_min × accent_density_scale`, each about `event_s` (default 8 s)
  long. An accent may not be louder than the loudest bed; the "−24 dB relative
  to the bed" line was not enforced.
- **Per-layer seeds** come from the concept seed plus the layer's id, so adding
  a layer does not change the others.
- **Output is `audio/mix.wav`**, not stems plus `mix.flac`. Fades are 10 s in and
  20 s out, capped at a tenth of a short preview.
- **Validator bounds:** `gain_db` −48 to −10, `density_per_min` 0.01 to 6, at
  most 8 layers and 2 beds. `pan`, `hpf_hz`, `lpf_hz` and `movement` (LFO) are
  applied. `pan_jitter`, `variants` and `prompt` are rejected as unknown keys
  until generated layers exist.
- **Ledger.** `sound` adds `procedural_seconds` to the manifest's spend. No
  ledger row is written, because procedural rendering costs nothing.

**Jobs.** `sound` and auditions run through `runner.start_command` with
`pipeline="ambience"`, keyed `ambience-<slug>` and `ambience-_auditions`. A dead
or cancelled `sound` job has its stage put back to `pending` by
`ambience_api.release`. The CLI emits `::progress` `plan`/`block`/`loudness`/
`artifact`/`done`. The page plays the `artifact` WAV for auditions, and the
rendered mix and preview from the bundle. Roadmap item #1 (artifact links in the
shared log pane) is not done: the marker exists, but the story page does not
read it.

