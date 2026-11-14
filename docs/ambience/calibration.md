# Calibrating the recipes

*Written 2026-08-28. Status: in progress — fire and wind are not signed off.*

The synth's defaults are conventions chosen to get started, not measurements.
This is where they get judged and where the verdicts live, the same role
[docs/story/calibration.md](../story/calibration.md) plays for the length dial.

Two kinds of thing are settled here, and they do not settle the same way:

- **What a recipe sounds like** is decided by ear. No metric substitutes, and
  every attempt to find one has either failed or measured the wrong thing.
- **Whether a recipe is broken** is decided by measurement, because the failures
  that matter over an eight-hour render are invisible to listening: a seam at a
  block boundary, a seed that does not reproduce, a parameter wired to nothing.

## Current dial

| Recipe | Parameters | `gain_db` (LUFS) | `TRIM_DB` |
|---|---|---|---|
| `rain` | `intensity=0.45`, `surface=glass` | −14.0 | +7.4 |
| `wind` | `strength=0.5`, `gust_s=20.0` | −18.0 | +10.3 |
| `fire` | `crackle=1.0`, `roar=0.6` | −20.0 | −5.0 |
| `ocean` | `swell_s=11.0`, `brightness=0.5` | −16.0 | −1.5 |
| `room_tone` | `size=0.5` | −30.0 | −3.8 |

`rain.surface` takes `glass`, `canvas`, `leaves`, `roof`.

`TRIM_DB` is measured, not chosen — `python3 scripts/measure_trims.py --write`.
Do not hand-edit it.

## By-ear verdicts

Recorded as given, with what changed in response. Detail on each change is in
the recipe docstrings, which carry the measurements beside the code they explain.

### fire — **not signed off**

| Feedback | Diagnosis | Change |
|---|---|---|
| "a low end sound that shouldn't be there" | Brown noise into a 420 Hz lowpass: 78% of the layer's power below 120 Hz | Bandpass 100–500 Hz |
| "still that low hum, constant" | Reducing the *amount* of low energy but not its *concentration*. Still peaked at 115 Hz, spectral flatness 0.0000 — a narrow pile of energy is a tone however quiet | Pink noise, 250–3000 Hz. Flatness 0.0076, 190× flatter, 1.2% below 160 Hz |
| "sounds like crinkling paper" | Every tick shared one filter, on an even Poisson rate: identical timbre, evenly spaced, eleven a second | Palette of six bands; events are *flurries* of 1–6 ticks over 25–380 ms. Clustering went from Fano ≈1 to 2.3–3.6 |
| "still crinkling paper" | The ticks themselves. 3–15 ms bursts at 700 Hz–8 kHz is a precise description of crinkling paper | Ticks 12–70 ms, bands down to 250 Hz–6.5 kHz, amplitudes on a steeper curve, flurry rate 7/s → 3.2/s |
| "an occasional drum beat" | A damped 120–260 Hz sine I had added under each snap. A decaying tone is a drum however it was arrived at | Narrow noise band 150–400 Hz: same weight, no pitch |

**Open:** whether the pops now read as fire. `fire-crackle-only` (`roar=0.05`)
isolates the transients from the bed for judging them separately.

**If it still does not convince, stop tuning it.** Fire is the one recipe on the
wrong side of the routing rule — it is mostly events rather than filtered noise,
which is why it has needed four rounds where nothing else has needed two. One or
two more passes is reasonable; a fifth is a signal to reclassify it as a
generated layer with a procedural bed under it for the low roar. See the routing
rule in [pipeline-design.md](pipeline-design.md).

### wind — **not signed off**

| Feedback | Diagnosis | Change |
|---|---|---|
| "usually wind is not constant, there is an ebb and flow" | One modulator, and smooth noise rarely visits its own extremes: about 4 dB of swing | Weather envelope 6.5× slower multiplying the gust, both stretched to reach their rails |
| "the wind is constant… no ebb or flow to it" | Gusts were a wandering *level*, not events. `SmoothNoise` interpolates across its whole period, so at `gust_s=14` the level took 14 s to rise — too gradual to read as an arrival. My "16 dB" was a 5th–95th percentile over five minutes; inside a ten-second window it swung under 1 dB | `EnvelopeEvents`: scheduled gusts with attack and decay. 10 s swing now 5–7 dB |
| "intensity changes too quickly" | Overshot. 0.6–2.6 s attack, 3.5–14 s decay, floor 0.075 (a 22 dB drop between gusts) | Attack 1.5–5.0 s, decay 6–20 s, floor 0.10, `gust_s` default 14 → 20 |
| "sounds like the wind is changing directions" | Independent gust envelopes per channel — a gust arriving left and not right swings the image | One shared envelope; only the resonator bank differs per side. L/R correlation 0.00 → 0.97 |
| "the pitch changes too quickly" (range was fine) | Narrowing the range shortened the journey without slowing it; the modulator still tracked the gust sample for sample | `OnePole(14.0)` lag between them. Peak rate 126 → 13.5 Hz/s |
| "the bed starts on left only and wind on right, then they mix 20 s in and the volume goes up" | **Two structural bugs.** The right bank was built with `reversed(range(5))`, so at any modulator position the sides sat at opposite ends of the frequency bank, mirrored. And `EnvelopeEvents` scheduled its first gust a full mean interval ahead, so the layer started at its floor and jumped | Both banks in the same order (L−R centroid difference now 1 Hz); `warmup_s` on the primitive |

