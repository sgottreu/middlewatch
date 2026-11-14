# Music in an ambience video

*Written 2026-08-28.*

Design for the music layer. Not built. This is the plan to build from.

Scope: **one-off ambience, not series work.** A drone under a soundscape stays
procedural for the reasons in [the pipeline design](pipeline-design.md) — it is
free, non-repeating, and cannot be claimed. This document is about the other
case, where the music is the subject rather than the furniture and a generative
model is the only way to get it.

The distinction is the whole decision. Music as *furniture* — a quiet pad under
rain and fire — is the `tonal` role, and synthesis handles it: using a
generative model there means paying for sophistication you then bury. Music as
the *subject* — a piano piece with rain behind it, which is a large and
well-performing category — is this document. The licensing question only bites
on the second.

Nothing here is an argument against having music. Overlaying other sounds does
not change the licensing position either: Content ID matches fingerprints
regardless of what sits on top, and burying a track under rain to avoid
detection is not a strategy to build on.

## One positioning question first

Not a technical one, and not mine to settle. A meaningful share of the sleep and
focus audience actively filters *for* no music, and music narrows the
eight-hour use case that carries the watch time. That may argue for music-forward
videos being their own series rather than mixed into the same playlist as the
silent soundscapes — a decision about the channel, taken once, rather than per
video.

## The problem

A generated track is a *piece*. It starts from nothing, goes somewhere, and
resolves. An ambience video needs one to fifteen hours of audio that never
arrives anywhere. Playing a ten-minute track eighteen times has two separate
failures, and they are worth keeping apart:

- **The seam.** The resolution at the end butted against the onset at the
  start. Audible once per cycle, and unmistakable.
- **The period.** Even with a perfect seam, ten minutes is short enough that a
  listener recognises the material coming round again. This is the failure that
  ends the whole niche, and it is not fixed by fixing the seam.

Note that this needs a *different* treatment from the SFX looping already
specified in the pipeline design. Granular resynthesis works there precisely
because it destroys structure. Music is structure, so the techniques here are
crossfade and loop-point search instead.

---

## Four techniques

In order of how much they matter, which is not the order they are applied in.

### 1. Prompt for material that can loop

A track with an intro, a build and a resolution cannot be looped invisibly by any
technique downstream. The seam is structural. Everything below is cheap when the
material is right and impossible when it is not, so this is most of the battle
and it happens before a single sample is generated.

Ask for: constant density, no intro, no outro, no build, a single harmonic
centre, no percussion, and a defined instrument palette. Ask against: crescendo,
drop, section change, key change, ritardando, and anything described as
"journey" or "evolving".

The prompt is a pinned decision like any other and belongs in `concept.json`.

### 2. Fold the ends into each other

Not a butt-join with a fade across it — that dips in the middle and is audible as
a dropout once a cycle. The tail is mixed *onto* the head instead, so the loop's
end already contains its own beginning.

Given a track `x` of length `T` and a crossfade of `N`:

```
body   = x[0 : T-N]
tail   = x[T-N : T]
head   = body[0 : N] * fade_in  +  tail * fade_out
loop   = concat(head, body[N:])          # length T - N
```

Playing `loop` repeatedly, the sample after `x[T-N-1]` is a blend weighted almost
entirely toward `x[T-N]`, which is what naturally followed it. The join is
continuous by construction.

**Equal power, not linear.** `fade_in = sin(πt/2)`, `fade_out = cos(πt/2)`. Two
decorrelated signals summed under linear fades lose 3 dB at the midpoint; under
these they do not.

Two traps:

- Generated tracks usually end in a reverb tail or a fade to silence. Trim it
  before folding, or the fold crossfades into nothing and the loop gets quieter
  once a cycle. Detect by RMS: walk back from the end until level recovers.
- If there is any pulse, `N` must be a whole number of bars or the loop drifts
  against itself. For ambient without a beat this is moot, which is another
  argument for the prompt in (1).

### 3. Find the loop points, don't assume them

Looping `0 → T` is an arbitrary choice. Somewhere inside the track there is
almost certainly a pair of positions whose material matches far better, and
looping between *those* needs a two-second crossfade where the ends need twenty.
It also skips a generated intro for free.

```
feature:   STFT magnitude, ~20 ms hop, grouped into ~24 log-spaced bands,
           per-frame energy-normalised so the match is about colour, not level
candidates: start s in [0, max_skip]; end e with (e - s) >= min_loop_s
cost:      || f(s) - f(e) ||  +  w · || f(s..s+W) - f(e..e+W) ||  +  |rms(s) - rms(e)|
choose:    the (s, e) minimising cost, then fold per (2) with N scaled to the
           residual — a good match needs a short crossfade
```

The windowed term is what stops it picking a pair that agree for one frame and
diverge immediately after: the trajectory has to match, not just the instant.

