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