**Open:** the last iteration is unheard. `strength` semantics are also a
judgement call — light wind is currently intermittent and strong wind sustained,
on the reasoning that a gale roars continuously while a breeze comes and goes. If
it should mean "more dramatic" instead, that is one line.

### rain, ocean, room_tone — **unheard**

No feedback yet. `rain` and `ocean` are the two most likely to land first, being
the closest to pure filtered noise, which is what procedural synthesis is good
at.

## Measured baselines

Re-measure after changing a recipe. All figures from seed 1–5 unless noted.

| Recipe | <320 Hz share | Allowed (`LOW_SHARE`) | Spectral flatness | L/R corr |
|---|---|---|---|---|
| `rain` | 0.0% | 0–5% | 0.65 | +0.03 |
| `fire` | 12.4% | 2–30% | 0.045 | +0.01 |
| `ocean` | 56–72% | 35–85% | 0.006–0.44 | +0.01 |
| `wind` | 28% | 10–80% | 0.000 | +0.01 |
| `room_tone` | 99.9% | 90–100% | 0.000 | +0.01 |

Wind, per `strength`:

| `strength` | lull→gust (5 min) | 10 s swing | gusts/min |
|---|---|---|---|
| 0.15 | 20 dB | 6.9 dB | 2.2 |
| 0.5 | 17 dB | 5.1 dB | 2.0 |
| 0.9 | 15 dB | 4.7 dB | 2.0 |

Pitch drift: 102 Hz travel, 3.0 Hz/s median, 13.5 Hz/s peak.

Fire, per `crackle` (with `roar=0.05` so the bed does not mask):

| `crackle` | onsets/min | Fano |
|---|---|---|
| 0.2 | 241 | 3.6 |
| 1.0 | 671 | 2.4 |
| 2.5 | 1051 | 2.3 |

Fano is the variance-to-mean ratio of counts per second: 1.0 is an even Poisson
stream, above 1 is clustered. It is the number that distinguishes a hearth from
a Geiger counter.

## Measuring the right thing

Four metrics in this session reported a working control dead or a broken one
healthy. Each failure is worth keeping, because the same mistake is available
every time a parameter is added.

**A percentile threshold cannot measure density.** Counting fire transients
above the 97th percentile reported the same ~80 per minute whether crackle was
at 0.2 or 2.5 — the threshold rises with the thing it is measuring. A median
plus MADs fails the same way. Density needs an **absolute** threshold, and the
bed turned down so it is not masking.

**A spectral centroid cannot see a change in rate.** `gust_s` moves how often a
gust arrives; the distribution and the spectrum are identical either way. An
envelope-spectrum centroid cannot see it either, because broadband noise
fluctuation swamps a 0.07 Hz gust. Counting envelope peaks separates it cleanly
— 8.0 / 4.0 / 1.5 per minute at `gust_s` 4 / 14 / 40.

**A window shorter than the modulation period measures nothing.** A six-second
window judged an eleven-second ocean swell on a fragment of one wave and called
`brightness` dead. A thirty-second window on the ocean spans less than one cycle
of its 37-second set drift and reads 12 dB apart across seeds, which says nothing
about whether the trim is right. `WINDOW_S` is 60 s; the level check averages
three seeds the same way `measure_trims.py` does.

**A measurement over the wrong span answers the wrong question.** The wind's
"16 dB lull-to-gust" was true over five minutes and irrelevant: the listening was
over two minutes, and the judgement is made over about ten seconds, where it swung
under 1 dB. Measure over the span the listener uses.

And one that is worth stating because it has no fix: a half-second spectral
centroid is dominated by the noise in the signal rather than the resonance
position, so it reported the pitch sweep getting *faster* after the lag was
added. Measure the control signal when you can; use windows long enough to be
stable when you cannot.

## No universal test for "hums when it shouldn't"

Worth recording, because it looks like it should exist. The thing that made the
fire hum is exactly what makes the room tone correct — both measure 0.0000
spectral flatness and both put nearly all their energy below 320 Hz. One is a
defect and one is the entire point.

So the guard is `LOW_SHARE`, a per-recipe bound that encodes intent rather than a
general law. Fire is allowed 2–30% below 320 Hz; it measured 96% and 84% in the
two humming versions.

The narrow-tone check is a real test and catches a different failure: a decaying
sine stands clear of its own spectral neighbourhood where broadband noise does
not. Its window matters — at 357 Hz wide it rated the drum identically to wind's
resonators, which are legitimately narrow; at 44 Hz the two separate, with every
recipe under 2.4 dB and the drum at 5.5. A quiet enough tone still hides (the
drum at half level reads 2.9), so it catches gross ones, not all of them.

## Still assumed

Starting points that no listening has yet confirmed:

- Grain size 0.5–2 s and ±2% pitch jitter as where granular resynthesis stops
  sounding granular. **Untested — nothing uses it yet.**
- 40 seconds as a video loop length long enough not to read as a loop.
- −20 to −23 LUFS as right for sleep content specifically.
- 4–6 minutes per image before the crossfade.
- Accent density scaling across the 1 h / 3 h / 8 h tiers.
- Six tracks as where a music cycle's period stops being noticed, and 20 s as
  the crossfade between them. See [music.md](music.md).

---

[← The synth](synth.md)  ·  [Pipeline design](pipeline-design.md)  ·  [Index](index.md)
