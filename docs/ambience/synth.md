# The synth module

*Written 2026-08-28.*

`ambience_pipeline/synth/` — procedural audio. The only part of the ambience
pipeline that is built. No credentials, no network, no cost: rain, wind, fire,
surf and room tone are generated rather than sampled, so nothing here loops and
nothing here is licensed by anybody.

```bash
python3 -m ambience_pipeline.synth list
python3 -m ambience_pipeline.synth rain --seconds 60 --out rain.wav
python3 -m ambience_pipeline.synth fire -p crackle=1.8 -p roar=0.4
python3 -m ambience_pipeline.tests.test_synth
```

Needs `numpy` and `scipy`, which are in `requirements.txt`. Install into the
venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Three invariants

Everything in `dsp.py` holds these, and the recipes are only correct because it
does. Breaking one does not produce an error; it produces audio that is subtly
wrong in a way nobody notices until a long render.

**Sample continuity.** Every source is an object with `render(n)` that advances
internal state. Rendering sixty seconds in one call and in sixty one-second
calls produces identical samples, to the bit. That is what makes the streaming
render possible: an eight-hour soundscape is 2,880 calls to the same objects, and
no seam exists because no block boundary is visible to a filter, an oscillator or
an event queue. `test_synth.py` asserts it against six ragged block sizes, none
of them divisors of the total.

**Determinism.** Every stochastic component gets its own generator, spawned from
one seed at construction via `spawn_rngs`. Sharing one generator would break
block invariance: with two sources drawing from a single stream, `A(2000), B(2000)`
interleaves differently from `A(1000), B(1000), A(1000), B(1000)`, and the same
seed would render differently at a different block size.

The consequence worth knowing: `spawn_rngs` hands out streams in a fixed order,
so **adding a source to a recipe shifts every later source's stream**. A recipe
that gains a layer renders differently at the same seed. That is correct, and
worth knowing before going looking for the bug.

**No time-varying IIR filters.** A filter whose coefficients move within a block
is not block invariant, and moving them per block makes the block size audible.
Where a recipe wants a resonance that drifts — wind — it crossfades between a
bank of fixed filters using weights driven by absolute sample index (`Morph`).
Same effect, vectorised, invariant by construction.

## Files

| File | What |
|---|---|
| `dsp.py` | Primitives. Noise colours, filters, modulators, event schedulers, levels. |
| `recipes.py` | The five recipes and the registry the CLI and the mixer read. |
| `render.py` | Drives a layer through a wav a block at a time. |
| `wav.py` | Streaming 16-bit PCM writer. Stdlib `wave`, no `soundfile`. |
| `__main__.py` | The audition CLI. |
| `../tests/test_synth.py` | The checks that do not need ears. |
| `../../scripts/measure_trims.py` | Re-measures `TRIM_DB`. |

## Primitives

Sources — anything with `render(n)`, returning mono float32 unless noted:

| | |
|---|---|
| `Noise(rng, colour)` | White, pink or brown, continuous across calls |
| `Sos(src, sos)` | A fixed filter over another source, state carried |
| `SmoothNoise(rng, period_s)` | Slow random modulation in [0,1], smoothstep between nodes |
| `Sine(freq, phase)` | Phase from absolute sample index, so it cannot drift or click |
| `Morph(bands, mod, focus)` | Blends a bank of fixed filters; `render_with(m, n)` drives it from a modulator the caller already holds |
| `EventLayer(rng, rate, make, pan_jitter)` | Poisson one-shots mixed in stereo, overhang carried across blocks |
| `EnvelopeEvents(rng, rate, make, warmup_s)` | Poisson envelopes summed as a mono control signal |
| `OnePole(tau_s)` | A lag, for when two things follow one gesture at different speeds |

Helpers: `bandpass`, `lowpass`, `highpass`, `resonator`, `burst`, `gust_shape`,
`to_stereo`, `fade`, `soft_limit`, `spawn_rngs`.

Levels: `db_to_gain`, `gain_to_db`, `rms_db`, `peak_db`, `lufs`.

