# Ideation

The ideator runs in two modes. They differ only in whether you hand it notes, and
they end at the same review gate.

### Open commission

```bash
python3 -m story_pipeline.cli new --genre regency --subgenre gothic
```

Genre and subgenre only. The ideator invents the premise, cast, tropes and beats
inside that genre's conventions, and you judge the result rather than specify it.

Use this when you want volume. Generating five and keeping one is cheaper in your
time than briefing five, and it surfaces premises you would not have asked for —
which is the whole reason to have an ideator rather than an outline template.

`--subgenre` is optional and defaults to the genre's first, but it's worth
setting. It's the difference between a Regency mystery and a Regency gothic, and
it steers the beats more than anything else at this stage. `cli genres` lists
what each genre has.

### Directed commission

```bash
python3 -m story_pipeline.cli new --genre scifi --subgenre mystery \
    --notes "Two engineers on a cargo hauler, one lying about the manifest. The liar should be the sympathetic one."

python3 -m story_pipeline.cli new --genre fantasy --subgenre folk_tale \
    --notes-file briefs/the-miller.md
```

Same thing with a brief. Your notes are binding on premise, character, and
setting; everything you leave unsaid stays the ideator's to decide. `--notes` is
a string and `--notes-file` reads one from disk. Use the file for anything past a
sentence or two: a shell string that wraps across lines bakes its own indentation
into the note, and a file keeps the brief around to re-run later.

Use this when you have a premise you want to see executed, when you're writing a
series and a new story has to fit beside existing ones, or when a previous story
did something you want done again with different people.

**Notes work better as constraints than as plot.** "The letter must arrive too
late" and "set it in a coaching inn over one night" and "the aunt is right about
everything and insufferable about it" give the ideator a shape to solve. A note
that specifies the whole plot beat by beat leaves it nothing to do but transcribe
you, and you'll get a worse outline than either of you would have produced alone.

Good notes tend to be one of:

- a situation, without its resolution — "two people who have to share a carriage
  for three days and cannot admit they've met before"
- a character, without their arc — "a curate who is genuinely good at his job and
  bad at everything else"
- a constraint — "no ballroom", "it has to work with only two speaking parts",
  "the ending is quiet"
- a texture — "the same register as the Combe story but colder"

**Precedence is boundaries > your notes > genre convention.** A note that asks
for something the content boundaries forbid does not get quietly dropped and does
not get followed: the ideator builds the closest story it can and reports what it
couldn't do in `notes_conflict`, which the review gate prints. A note that
contradicts genre convention just wins — if you want a Regency story with no
marriage plot in it, say so and you'll get one.


For a series with recurring characters and a shared setting, see [Series bibles](bibles.md).

### Review, revise, approve

Both modes stop at the same gate. `new` prints the outline and marks the story
`awaiting_review`; `write` refuses to run until you approve it.

```bash
python3 -m story_pipeline.cli revise stories/the-hartfield-letter \
    "Age the sister to 24. Cut the ball; do it in the churchyard instead. Chapter 4 resolves too easily."

python3 -m story_pipeline.cli approve stories/the-hartfield-letter
```

`revise` regenerates the outline in the same bundle, with instructions to keep
everything it wasn't asked to change — same title, same cast names, same tropes —
so a note about chapter four doesn't silently rewrite chapter one. Each round is
appended to `revisions` in the outline, so the bundle records what you asked for
as well as what you got.

Revise as often as you like. Ideation is the cheapest stage in the pipeline by
two orders of magnitude: a few cents against roughly a dollar for narration and
twenty image generations downstream. Every problem fixed here is a problem you
don't pay to discover after the writer, the actor and the designer have all run.
Things worth catching at this gate specifically:

- Names that collide by ear. The validator catches shared opening syllables;
  it won't catch two names with the same rhythm, and you're the last check
  before they're read aloud.
- Any under-18 in the cast. The gate prints these with their role, because the
  structural age check keys off `role` and a mislabelled minor slips past it.
- A chapter whose beats don't force the next one. Cheap to see here, expensive
  to fix once it's 400 words of prose.
- Tropes that aren't what you wanted. They're the promise to the viewer, and
  they're locked in for the writer and editor after this.

### Skipping the gate

```bash
python3 -m story_pipeline.cli all --genre fantasy --subgenre folk_tale
python3 -m story_pipeline.cli write stories/<slug> --force
```

`all` auto-approves and runs every stage. It's for batches you intend to triage
afterwards, not for a story you care about.

### What lands in the outline

`outline.json` carries the genre, subgenre, the two or three declared tropes,
your notes verbatim if you gave any, the revision history, a `logline` holding
the central irony, and a `promise` — one sentence naming what a viewer who
clicked this is owed by the end. Everything downstream reads from this file, so
it's also the place to hand-edit if a single detail is wrong and a full revise
would be overkill.



## Keeping part of an outline

`revise` regenerates against a note and keeps everything you did not object to.
That is the tool for "the premise is right, the execution isn't".

```bash
python3 -m story_pipeline.cli revise stories/<slug> \
  "Keep the logline, promise, setting and tropes exactly as they are. Replace
   the cast and the chapters entirely — a new cast alongside the bible's
   recurring characters, and new chapters with 4 to 5 beats each."
```

Roughly $0.07, and it rewrites the outline in the same bundle: same slug, same
pinned length, same series.

Be explicit about what to **replace**, not only what to keep. The default is to
preserve title, cast names, tropes, logline, promise and setting; your note
overrides that, but only for what it actually names. "Make it better" changes
almost nothing.

The outline being replaced is saved to `drafts/outline.N.json` first, so a
revision that turns out worse than the original is recoverable.

### revise or a fresh `new`?

| | `revise` | `new --notes` |
|---|---|---|
| Bundle | Same one, same slug | A new one |
| Premise | Kept unless you say otherwise | Restated by you in the note |
| Tropes | Kept | Chosen from the sampled menu, unless your note names them |
| Cost | ~$0.07 | ~$0.07 |

`revise` when you are keeping most of it. `new` when the premise itself is the
problem — the trope weighting will steer a fresh run away from what you have
already approved, which a revision deliberately does not do.

---

[← Running the pipeline](running.md)  ·  [Index](index.md)  ·  [Series bibles →](bibles.md)
