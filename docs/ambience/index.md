# Ambience documentation index

*Written 2026-08-28.*

Four documents, what each is for, and which to open when you want to change
something. The [root README](../../README.md) covers the shared ledger, the
brand material and the story pipeline.

Run every command from the repo root. Installs go into the venv —
`python3 -m venv .venv`, `source .venv/bin/activate`,
`python3 -m pip install -r requirements.txt`.

---

## Start here

| | |
|---|---|
| [Pipeline design](pipeline-design.md) | The architecture. Two modes, the stage graph, `concept.json`, the audio and visual strategy, the ledger, and the defects fixed on the way. **Not built** except the synth. |
| [The synth](synth.md) | The one part that is built. Three invariants everything rests on, the primitives, how to add a recipe. |
| [Calibration](calibration.md) | The dial, the by-ear verdicts, the measured baselines, and the four ways a metric lied. |
| [Music](music.md) | Plan for music-forward one-offs: providers, the licensing gate, and four techniques for looping a ten-minute generated track for hours. Not built. |

## State, 2026-08-28

| | |
|---|---|
| Built | `ambience_pipeline/synth/` and its tests. Five recipes, an audition CLI, a trim-measuring script. |
| Signed off | Nothing. Fire and wind have been through several rounds and neither is accepted; rain, ocean and room tone are unheard. |
| Not built | Everything else: the bundle, the CLI, the mixer, the visual compositor, the concept agent, the ledger wiring, the music module. |
| Blocked | The generated-audio half, on whether the ElevenLabs plan grants commercial rights. Same decision covers SFX accents and music. |

## I want to change… → edit this

### A recipe's sound

| I want to… | Edit |
|---|---|
| Change what a recipe sounds like | Its function in `ambience_pipeline/synth/recipes.py` |
| Change a default parameter | The `@recipe(...)` decorator arguments |
| Change what level a recipe sits at | `gain_db` in the decorator, then re-run `python3 scripts/measure_trims.py --write` |
| Add a recipe | A new `@recipe` function, a `TRIM_DB` entry, a `LOW_SHARE` bound, a parameter case in the tests. See [synth.md](synth.md). |
| Add a noise colour, filter or modulator | `ambience_pipeline/synth/dsp.py` |

### Auditioning

| I want to… | Edit or run |
|---|---|
| Hear a recipe | `python3 -m ambience_pipeline.synth <recipe> --seconds 120` |
| Hear a parameter change | `-p name=value`, repeatable |
| Hear it without the headroom attenuation | `--headroom 0` (it may clip; the CLI says so) |
| Hear what a limiter would do to it | `--ceiling -3` |
| Change the default headroom | `render_to_wav` in `ambience_pipeline/synth/render.py` |

### Checks

| I want to… | Edit |
|---|---|
| Run the checks | `python3 -m ambience_pipeline.tests.test_synth` |
| Change what a parameter is asserted on | The `cases` table in `ambience_pipeline/tests/test_synth.py` — pick the metric the parameter actually moves |
| Change a recipe's allowed low-end share | `LOW_SHARE` in the tests |
| Make the suite cheaper or slower | `WINDOW_S`, `LEVEL_SEEDS`, `STREAM_MINUTES` at the top of the tests |
| Re-measure the trims | `python3 scripts/measure_trims.py --write` |

### Spend

| I want to… | Edit |
|---|---|
| Add a rate for a model | `ledger/rates.json`, keyed by the exact model string |
| Check every rate key resolves | The snippet in [pipeline-design.md](pipeline-design.md#verifying-the-rate-table) — free, no credentials |
| Change the ledger schema | `COST_FIELDS` in `ledger/ledger.py`, then `python3 -m ledger.migrate` |

---

## Every file

### Documentation — `docs/ambience/`

`index.md` (this file) · `pipeline-design.md` · `synth.md` · `calibration.md` ·
`music.md`

### Code — `ambience_pipeline/`

| Module | Responsibility |
|---|---|
| `synth/dsp.py` | Primitives, and the three invariants. |
| `synth/recipes.py` | The five recipes, the registry, `TRIM_DB`. |
| `synth/render.py` | Block-streamed render to a wav, with headroom. |
| `synth/wav.py` | Streaming 16-bit PCM. Stdlib only. |
| `synth/__main__.py` | The audition CLI. |
| `tests/test_synth.py` | Determinism, block invariance, parameter wiring, levels, tones, balance. |

Planned, not built: `bundle.py`, `cli.py`, `mix.py`, `motion.py`, `music.py`,
`agents/concept.py`. See [pipeline-design.md](pipeline-design.md#build-order).

### Output

| Path | What |
|---|---|
| `ambience/<slug>/` | A bundle. Gitignored. Not built yet. |
| `auditions/` | Audition renders. Gitignored — regenerable from a seed in one command. |
| `assets/sfx_cache/` | Generated SFX, shared across projects. Gitignored deliberately; see [pipeline-design.md](pipeline-design.md). |

---

## Reading order, if this is all new

1. [pipeline-design.md](pipeline-design.md) — what is being built and why, including
   the two modes and the stage graph.
2. [synth.md](synth.md) — the three invariants. Nothing in the synth makes sense
   without them, and breaking one produces wrong audio rather than an error.
3. [calibration.md](calibration.md) — what is settled, what is a guess, and the
   record of which metrics lied.
4. [music.md](music.md) — only when music-forward videos are on the table.

---

[Root README](../../README.md)  ·  [Story docs](../story/index.md)
