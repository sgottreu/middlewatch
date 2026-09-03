# A review UI

`cli review` — a local page listing everything waiting on you, with the document
in a side pane and approve / reject in reach.

**Status:** the three prerequisites, steps 1–2 of the build order, and Revise are
done. In-place editing, the diff view and the remaining suggestions are not.

```bash
python3 -m story_pipeline.cli review          # opens a browser on :8765
python3 -m story_pipeline.cli review --no-open
```

The review gate is the only place a human is required, and it is currently a
terminal print followed by copying a slug into another command. That is fine for
one story and tiring for twelve, which is what a sweep produces.

---

## Three things to fix first — done

The UI could not do the job until these were true. Each was small, and each is
worth having on its own merits.

### 1. Nothing keeps the raw draft

You asked to see "raw stories and edited stories". Right now only one exists.
`write_chapter()` writes `chapters/NN.json`, the editor reviews it, and if fixes
are required `write_chapter()` is called again and **overwrites the same file**.
`review_chapter()` overwrites `reviews/NN.json` the same way.

So a chapter that took two revisions leaves no trace of what it looked like
before, and no record of what the editor actually changed. There is nothing to
put in a side-by-side.

**Done.** Every pass is written to `drafts/NN.<attempt>.json` with its review
beside it; `chapters/NN.json` stays the current one. `Bundle.drafts(n)` lists them. They are small JSON files, and keeping them makes
three things possible: the diff view, an answer to "is the editor earning its
keep", and a way to recover a first draft you liked better.

### 2. Nothing gates the expensive stage

`write` is gated: it refuses unless `ideate` is `approved`. `record` is not
gated at all. It will narrate whatever is on disk, including chapters that failed
review, and narration is the largest single cost in the pipeline.

**Done.** `run_text_stages` leaves `edit: awaiting_review`; `record` refuses
unless approved, mirroring the ideate gate, with `--force` and an always-allowed
`--dry-run`. Approve with `cli approve <story> --chapters` or from the UI. That
is what makes the chapter queue meaningful rather than decorative.

### 3. Editing after `record` silently ships the wrong audio

`record_all()` skips any chapter that already has an mp3 and a timeline. So
editing `chapters/NN.json` after narration and re-running `record` keeps the
**old** audio. The video then has prose on screen that the voice never says, and
nothing warns you.

This is the one genuine hazard in letting a UI edit chapters, and it exists
already for anyone editing by hand.

**Done.** `Bundle.chapter_hash()` hashes the spoken text into the timeline;
`narration_stale()` compares. `record` re-records a changed chapter instead of
skipping it, the queue has a *Narration out of date* group, and a retitled
chapter is correctly *not* stale. Timelines written before this carry no hash and
are trusted rather than re-billed.

---

## The UI

### Local, stdlib, one file

`cli review` starts `http.server` on `127.0.0.1`, opens a browser, and serves a
single self-contained HTML page. No new dependencies, no build step, no
framework — matching the way `ledger/` is stdlib-only.

This is a single-user tool on your own machine looking at files in your own repo.
Flask, a database, a bundler and a login screen would all be real work spent on
problems you do not have. Bind to localhost only and there is no auth question
either.

```
story_pipeline/
  review/
    __init__.py
    server.py      routes, and the JSON the page reads
    app.html       the whole interface: markup, styles, script
```

**`app.html` is re-read from disk on every request; the Python is not.** It is
imported once when the server starts, so a change to `server.py`, `config.py` or
anything else in the package needs a restart — which is exactly the kind of thing
that costs an afternoon before anyone says it out loud.

```bash
python3 -m story_pipeline.cli review --reload
```

watches `story_pipeline/` and re-execs when a `.py` file changes. It waits for
any in-flight request first, so a reload cannot land in the middle of a revise
and spend the money without keeping the reply. Without `--reload` the startup
banner says which half is live.

### Layout

```
┌────────────────────┬──────────────────────────────────────────┐
│ QUEUE              │  The Bath Will                    15 min │
│                    │  regency / mystery · Amberlight          │
│ ▼ Outlines (3)     │  ┌────────────────────────────────────┐  │
│   The Bath Will  ● │  │                                    │  │
│   The Cracked Beam │  │  outline, chapter, or review        │  │
│   The Shuttered W. │  │  rendered readably — editable in    │  │
│                    │  │  place                              │  │
│ ▼ Chapters (5)     │  │                                    │  │
│   3. The Assembly ⚠│  └────────────────────────────────────┘  │
│   4. A Refusal     │  20 beats · 112 w/beat · $0.07 so far    │
│                    │  [ Approve ]  [ Reject ]  [ Revise… ]    │
└────────────────────┴──────────────────────────────────────────┘
```

