"""Primitives every recipe is written in.

Three properties hold for everything in this file, and the recipes are only
correct because they do.

**Sample continuity.** Every source is an object with `render(n)` that advances
internal state. Rendering 60 seconds in one call and rendering it in 60 one-second
calls produce identical samples, to the bit. That is what makes the streaming
render in `pipeline-design.md` possible at all: an eight-hour soundscape is 2,880
calls to the same objects, and no seam exists because no block boundary is ever
visible to a filter, an oscillator or an event queue.

**Determinism.** Every stochastic component gets its own generator, spawned from
one seed at construction. Sharing one generator would break block invariance:
with two sources drawing from one stream, `A(2000), B(2000)` interleaves
differently from `A(1000), B(1000), A(1000), B(1000)`, and the same seed would
render differently at a different block size. Independent streams cost nothing
and remove the whole class of bug.

**No time-varying IIRs.** A filter whose coefficients move within a block is not
block invariant, and moving them per block makes the block size audible. Where a
recipe wants a resonance that drifts — wind, mainly — it crossfades between a
bank of fixed filters using weights driven by absolute sample index. Same effect,
vectorised, and invariant by construction.
"""

from __future__ import annotations

import numpy as np
from scipy import signal

SR = 48_000

# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #


def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def gain_to_db(g: float) -> float:
    return float(20.0 * np.log10(max(abs(g), 1e-12)))


def rms_db(x: np.ndarray) -> float:
    return gain_to_db(float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))))


def peak_db(x: np.ndarray) -> float:
    return gain_to_db(float(np.max(np.abs(x))) if x.size else 0.0)


# ITU-R BS.1770 K-weighting at 48 kHz: a high shelf standing in for the head,
# then a high-pass. Loudness is what the mix target is written in, and it is not
# RMS — K-weighting favours the highs, so bright content measures hotter than its
# RMS suggests. Rain at -15 dBFS RMS is -8.7 LUFS; fire at -20.7 is -21.3. Level
# two layers by RMS and the bright one arrives 6 dB louder than intended.
_K_SHELF_B = np.array([1.53512485958697, -2.69169618940638, 1.19839281085285])
_K_SHELF_A = np.array([1.0, -1.69065929318241, 0.73248077421585])
_K_HPF_B = np.array([1.0, -2.0, 1.0])
_K_HPF_A = np.array([1.0, -1.99004745483398, 0.99007225036621])


def lufs(x: np.ndarray, sr: int = SR) -> float:
    """Integrated loudness, ungated, in LUFS.

    Ungated because ambience has no silence to gate out — the absolute and
    relative gates in BS.1770 exist for programme material with dialogue and
    pauses, and on a constant soundscape they remove nothing. Checked against
    ffmpeg's `ebur128` on all five recipes and agrees within a few tenths, which
    is well inside the tolerance anything here is tuned to.

    Implemented rather than shelled out so the tests and the trim script do not
    need a subprocess per measurement. The CLI still prints ffmpeg's figure,
    because that is the one a mastering question would be settled against.
    """
    x = np.atleast_2d(np.asarray(x, dtype=np.float64))
    if x.shape[0] > x.shape[1]:
        x = x.T
    total = 0.0
    for ch in x:
        y = signal.lfilter(_K_SHELF_B, _K_SHELF_A, ch)
        y = signal.lfilter(_K_HPF_B, _K_HPF_A, y)
        total += float(np.mean(np.square(y)))          # G = 1.0 for L and R
    return -0.691 + 10.0 * np.log10(max(total, 1e-24))


def spawn_rngs(seed: int, n: int) -> list[np.random.Generator]:
    """Independent generators from one seed, requested in a fixed order.

    Fixed order is load-bearing: it is what makes a recipe reproducible from its
    seed alone. Adding a source to a recipe shifts every later source's stream,
    so a recipe that gains a layer renders differently at the same seed — which
    is correct, and worth knowing before you go looking for the bug.
    """
    return [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(n)]


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #


class Source:
    """Anything that can be asked for the next `n` frames.

    `render` returns mono float32 of shape (n,) unless a subclass says otherwise.
    Subclasses hold whatever state continuity requires and must never consult a
    block size to decide what to produce.
    """

    def render(self, n: int) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


# Gains that bring each colour back to unit RMS for unit-variance white input.
# Measured over 20s of each rather than derived, because the pink filter is an
# approximation and the brown one is a leaky integrator whose gain depends on the
# leak. Re-measure if either coefficient set changes: recipes are levelled in dB
# against these, so a wrong constant here moves every recipe at once.
_COLOUR_NORM = {"white": 1.0015, "pink": 11.5455, "brown": 0.1397}

