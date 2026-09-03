# Calibrating the length dial

The dial rests on one number that has never been measured.

```python
WPM = 150   # story_pipeline/agents/text.py
```

Every figure in `LENGTHS` derives from it — 15 minutes means 2,250 words only
because 15 × 150 = 2,250. `record --dry-run` reports predicted runtime from the
same constant, so the estimate and the target agree with each other by
construction and neither has been checked against audio.

This document is the plan for checking it, and for the other three things the
dial introduced that no test can reach without real stories.

There is nothing to do here until stories exist. It is written now because the
measurements are only available at specific moments — the outline sweep is worth
running before the first `write`, and per-chapter durations are worth capturing
before anything is deleted.

---

## What is actually unknown

Four things, in the order they will bite.

1. **The WPM constant.** 150 is a convention inherited from `actor.py`, not a
   measurement of the voices actually in use.
2. **Whether 30 minutes reads as a bigger story or a padded one.** The failure
   mode that scoping to 15/30 was meant to postpone, not avoid.
3. **Whether the editor's new `length_note` is calibrated.** The one-fifth
   threshold was chosen without evidence.
4. **Whether the two new outline validators fire often enough to need a retry.**
   Both reject *after* the ideator has been paid for.

---

## Before spending anything

**The rate card does not know your models.** `config.py` runs
`claude-opus-5` and `claude-sonnet-5`; `ledger/rates.json` prices
`claude-opus-4-6`, `claude-sonnet-4-6` and `claude-haiku-4-5`. `ledger.py` raises
on an unknown model rather than recording zero, deliberately — so the first
recorded writer call will throw `KeyError`. Add both models to
`providers.anthropic.models` from the pricing page in `rates.json`'s own
`sources` block, and move `rates_verified` to the day you check.

This does not block calibration, which needs only timelines. It blocks any
statement about what a story costs.

**Every command below runs from the repo root, with the venv active.**

```bash
cd ~/Development/claude/middlewatch
source .venv/bin/activate
```

`stories_dir`, `bibles_dir` and `config.yaml` all resolve against the working
directory, so a sweep started from the wrong place writes its bundles somewhere
you won't think to look — and reports nothing wrong while doing it.

---

## Phase 1 — the outline sweep

**Cost: about $0.07 per outline.** The ideator is Sonnet with a 7,000-token
ceiling; a run is roughly 8k tokens in and 3k out. Ten outlines cost well under a
dollar. This is the cheapest signal in the pipeline and it answers three of the
four questions above before a single word is written.

Run five at each length, varying genre — cast pressure and trope density differ
between them, and a check that only ever sees regency has only been half tested:

```bash
for g in regency scifi fantasy regency scifi; do
  python3 -m story_pipeline.cli new --genre "$g" --length 15
  python3 -m story_pipeline.cli new --genre "$g" --length 30
done
```

Nothing here needs approving. You are reading outlines, not making videos.

Do **not** use `cli all` for this. It accepts `--length`, but it skips the review
gate and runs every stage through `direct` — narration, images and video for ten
throwaway outlines.

### Reading the sweep

Each successful run writes `stories/<slug>/story.md` with the chapter count,
cast, planned words and planned runtime already tabulated. To see the whole
sweep at once:

```bash
grep -H "^| Planned" stories/*/story.md
```

The only thing to note by hand is what *failed*. A failed run writes no bundle at
all — `_validate_outline` raises inside `build_outline`, before `Bundle.create`
— so there is nothing to inspect afterwards. Copy the `ValueError` text verbatim
when you see it; it is the entire result of that run.

When you're done reading, delete the bundles. They cost nothing to regenerate
and nothing here is worth keeping.

### What each failure means

**`names too similar to tell apart by ear`** — the first-three-letters check in
`_validate_outline`. Harmless once at 15 minutes. Twice or more across the five
30-minute runs means the wider cast range has made it likely rather than
unlucky, and the ideator retry deferred during implementation is now worth
building: one retry, feeding the rejection back, before the error surfaces.

**`outline has N named characters`** — new, and previously unchecked entirely. If
it fires at all, the ideator is not reading the cast ceiling out of the structure
block. That is a prompt problem in `ideator.md`, not a bounds problem.

**`chapter targets sum to N words`** — the 15% check. If it fires at 30 but never
at 15, the ideator is anchoring on the shorter shape it was trained into and the
length instruction is not carrying. Widening the tolerance would hide that rather
than fix it.

**Nothing fires and every outline lands mid-range** — the likeliest outcome, and
it means Phase 1 is done.

---

## Phase 2 — the calibration pair

Two stories, same genre, one at each length, all the way through `record`. Stop
there; `design` and `direct` cost money and tell you nothing about length.

```bash
python3 -m story_pipeline.cli new     --genre regency --subgenre mystery --length 15
python3 -m story_pipeline.cli approve stories/<slug>
python3 -m story_pipeline.cli write   stories/<slug>
python3 -m story_pipeline.cli record  stories/<slug> --dry-run   # free
python3 -m story_pipeline.cli record  stories/<slug>
```

Narration is the bulk of the spend and it scales linearly — roughly $2.70 at 15
minutes and $5.40 at 30, at the ElevenLabs rate in `config.yaml`. `record
--dry-run` gives you the exact figure first, and is worth reading rather than
skipping.

### The measurement

`record` rewrites `stories/<slug>/story.md`, and it does the arithmetic for you:

```
| | Words | Runtime |
|---|---|---|
| Target (30 min) | 4,500 | 30:00 |
| Planned | 4,480 | 29:52 |
| Written | 4,375 | 29:10 |
| **Narrated** | | **31:42** |

Measured pace 138 wpm against an assumed 150. Runtime is +6% for a
30-minute story.
```