Left is the queue, grouped by what is waiting. Right is the document. The bar
underneath carries the facts you need to decide and the actions.

### The queue

Built by scanning `stories/*/manifest.json` — the same walk `history.scan()`
already does.

| Group | What is in it |
|---|---|
| **Outlines** | `ideate: awaiting_review`. The main queue after a sweep. |
| **Chapters** | `edit: awaiting_review`. |
| **Failed review** | Chapters where `reviews/NN.json` has `pass: false` or `exhausted: true`. These need you most and were invisible before. |
| **Narration out of date** | Chapters edited since they were recorded. |

Each row shows the title, length, and a badge for anything wrong — a failed
review, a thin beat density, an under-18 in the cast.

### What each document looks like

**An outline** renders as `story.md` already does — logline, cast, chapters with
beats, the length and density lines. Everything `_show_outline` prints today,
plus what it cannot: the under-18 warning as a red banner rather than a line of
terminal text that scrolls past.

**A chapter** shows the prose from `chapters/NN.md`. That file is a *view* of
`chapters/NN.json`, and the difference matters: the JSON records a `speaker` per
segment and the writer is told never to put a name inside a dialogue segment,
because each character is cast to their own voice and a spoken *said Elizabeth*
is redundant when you can hear who it is. Read on the page, the same chapter
loses its attribution entirely.

`render_markdown` puts it back, from the speaker field and without inventing
prose:

- **A paragraph break at every change of speaker**, as in any printed novel.
  Before this the whole chapter was a single run-on paragraph.
- **An attribution tag stays on the line it tags.** `writer.md` splits
  `'I could not,' said Elizabeth, 'think of it.'` into three segments, so a tag
  arrives as its own narrator segment; a lower-case opening marks it as a
  continuation rather than narration, and it is joined rather than left standing
  as a bare `said Elizabeth.` on a line of its own.
- **Each turn is labelled** — `**Mrs Pike** "Old Mr Fenner is dead."` — shortened
  to first-and-last while that stays unique in the cast, and left in full when it
  does not. `render_markdown(..., attribute=False)` gives the unlabelled reading
  copy.

The label is an annotation, never prose. Writing a real *said Mrs Pike* into the
markdown would put a sentence on the page that the writer never wrote and the
narration will never say, and the two would drift. If you want real tags in the
prose, that is a change to `writer.md` — and it lands in the audio too.

The editor's verdict sits beside it — `generic_prose` quotes highlighted in the text itself, `fixes` and
`continuity` listed. The linter's findings sit in the same list, since they are
already merged into the review.

### Write resumes

`write` used to draft every chapter in the outline on every run, so a crash in
chapter 7 meant paying for chapters 1 to 6 a second time to get back prose
already sitting on disk. It now writes only chapters that have not been through
the write/edit loop — a chapter counts as done when it has both a
`chapters/NN.json` and a review that either passed or exhausted its revisions,
since running the loop again on either would spend the same money for the same
outcome.

The dialog prices what will actually happen, and offers the other choice:

```
about $0.20 – $0.39
estimate · for the 2 chapters still to write

2 of 8 chapters — 6 already written, loaded from disk and not paid for again
[ ] rewrite the 6 already written too
```

Ticking the box re-prices without a round trip — the page is sent both
estimates. Note that the resumed price is not the whole price divided by
chapters: every chapter is sent the entire story before it as context, so the
last two are the most expensive two.

The progress list distinguishes them, which is what made a resumed run look
like a full rewrite:

```
chapter 1: already written, kept
chapter 7: passed (8)
```

`cli write <story> --restart` is the same thing from the terminal.

### Editing a chapter

The prose you read is rendered from `chapters/NN.json`, and editing works on
those segments rather than on the rendered page. That is what makes it safe:
`speaker` is what casts the voice, and if editing meant typing over the markdown
it would have to be parsed back out of `**Mrs Pike**` labels — a formatting slip
would silently recast a line.

So each segment is one editable block, with the speaker as a dropdown beside it:

```
[ narrator     ▾ ]  Owen Vane came up the stairs at four o'clock with his
                    apron still on under his coat.
[ Mr Silas Rowe ▾]  I do not know.                                       ×

  [Save chapter] [Cancel] [+ segment]      517 words · target 560 · band 448–672
```