# A standard 3-pole/3-zero approximation to -3 dB/octave across the audio band.
_PINK_B = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
_PINK_A = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])

# Leaky integrator: -6 dB/octave with the DC runaway of a true integrator
# removed. The leak is what stops an eight-hour render drifting off centre.
_BROWN_B = np.array([1.0])
_BROWN_A = np.array([1.0, -0.99])


class Noise(Source):
    """White, pink or brown noise, continuous across calls."""

    def __init__(self, rng: np.random.Generator, colour: str = "white"):
        if colour not in _COLOUR_NORM:
            raise ValueError(f"unknown noise colour {colour!r}; "
                             f"have {sorted(_COLOUR_NORM)}")
        self.rng = rng
        self.colour = colour
        self._b, self._a = {
            "white": (None, None),
            "pink": (_PINK_B, _PINK_A),
            "brown": (_BROWN_B, _BROWN_A),
        }[colour]
        self._zi = (None if self._b is None
                    else np.zeros(max(len(self._b), len(self._a)) - 1))

    def render(self, n: int) -> np.ndarray:
        x = self.rng.standard_normal(n)
        if self._b is None:
            out = x
        else:
            out, self._zi = signal.lfilter(self._b, self._a, x, zi=self._zi)
        return (out * _COLOUR_NORM[self.colour]).astype(np.float32)


class Sos(Source):
    """A fixed second-order-section filter over another source.

    Coefficients never change, so state carries cleanly and the block size is
    invisible. Anything that needs to move belongs in `Morph`.
    """

    def __init__(self, src: Source, sos: np.ndarray):
        self.src = src
        self.sos = sos
        self._zi = np.zeros((sos.shape[0], 2))

    def render(self, n: int) -> np.ndarray:
        y, self._zi = signal.sosfilt(self.sos, self.src.render(n), zi=self._zi)
        return y.astype(np.float32)


def bandpass(low: float, high: float, order: int = 4, sr: int = SR) -> np.ndarray:
    ny = sr / 2
    lo, hi = max(low / ny, 1e-5), min(high / ny, 0.999)
    return signal.butter(order, [lo, hi], btype="band", output="sos")


def lowpass(cut: float, order: int = 4, sr: int = SR) -> np.ndarray:
    return signal.butter(order, min(cut / (sr / 2), 0.999), btype="low", output="sos")


def highpass(cut: float, order: int = 2, sr: int = SR) -> np.ndarray:
    return signal.butter(order, max(cut / (sr / 2), 1e-5), btype="high", output="sos")


def resonator(centre: float, q: float, sr: int = SR) -> np.ndarray:
    bw = max(centre / q, 1.0)
    return bandpass(max(centre - bw / 2, 10.0), centre + bw / 2, order=2, sr=sr)


class SmoothNoise(Source):
    """Slow random modulation in [0, 1], smooth and continuous across calls.

    Random values on a fixed grid, smoothstep-interpolated. Indexed by absolute
    sample position rather than by anything block-relative, so a value at sample
    t is the same value whatever call happened to produce it.
    """

    def __init__(self, rng: np.random.Generator, period_s: float, sr: int = SR):
        self.rng = rng
        self.hop = max(int(period_s * sr), 1)
        self._t = 0
        self._vals: list[float] = [float(rng.random()), float(rng.random())]

    def _ensure(self, upto_node: int) -> None:
        while len(self._vals) <= upto_node + 1:
            self._vals.append(float(self.rng.random()))

    def render(self, n: int) -> np.ndarray:
        t = np.arange(self._t, self._t + n, dtype=np.float64)
        self._t += n
        node = (t / self.hop).astype(np.int64)
        self._ensure(int(node[-1]) if n else 0)
        frac = t / self.hop - node
        s = frac * frac * (3.0 - 2.0 * frac)          # smoothstep
        v = np.asarray(self._vals, dtype=np.float64)
        return ((v[node] * (1.0 - s)) + (v[node + 1] * s)).astype(np.float32)


class Sine(Source):
    """Phase derived from absolute sample index, so it cannot drift or click."""

    def __init__(self, freq: float, phase: float = 0.0, sr: int = SR):
        self.freq, self.phase, self.sr = freq, phase, sr
        self._t = 0

    def render(self, n: int) -> np.ndarray:
        t = np.arange(self._t, self._t + n, dtype=np.float64)
        self._t += n
        return np.sin(2 * np.pi * self.freq * t / self.sr + self.phase).astype(np.float32)