**Check for cancellation at the join.** If the crossfade region holds correlated
content in opposing phase, the blend nulls rather than sums. Measure the
crossfaded region's RMS against the mean of its two inputs; a significant drop
means picking a different candidate rather than accepting the null.

### 4. Cycle several tracks, don't loop one

The largest perceptual win, and the cheapest. Six ten-minute tracks crossfaded
over twenty seconds and cycled give a sixty-minute period instead of a
ten-minute one, for about $0.48 of generation.

This is the same argument variant-stacking already makes for SFX beds:
repetition is noticed on the **period**, not on the seam. A flawless loop of one
track is worse than a merely good loop of six.

Build it as a macro-loop — chain the tracks with crossfades, then fold the last
into the first using (2) so the wrap is seamless like every other join. The
result is one long loop that the video's `-stream_loop` can repeat.

Generate the tracks from one prompt with different seeds, so they share a palette
and a key. Different prompts give a compilation, not a piece.

---

## Module

`ambience_pipeline/music.py`, with the CLI in `__main__.py` beside the synth's.

```python
find_loop(x, sr, min_loop_s, max_skip_s) -> (start, end, cost)
fold(x, sr, crossfade_s)                 -> np.ndarray     # technique 2
loopify(x, sr, ...)                      -> np.ndarray     # 3 then 2
macro_loop(tracks, sr, crossfade_s)      -> np.ndarray     # technique 4
trim_tail(x, sr)                         -> np.ndarray     # the reverb trap
```

```bash
python3 -m ambience_pipeline.music loopify track.wav --out loop.wav
python3 -m ambience_pipeline.music cycle t1.wav t2.wav t3.wav --out macro.wav
python3 -m ambience_pipeline.music preview loop.wav --cycles 3
```

`preview` is the one that earns its keep: it renders three cycles back to back so
you can listen straight at the seams, which is the only way to judge this.

Develop against a procedurally generated pad — no API key, no cost, and a known
answer. Point it at real Lyria or ElevenLabs output once it works.

## Where it fits

A new layer role in `concept.json`, alongside `bed`, `texture`, `accent` and
`tonal`:

```json
{
  "id": "piano_rain",
  "role": "music",
  "source": "generated",
  "provider": "elevenlabs",
  "prompt": "solo piano, constant density, no intro or outro, single key, no percussion",
  "tracks": 6,
  "seconds_each": 600,
  "crossfade_s": 20,
  "gain_db": -22.0
}
```

`gain_db` means LUFS as everywhere else. Music under a soundscape sits below the
bed; music *as* the subject sits above it, and that is the difference between the
two kinds of video rather than a knob to be discovered by rendering.

Cache generated tracks by prompt-and-seed hash in `assets/music_cache/`, the same
pattern and the same gitignore decision as `assets/sfx_cache/`.

## Cost and ledger

Per the rate card verified 2026-08-29: Lyria 3 is $0.04 per 30-second clip and
$0.08 per full song; ElevenLabs Music bills from the shared credit pool.

Six ten-minute tracks is roughly $0.48 on Lyria. That is small enough that the
cycle in (4) should be the default rather than an upgrade.

`rates.json` has no `lyria` model under the `gemini` provider — add one before
the first billed call, or `price()` will raise, which is the same failure the
image model already had. ElevenLabs Music needs no new provider, only a model key
alongside `sound_effects`.

## Licensing, which gates all of it

- **ElevenLabs Music** grants commercial rights from the Starter tier upward; the
  free tier explicitly does not. Trained on licensed catalogues via Merlin and
  Kobalt.
- **Lyria 3** offers commercial use and indemnification, but platform commercial
  rights are not the same thing as owning the commercial rights YouTube's
  monetisation policy requires, and there is active litigation over its training
  data.

ElevenLabs has the clearer position, and it is the **same subscription decision**
that unblocks the SFX accents. One plan resolves both. Settle it before either
cache fills, because both are designed to be permanent.

## Assumed, not measured

In the spirit of `WPM = 150` — these are starting points, and every one is a
guess until a real track has been through the pipeline:

- 20 ms hop and 24 bands as enough resolution to find a loop point.
- 20 seconds as the right crossfade between tracks in a cycle.
- Six tracks as the point where the period stops being noticed.
- That a well-chosen loop point needs a crossfade of only 2-3 seconds.

## A verification worth building

The seam is checkable without ears, which matters because the ear stops noticing
a seam it has heard fifty times. Render three cycles, compute short-window
spectral flux across the whole thing, and compare the flux at each join against
the distribution elsewhere. A join that sits inside the normal spread is
inaudible; one that stands out is a seam, whatever it sounds like on the fourth
listen. Same principle as the narrow-tone and low-share checks in
`ambience_pipeline/tests/test_synth.py`.

---

[← Pipeline design](pipeline-design.md)  ·  [Root README](../../README.md)
