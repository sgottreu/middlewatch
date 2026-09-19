# Series bibles

A genre says what Regency stories sound like. A **bible** says what *this series*
is: the same parish, the same recurring busybody, the fact established two
stories ago that the living at Combe is worth two hundred and forty a year.

```bash
python3 -m story_pipeline.cli bible new amberlight --genre regency \
    --series "Middle Watch: Amberlight" --example      # --example ships it filled in
python3 -m story_pipeline.cli bible check amberlight   # validate; free, no API calls
python3 -m story_pipeline.cli bibles                    # what exists, and who recurs

python3 -m story_pipeline.cli new --bible amberlight --subgenre mystery
python3 -m story_pipeline.cli new --bible amberlight --subgenre romance \
    --notes "this one is about the empty house at the end of the lane"
```

`bible check` runs the same error-level checks the ideator runs, so a bible that
cannot work fails for free rather than after a paid call. Errors stop
`new --bible`; warnings name the ways a series will drift.

A bible declares its own genre, so `--genre` becomes optional beside one. Passing
both and disagreeing is refused rather than resolved — the wrong one produces a
plausible bad story, which is worse than an error.

Bibles live in `bibles/` (yours, not shipped) in the same format as a genre file:
YAML frontmatter plus prose. `bible new` scaffolds one with the fields commented.

| Frontmatter | What it does |
|---|---|
| `canon` | Facts true in every story. The editor checks chapters against them. |
| `recurring_cast` | Fixed name, age, gender, station, appearance, dress. Optional `voice` and `portrait`. |
| `art_style`, `voices` | Optional overrides so a series looks and sounds like itself, not like its genre. |

Everything downstream inherits it. The ideator writes inside the canon, the
writer and editor see it every chapter, the editor treats a contradiction as a
continuity failure, and the designer gets it too.

**Recurring characters keep their face and their voice across stories.** That's
the part that earns the feature. A portrait generated for Mrs Pike in story one
is promoted into `bibles/amberlight/amberlight.cast/` and reused in story four — because two
portraits from the same description are not the same person, and across a
playlist that reads as recasting the role between episodes. A `voice` in the
bible flows through as an explicit casting override, so she sounds the same even
in a story where she barely speaks.

Canon also wins over the model. The ideator is told to copy recurring characters'
fields verbatim and mostly does, but "mostly" is not continuity, so
`_apply_bible_casting()` forces every bible-stated field back over whatever came
back before the outline is validated.

**Precedence is boundaries > bible > your notes > genre convention.** A note that
contradicts established canon doesn't silently break it: the ideator builds the
closest story it can and reports the clash in `bible_conflict`, which the review
gate prints. Continuity across a series is worth more than one story's
convenience, and a viewer three episodes in notices immediately.

**A recurring character who marries in one story is not married in the next.**
Nothing appends to `canon` after a story finishes — the code only ever reads it —
so a consequence persists only if you write it into the bible yourself. Whether a
series *should* carry consequences is a per-series question, and
[Consequences across a series](continuity.md) is the design for answering it.

Every story still stands alone. The scaffold says so explicitly and the shipped
example repeats it: no recaps, no cliffhangers, no arc that only pays off
elsewhere. A shared world, not a serial — which is what makes the playlist work
when someone arrives at episode six first.

## Writing one yourself

[Writing a series bible by hand](bible-format.md) is the field-by-field
reference: what the loader requires, which fields reach which agent, and the
three ways a hand-written bible silently does nothing. Read it before preloading
a bible or editing a scaffold.

The short version, if you only read one thing: **the ideator is shown only
`name`, `age`, `station` and `note`.** A character's appearance, dress, gender
and voice never appear in any prompt — they are forced onto the outline by code
after generation. And `name` joins on the full string, so "Honoria Pike" will not
match "Mrs Honoria Pike".

## The shipped example

`bibles/amberlight/amberlight.md` is filled in as a working example rather than a stub — the format matters more than the loader. Read it before writing your own.


---

[← Ideation](ideation.md)  ·  [Index](index.md)  ·  [Style and genres →](style.md)
