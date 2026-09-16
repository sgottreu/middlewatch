# Narration

`tts.py` holds two providers behind one interface. Both return PCM plus
sentence-level timings, because everything downstream is built on those timings
and a provider that can't produce them is useless here. ElevenLabs is the
default; Polly still works via `tts_provider: polly`.

Three things differ, and two of them are why the default changed.

**One call instead of two.** Polly bills the audio and the speech marks
separately, so timing costs a second full pass over the text. ElevenLabs returns
`alignment` alongside the audio in a single response.

**Continuity across segment joins.** Chapters are synthesized segment by segment
so each speaker gets their own voice, which means every narrator/dialogue switch
is a separate request. Polly has no way to tell a request what preceded it, so
the model restarts its prosody cold at every join and the seam is audible.
ElevenLabs takes `previous_text` and `next_text`, and `_plan()` in `actor.py`
threads each unit's neighbours through. This is the single biggest quality
difference between the two providers on this workload.

**Sentence boundaries have to be derived.** Polly hands back sentence marks;
ElevenLabs hands back per-character timings, which is finer but needs collapsing.
`sentences_from_alignment()` does it, and the tricky part is knowing which full
stop ends a sentence. Titles are the problem — Regency prose is thick with them,
and a naive split turns "Mr. Darcy called" into two subtitle cues with an image
cut landing mid-name. Initials (`J. Fairfax`) and decimals (`3.5 per cent`) are
handled the same way. `No.` is deliberately special-cased: it abbreviates only
when a number follows, because on its own it's a complete line of dialogue and
very common in this genre.

### Casting

Voice names live in genre files; `elevenlabs.voice_library` maps names to IDs.
That indirection means a deprecated voice is a one-line config fix rather than an
edit to every genre file, and genre frontmatter stays readable.

Casting runs at first record and is pinned to the manifest, so a re-record months
later produces the same performance even if the defaults have changed since.
Precedence:

1. **An explicit `voice` on a cast member in `outline.json`.** Always honoured,
   and it doesn't consume a `max_named_voices` slot — casting someone by hand
   outranks how much they happen to say.
2. **Gender-matched from the genre's pool**, for the most talkative characters up
   to `max_named_voices`. Matching on `gender` rather than rank matters: ranking
   alone will read Elinor in a baritone, and that error only surfaces after the
   audio is paid for. An outline with no `gender` field falls back to the female
   pool.
3. **The narrator**, for everyone else. That's how an audiobook does it.

```bash
python3 -m story_pipeline.cli voices     # your account's voices, flagged against voice_library
```

### Cost

ElevenLabs bills credits, one per character on the standard models and half that
on the turbo and flash line. A 2,000-word story is roughly 11,000 characters, so
about 11,000 credits — a meaningful fraction of a Creator month, and several
times what the same story costs on Polly's generative engine. The trade is voice
quality and the continuity handling described earlier on this page.

`record --dry-run` reports characters, credits and dollars without calling
anything and without needing credentials, so it works before the account is even
set up. It reads `usd_per_1k_credits` from config; correct that against your plan
rather than trusting the shipped figure.


---

[← Content boundaries](boundaries.md)  ·  [Index](index.md)  ·  [Architecture →](architecture.md)

---

## Solo narration is the default

One voice reads the whole story, the way an audiobook is read. `casting.mode`
takes `solo` (default) or `cast`; `casting.solo_voice` names the reader and
falls back to the narrator.

The reason is arithmetic rather than taste. Every change of speaker is a
separate generation with its own beginning and end, and a 400ms silence was
stapled into each join:

| | Generations for The Cracked Beam |
|---|---|
| A voice per character, one call per segment | **182** |
| Merging consecutive same-speaker runs | 153 |
| One voice, chunked at the provider's limit | **16** |

A cast cannot escape this. Dialogue alternates every few lines, so merging
same-speaker runs saves 29 joins out of 181 and the switching noise stays. Solo
joins the whole chapter into runs and cuts only where the character limit
forces it — the seam stops being something you hear several times a minute.

Two consequences worth knowing:

