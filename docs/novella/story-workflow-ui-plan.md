# Story Workflow UI Plan

> Moved from the retired `kindle-novellas` repo on 2026-11-25. Historical: it describes the FastAPI app that `novella_pipeline` replaced; current plan is [plan-2026-11-24.md](plan-2026-11-24.md). Links in it point at files in the old repo.

The Novella Workflow app is built and runs locally: a FastAPI app that takes each chapter from outline to approval with a writer agent, Scott's own edit, and an editor agent. This doc describes the app as built (commit `9b49cd9`, Nov 22, 2026), what's left to build, and what the merge into MiddleWatch needs to settle. Setup and day-to-day use are in the [README](../README.md).

## Chapter Workflow

Each chapter moves through five stages, defined in `storyflow/workflow.py`.

| Stage | How it gets there | Next action shown |
| --- | --- | --- |
| Outlined | Scott adds the chapter with title, target length and beats | Run the writer |
| Drafted | The writer agent produces a draft | Edit the draft |
| Author edited | Scott saves his revision | Run the editor |
| Editor reviewed | The editor agent returns suggestions | Revise or approve |
| Approved | Scott approves; the chapter locks and its summary feeds later chapters | Done |

Stage rules:

| Action | Allowed from | Result |
| --- | --- | --- |
| Draft or redraft | Outlined, Drafted | Drafted |
| Save edits | Drafted, Author edited, Editor reviewed | Drafted moves to Author edited; later stages keep their stage, so revising after a review doesn't force another review |
| Review | Author edited, Editor reviewed | Editor reviewed |
| Approve | Editor reviewed | Approved |
| Send back | Any stage | Any earlier stage |

Redrafting stops once Scott starts editing. To redraft after that, he sends the chapter back to Outlined first, so edits are never lost by accident.

## Screens

