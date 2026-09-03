# Documentation index

Every file in the story pipeline, what it's for, and which one to open when you
want to change something. The repo holds more than this — the shared ledger, the
brand material, the ambience pipeline — and the [root README](../../README.md)
covers those.

Two kinds of file live here and it's worth keeping them straight:

- **Docs** (`docs/story/`) describe how the thing works. Editing them changes
  nothing.
- **Prompts, genres and bibles** are the working parts. Editing them changes what
  the stories are. They read like documentation, which is the point, but they are
  code as much as `cli.py` is.

Run every command from the repo root. Four paths — `stories/`, `bibles/`,
`config.yaml` and the ledger — resolve against the working directory, so running
from inside `story_pipeline/` puts your output somewhere you didn't mean. The
[root README](../../README.md) has the details.

---

## Start here

| | |
|---|---|
| [Setup](setup.md) | Install, and the three accounts. **Read the licensing notes** — ElevenLabs grants no commercial rights on its free tier. |
| [Running the pipeline](running.md) | The six stages, the commands that drive them, and the length dial. |
| [Architecture](architecture.md) | How stages hand off, why every run is resumable, and the three decisions everything rests on. |

## Making stories

| | |
|---|---|
| [Ideation](ideation.md) | The two ways to brief the ideator, what makes a good note, and the review gate. |
| [Series bibles](bibles.md) | Recurring cast, shared canon, and how a character keeps their face and voice across stories. |
| [Writing a bible by hand](bible-format.md) | The field-by-field format reference, and the three ways a hand-written bible silently does nothing. |
| [Consequences across a series](continuity.md) | Whether a recurring character can marry and stay married. Per-series, and not built. |
| [Style and genres](style.md) | The four prompt layers, tropes, the AI-tell linter, and the edit loop. |
| [Content boundaries](boundaries.md) | What never appears in a story, and the three depths it's enforced at. |
| [Narration](narration.md) | TTS providers, voice casting, and where the timings come from. |

## Reference

| | |
|---|---|
| [Cost](cost.md) | Per story, across all three services. |
| [Calibrating the length dial](calibration.md) | What to check once stories exist, and what to change based on the result. |
| [A review UI](review-ui.md) | `cli review` — the queue, and the three pipeline gaps it closed. |
| [Troubleshooting](troubleshooting.md) | Symptom → cause. |

---

## The two gates

Two stages stop and wait for you. Everything else runs straight through.

| Stage | Waits at | Unlock | Protects |
|---|---|---|---|
| `ideate` | `awaiting_review` after `new` | `cli approve <story>` | The writing spend — 2,000+ words you have not read an outline for. |
| `edit` | `awaiting_review` after `write` | `cli approve <story> --chapters` | The narration spend, which is the largest single cost here. |

Both take `--force` on the stage they gate (`write --force`, `record --force`),
and `record --dry-run` is always allowed because it is free and it is how you
decide. `cli all` skips both by design.

`cli review` is the way to work through them — see [the review UI](review-ui.md).

Stage values you will see in `manifest.json`: `pending`, `running`,
`awaiting_review`, `approved`, `done`.

---

## I want to change… → edit this

This is the table most worth having. Almost every change you'll want is a text
edit to one file, not a code change.

### The writing

| I want to… | Edit |
|---|---|
| Change what a genre sounds like — voice, diction, period | `story_pipeline/prompts/genres/<genre>.md`, prose section |
| Add or reword a trope | `story_pipeline/prompts/genres/<genre>.md`, `tropes:` frontmatter |
| Add a subgenre | Same file, `subgenres:` frontmatter |
| Add a whole new genre | One new file in `story_pipeline/prompts/genres/`. Nothing else changes. |
| Change trope rules, or anything about shape that isn't length | `story_pipeline/prompts/house.md` |
| Change what's forbidden in any story | `story_pipeline/prompts/boundaries.md` |
| Change how stories relate across an arc | `story_pipeline/prompts/arc.md` |
| Ban a word that reads as machine-written | `story_pipeline/prompts/ai-tells.md` for the guidance, `story_pipeline/lint.py` for enforcement |
| Stop the linter flagging a legitimate period usage | `CONTEXT_EXEMPT` in `story_pipeline/lint.py` |
| Change what the editor checks or how strictly | `story_pipeline/prompts/editor.md` |
| Change the outline's shape or fields | `story_pipeline/prompts/ideator.md` **and** `_validate_outline()` in `story_pipeline/agents/text.py` |

### Length

| I want to… | Edit |
|---|---|
| Change one story's length | `--length {15,30}` on `cli new`. Pinned at creation; `revise` won't move it. |
| Change the default length | `length_min` in `config.yaml` |
| Change what a setting *means* — chapters, words, cast, beats, images | `LENGTHS` in `story_pipeline/agents/text.py`. One table drives all of it. |
| Make stories denser or looser | `beats_min` / `beats_max` in `LENGTHS`. Words-per-beat shows at the review gate and in `story.md`. |
| Add a new length setting | One new entry in `LENGTHS`. CLI choices, validation bounds and image count all read from it. |
| Correct the words-per-minute assumption | `WPM` in `story_pipeline/agents/text.py` — but read [calibration](calibration.md) first. |