- **The prose carries attribution now.** With a cast, `said Mrs Pike` was
  redundant — you could hear who it was — and `writer.md` forbade it. With one
  reader an unattributed line in a scene of three is lost the moment it is
  heard. The writer is told to attribute, and the editor checks it (both ways:
  a tag on every line is padding).
- **Solo casts one voice.** `cast_voices` asks the account for the reader and
  nothing else, so a story does not refuse to record because six voices you
  were never going to hear are missing from your library.

`casting.mode: cast` keeps the old behaviour, seams and all, for a story that
wants it.

### Putting attribution into stories already written

Everything written before this was written under the old rule, which forbade
`said Mrs Pike` because seven voices made it redundant. One voice makes it
essential, so those chapters need a pass:

```bash
python3 -m story_pipeline.cli attribute stories/the-cracked-beam --dry-run
python3 -m story_pipeline.cli attribute stories/the-cracked-beam
```

It runs the ordinary write/edit loop per chapter, seeded with one standing note
(`text.ATTRIBUTION_NOTE` — one string in one place, because a note that drifts
between chapters produces a story that drifts with it). Roughly $0.09 a chapter,
$0.27 if the editor sends it back twice.

Three things it is careful about:

- **Chapters with no dialogue are skipped**, not paid for.
- **The note asks for no net growth.** These chapters already run 8–20% over
  their ceiling, and attribution adds words. Without that instruction the length
  check sends every chapter straight back to be cut, spending the revision
  budget on arithmetic rather than prose.
- **Approvals and narration lapse**, which is correct — the words changed — so
  the chapters return to the review queue and `record` will redo them rather
  than keep audio of a story that no longer matches.

## Audio tags

A segment may carry an optional `tag` — `"[quietly]"`, `"[a beat]"` — which is
performance direction for an Eleven v3 voice. It is sent with the line and
**stripped back out of the alignment**, so it never reaches the page, the
subtitles, or the cues the video is built from. A tag written into the prose is
lifted into the field by `_validate_chapter` for the same reason.

Tags are worth using sparingly: a line every few pages that would otherwise be
misread. Tagging everything flattens the reading into a list of instructions,
and the model gets less stable the more direction it is holding. They cannot
make one voice into another person — there is no pitch or timbre dial, and
`[deep voice]` on a female reader is not a man, it is a woman doing an
impression.

Tags change the audio, so they are in `chapter_hash` — but only when present, so
nothing already approved or narrated lapsed the day this shipped.

## What each model can and cannot do

| | `eleven_multilingual_v2` | `eleven_v3` |
|---|---|---|
| Character alignment | yes | yes |
| Audio tags | billed and read aloud — stripped before sending | performed |
| `previous_text` / `next_text` | accepted | **400, refused outright** |

The last row is a real trade, not a detail. Those fields are what carries pace
and pitch across a join, so on v3 every generation starts cold. Solo narration
is what makes that affordable — 16 long generations per story instead of 182
short ones, so there are far fewer joins to smooth. On a cast, v3 would sound
worse than v2 for exactly this reason.

Both capabilities are lists in `tts.py` (`TAG_MODELS`, `NO_CONTEXT_MODELS`)
rather than one "is it v3" test, because they point opposite ways and a single
flag would eventually be read as meaning the other one.

## Check the model before you trust it

```bash
python3 -m story_pipeline.cli tts-check          # a few hundred credits
```

Synthesizes one tagged line **with the continuity fields attached, exactly as
`record` sends them**, and prints the sentence spans it got back. The first v3
run died on those fields rather than on anything the check had looked at: a
probe that exercises less than the real thing passes, and then lets the real
thing fail. Two
things it settles that documentation will not: whether the configured model
returns character alignment at all — the timeline, the subtitles and the video
sync are all built from it — and whether an audio tag is performed or read out
loud. Both cost cents here and a whole story to discover later. Run it after any
change to `elevenlabs.model`.

## Design resumes, and what it costs when it cannot

`design` skips any image already on disk, so a stopped run picks up where it
left off. Two things make that honest rather than approximate:

- **The shot plan is written to `scenes.json` before a single image of that
  chapter is paid for.** It used to be saved only when the chapter finished, so
  a run that stopped part-way left images with no plan to describe them — and
  planning is a model call, so the resumed run bought a new one. A new plan can
  choose different moments, and the images keep their numbers, so they would be
  quietly adopted for moments they were never drawn for.