class OnePole:
    """A lag. Smooths a control signal without changing what it is.

    For when two things should follow one gesture at different speeds. Wind's
    loudness tracks each gust; its resonance tracks the average flow, which
    moves far more slowly — driving both from the same envelope made the pitch
    sweep at the gust's attack rate, which is heard as a filter being swept.

    `lfilter` with carried state, so it is continuous across blocks like
    everything else here.
    """

    def __init__(self, tau_s: float, sr: int = SR, init: float = 0.0):
        self.alpha = 1.0 - float(np.exp(-1.0 / max(tau_s * sr, 1.0)))
        self._b = np.array([self.alpha])
        self._a = np.array([1.0, -(1.0 - self.alpha)])
        self._zi = np.array([init * (1.0 - self.alpha)])

    def filter(self, x: np.ndarray) -> np.ndarray:
        y, self._zi = signal.lfilter(self._b, self._a, x, zi=self._zi)
        return y


class Morph(Source):
    """Mix a bank of fixed-filter sources with slowly moving weights.

    This is how a resonance drifts without a time-varying filter. Each band runs
    its own always-on filter; the weights come from `SmoothNoise`, so they are a
    function of absolute time. `focus` sharpens the weighting from a broad blend
    toward a single band — the audible equivalent of raising Q.
    """

    def __init__(self, bands: list[Source], mod: Source | None = None,
                 focus: float = 3.0):
        self.bands = bands
        self.mod = mod
        self.focus = focus
        self._centres = np.linspace(0.0, 1.0, len(bands))

    def render(self, n: int) -> np.ndarray:
        if self.mod is None:
            raise RuntimeError("this Morph has no modulator; use render_with()")
        return self.render_with(self.mod.render(n).astype(np.float64), n)

    def render_with(self, m: np.ndarray, n: int) -> np.ndarray:
        """Drive the blend from a modulator the caller already has.

        Needed whenever one signal has to do two jobs — wind's gust sets both
        how loud it is and how high it sits, and calling `render` on a shared
        modulator twice would advance it twice and decorrelate the two.
        """
        m = np.asarray(m, dtype=np.float64)
        d = np.abs(m[None, :] - self._centres[:, None])
        w = np.exp(-self.focus * d * len(self.bands))
        w /= np.sum(w, axis=0, keepdims=True)
        out = np.zeros(n, dtype=np.float64)
        for i, band in enumerate(self.bands):
            out += band.render(n) * w[i]
        return out.astype(np.float32)


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


class EventLayer:
    """Poisson-scheduled one-shots, mixed in stereo, continuous across calls.

    Onsets come from an exponential distribution, so no rhythm ever emerges from
    repetition — the failure that makes a long ambience track audibly a loop. A
    shot that starts near the end of a block is not truncated: the overhang is
    carried in `_tail` and added to the next block, which is the whole reason
    block-wise output matches a single-shot render exactly.

    `make` is called once per event and returns mono float32 of any length. It
    draws from the layer's own generator, in event order, so the sequence is a
    function of the seed and not of the block size.
    """

    def __init__(self, rng: np.random.Generator, rate_per_s: float,
                 make, pan_jitter: float = 0.0, sr: int = SR):
        self.rng = rng
        self.rate = max(float(rate_per_s), 1e-9)
        self.make = make
        self.pan_jitter = float(pan_jitter)
        self.sr = sr
        self._t = 0
        self._next = int(self.rng.exponential(1.0 / self.rate) * sr)
        self._tail = np.zeros((0, 2), dtype=np.float32)

    def render(self, n: int) -> np.ndarray:
        span = max(n, self._tail.shape[0])
        buf = np.zeros((span, 2), dtype=np.float32)
        buf[: self._tail.shape[0]] += self._tail

        end = self._t + n
        while self._next < end:
            shot = np.asarray(self.make(self.rng), dtype=np.float32)
            pan = float(self.rng.uniform(-1, 1)) * self.pan_jitter
            l = np.sqrt(max(0.0, (1.0 - pan) / 2.0)) * np.sqrt(2.0)
            r = np.sqrt(max(0.0, (1.0 + pan) / 2.0)) * np.sqrt(2.0)

            at = self._next - self._t
            need = at + shot.size
            if need > buf.shape[0]:
                buf = np.vstack([buf, np.zeros((need - buf.shape[0], 2), np.float32)])
            buf[at : at + shot.size, 0] += shot * l
            buf[at : at + shot.size, 1] += shot * r

            self._next += max(int(self.rng.exponential(1.0 / self.rate) * self.sr), 1)

        self._t = end
        self._tail = buf[n:].copy()
        return buf[:n]