That is the whole measurement. The per-chapter table below it shows where the
drift accumulated, which matters when the total is off but no single chapter
looks wrong.

`cli status` prints the path if you lose it.

The figure comes from `duration_ms` in each chapter's `timeline.json`, which
already includes the 400ms `casting.gap_ms` inserted between segments. That is
the number to trust: it is the audio, not a prediction of it.

One thing to watch: the narrated row is all-or-nothing. `Bundle.duration_ms()`
returns nothing unless *every* chapter has a timeline, so an interrupted
`record` leaves `story.md` reading *not yet recorded* even with most of the
audio on disk. That is deliberate — a partial sum presented as a runtime is
worse than no runtime — but if the row is blank when you expected a figure, the
cause is a missing chapter rather than a broken measurement. Re-run `record`;
it skips what already exists.

Expect a figure **below** 150. Raw narration pace for these voices is likely
close to it, but the inter-segment gaps are not speech and they are not free: a
chapter with thirty segments carries twelve seconds of silence. Across a
30-minute story that is minutes, not seconds. If the measured figure comes back
*above* 150, something is wrong with the reasoning rather than the voices, and
it is worth understanding before changing the constant.

### Acting on it

Within 5% of 150 — leave it. The dial is honest and the estimate is honest.

Further off — update `WPM`, and note that `LENGTHS` currently hardcodes figures
that are all derivable from it:

```
words         == minutes × WPM
chapter_words == words / chapters
```

Rather than editing six numbers by hand and letting them drift out of agreement,
derive them. Then recalibrating is a one-line change and the table cannot lie
about its own arithmetic.

One story per length is enough to see the direction. Three or four gives a figure
worth writing down, and they are worth accumulating over the first month rather
than bought all at once.

---

## Phase 3 — reading them

The part no script does.

Read the 15 and the 30 back to back, ideally as audio rather than text, since
that is the medium. The question is not whether the longer one is good. It is
whether it is **bigger** — more incident, a fuller cast, more turns — or the same
story told slower.

Three places carry evidence:

**`reviews/NN.json`, the `generic_prose` field.** The editor's judgement on
whether a sentence would still be true of a different story. If those entries
cluster in the middle chapters at 30 minutes and not at 15, the ideator has
stretched a 15-minute premise across eight chapters and the middle is where the
padding went. That is a prompt problem in `ideator.md` — the length instruction
already says a longer story earns its length with incident, and it would need
sharpening rather than repeating.

**`length_note`, new and untested.** If it fires on most chapters, either the
writer cannot hit 560 words or one-fifth is too tight a threshold. Distinguish
them by looking at the direction: consistently under means the writer is
defaulting to the shorter chapter it knows; scattered both ways means the
threshold is wrong.

**`cli lint`, free and worth running on both.** `lint.py` normalizes its
AI-signature count per thousand words, so the figure is comparable across
lengths. A materially worse rate at 30 minutes is the machine register creeping
in as the writer runs out of story — the same padding, seen from a different
angle.

---

## What this does not measure

**Your time.** Unchanged from the note in `ledger/README.md`: at a few dollars a
story the API spend is rounding error against the hours, and nothing here
observes them. If the 30-minute setting takes three times the review effort for
twice the runtime, that is the real result, and only you can see it.

**Whether 30 minutes performs better.** That needs published videos and months
of retention data, and it is the ledger's question rather than this document's.
The `--by flavor` and `--by genre` rollups will eventually answer it; a length
rollup would need `length_min` added to the ledger's story dict, which is one
line whenever the ledger is wired in.

**Anything about 45 or 60.** Deliberately. The costs that scale worst — the
writer receiving every prior chapter, images minted per chapter — are mild at
eight chapters and are not mild at twenty. Nothing measured here extrapolates.

---

## Measuring it

`WPM = 150` decides the whole dial: it sets how many words a 15- or 30-minute
story is, which sets the per-chapter target, which the length check now enforces
to within a fifth. Nobody has ever measured it.

That was tolerable while chapters overran by 29% anyway — the error in the
assumption was smaller than the error in the output. It is not tolerable now
that the output lands within 12%, because the next thing you would reach for is
tightening `length_tolerance`, and that means spending revisions to hit a target
that may itself be wrong.

`record` reports the real figure the moment the audio exists, and
`cli status` repeats it for anything already narrated:

```
5,019 words narrated in 39.2 min

  assumed    150 wpm  ->  predicted 33.5 min
  measured   127.9 wpm  ->  actual    39.2 min
  speaking   132.0 wpm excluding 73s of gaps (3% of the runtime)

  The assumption is 17% fast.
  Set WPM = 128 in story_pipeline/agents/text.py.

    dial   words now    words at 128   per chapter
    15min       2,250           1,918           384
    30min       4,500           3,837           480
```

Two paces, answering different questions:

- **speaking** excludes the silence between segments. It is how fast the voice
  reads, and it is what carries to another story with a different `gap_ms`.
- **measured** includes the gaps — words over the runtime you actually get. That
  is what the dial is trying to predict, so that is what belongs in `WPM`.

The gap between them is what `casting.gap_ms` costs, which is worth seeing on
its own. At 400ms it is around 3% of runtime; at 1200ms it is 10%, and a voice
reading at exactly 150 delivers an effective 135.

Under 5% it says leave it alone. Over, it names the number and shows what
changing it would do to every dial setting first — because that is a decision
about what a "30-minute story" means, not a bug fix.

**Do this before tightening `length_tolerance`.** One narrated story answers it.
