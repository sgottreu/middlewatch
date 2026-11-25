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