The word count is live against the band the length check enforces. Trimming by
hand is the one case where knowing the number *as you cut* changes what you cut.

A saved edit goes through the same normalisation a fresh draft does — a
shortened speaker resolves against the cast (`Rowe` → `Mr Silas Rowe`), empty
segments are dropped, a stray `**label**` is stripped — and anything the
validator refuses raises before it touches disk:

```
chapter 6 segment 0: unknown speaker 'Miss Ansell'.
file unchanged: True
```

**Three decisions lapse on their own,** because all three are stamped with the
chapter hash and the hash has just changed:

| Stamp | Lapses to |
|---|---|
| `chapter_approvals` in the manifest | *rewritten since you approved it* |
| `chapter_hash` in `reviews/NN.json` | *edited since the editor read it* |
| `chapter_hash` in the timeline | *narration out of date* |

The save says which, rather than leaving it to be discovered:

```
Chapter 6 saved — 517 words · 1 empty segment dropped · approval withdrawn
```

Reviews written before the stamp existed carry no hash and are trusted rather
than flagging every chapter written so far as unjudged — the same fallback the
timeline uses.

Editing does **not** re-run the editor. A human edit is not something to pay a
model to second-guess; the verdict is simply marked as no longer describing the
chapter, and your approval is what clears it.

### Approving a chapter at a time

Approval used to be all-or-nothing, which asked you to read eight chapters in
one sitting before anything could move. Every chapter now carries its own
button, and the action bar counts the progress:

```
8 chapters · 40 beats · 112 w/beat · 5,019 words · 3 of 8 approved
```

Recording unlocks when the last one is approved. `Approve 5 chapters` in the
bar does the remainder in one go, and clicking `✓ approved` withdraws it.

**Approval is recorded against the hash of what was approved.** A chapter
rewritten afterwards — by a `--restart`, or by editing `chapters/NN.json`
yourself — lapses back to unapproved and is marked *rewritten since you approved
it*, because the decision was about prose that no longer exists. That is the
same rule `narration_stale` applies to audio.

The story-level `edit` stage is derived from the chapters rather than stored
beside them. Editing a chapter by hand changes its hash without going through an
approve action, so a stored `approved` could otherwise stand over prose nobody
agreed to; the queue reports what the chapters actually say, and the story
reappears under *Chapters awaiting review*. `record` refuses on the same
condition, checked in the actor rather than only in the CLI wrapper.

From the terminal:

```bash
python3 -m story_pipeline.cli approve <story> --chapter 3
python3 -m story_pipeline.cli approve <story> --chapter 3 --chapter 4
python3 -m story_pipeline.cli approve <story> --chapters      # all of them
```

`cli status` shows the same state, with `!` marking a chapter approved and then
rewritten.

### The icon

`brand/icon.svg` is the master: a watch face with both hands struck, one at
twelve and one at four. Equal length on purpose — neither is an hour hand
pointing at a time, they are the two ends of the middle watch. Palette is the
app's own, #14140f and #c9a227.

It was drawn for 16 pixels and the rest follows, because that is where a favicon
actually lives. Three earlier attempts died there: a filled twelve-to-four wedge
became a gold blob, an arc struck on the rim vanished into the ring, and a
crescent read beautifully while saying nothing about a watch. The stroke weights
look coarse at 512 and are the reason it survives a tab.

The server has one small allowlist of static files — named explicitly rather
than serving the package directory, since `app.html` and everything else in the
package sits there too:

| Route | For |
|---|---|
| `/icon.svg` | the tab, at any size |
| `/icon-180.png` | `apple-touch-icon` |
| `/icon-192.png`, `/icon-512.png` | the manifest |
| `/icon-maskable-512.png` | Android's circle crop — full-bleed, mark inside the 80% safe zone |
| `/manifest.webmanifest` | generated from `MANIFEST` in `server.py` |

With the manifest present, Chrome's **Install** (or *Cast, save and share →
Install page as app*) gives the review tool its own dock icon and a window
without browser chrome, which is the point of having one for a tool you keep
open all day.

### Scroll position

Every action refetches the story and re-renders the document, and `select()`
finished with `docbody.scrollTop = 0`. So approving chapter 6 — or saving an
edit to it, or finishing a write — returned you to the title, which is the worst
possible moment to lose your place: the chapter you just acted on is the one you
were reading.

`select()` now decides for itself. A **different** story is a different document
and starts at the top; the **same** story means an action on what you are
already reading, so the position is kept. Nothing has to pass a flag, which
matters because `act()` deliberately moves to the next story in the queue and
should reset.

