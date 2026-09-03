# Middle Watch

Two production pipelines and the operations shared between them.

- **Stories** — original short fiction, narrated and illustrated, assembled into
  a YouTube video. See [docs/story/index.md](docs/story/index.md).
- **Ambience** — long-form soundscapes with looping visuals. Design doc at
  [docs/ambience/pipeline-design.md](docs/ambience/pipeline-design.md);
  implementation not started.

Both pipelines record spend to one `ledger/`, and both draw on `assets/`.

## Layout

```
story_pipeline/     stories code + prompts (prompts must stay inside the package)
ambience_pipeline/  ambience code
ledger/             spend and revenue, shared
brand/              launch checklist, merch reference — reference, not code
bibles/             series canon and shared cast portraits
docs/story/         stories documentation
docs/ambience/      ambience documentation
assets/sfx_cache/   generated audio, shared across projects
stories/            story outputs      (gitignored)
ambience/           ambience outputs   (gitignored)
config.yaml .env    yours              (gitignored)
```

## Run everything from this directory

Not from inside a pipeline folder. Four paths resolve against the current
working directory, and every one of them fails quietly rather than loudly if
you get it wrong:

| Resolves against cwd | Default |
|---|---|
| `LEDGER_DIR` (`ledger/ledger.py`) | `ledger` |
| `stories_dir` (`config.yaml`) | `stories` |
| `bibles_dir` (`config.yaml`) | `bibles` |
| `config.yaml` itself | `./config.yaml` |

Run from `story_pipeline/` and you get a second `story_pipeline/ledger/costs.csv`
holding half your spend history, with no error to tell you. Setting
`MW_LEDGER_DIR` to an absolute path in `.env` removes that particular footgun.

Prompts are the exception — `config.py` and `genres.py` resolve `prompts/` from
`Path(__file__)`, so they follow the package wherever it moves and never depend
on cwd. That is also why `prompts/` must stay inside `story_pipeline/`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env          # three API keys — see docs/story/setup.md
ffmpeg -version               # must be on PATH
```

Install into the venv, never into system Python. Reactivate it in each new shell.

Read the licensing notes in [docs/story/setup.md](docs/story/setup.md) before
publishing anything. ElevenLabs grants no commercial rights on its free tier.

## Stories, end to end

```bash
python3 -m story_pipeline.cli new --genre regency --subgenre mystery --length 30
python3 -m story_pipeline.cli approve stories/<slug>
python3 -m story_pipeline.cli write   stories/<slug>
python3 -m story_pipeline.cli record  stories/<slug> --dry-run
python3 -m story_pipeline.cli record  stories/<slug>
python3 -m story_pipeline.cli design  stories/<slug>
python3 -m story_pipeline.cli direct  stories/<slug>
```

Stages are separate on purpose: read the outline before paying for 2,000 words,
read the chapters before paying for narration. Every stage skips work already on
disk, so a failure at the last stage is cheap.

`--length` takes 15 or 30 minutes, defaulting to `length_min` in `config.yaml`.
It is pinned at creation and `revise` will not move it — changing length means a
different story, not a revision of this one. Longer settings are a later problem;
see `LENGTHS` in `story_pipeline/agents/text.py` for what each one implies.

Four commands cost nothing and need no credentials: `genres`, `bibles`, `lint`,
and `record --dry-run`.

## Ledger

```bash
python3 -m ledger.report              # per story, with break-even status
python3 -m ledger.report --by genre   # also --by flavor, --by arc_slug, --stages
```

Revenue import is manual and monthly — see [ledger/README.md](ledger/README.md).

`genre`, `flavor`, `title` and `series` are now pinned at creation and read back
through `Bundle.ledger_story()`, and every row carries the `pipeline` that
produced it. Two things are still owed before the ledger reports anything true:

- Nothing calls the ledger yet. `add_spend` writes unit counts into
  `manifest.json`; no stage writes a costed row, so `costs.csv` does not exist
  and `report` has nothing to read. See `ledger/README.md` for the one line each
  agent needs.
- `ledger/videos.csv` does not exist yet. Create it when you publish your first
  video: two columns, `story_slug,video_id`.

`rates.json` was verified 2026-08-28 against list pricing. Check it against real
invoices before trusting a total.

## Not built yet

No YouTube upload — the director stops at an mp4 and an srt. No music bed or
room tone, no thumbnail generation. Cast portraits are still per-story; moving
them to `arcs/<arc-slug>/cast/` is what lets four episodes share one cast sheet,
and it is the largest single saving available.
