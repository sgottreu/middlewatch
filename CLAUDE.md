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
| `story_pipeline/` | Narrated short fiction → YouTube video. Six stages. |
| `ambience_pipeline/` | Long-form soundscapes. Design only, not built. |
| `ledger/` | Shared spend and revenue, both pipelines. |
| `docs/story/`, `docs/ambience/` | Documentation. `docs/story/index.md` is the wiki. |
| `bibles/`, `stories/`, `config.yaml` | Resolve against cwd — see below. |

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
- **Nothing calls the ledger yet.** `add_spend` writes unit counts into
  `manifest.json`; no stage writes a costed row. `costs.csv` does not exist and
  `report` has nothing to read. This is the largest gap in the repo.
- No stories have been generated yet.