Chapter-level actions set `state.anchor` and are returned to the chapter itself
rather than to a pixel offset — an edit changes the height of everything above
it, so a restored offset would drift by however much the edit added or removed.

Every button in the page is explicitly `type="button"` apart from the two that
submit their dialogs. None of the others sits in a form today, but the HTML
default is `submit`, and the day one of them does the page navigates and
everything on screen is lost.

### Revise

The button opens a form rather than a text box, because the same sentence gets
retyped otherwise. Checkboxes for what to hold fixed — logline, promise, setting,
tropes, title, cast, chapters — and a free-text note for anything else.

Defaults to keeping the premise and replacing the execution, which is the case
the form exists for: the idea is right, the outline is thin.

The server composes the feedback from the boxes, and says both halves out loud —
what to keep *and* what to replace wholesale — because `revise` already preserves
everything you do not object to, so a note that only says what to keep changes
nothing. When the chapters are being replaced it adds the beat range for that
length, which is what fixes a thin outline.

Two guards worth having:

- **Keeping everything with no note is refused**, server-side as well as in the
  form. It would be a paid call that returns what you already had.
- **Keeping the chapters while replacing the cast** warns, since beats naming the
  old characters have to be rewritten around new ones. It is allowed — sometimes
  that is what you want — and the composed note tells the ideator to preserve the
  events while changing who is in them.

### Write

The step between the two gates. Available once the outline is approved and no
chapters exist yet, and it is the most expensive action in the UI — so it opens
a confirmation that leads with the number:

```
about $0.34 – $0.69
estimate · the range spans no revisions to one per chapter

  · 5 chapters, 2,250 words planned
  · writer claude-opus-5, editor claude-sonnet-5, up to 2 revisions per chapter
  · takes a few minutes — you can keep using the page
  · every chapter is written to disk as it finishes, so a failure part-way
    keeps what completed
```

`estimate_write()` computes it structurally rather than guessing: the writer is
sent the shared prompt layers plus **every prior chapter**, so input grows with
each chapter and the total is quadratic in chapter count. Prices come from
`ledger/rates.json`, so one rate card serves both estimates and accounting. If a
model has no price there the dialog says *cost unknown* rather than inventing a
figure.

**It runs as a background job.** A write is minutes and a dozen or more model
calls; holding an HTTP request open for that would hang the browser and lose the
run to any timeout. `POST /api/write` returns in milliseconds and the page polls
`/api/job/<slug>`, showing a bar and a line per chapter as the editor passes or
fails it.

Closing the dialog does not stop the run — the toast says so, because a Cancel
button that only closes a window should not imply otherwise.

Three guards:

- **An unapproved outline is refused.** The approval gate is what stops the
  writer running against something you have not read.
- **A second write for the same story is refused** while one is in flight.
  Different stories may write concurrently.
- **A crash mid-run leaves the job `failed` with the error**, not hung. Whatever
  chapters completed stay on disk, and the `write` stage is reset to `pending`
  so the story is startable again — `run_text_stages` sets `running` on entry
  and only clears it on success, so without that reset a failed run left the
  story looking neither finished nor runnable. The job reports how many chapters
  survived.

### What revise does to the bundle

**No new bundle, and no new slug.** It is the same directory throughout:

```
stories/the-oakmere-will/
  outline.json            <- replaced with the revision
  drafts/outline.1.json   <- the outline you were looking at, archived
  story.md                <- regenerated
```

The slug is deliberately preserved even when the revision changes the title, so
every path that already points at the bundle keeps working. The consequence is a
directory whose name can end up describing an earlier version of the story —
`story.md` and `outline.json` carry the real title.

Each revision archives to the next number, so `drafts/outline.1.json`,
`outline.2.json` and so on read oldest to newest. `outline.json` is always the
current one.

While the call is running the dialog says so and locks — a second click cannot
spend twice — and on success it confirms **in place** before closing:

```
Rewritten as The Oakmere Inheritance
revision 1 · 8 chapters · 40 beats · 112 words per beat · previous saved to drafts/
```

Confirming where the eye already is matters more than it sounds: a dialog
vanishing is a large visual change and a small toast in the corner is easy to
miss, so "did that work?" is the obvious next thought. The line names the beat
density because that is usually the number a revision was chasing.

An error leaves the dialog open with the form re-enabled, so the note is not lost.