- **Chapter board** (home page): every chapter with its stage, word count against target and next action, plus overall progress. New stories and chapters are added here.
- **Canon:** series bible, story brief and outline side by side with a preview. Each save is a git commit; the version history lists and shows every saved version.
- **Canon sources:** pulls series bibles from the middlewatch repo, shows each one's sync status (up to date, update available, edited here, changed in both), and pulls updates with a diff first.
- **Chapter page**, one tab per step:
  - **Beats:** title, target words and the beats the writer must follow.
  - **Draft:** runs the writer agent and streams the draft onto the page. The exact prompt can be previewed first.
  - **Edit:** text editor with a live word count, and the writer's flags, new details and summary alongside.
  - **Review:** runs the editor agent; suggestions appear next to the text, grouped by severity (Must fix, Canon, Should consider, Minor, What's working). The editor never changes the text.
  - **Approve:** edit or regenerate the summary, tick new details to add to the bible, then lock the chapter.

## Files on Disk

The repo is the database. Books are organised as genre, then series (holding its bible), then a folder per story. A standalone novella can skip the series level.

```
fantasy/broken-edges/
├── bible.md                  series bible
└── glasswood/
    ├── brief.md
    ├── outline.md
    ├── status.json           title, each chapter's stage and target length
    └── chapters/01/
        ├── beats.md
        ├── draft.md          writer's original draft
        ├── edited.md         Scott's revision (what the editor reviews)
        ├── summary.md        passed to later chapters
        ├── new-details.md    names and facts the writer invented
        ├── flags.md          canon conflicts the writer noticed
        └── review.md         editor suggestions
```

Bibles pulled from middlewatch are tracked in `canon-sources.json` with the middlewatch commit each came from. The middlewatch clone lives in `.storyflow/sources/`, which git ignores.

The app commits after every save, touching only the files that step wrote, with messages like `The Glasswood Leaves ch3: author edit`. It never pushes. `STORYFLOW_AUTOCOMMIT=0` turns commits off.

## Tech

- **Server:** FastAPI with server-rendered Jinja2 templates, run with uvicorn on port 8517.
- **Styling:** Beer CSS 5.0.3 (Material Design 3), saved in `webapp/static/beercss/` so the app works offline.
- **Browser code:** one small `webapp/static/app.js` that streams agent output with `fetch` and handles summary regeneration. Every other action is a plain form post that redirects back.
- **Agents:** Anthropic API. System prompts are `agents/draft-writer.md` and `agents/editor.md`. Both agents default to `claude-opus-5-5` with `high` effort, set by `STORYFLOW_*` variables.
- **Dependencies:** `requirements.txt` (anthropic, fastapi, uvicorn, jinja2, python-multipart, markdown, python-dotenv, httpx, pytest).

## Code Layout

| Path | What it does |
| --- | --- |
| `storyflow/store.py` | Reads and writes stories, chapters and `status.json` |
| `storyflow/gitops.py` | Commits each step's files |
| `storyflow/workflow.py` | Stages and transition rules |
| `storyflow/prompts.py` | Builds each agent's prompt from canon, brief, outline, summaries and beats |
| `storyflow/agents.py` | Anthropic calls and streaming |
| `storyflow/parsing.py` | Splits agent replies into draft, flags, new details, summary and review |
| `storyflow/canon_sources.py` | Fetching and syncing bibles from middlewatch |
| `storyflow/config.py` | Settings from `.env` (`ANTHROPIC_*` and `STORYFLOW_*` only) |
| `webapp/main.py` | All routes |
| `webapp/templates/` | `base`, `board`, `canon`, `sources`, `chapter`, `macros` |
| `tests/` | Storage, workflow, agent request shape, HTTP routes, canon sync |

## Routes

`{story}` is the story's path, for example `fantasy/broken-edges/glasswood`. `{n}` is the chapter number.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Chapter board |
| POST | `/stories` | Create a story |
| POST | `/stories/{story}/chapters` | Add a chapter |
| GET / POST | `/canon` | Canon page; save bible, brief or outline |
| GET | `/sources` | Canon sources page |
| POST | `/sources/fetch` | Clone or update middlewatch and list its bibles |
| POST | `/sources/include` | Copy a bible into a series folder |
| POST | `/sources/pull` | Pull an update for an included bible |
| POST | `/sources/unlink` | Stop syncing a bible |
| GET | `/stories/{story}/ch/{n}` | Chapter page |
| POST | `/stories/{story}/ch/{n}/beats` | Save beats, title and target |
| POST | `/stories/{story}/ch/{n}/draft` | Run the writer; streams plain text |
| POST | `/stories/{story}/ch/{n}/edit` | Save the edited text |
| POST | `/stories/{story}/ch/{n}/review` | Run the editor; streams plain text |
| POST | `/stories/{story}/ch/{n}/summary` | Regenerate the summary |
| POST | `/stories/{story}/ch/{n}/approve` | Approve and add ticked details to the bible |
| POST | `/stories/{story}/ch/{n}/send-back` | Move to an earlier stage |
| GET | `/api/stories` | JSON: all stories with chapters and stages |
| GET | `/api/stories/{story}` | JSON: one story |
| GET | `/api/stories/{story}/ch/{n}` | JSON: one chapter with all its files |

The JSON API is read-only. FastAPI's docs page is at `/docs`.

## Not Built Yet

Ideas from the earlier, larger design that would still help:

- [ ] Accept and dismiss buttons on each editor suggestion, with the state saved, so Scott can track what he's handled.
- [ ] Writer flags and new details as a checklist on the Edit tab.
- [ ] A Runs screen listing each agent call with model, tokens, cost, duration and status, plus cancel and retry.
- [ ] Prompt caching on the bible, brief and outline to cut cost when drafting chapters in a row.
- [ ] Structured agent output (tool-use JSON schemas) instead of parsing Markdown replies.
- [ ] A series-level colour theme from Material Theme Builder, and a light and dark toggle.
- [ ] Write endpoints in the JSON API, if another app needs to drive the workflow.

## Merging into MiddleWatch

Scott plans to merge this app into MiddleWatch. The app already pulls its bibles from the middlewatch repo, so the merge should remove that copy step. Questions to settle:

- Does MiddleWatch read its bibles in place (`bibles/<name>/<name>.md`), making `bible.md` copies, `canon-sources.json` and the Canon sources page unnecessary?
- Where do stories, chapters and `status.json` live: in the MiddleWatch repo, or in this repo with MiddleWatch pointing at it?
- Does `storyflow/` become a package MiddleWatch imports, with MiddleWatch providing the UI, or do the pages move over too?
- Do new details from approved chapters go straight into the MiddleWatch bible?
- Which stack MiddleWatch uses for its web app, and whether the Beer CSS screens fit it.

## Decisions

- **Models:** Anthropic for both agents. Both default to Opus; `STORYFLOW_EDITOR_MODEL=claude-sonnet-5-5` is the cheaper option for the editor.
- **Hosting:** runs locally; hosting later is still open.
- **Scope:** novellas only. YouTube short stories have their own workflow.
- **Storage:** everything is Markdown in git, committed at each step so every draft and edit has history.