class EnvelopeEvents(Source):
    """Poisson-scheduled envelopes, summed into a continuous mono control signal.

    `EventLayer` for things that shape other things rather than being heard. The
    difference that matters is timescale: these events last seconds, overlap
    freely, and are added rather than mixed.

    Wind is why this exists. Modulating a layer with smooth noise gives a level
    that wanders, and a wander is not a gust — `SmoothNoise` interpolates across
    its whole period, so at a 14-second setting the loudness takes 14 seconds to
    rise and the ear reads the result as constant. A gust is an event: it
    arrives over a second or two and dies away over five or ten, and it is the
    arrival that makes it audible.

    Same tail-carry as `EventLayer`, so an envelope that starts near the end of
    a block continues correctly into the next and block-wise output matches a
    single-shot render exactly.
    """

    def __init__(self, rng: np.random.Generator, rate_per_s: float, make,
                 sr: int = SR, warmup_s: float = 0.0):
        self.rng = rng
        self.rate = max(float(rate_per_s), 1e-9)
        self.make = make
        self.sr = sr
        self._t = 0
        self._next = int(self.rng.exponential(1.0 / self.rate) * sr)
        self._tail = np.zeros(0, dtype=np.float32)
        if warmup_s > 0:
            # Run the process and throw the output away, so that t=0 is the
            # middle of a running stream rather than its start.
            #
            # Without this the first event is a full mean interval away — at a
            # 20-second wind, twenty seconds of floor and then a jump as the
            # first gust lands. Every audition opened the same way and it was
            # audible as the volume coming up. Discarding a warmup leaves the
            # tails of events that began before the render did, which is what a
            # stationary process looks like when you start listening to it.
            self.render(int(warmup_s * sr))

    def render(self, n: int) -> np.ndarray:
        buf = np.zeros(max(n, self._tail.size), dtype=np.float32)
        buf[: self._tail.size] += self._tail

        end = self._t + n
        while self._next < end:
            shape = np.asarray(self.make(self.rng), dtype=np.float32)
            at = self._next - self._t
            need = at + shape.size
            if need > buf.size:
                buf = np.concatenate([buf, np.zeros(need - buf.size, np.float32)])
            buf[at : at + shape.size] += shape
            self._next += max(int(self.rng.exponential(1.0 / self.rate) * self.sr), 1)

        self._t = end
        self._tail = buf[n:].copy()
        return buf[:n]


def gust_shape(rng: np.random.Generator, attack_s: tuple[float, float],
               decay_s: tuple[float, float], sr: int = SR) -> np.ndarray:
    """One swell: a smooth rise, then a longer exponential fall.

    Asymmetric on purpose. Wind arrives faster than it leaves, and an envelope
    that rises and falls at the same rate reads as a fader being moved.
    """
    a = float(rng.uniform(*attack_s))
    d = float(rng.uniform(*decay_s))
    n = int((a + 4.0 * d) * sr)
    t = np.arange(n, dtype=np.float64) / sr
    rise = np.clip(t / a, 0.0, 1.0)
    rise = rise * rise * (3.0 - 2.0 * rise)                  # smoothstep
    fall = np.exp(-np.clip(t - a, 0.0, None) / d)
    return (rise * fall).astype(np.float32)


def burst(rng: np.random.Generator, ms: float, decay: float,
          sos: np.ndarray, amp: float = 1.0, sr: int = SR) -> np.ndarray:
    """One short filtered noise transient — the shape of a raindrop or a crackle."""
    n = max(int(ms * sr / 1000.0), 4)
    env = np.exp(-np.linspace(0.0, decay, n))
    return (signal.sosfilt(sos, rng.standard_normal(n)) * env * amp).astype(np.float32)


# --------------------------------------------------------------------------- #
# Shaping
# --------------------------------------------------------------------------- #


def to_stereo(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.stack([left, right], axis=1).astype(np.float32)


def fade(x: np.ndarray, t0: int, total: int, in_s: float, out_s: float,
         sr: int = SR) -> np.ndarray:
    """Apply the ends of a global fade to a block that knows where it sits.

    Takes absolute position so it can be applied per block without the envelope
    depending on how the render was divided.
    """
    n = x.shape[0]
    t = np.arange(t0, t0 + n, dtype=np.float64)
    env = np.ones(n, dtype=np.float64)
    if in_s > 0:
        env = np.minimum(env, np.clip(t / (in_s * sr), 0.0, 1.0))
    if out_s > 0:
        env = np.minimum(env, np.clip((total - t) / (out_s * sr), 0.0, 1.0))
    return (x * env[:, None]).astype(np.float32)


def soft_limit(x: np.ndarray, ceiling_db: float = -3.0) -> np.ndarray:
    """Gentle saturation into a ceiling.

    Not a lookahead limiter. It exists so a stray transient cannot clip the wav,
    not to shape dynamics — ambience that needs limiting to sit right is mixed
    wrong, and the levels reported by the CLI are how you find that out.
    """
    c = db_to_gain(ceiling_db)
    return (np.tanh(x / c) * c).astype(np.float32)
