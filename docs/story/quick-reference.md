# Quick reference

Every command on one page. Details live in the linked docs.

## Before anything

Run from the **repo root**, venv active. Paths, the ledger and `config.yaml`
resolve against cwd, so running from a subfolder writes to the wrong place silently.

```bash
cd ~/Development/claude/middlewatch
source .venv/bin/activate
```

`.env` is loaded automatically on every command. Every command takes
`--config <file>` (default `config.yaml`) **before** the subcommand.

`<story>` below is a bundle path, e.g. `stories/the-hartfield-letter`.

## Review UI

Easiest — one script, from any directory. It cds to the repo root, activates
`.venv` (creating it and installing requirements if missing), then starts the app:

```bash
scripts/review-app.sh                             # opens http://localhost:8765
scripts/review-app.sh --no-open                   # any flag below passes through
```

Or by hand, from the repo root with the venv active:

```bash
python3 -m story_pipeline.cli review              # opens http://localhost:8765
python3 -m story_pipeline.cli review --no-open    # don't open a browser
python3 -m story_pipeline.cli review --port 9000
python3 -m story_pipeline.cli review --reload     # restart on code changes (dev)
```

| Page | URL |
|---|---|
| Queue — approve, revise, write, record, design, direct, publish | `http://localhost:8765/` |
| Casting — voice auditions per arc | `http://localhost:8765/casting` |

Starting the server is free; the paid buttons say so. Stop with `Ctrl-C`.
More: [review-ui.md](review-ui.md), [casting.md](casting.md).

## The pipeline, in order

| Step | Command | Cost |
|---|---|---|
| Ideate | `new --genre G [--subgenre S] [--bible B] [--length {15,30}] [--notes "…" \| --notes-file F]` | paid |
| Revise outline | `revise <story> "what to change"` | paid |
| **Gate 1** | `approve <story>` | free |
| Write | `write <story> [--restart] [--force]` | paid, largest text cost |
| Redraft one chapter | `redraft <story> --chapter N --note "…"` (or `--note-file F`) | paid |
| Re-run editor only | `critique <story> [--chapter N] [-y]` | paid |
| **Gate 2** | `approve <story> --chapters` or `--chapter N` (repeatable) | free |
| Attribution (solo narration) | `attribute <story> [--chapter N] [--tags/--no-tags] [--dry-run] [-y]` | paid |
| Narrate | `record <story> --dry-run` first, then `record <story>` | paid, largest overall |
| Images | `design <story> [--replan]` | paid |
| Video | `direct <story> [--force]` | free, slow |
| **Gate 3** | `approve <story> --video` (`--undo` to withdraw) | free |
| Publish | `publish <story> <youtube-url> [--date YYYY-MM-DD]` (`--undo`) | free |
| Archive | `archive <slug> [--arc A] [--dry-run] [--delete-local]` | S3 |

`all --genre G …` runs every stage with no gates. Avoid unless testing.

All prefixed with `python3 -m story_pipeline.cli`.

## Inspecting (free)

| Command | What |
|---|---|
| `status <story>` | stage state and spend |
| `lint <story>` | AI-tell / machine-register check on drafts |
| `render <story> [--plain] [--strip-titles]` | rebuild chapter markdown from JSON |
| `genres [--tropes]` | genres, subgenres, tropes |
| `history [--bible B] [--genre G]` | what the channel has repeated |

## Series bibles (free)

| Command | What |
|---|---|
| `bibles` | list bibles and recurring cast |
| `bible new <name> --genre G [--series "Name"] [--example]` | scaffold one |
| `bible check [name]` | validate one, or all |
| `bible log <name>` | show change history |
| `bible log <name> -m "what changed" [--kind canon]` | record a change |

## Voices

| Command | Cost |
|---|---|
| `voices` | free — list TTS account voices and IDs, flagged against `voices.yaml` |
| `python3 scripts/sync-voices.py [--dry-run]` | free — write your account's ids into `voices.yaml` |
| `tts-check [--text "…"] [--voice NAME]` | a few hundred credits |
| `python3 check_voices.py --config config.yaml …` | paid — CLI auditions; the Casting page drives this for you |

## Ledger

```bash
python3 -m ledger.report [--by pipeline|genre|flavor|series|arc_slug] [--stages] [--csv out.csv]
python3 -m ledger.import_youtube <analytics.csv> --period YYYY-MM
python3 -m ledger.migrate          # reconcile costs.csv to COST_FIELDS after a schema change
```

No stage writes costed rows yet, so `report` has nothing to read.

## Help

```bash
python3 -m story_pipeline.cli --help
python3 -m story_pipeline.cli <command> --help
```

Something broke: [troubleshooting.md](troubleshooting.md).