`e` opens the form; `Escape` closes it. It is available while no chapters have
been written — including an outline you approved and then thought better of —
and disabled once they have, because the chapters would no longer match.

### Actions

| Button | What it does | Cost |
|---|---|---|
| Approve | `text.approve(b)`, or sets `edit: approved` | free |
| Reject | Deletes the bundle, with a confirm | free |
| Save edits | Writes back, re-validates, re-renders `story.md` | free |
| Revise… | A form: checkboxes for what to keep, plus a free note. Composes the feedback and calls `revise_outline()`. | **~$0.07** |
| Re-record | Clears the stale chapter's audio | costs narration |

Anything that spends money says so on the button and confirms. That rule is
worth keeping absolute — it is the difference between a review tool and a way to
run up a bill by clicking.

### Editing safely

Editing is the part that needs care, because the files are load-bearing.

- **An outline edit re-runs `_validate_outline()`** before saving. Same rules as
  generation: chapter bounds, cast bounds, the ear check, the word-sum check. If
  it fails, the save is refused and the error shown — you cannot hand-edit your
  way into an outline the pipeline will choke on later.
- **A chapter edit re-renders `chapters/NN.md`** from the segments, so the two
  never drift.
- **Edits after narration** set the stale flag from gate 3.
- **Write to a temp file and rename**, the way `ledger.py` already does for
  revenue. A browser tab closed mid-save should not truncate a chapter.

---

## Build order

Each step is usable before the next exists.

1. **Read-only queue and viewer.** Done.
2. **Approve and reject.** Done, with `j`/`k` to move and `a`/`r` to act, landing
   on the next item so a queue can be worked straight through.
3. **Draft history** (fix 1) — done; the diff view that uses it is not.
4. **The chapter gate** (fix 2) — done; fills the chapter queue.
5. **Stale-audio detection** (fix 3) — done; fills the stale queue.
6. **Editing**, with validation on save. **Not done** — the section above says
   how it has to behave when it is.

---

## Other things worth adding

Ordered by value against effort, roughly.

**Keyboard navigation.** *Done.* `j`/`k` to move, `a` to approve, `r` to reject,
`?` for the reminder. Acting lands you on the next item, so a sweep is worked
through without touching the mouse.

**Batch approve and reject.** Checkboxes and one button. You just reviewed
twelve; most sweeps will be the same shape.

**Show what approving would cost you.** *Done.* Each story's panel lists its
tropes with the weight now and the weight after approving, plus who has been
leading. The weighting is otherwise invisible — it happens inside sampling and
nobody sees it — and showing it at the moment of decision is what turns a
mechanism into a choice.

**Spend per story.** *Partly done* — the action bar shows what a story has cost
so far, from `manifest.json`. What it does not yet show is what finishing it
would cost, which is the more useful half and needs `actor.estimate()`.

**Audio playback with the timeline.** Once a story is recorded, `timeline.json`
has sentence-level timings. Clicking a sentence to hear it is how you catch a
name the voice mangles — the failure that is invisible in text and obvious in
audio.

**A bible editor.** `bibles.validate()` already returns structured errors and
warnings. A form that runs it on save, plus the changelog prompting for a note
when you change something, would close that loop nicely.

**A sweep launcher.** Genre, subgenre, length, count — with the cost shown before
you commit. The same guard rails as `calibration-sweep.sh`, minus remembering the
flags.

**Compare two outlines side by side.** The repetition you found — four contested
wills — was visible only because we tabulated all twelve at once. A two-column
view makes that a normal part of reviewing rather than an investigation.

---

## What not to build

**Authentication, accounts, sharing.** One user, one machine, localhost.

**A database.** The bundles on disk are the state, and everything else in this
pipeline treats them that way. A second source of truth would immediately
disagree with the first.

**A JavaScript framework or a build step.** The page is a list, a document pane
and five buttons. Adding a toolchain means the UI can break in ways the pipeline
cannot, and `story.md` already proves the content renders fine as plain markup.

**A live-updating view of a running stage.** The stages print progress to a
terminal already, and a job runner is a much larger thing than a review queue.
Refreshing after a sweep is enough.

**Video preview.** The mp4 is on disk; the operating system has a video player.

---

## The honest caveat

The three prerequisites are worth doing whether or not the UI gets built —
especially the stale-audio one, which is a live hazard for hand-editing today.
If the UI stalls halfway, those fixes still stand on their own.

And a terminal review is not actually slow for one story. This earns its keep at
ten, which is the shape the calibration sweeps produce and the shape a channel
producing weekly eventually settles into.