Two of these exist because of specific audible failures and are worth
understanding before reaching for the obvious alternative:

`EnvelopeEvents` is for modulation that has to *arrive*. Modulating with
`SmoothNoise` gives a level that wanders, and a wander is not a gust — at a
fourteen-second setting the loudness takes fourteen seconds to rise and the ear
reads it as constant. `warmup_s` runs the process and discards the output so that
t=0 is the middle of a running stream: without it the first event is a full mean
interval away, which was audible as the volume coming up twenty seconds in.

`OnePole` exists so loudness and resonance can follow one gesture at different
speeds. Driving both from the same envelope swept the wind's pitch at the gust's
attack rate, which is heard as a filter being swept rather than as weather.

## Loudness, not RMS

`gain_db` on a recipe names the layer's approximate **integrated loudness in
LUFS**, and the two are not interchangeable. K-weighting favours the highs, so
bright content measures hotter than its RMS suggests: rain at −15 dBFS RMS is
−8.7 LUFS while fire at −20.7 RMS is −21.3. Level two layers by RMS and the
bright one arrives 6 dB louder than its number claims, which would make every
mix written in a concept file wrong.

`dsp.lufs` is an ungated BS.1770 implementation — ungated because ambience has
no silence to gate out, and the gates exist for programme material with dialogue
and pauses. Checked against ffmpeg's `ebur128` on all five recipes and agrees
within 0.3 LU. The CLI reports ffmpeg's figure because that is the conforming
meter; the trims and tests use `dsp.lufs` to avoid a subprocess per measurement.
If they ever stop agreeing, ffmpeg is right.

`TRIM_DB` is the per-recipe constant that makes `gain_db` mean something. Left
raw, a recipe's level is an accident of how many components it happens to sum.
Re-measure with `python3 scripts/measure_trims.py --write` after changing a
recipe's internal balance.

## Headroom, not limiting

A layer at its mix level does not fit in a file on its own: wind and ocean carry
a 17 dB crest factor, so at −18 LUFS their peaks land at 0 dBFS and above.
Limiting that back is the wrong answer — the peaks *are* the gusts and the
breaking waves, and a limiter flattens them into exactly the constant texture the
recipes were rewritten to avoid. Every wind audition came back with its peak
pinned at −3.5 dBFS and 2 dB of its swing gone.

So `render_to_wav` scales the whole render down by a constant instead
(`headroom_db`, default 6). Dynamics survive untouched and the CLI reports the
layer's own level with the headroom added back. `ceiling_db` is still there for
the mix, where limiting a sum is reasonable.

## Adding a recipe

```python
@recipe("thunder", "Distant storm — rare, low, long.",
        distance=0.6, gain_db=-24.0)
def thunder(seed: int, sr: int, distance: float, gain_db: float) -> Layer:
    r = dsp.spawn_rngs(seed, 3)
    ...
    return Layer(render, gain_db, TRIM_DB["thunder"])
```

Then: add a `TRIM_DB` entry (0.0 is fine, it gets measured), run
`python3 scripts/measure_trims.py --write`, add a `LOW_SHARE` bound and a
parameter case in `test_synth.py`, and audition it.

The parameter case needs the right metric. `test_synth.py` offers `level`,
`tone`, `rate`, `density` and `swell`, and asserting on the wrong one calls a
working control dead — which is how `gust_s` was nearly "fixed" by breaking it.
See [calibration.md](calibration.md).

## Performance

15–170× realtime depending on recipe; wind is slowest at about 15×, since it runs
ten brown-noise generators through two filter banks. An eight-hour wind bed is
therefore roughly half an hour of CPU. Acceptable, and worth knowing before the
mixer stacks five layers and multiplies it.

The test suite takes about a minute. It asks for roughly forty-five minutes of
audio, which is why `WINDOW_S` is 60 s rather than the 90 it wants to be.

---

[← Pipeline design](pipeline-design.md)  ·  [Calibration →](calibration.md)  ·  [Index](index.md)
