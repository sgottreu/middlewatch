# Architecture

How the stages hand off, and the three decisions everything else rests on.

## Three things worth understanding

**The timeline is the load-bearing artifact.** ElevenLabs returns character-level
alignment in the same response as the audio, and `tts.py` collapses that into
sentence spans. Those offsets drive both the subtitle file and the image cut
points. The designer picks a sentence; the director cuts there. Nothing
downstream ever guesses at timing, and the video length matches the narration to
the frame.

**The writer emits segments, not prose.** Each segment carries its speaker, so
the actor never has to parse dialogue attribution out of finished paragraphs with
a regex. `'I could not,' said Elizabeth, 'think of it.'` is three segments —
dialogue, narrator, dialogue — and the voice switches twice. The readable
markdown is rendered from the segments, not the other way round.

**The cast sheet comes before the scenes.** The designer generates one reference
portrait per character first, then passes those portraits back in as character
references on every scene call. Nano Banana 2 holds up to four character
references and keeps faces consistent across them. Without that first pass your
protagonist is a different woman by chapter three, which is the most visible way
this kind of video looks cheap.

# The bundle

```
stories/<slug>/
  manifest.json           stage state, spend, pinned decisions
  outline.json            ideator — genre, subgenre, bible, tropes, notes, revisions
  story.md                human-readable summary; planned vs measured length
  drafts/NN.<n>.json      every pass of the write/edit loop, with its review
  chapters/01.json        writer — speaker-tagged segments
  chapters/01.md          the same chapter, readable
  reviews/01.json         editor verdict + lint findings
  audio/01.mp3
  audio/01.timeline.json  sentence-level timings from Polly speech marks
  images/cast/*.png       reference portrait per character
  images/01-00.png        scene stills
  images/scenes.json      shot list with the timestamp each image cuts on
  video/subtitles.srt
  video/final.mp4
```

Nothing passes between agents through context. Every handoff is a file, which is
why a crash in the director doesn't cost you a rewrite.

`manifest.json` also pins the decisions that would otherwise drift: the art style
string, the voice casting, the length setting and the images-per-chapter count.
Change the defaults in `config.yaml` and existing stories keep rendering the way
they were first rendered. That's deliberate — changing a default should not
silently rewrite work you already paid for.

Two stages stop and wait for a human, at the two points where continuing costs
real money: `ideate` after `new`, and `edit` after `write`. Both sit at
`awaiting_review` until approved, both take `--force`, and `cli all` skips both.
See [Running the pipeline](running.md).

The audio is the one artifact that can silently disagree with its source, because
`record` skips a chapter that already has an mp3. Each timeline stores a hash of
the words it narrated, so an edited chapter is re-recorded rather than left
carrying narration of prose that is no longer in the story.

`story.md` is the exception to all of this: it is the one file in a bundle that
is a *view* rather than a source. Everything in it is derived from the outline,
the manifest, the chapters and the timelines, so deleting it loses nothing and
regenerating it can never disagree with the bundle. It is rewritten after
ideate, write and record — which is when planned length can finally be set
beside measured length, the comparison the length dial is judged on.


---

[← Narration](narration.md)  ·  [Index](index.md)  ·  [Cost →](cost.md)

---

## Length is counted, not judged

The first 30-minute story came out at 5,789 words against a 4,500 target — 39
minutes of narration for a 30-minute dial, 29% over. Every chapter overran, the
mildest by 16% and the worst by 64%.

The editor caught all of it. Its `length_note` on chapter 6 read *"about 68%
over — well past the one-fifth tolerance"*, and it passed the chapter anyway,
because rule 10 told it not to fail on length alone and it read the excess as
incident rather than padding. The system diagnosed the problem accurately eight
times and acted on it zero times.

So length moved out of the verdict and into code. `length_fix()` counts the
words, and a chapter over `length_tolerance` is failed and sent back with an
exact instruction:

```
LENGTH: this chapter is 918 words against a target of 560 — 64% over.
Cut it to at most 616 words. Lose no beat and no incident: take the cut
out of restatement, out of dialogue that makes its point twice...
```

Three things make this work where the prompt did not:

- **It is exact.** The editor estimated 719 words where the count is 721. A
  measurement does not need to be argued with.
- **It cannot be reasoned away.** "The overage is incident rather than padding"
  is a true and irrelevant observation once the runtime is the constraint.
- **It spends a revision, not a judgement.** The trim goes to the front of the
  fix list, because it is the only instruction that makes the chapter shorter
  and the rest mostly make it longer.

The writer is given the same band in the same numbers, from the same config
value, so the instruction and the rule cannot drift apart. The editor keeps the
half only it can do: naming *which* passages restate something, and deciding
whether a short chapter skipped a beat.

On the last revision an overrun is recorded rather than enforced — a good long
chapter beats no chapter — and `length_over` in the review is what `cli status`
reads.