- **A plan is kept unless the narration changed.** It carries the chapter's
  timeline hash: re-record a chapter and it is planned again, because the
  sentences it anchored to are gone. `--replan` forces it for every chapter.

If images are ever found with no plan — a run stopped before this existed —
`design` names the files, explains that a fresh plan may not match them, and
gives the price of redrawing. It does not decide for you.

## The voice ids are yours, not ours

`elevenlabs.voice_library` ships the public library ids. They only work for an
account that has added those voices, so the first run on a new key fails with
`404 voice_not_found` — and it fails *after* chapter one's narrator segments
have been synthesized and billed, because the 404 arrives on whichever voice
happens to be cast second.

`cast_voices` now asks the account what it actually has (one free `GET /v1/voices`)
and refuses before anything is synthesized, naming every voice that is missing.
Fix it once, per machine:

```bash
python3 -m story_pipeline.cli voices     # your real ids, flagged against the library
```

and put those ids in `elevenlabs.voice_library`. The manifest pins voice
*names*, not ids, so correcting the library is enough — no re-casting needed.

## Casting never crosses gender

When the pool for a gender runs out — four women speaking against three female
voices — the leftover part is read by **the narrator**, not by whatever voice is
free. Taking any free voice is how Mrs Hale ended up cast as Brian on the first
story to hit it: silent, invisible in the manifest unless you read it, and only
audible after the chapter was paid for. The narrator covering a minor part is
what an audiobook does anyway, and the casting line in `record` marks it:

```
casting:  narrator=Charlotte, ..., Mrs Hale=Charlotte (narrator covers)
```

To give her a voice of her own instead, either pin one on her cast entry in
`outline.json` (`"voice": "Rachel"` — pinned voices never count against
`max_named_voices`) or widen the genre's pool.

## Scene breaks

`casting.break_ms` (2,000 by default) is the silence a `break` segment becomes,
against `gap_ms` (400) between ordinary segments. The break is never
synthesized and never billed, it carries no prosody context across itself, and
its length lands in `timeline.json` like any other time, so the subtitles and
the video stay in step.

Two seconds is a first guess made without listening to one. It has to be long
enough not to be heard as the pause between paragraphs and short enough not to
be heard as the end of the chapter, and the honest way to settle it is to
record a chapter that has one. A struck ship's bell inside the silence is the
obvious next move — it is the channel's own image, `ambience_pipeline.synth`
can render one for nothing, and it removes the ambiguity that silence alone
always has. Left undone deliberately until a real story has been heard with
plain silence in it.

## Chapter announcements

The heading a listener hears — *"Chapter 4. The Bramdean Road."* — is generated
at record time from a template, not written by the model:

```yaml
casting:
  chapter_announcement: "Chapter {n}. {title}."   # null for none
```

It was left to the writer at first, and the result was inconsistent in a way
only the audio would have revealed. Of two stories written the same week, one
announced seven of its eight chapters and the other announced none — so the
first would have opened cold and then started announcing itself at chapter 2.
Nothing in the text, the review, or the linter could see it.

A template is consistent by construction, applies to chapters already written,
and can be changed later without touching a word of prose.

**It is not stored in `chapters/NN.json`.** The announcement is a production
choice, not story content: keeping it out means the chapter text stays the
chapter text, turning the template off does not require rewriting every chapter
to remove a line, and the word counts the length check enforces stay counts of
actual prose.

Two consequences worth knowing:

- The timeline records the announcement it used, so `narration_stale` catches a
  changed template as well as changed prose. A timeline written before
  announcements existed carries no such key and is left alone.
- `writer.md` now forbids writing the title as a segment. For chapters written
  before that, `cli render <story> --strip-titles` removes them:

```
Removed 7 announced title(s):
  chapter 2: 'Chapter 2. Old Grievances.'
  ...
```

The detector is deliberately narrow — narration only, at the very start, under
twelve words, and reducing exactly to the chapter's own title once "Chapter N"
and punctuation are stripped. *"Old grievances have a way of keeping"* is left
alone.
