# Memory

## Preferences

### Python and pip — non-negotiable

| Do | Never |
|---|---|
| `python3` | `python` |
| `python3 -m pip install ...` | `pip install ...` |
| Install into a venv | Install into system Python |
| — | `--break-system-packages` |

Scott has asked for this across multiple sessions. Treat it as a standing rule,
not a suggestion, and apply it to every command, snippet, and doc example —
including throwaway one-liners and anything written into a README.

**Always use a venv for installs.** Activate the existing one before installing
anything; if there isn't one, create it before installing rather than falling
back to a system install.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

`--break-system-packages` is a symptom of skipping the venv. If reaching for it,
the venv step was missed.

### Response style

- Concise and direct. Cut words that don't change the meaning.
- No preamble before tool calls, no recap of steps already visible.

## This repo — middlewatch

A monorepo with two pipelines and the operations shared between them.

| Path | What |
|---|---|
| `story_pipeline/` | Narrated short fiction → YouTube video. Six stages, then approve and publish (manual, `release.py`). |
| `ambience_pipeline/` | Long-form soundscapes: `synth/` recipes, `mix.py` (streamed mix, LUFS gate), `bundle.py` (`ambience/<slug>/`, gitignored), `cli.py` (`new --scene`/`--from-story`, `approve`, `sound`, `audition`). Procedural layers only; concept agent not built. Page: `/ambience`. `docs/ambience/index.md` is the wiki. |
| `novella_pipeline/` | Kindle novellas: bundles in `novellas/<series>/<slug>/`, chapter stages ported from kindle-novellas, numbered drafts, writer/editor via `story_pipeline.llm`. Page: `/novellas`. Plan: `docs/novella/plan-2026-11-24.md`. |
| `ledger/` | Shared spend and revenue, both pipelines. |
| `docs/story/`, `docs/ambience/` | Documentation. `docs/story/index.md` is the wiki. |
| `bibles/`, `stories/`, `config.yaml` | Resolve against cwd — see below. |
| `voices.yaml` | The voice library (name → ElevenLabs id). Tracked; `config.yaml` is not, and must not hold a `voice_library` too — load refuses. |

**Run everything from the repo root.** `LEDGER_DIR`, `stories_dir`, `bibles_dir`
and `config.yaml` all resolve against the working directory. Running from inside
a pipeline folder silently splits the ledger and writes output to the wrong
place, with no error.

Invocation is `python3 -m story_pipeline.cli <command>`.

### Known state

- The story length dial is `--length {15,30}`, pinned per story at creation.
  `LENGTHS` in `story_pipeline/agents/text.py` is the single source for what each
  setting means. 45 and 60 are deliberately deferred.
- `WPM = 150` is an assumption, not a measurement. See
  `docs/story/calibration.md` before trusting any runtime figure.
- `ledger/rates.json` was corrected 2026-08-29 and now prices every model
  `config.py` actually runs, across all four providers. Rates are per *model
  string*, so renaming a model in `config.yaml` without touching `rates.json`
  raises at the first billed call — `docs/ambience/pipeline-design.md` has a
  credential-free snippet that checks every key.
- Gemini images are billed by output tokens, so the per-image rate depends on
  `gemini.image_size`. `rates.json` holds the 2K figure because that is what
  `config.yaml` sets. Changing one means changing the other.
- The ledger schema is settled: `COST_FIELDS` carries `pipeline`, validated
  against `PIPELINES` and never defaulted, and `genre` / `flavor` / `title` /
  `series` are pinned at ideate. Build the `story` dict with
  `Bundle.ledger_story()`, never by hand. Schema changes from here go through
  `python3 -m ledger.migrate`, which reconciles the CSV to `COST_FIELDS`.
- **The ledger records LLM, image and narration spend.** `story_pipeline/spend.py`
  `record_llm()` runs after every Anthropic call (ideate, revise, ask, write,
  edit, design shot planning): token counts go into `manifest.spend`, costed rows
  into `ledger/costs.csv` via `Bundle.ledger_story()`. It never raises — a ledger
  failure (unpriced model, manifest without `pipeline`) is a stderr warning, so a
  paid reply is never lost. Gemini images (`spend.record_images`, stage
  `design`) and narration (`spend.record_tts`, stage `record`: ElevenLabs
  credits, Polly characters) write ledger rows the same way. Not yet costed:
  casting auditions and ambience SFX. A model missing from `rates.json` (e.g.
  `eleven_v4`) gives a warning and no row, so price it before relying on reports.
- `llm.complete` / `complete_json` take an optional `on_text` callback that
  receives the reply as it streams. Return value is unchanged.
- Stories exist under `stories/amberlight/`; `stories/amberlight/the-cracked-beam`
  is the reference bundle the tests copy.

### Tests

`python3 -m pytest` from the repo root (dev deps: `requirements-dev.txt`;
`-m "not slow"` skips renders). Every test runs in a temp copy of the repo root
with network blocked and fake API keys. Tests use **fake bibles** (`sample_draft`,
`broken_draft` fixtures in `tests/conftest.py`); the only real bible they read is
amberlight. Never point a test at the in-progress drafts in `bibles/`. Known bugs
are strict `xfail`s with a `BUG:` reason — fixing one makes its test XPASS and
fail, so remove the marker with the fix. Scott's `.venv` is a macOS one. A session
working through the Linux device shell can't run it, so it uses a separate venv
outside the repo (`python3 -m venv ~/.mwvenv`, then install `requirements.txt`
and `requirements-dev.txt` into it). Never install into the repo or into system
Python. The sibling `kindle-novellas` repo needs Python 3.12 or later: an
f-string in `storyflow/store.py` won't parse on 3.10.

The older script checks still run on their own:
`python3 -m story_pipeline.tests.test_bible_promote`,
`python3 -m ambience_pipeline.tests.test_synth`.

### Plan in progress

Ambience page and novellas-in-middlewatch:
`docs/novella/plan-2026-11-24.md` and `docs/ambience/review-ui-plan-2026-11-24.md`.
Do not promote any draft bible until Scott says so.