### A series

| I want to… | Edit |
|---|---|
| Start a series | `cli bible new <name> --genre <genre> --example`, then edit. Format: [bible-format.md](bible-format.md). |
| Check a bible before spending | `cli bible check [<name>]`. Free. The ideator runs the same error checks. |
| Record why a bible changed | `cli bible log <name> -m "..."`. Sidecar at `bibles/<name>.changelog.md`. |
| See what a write will cost before running it | The Write dialog in `cli review`, or `estimate_write()` in `story_pipeline/agents/text.py`. |
| Preload a character's face | Drop a png at `bibles/<name>.cast/<slugified-name>.png` before the first story. |
| Add a fact every story must respect | `canon:` in the bible. Nothing writes back automatically — see [continuity](continuity.md). |
| Add a recurring character | `recurring_cast:` in the bible |
| Change a recurring character's face | Delete their png from `bibles/<name>.cast/`; the next `design` regenerates it |
| Give a series its own look or voices | `art_style:` / `voices:` in the bible frontmatter |

### Sound and pictures

| I want to… | Edit |
|---|---|
| Change which voice narrates a genre | `voices:` in `story_pipeline/prompts/genres/<genre>.md` |
| Fix a voice ID, or add a voice | `elevenlabs.voice_library` in `config.yaml`. Run `cli voices` first. |
| Pin one character to one voice forever | `voice:` on that character in the bible, or on a cast member in `outline.json` |
| Switch back to Polly | `tts_provider: polly` in `config.yaml` |
| Change the art style for a genre | `art_style:` in the genre frontmatter |
| Change how many images per chapter | `gemini.scenes_per_chapter` in `config.yaml`. Leave it null to derive from length. |
| Change how the images are chosen or composed | `story_pipeline/prompts/designer.md` |

### The video

| I want to… | Edit |
|---|---|
| Resolution, frame rate, crossfade length | `video:` in `config.yaml` |
| Turn off the Ken Burns motion | `video.ken_burns: false` |
| Burn subtitles in rather than shipping an `.srt` | `video.burn_subtitles: true` |
| Change subtitle styling | The `force_style` string in `story_pipeline/agents/director.py` |

### Models and spend

| I want to… | Edit |
|---|---|
| Use a cheaper model for the writer | `models.writer` in `config.yaml` |
| Allow more revision rounds per chapter | `max_edit_passes` in `config.yaml` |
| Stop every story reaching for the same tropes | Raise `trope_sample` in `config.yaml`. Past use is already weighted down — see `cli history`. |
| See what the channel has repeated | `cli history [--bible X] [--genre Y]`. Free. |
| Work through the review queue | `cli review`. Free. `--port` to move it, `--no-open` to not launch a browser. |
| Approve chapters so `record` will run | `cli approve <story> --chapters`, or the UI |
| Narrate chapters that failed review anyway | `cli record <story> --force` |
| Re-narrate an edited chapter | Nothing — `record` notices the text changed and re-records it |
| Change what the review page looks like | `story_pipeline/review/app.html`. One file, no build step, re-read per request. |
| Iterate on the review server itself | `cli review --reload` — Python changes need a restart otherwise. |
| Change how hard past use pushes a trope down | `DECAY` in `story_pipeline/history.py` |
| Correct the cost estimate | `elevenlabs.usd_per_1k_credits` in `config.yaml` |
| Change what a story's `story.md` shows | `render()` in `story_pipeline/summary.py`. It's a view — safe to change, regenerated every stage. |

---

## Every file

### Documentation — `docs/story/`

Describes the system. Safe to edit freely.

`index.md` (this file) · `setup.md` · `running.md` · `ideation.md` ·
`bibles.md` · `style.md` · `boundaries.md` · `narration.md` ·
`architecture.md` · `cost.md` · `calibration.md` · `bible-format.md` ·
`continuity.md` · `review-ui.md` · `troubleshooting.md`

### Prompts — `story_pipeline/prompts/`

**Shipped, and load-bearing.** These are read by the agents at runtime. Editing
them changes output.

| File | Read by | What it governs |
|---|---|---|
| `house.md` | ideator, writer, editor, designer | Structure, listenability, trope rules. All genres. |
| `boundaries.md` | all four | Content lines. All genres, absolute. |
| `arc.md` | ideator, writer, editor | How stories relate across a four-story arc. Read with `style.md`; neither overrides the other. |
| `ai-tells.md` | writer, editor | Machine register. Paired with `lint.py`. |
| `ideator.md` | ideator | Outline schema and requirements. |
| `writer.md` | writer | Chapter output format, segment rules. |
| `editor.md` | editor | The review rubric and verdict schema. |
| `designer.md` | designer | Shot selection and image prompt construction. |
| `genres/regency.md` | all four | Regency voice, subgenres, tropes, art, casting. |
| `genres/scifi.md` | all four | Same, for science fiction. |
| `genres/fantasy.md` | all four | Same, for fantasy. |

