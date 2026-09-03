# Cost and revenue ledger

Tracks every paid API call per story, joins it against YouTube revenue, and
tells you which genres and flavors actually earn.

Stdlib only. Lives at the repo root, shared by `story_pipeline/` and
`ambience_pipeline/` alike.

**Run every command from the repo root.** `LEDGER_DIR` resolves against the
current working directory, so running from inside a pipeline folder silently
creates a second, separate ledger. Set `MW_LEDGER_DIR` to an absolute path in
`.env` if you want that guaranteed.

```
ledger/
  __init__.py
  ledger.py           record costs, upsert revenue
  rates.json          rate card, with a verified date
  import_youtube.py   YouTube Analytics CSV to revenue.csv
  report.py           the join and the rollups
  costs.csv           created on first write, append-only
  revenue.csv         created on first import, upserted
  videos.csv          you maintain this: story_slug to video_id
```

---

## Why two files instead of one

`costs.csv` is append-only. A cost event is a fact about money already spent and
nothing rewrites it.

`revenue.csv` is upserted, keyed on story plus platform plus month. YouTube
restates figures as data settles and revenue keeps accruing for years, so those
rows change repeatedly. Writes go through a temp file and an atomic replace, so
an interrupted import cannot leave a half-written ledger.

One combined file would mean rewriting immutable spend history on every revenue
refresh. Keeping them apart means a bad import can never destroy the part you
cannot reconstruct.

---

## Wiring it into the agents

Every call takes a `story` dict of the dimensions you want to slice by later.
Don't build it by hand — ask the bundle, so no two call sites can drift:

```python
story = b.ledger_story()
```

`genre`, `flavor`, `title` and `series` are pinned into the manifest at creation,
the same way art style and voice casting are. That is what makes the comparisons
worth reading: a genre file edited next year cannot restate what a story written
this year cost to make.

`pipeline` is the one label with no default. It is stamped into every manifest by
`Bundle.create` and validated against `PIPELINES` on the way in, so a caller that
forgets it raises instead of writing a row. Every other dimension degrades to a
blank cell, which costs you one line in a report; this one would silently merge
two channels' economics into a single number that nothing downstream could tell
was wrong.

### Ideator, writer, editor

Every Anthropic response carries `response.usage`. One line after each call:

```python
from ledger.ledger import record_anthropic

resp = client.messages.create(...)
record_anthropic(story=story, stage="write", model=MODEL,
                 usage=resp.usage, run_id=run_id, notes=f"ch{n}")
```

This writes a separate row per token class. Cached input is priced about an
order of magnitude below fresh input, and collapsing them hides your single
biggest lever on writing cost. If you later cache the style guide and the arc
bible across chapter calls, the ledger is what proves whether it worked.

Call it on failed and retried calls too. You were billed for those.

### Actor (Polly)

```python
from ledger.ledger import record_polly

record_polly(story=story, stage="record", engine=cfg.engine,
             billed_characters=len(chunk_text), run_id=run_id)
```

Record per chunk, not per story. You already split on the 3,000-character
ceiling, so the chunk boundary is the natural place. Bill on characters sent,
including SSML tags if you use them, since that is what AWS counts.

### Designer (Gemini)

```python
from ledger.ledger import record_gemini

record_gemini(story=story, stage="design", model=cfg.image_model,
              images=len(cast), run_id=run_id, notes="cast sheet")
```

Two calls, one for the cast sheet and one for scenes. Keeping them separate is
what will tell you whether arc-level cast reuse actually saves money, because
under an arc the cast sheet cost should appear once for four stories instead of
four times.

### Director

No API cost. ffmpeg is local. Nothing to record.

---

## Revenue

There is no free API for revenue figures, so this step is manual and always will
be. Monthly:

1. YouTube Studio, Analytics, Advanced mode.
2. Date range: one calendar month.
3. Dimension: Video. Add the Views, Watch time, and Estimated revenue columns.
4. Export CSV.
5. `python3 -m ledger.import_youtube ~/Downloads/export.csv --period 2026-09`

Maintain `videos.csv` as you publish:

```
story_slug,video_id
amberlight-01-curate,dQw4w9WgXcQ
```

Anything not in that map is reported as skipped rather than silently dropped.
Re-running an import is safe and idempotent, so add the missing rows and run it
again.

Import each month separately rather than as one range. Per-month rows let you
see the decay curve, and ambient especially earns on a long tail where the
shape matters more than the total.

---

## Reading it

```
python3 -m ledger.report                  # per story, with break-even status
python3 -m ledger.report --stages         # where the money goes
python3 -m ledger.report --by pipeline    # stories against ambience
python3 -m ledger.report --by genre       # genre comparison
python3 -m ledger.report --by flavor      # flavor comparison
python3 -m ledger.report --by arc_slug
python3 -m ledger.report --csv summary.csv
```

## Changing the schema

`costs.csv` is append-only for rows, not frozen for columns — a new dimension
worth slicing by means a rewrite of a file that is otherwise never rewritten.
`migrate.py` does that rewrite: it adds missing columns, drops unknown ones only
when every cell is empty, keeps populated ones at the end rather than discarding
data, and writes atomically after a timestamped backup. It is idempotent, so
`--check` is usable as a test.

```
python3 -m ledger.migrate --check                    # exit 1 if out of step
python3 -m ledger.migrate --dry-run                  # say what would change
python3 -m ledger.migrate                            # rewrite, keeping a backup
python3 -m ledger.migrate --backfill pipeline=story  # fill empty cells only
```

Add the column to `COST_FIELDS` first; the script reconciles the file to the
schema, never the other way round.

Sample output from synthetic data:

```
story                        genre      flavor              cost      rev      net   views  status
The Curate of Ashcombe       regency    comedy-of-manners  $2.50   $44.64   $42.14   9,820  paid back (3mo)
A Letter Misdirected         regency    gothic             $2.50    $0.00   $-2.50       0  unpublished
Signal from Kestrel          scifi      quiet-dread        $2.50    $3.36    $0.86   1,440  paid back (2mo)
```

---

## Rates

`rates.json` carries a `rates_verified` date. `report.py` prints a warning when
it is more than 90 days old. Every provider changes pricing and a stale rate
card produces confident wrong numbers, which is worse than no numbers.

An unknown provider, model, or unit raises immediately rather than recording
zero. A pricing gap should fail at the call site, not surface as a suspiciously
cheap story six months later.

Free tier is recorded as gross cost. `rates.json` documents the Polly free tier
separately so you can see both the real burn and the invoiced amount, because
the free tier expires and the underlying cost is what you plan against.

---

## What this will tell you, and what it will not

The per-story API cost lands around two to three dollars. At a typical narration
RPM that is roughly five hundred to a thousand views to break even, which most
published videos clear. So the break-even question is close to already answered:
almost every video that gets watched at all will repay its API cost.

The genre and flavor comparison is the part worth building. That is a real
question with a real answer you cannot guess.

Two honest limits.

**Your time is the actual cost and this does not track it.** At two dollars a
story, the API spend is rounding error against the hours you put in. If you want
a true cost per video, add a manual minutes column. The ledger has no way to
observe it.

**Nothing monetizes until the channel is monetized.** Until you clear 1,000
subscribers and 4,000 watch hours the revenue side stays empty and every row
reads as a loss. That is expected and not a signal. The cost side is still worth
recording from the first run, because backfilling it later is impossible.

Small sample sizes will mislead you. Treat any genre or flavor difference under
about a dozen stories per bucket as noise. The report prints that reminder
under every grouped view for a reason.
