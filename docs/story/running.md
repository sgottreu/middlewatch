# Running the pipeline

The six stages, and how they fit together.


```bash
python3 -m story_pipeline.cli genres --tropes                     # what's available
python3 -m story_pipeline.cli new     --genre regency --subgenre mystery --length 30
python3 -m story_pipeline.cli revise  stories/the-hartfield-letter "age the sister up"
python3 -m story_pipeline.cli approve stories/the-hartfield-letter
python3 -m story_pipeline.cli write   stories/the-hartfield-letter
python3 -m story_pipeline.cli approve stories/the-hartfield-letter --chapters
python3 -m story_pipeline.cli record  stories/the-hartfield-letter --dry-run
python3 -m story_pipeline.cli record  stories/the-hartfield-letter
python3 -m story_pipeline.cli design  stories/the-hartfield-letter
python3 -m story_pipeline.cli direct  stories/the-hartfield-letter

python3 -m story_pipeline.cli review                     # the whole queue, in a browser
python3 -m story_pipeline.cli status  stories/the-hartfield-letter
python3 -m story_pipeline.cli all --genre fantasy --subgenre folk_tale   # no review gate
```

The first four commands are the ideation stage; see [Ideation](ideation.md) for
the two ways to brief it and what to look for at the review gate.

## Two gates

The pipeline stops twice, at the two points where continuing costs real money.

| After | Stage sits at | Unlock with |
|---|---|---|
| `new` | `ideate: awaiting_review` | `approve <story>` |
| `write` | `edit: awaiting_review` | `approve <story> --chapters` |

The first protects the writing spend: 2,000 or more words drafted against an
outline you have not read. The second protects narration, which is the largest
single cost here and used to run against chapters the editor had just failed.

Both take `--force` on the stage they block, and `record --dry-run` always works
— it is free, and it is how you decide. `cli all` skips both, which is what it is
for.

```bash
python3 -m story_pipeline.cli review
```

opens the whole queue in a browser: outlines waiting, chapters waiting, chapters
that failed review, and any chapter whose narration no longer matches its text.
`j`/`k` to move, `a` to approve, `r` to reject. See [the review UI](review-ui.md).

## Length

`--length` takes 15 or 30 minutes, defaulting to `length_min` in `config.yaml`.

| | 15 | 30 |
|---|---|---|
| Words | 2,250 | 4,500 |
| Chapters | 4–6, target 5 | 7–9, target 8 |
| Words per chapter | ~450 | ~560 |
| Beats per chapter | 3–4 | 4–5 |
| Named cast | 3–6 | 4–7 |
| Images | 3/chapter, 15 total | 4/chapter, 32 total |

Beats scale too, and that is the number that decides whether a longer story is
bigger or just slower. The first 30-minute outline generated came back at 187
words per beat against 112 at 15 minutes — the same story told more slowly,
because the prompt asked for the same 2–4 beats in a 560-word chapter as in a
450-word one. Both settings now target roughly 112–150 words per beat, and the
review gate prints the figure so you can see it before approving.

Chapters get longer as well as more numerous. Holding them at 450 words would
put 30 minutes at ten chapters, and chapter count is what drives the two costs
that scale worst: the writer is sent every prior chapter for each new one, and
images are minted per chapter.

The setting is fixed when the story is created and pinned into the manifest, so
re-rendering it a year later keeps the shape it was written to. `revise` will not
move it — changing length means a different story, not a revision of this one.
`--length 30` roughly doubles the narration bill, which is the bulk of what a
story costs; `record --dry-run` gives you the figure before you commit.

A longer story earns its length with more incident and a fuller cast, not with
the same story told slower. If a 30-minute outline reads like a 15-minute one
padded, that's the failure to watch for at the review gate — and the outline
summary prints planned words and estimated runtime so you can see it before a
word is written. Longer settings than 30 are a later problem; adding one means a
new entry in `LENGTHS` in `story_pipeline/agents/text.py` and nothing else.

Stages are separate on purpose. Read the outline before you pay for 2,000 words;
read the chapters before you pay for narration. `record --dry-run` reports
character count, runtime and cost without calling Polly, and doubles as a format
check — a malformed chapter fails there rather than halfway through a paid render.

Every stage is idempotent on what already exists. Re-running `record` skips
chapters that already have audio, and `design` skips images already on disk.
That's what makes a failure in the last stage cheap.

The one exception is a chapter you have edited since it was narrated. `record`
compares the text against the hash stored in its timeline and re-records it
rather than skipping — otherwise the video would carry prose on screen that the
voice never says, and nothing would tell you.

## Every stage in one place

| Stage | Command | Reads | Writes |
|---|---|---|---|
| Ideate | `new` | genre, subgenre, notes, bible | `outline.json` |
| Write | `write` | outline | `chapters/NN.json`, `reviews/NN.json`, `drafts/NN.<n>.json` |
| Record | `record` | chapters | `audio/NN.mp3`, `audio/NN.timeline.json` |
| Design | `design` | chapters + timelines | `images/` |
| Direct | `direct` | audio + images + timelines | `video/final.mp4` |

Each is independently resumable — see [Architecture](architecture.md).


---

[← Setup](setup.md)  ·  [Index](index.md)  ·  [Ideation →](ideation.md)

---

## Where per-chapter state lives

Not in the prose and not in `chapters/NN.json`. The editor's verdict for each
chapter is a separate file:

```
stories/<slug>/
  chapters/NN.json     the segments — speaker and text, what gets narrated
  chapters/NN.md       the same thing rendered to read
  reviews/NN.json      pass, score, fixes, continuity, generic_prose
  drafts/NN.A.json     each pass of the write/edit loop, with its review
```

`cli status <story>` reads them all and prints one line per chapter, which is
the quickest answer to "what state is this story actually in":

```
 ch   words  target  drafts  verdict
  1     584     580       1  passed (8)  +1%
  5     758     560       2  passed (7)  +35%
  6     916     560       1  passed (7)  +64%

5,789 words ~39 min against a 30-minute target (+29%)
```

`drafts` above 1 means the editor sent it back and the writer revised it. The
review UI shows the same per chapter, with the failing quotes inline.

---

## Re-judging without rewriting

`write` couples judging to drafting: the only way to get a fresh verdict used to
be paying for a new chapter and losing the one you had. That is the wrong trade
after a hand edit, and the wrong trade after the rules change — the editor has
gained checks the chapters on disk were never held to.

```bash
python3 -m story_pipeline.cli critique <story>              # every chapter
python3 -m story_pipeline.cli critique <story> --chapter 2
```

One editor call per chapter, roughly $0.02 each, against a rewrite's
writer-plus-editor. **The chapter is not touched** — only `reviews/NN.json` is
rewritten.

That has one consequence worth knowing before you run it on a story you thought
was finished: a chapter whose new verdict fails is no longer *done*, so `write`
becomes willing to rewrite it. Which is the point — but it means `critique` can
turn a finished story back into a queue of work.

Afterwards there are two ways to act on the verdict, and they are not the same:

- **`write`** rewrites the failing chapters whole, with the editor's fixes as
  instructions. Right when the chapter is broadly wrong.
- **The segment editor in `cli review`** changes only the lines you change.
  Right when the verdict names two sentences — which, for a continuity-of-motive
  failure, it usually does.