`_render()` in `story_pipeline/agents/text.py` assembles house + boundaries +
genre + bible + ai-tells into one prompt, so the writer and editor always judge
against identical text.

`house.md` carries a `{structure}` placeholder rather than a fixed chapter and
word count; `_render()` fills it from `LENGTHS`. The designer assembles its own
prompt and so fills the placeholder separately — both paths must stay in step,
and `_render` raises rather than letting an unsubstituted placeholder reach a
model as if it were a rule.

### Yours — repo root

Not shipped, and they live at the repo root rather than inside the package,
because `stories/`, `bibles/` and `config.yaml` all resolve against the working
directory. `config.yaml` and `.env` are gitignored; `bibles/` is not, and should
be committed — a series bible and its cast portraits are content.

| File | What it is |
|---|---|
| `bibles/<name>.md` | A series: canon, recurring cast, optional art and voice overrides. |
| `bibles/<name>.cast/` | Shared character portraits. Generated once, reused across every story in the series. |
| `config.example.yaml` | Every setting with its default. Copy to `config.yaml`. |
| `.env.example` | The three API keys. Copy to `.env`. |

### A story — `stories/<slug>/`

Gitignored. Every stage writes here and reads what the last one left, which is
what makes a failed stage cheap.

| File | Written by | What it is |
|---|---|---|
| `manifest.json` | every stage | Stage state, spend, and the pinned decisions: art style, casting, length, images per chapter. |
| `outline.json` | ideator | Title, genre, tropes, cast, chapters and their word targets. |
| `story.md` | ideate, write, record | Human-readable summary. Planned length beside measured length. |
| `drafts/NN.<n>.json` | writer | Every pass of the write/edit loop, kept. The only record of what the editor changed. |
| `drafts/NN.<n>.review.json` | editor | The verdict on that pass, beside the draft it judged. |
| `chapters/NN.json` · `.md` | writer | Segments, and the same rendered for reading. |
| `reviews/NN.json` | editor | The verdict, the fixes, and `generic_prose`. |
| `audio/NN.mp3` | actor | Narration. |
| `audio/NN.timeline.json` | actor | Sentence-level timings, `duration_ms`, and `chapter_hash` — the last is how an edited chapter is spotted as needing re-narration. |
| `images/cast/*.png` · `NN-KK.png` | designer | Cast sheet, then scene images. |
| `video/final.mp4` · `subtitles.srt` | director | The deliverable. |

`story.md` is the one file here that is a *view* rather than a source —
everything in it derives from the others, so deleting it loses nothing and it is
rewritten whenever a stage completes.

### Code — `story_pipeline/`

| Module | Responsibility |
|---|---|
| `cli.py` | Commands and argument parsing. |
| `config.py` | Defaults, YAML merge, prompt loading. |
| `bundle.py` | Story directory layout, manifest, stage state, word and duration counts, draft history, narration staleness. |
| `summary.py` | `story.md` — the per-story view, planned length against measured. |
| `genres.py` | Genre registry, and trope sampling weighted by what has already been approved. |
| `bibles.py` | Bible loading, validation, portrait sharing, scaffolding. |
| `changelog.py` | Per-bible change history, with author and staleness detection. |
| `history.py` | What has been approved, and the sampling weights that keep the next story off the same ground. |
| `review/server.py` | The review queue: stdlib HTTP, JSON for the page, approve and reject. |
| `review/app.html` | The whole interface — markup, styles and script in one file. |
| `llm.py` | Anthropic client and JSON extraction. |
| `lint.py` | Deterministic AI-tell checks. |
| `tts.py` | ElevenLabs and Polly behind one interface. |
| `agents/text.py` | Ideator, writer, editor, the loop between them, the `LENGTHS` table, and both review gates. |
| `agents/actor.py` | Voice casting, synthesis, stitching, timeline. |
| `agents/designer.py` | Cast sheet, shot planning, image generation. |
| `agents/director.py` | Shot list, Ken Burns, crossfade, subtitles, mux. |

---

## Commands

```bash
cli genres [--tropes]        # genres, subgenres, trope menus
cli history [--bible X] [--genre Y]   # what's been repeated; free
cli review [--port N] [--no-open] [--reload]   # review queue; free
cli bibles                   # series, canon counts, recurring cast, portraits
cli bible new <name> --genre <g> [--series "..."] [--example]
cli bible check [<name>]     # validate; free, no API calls
cli bible log <name> [-m "..."] [--author X] [--kind canon]
cli voices                   # your ElevenLabs voices, flagged against voice_library

cli new --genre <g> [--subgenre <s>] [--length {15,30}] [--notes "..."] [--bible <b>]
cli revise <story> "what to change"
cli approve <story> [--chapters]

cli write <story> [--force]
cli lint <story>             # free, no API calls
cli record <story> [--dry-run] [--force]
cli design <story>
cli direct <story> [--force]

cli status <story>
cli all --genre <g> [--length {15,30}]   # every stage, no review gate
```

Nine of these cost nothing and need no credentials: `genres`, `bibles`,
`bible check`, `bible log`, `history`, `review`, `lint`, `status`, and
`record --dry-run`.
