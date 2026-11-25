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
(the concept agent) followed the same day — see
[Concept agent](#concept-agent-built-2026-11-25) below. Generated layers are
not built.

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

**What `new --starter` does (and what every `new` does before the agent's reply lands).** It writes `brief.md` (story:
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

## Concept agent (built 2026-11-25)

Step 6's ambience half: "Back end first" step 4.

```bash
python3 -m ambience_pipeline.cli new --scene "rain on a library window, a low fire" --bible amberlight
python3 -m ambience_pipeline.cli new --scene "…" --starter        # free keyword layers, no call
python3 -m ambience_pipeline.cli draft ambience/<slug>            # redraft from brief.md (paid)
python3 -m ambience_pipeline.cli revise ambience/<slug> --note "less fire, a slower wind"
python3 -m pytest -q tests/ambience_pipeline/test_concept.py
```

- **The call.** `ambience_pipeline/concept.py` sends `brief.md`, the hours (and
  the accent scale it implies) and the pinned bible's `prompt_block()` — skipped
  with a note if the bible will not load — to `llm.complete_json` with
  `models.concept` (`claude-sonnet-5`) and `max_tokens.concept` (8000). The
  model supplies `scene`, `layers` and `notes`; slug, title, series, hours and
  seed always come from the manifest.
- **The prompt can't drift.** `prompts/concept.md` holds the prose; the recipe
  catalogue (summaries, parameters, defaults, ranges), the roles and the
  mixer's conventions (gain bounds, accent density and the hours scale, event
  length, filters, LFO, layer limits) are filled in from `REGISTRY`,
  `bundle.py` and `mix.py`. It says procedural only: generated layers are
  blocked on ElevenLabs rights and the validator rejects them anyway.
- **Ranges are new.** `Recipe.ranges` (in the `@recipe` decorator) gives each
  parameter's listened-to range; `rain`'s surfaces moved to `RAIN_SURFACES` so
  the choice list and the build share one table. The numbers are my reading of
  each recipe's code, not measurements — check them by ear. They bind the agent
  (an out-of-range parameter counts as a problem) but **not** a hand edit or
  the gate, so a value found by ear outside a range still renders.
- **Validation and retry.** `bundle.validate_concept` plus the ranges. A reply
  with problems is sent back once, with the reply and its problems; a second
  failure raises `ConceptError`, leaves `concept.json` as it was, and the CLI
  exits 1. For `new` that leaves the starter layers in place.
- **History and the gate.** Every agent write keeps the concept it replaces as
  `concept.N.json` (the starter becomes `concept.1.json`), drops
  `concept_approved` and sets the stage to `awaiting_review` — even a revision
  that changes nothing has to be approved again. `stage_info.concept` records
  the model and the revise note.
- **Spend.** Each call, retry included, goes through
  `spend.record_llm(b, "concept", …)`: token counts in the manifest's `spend`,
  ledger rows with `pipeline=ambience`, `stage=concept`. These are the first
  ambience rows in `ledger/costs.csv`.
- **Page.** `POST /api/ambience/new` creates the bundle (free, starter layers)
  and starts `cli draft` as a job keyed `ambience-<slug>`, stage `concept`,
  unless `starter: true` (the "Starter layers (free)" checkbox). The editor has
  a Revise box (`POST /api/ambience/<slug>/revise {note}`) and "Draft again from
  the brief" (`POST /api/ambience/<slug>/draft`). All three are armed with
  "Confirm — paid call, about $…", estimated from rates.json for the system
  prompt plus a 3,000-character brief and 1,500 output tokens (`concept_estimate_usd` in
  `GET /api/ambience`). Saving, approving and rendering are refused while a
  concept job runs; a dead one is put back to `awaiting_review` by `release`
  and on read. The job log shows the reply as it streams; in a terminal, pass
  `--stream` to see it.
- **Not done.** The one-off art-style proposal pipeline-design.md asks of this
  agent (nothing would use it until `design` exists), and no run against the
  real API yet — the first one is worth watching for the reply's gain balance.
