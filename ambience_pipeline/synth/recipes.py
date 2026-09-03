"""The five procedural recipes, and the registry the CLI and the mixer read.

A recipe is a small factory: given a seed and its parameters, it returns a
`Layer` that can be asked for frames until you stop asking. It holds no opinion
about duration, and none of them ever repeat — the noise is generated, not
looped, so an eight-hour render is eight hours of different air.

Every parameter here is a starting point, not a measurement. These are the
figures the design doc lists as assumed, and the whole point of the audition CLI
is that you change them by ear and tell me what moved. `docs/ambience/` should
gain a calibration note once the first ones are settled.

Levels are quoted as integrated loudness in LUFS for the layer alone, because
that is the unit the mix target is written in and it is not interchangeable with
RMS — K-weighting favours the highs, and levelling bright rain against dark fire
by RMS puts them 6 dB from where the numbers claim. They are deliberately low: a
bed sits around -20 and a texture below it, because these are components of a
mix rather than finished tracks. The CLI reports what it actually measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from . import dsp
from .dsp import SR


class Layer:
    """A stereo source with a level. The unit the mixer will stack.

    `trim_db` is a per-recipe constant that makes `gain_db` mean something. Left
    raw, a recipe's output level is an accident of how many components it happens
    to sum, and "-14 dB" would name a number nobody could act on. The trim is
    measured at default parameters so that `gain_db` *is* the layer's approximate
    integrated loudness in LUFS — which is what lets a mix be specified in the
    concept file rather than discovered by rendering it.

    Approximate, not exact: change a parameter far from its default and the
    level moves, as it should. The CLI reports what was actually measured.
    """

    def __init__(self, render_fn: Callable[[int], np.ndarray],
                 gain_db: float = 0.0, trim_db: float = 0.0):
        self._render = render_fn
        self.gain = dsp.db_to_gain(gain_db + trim_db)

    def render(self, n: int) -> np.ndarray:
        return (self._render(n) * self.gain).astype(np.float32)


# Measured so that each recipe at its default parameters renders at roughly the
# loudness its `gain_db` names. Re-measure with `scripts/measure_trims.py` if a recipe
# gains a component or its internal balance changes.
TRIM_DB = {
    "fire": -5.0,
    "ocean": -1.5,
    "rain": 7.4,
    "room_tone": -3.8,
    "wind": 10.3,
}


@dataclass
class Recipe:
    name: str
    summary: str
    params: dict[str, Any]
    build: Callable[..., Layer] = field(repr=False)

    def make(self, seed: int, overrides: dict[str, Any] | None = None,
             sr: int = SR) -> Layer:
        p = dict(self.params)
        for k, v in (overrides or {}).items():
            if k not in p:
                raise KeyError(
                    f"{self.name} has no parameter {k!r}; "
                    f"it takes {', '.join(sorted(p))}"
                )
            p[k] = type(p[k])(v) if p[k] is not None else v
        return self.build(seed=seed, sr=sr, **p)


REGISTRY: dict[str, Recipe] = {}


def recipe(name: str, summary: str, **params):
    def wrap(fn):
        REGISTRY[name] = Recipe(name=name, summary=summary, params=params, build=fn)
        return fn
    return wrap


def _stereo_noise(rngs, colour, sos_factory):
    """Two independently generated channels.

    Decorrelated at the source rather than by delaying one side, which would
    comb-filter the sum the moment anyone listens on a phone speaker.
    """
    return [dsp.Sos(dsp.Noise(r, colour), sos_factory()) for r in rngs]


# --------------------------------------------------------------------------- #


@recipe("rain", "Rain on a surface — bed plus individual drops.",
        intensity=0.45, surface="glass", gain_db=-14.0)
def rain(seed: int, sr: int, intensity: float, surface: str, gain_db: float) -> Layer:
    """Broadband hiss for the sheet, Poisson transients for the drops.

    `surface` moves both halves together: glass is bright and ticky, leaves are
    darker and softer, canvas sits between them with the drops nearly gone. The
    drop rate scales with intensity far faster than the bed does, because heavy
    rain reads as *more events*, not as louder hiss.
    """
    bands = {
        "glass":  (900.0, 11000.0, 2400.0, 9000.0, 14.0, 7.0),
        "canvas": (500.0, 7000.0, 1200.0, 5000.0, 22.0, 5.0),
        "leaves": (300.0, 5000.0, 700.0, 3500.0, 30.0, 4.0),
        "roof":   (400.0, 9000.0, 1800.0, 7000.0, 18.0, 6.0),
    }
    if surface not in bands:
        raise ValueError(f"unknown surface {surface!r}; have {sorted(bands)}")
    lo, hi, dlo, dhi, ms, decay = bands[surface]

    r = dsp.spawn_rngs(seed, 4)
    bed_l, bed_r = _stereo_noise(r[:2], "white", lambda: dsp.bandpass(lo, hi, 4, sr))
    swell = dsp.SmoothNoise(r[2], 9.0, sr)

    drop_sos = dsp.bandpass(dlo, dhi, 2, sr)
    rate = 20.0 + 260.0 * float(intensity) ** 1.6

    def make(g):
        return dsp.burst(g, ms * float(g.uniform(0.6, 1.4)), decay, drop_sos,
                         amp=float(g.uniform(0.25, 1.0)) ** 2, sr=sr)

    drops = dsp.EventLayer(r[3], rate, make, pan_jitter=0.9, sr=sr)
    bed_gain = 0.35 + 0.65 * float(intensity)

    def render(n):
        # A slow breathing on the bed only. Drops keep their own density, so the
        # rain gets thicker without the hiss pumping.
        env = (0.85 + 0.30 * swell.render(n))[:, None]
        bed = dsp.to_stereo(bed_l.render(n), bed_r.render(n)) * env * bed_gain
        return bed * 0.5 + drops.render(n) * 0.5

    return Layer(render, gain_db, TRIM_DB['rain'])


# --------------------------------------------------------------------------- #


@recipe("wind", "Wind with gusts — a resonance that wanders.",
        strength=0.5, gust_s=20.0, gain_db=-18.0)
def wind(seed: int, sr: int, strength: float, gust_s: float, gain_db: float) -> Layer:
    """Brown noise through a bank of resonators, driven by gusts that arrive.

    The bank is the trick described in `dsp.Morph`: a real moving filter would
    make the block size audible, so five fixed resonators run continuously and
    the weighting between them drifts instead.

    **A gust is an event, not a level.** Two earlier versions modulated the
    amplitude with smooth noise and both were heard as constant — the second
    measured 16 dB from lull to gust and still sounded like a fader nobody was
    touching. The measurement was over five minutes; the listening was over two.
    `SmoothNoise` interpolates across its whole period, so at a 14-second
    setting the level takes 14 seconds to rise, and nothing that gradual reads
    as an arrival. What makes a gust audible is the attack.

    So gusts are scheduled: `dsp.EnvelopeEvents` at a mean interval of `gust_s`,
    each rising over one and a half to five seconds and falling over six to
    twenty, overlapping freely. Measured inside a ten-second window — the span
    over which a listener actually judges whether something is moving — the
    level swings 5 dB where before it swung under 1.

    Those figures are the second attempt. The first scheduled gusts at 0.6-2.6 s
    attack and 3.5-14 s decay and was heard as intensity changing too fast: a
    gust is a swell, not a stab. The floor came up with it, from 0.075 to 0.10,
    because a 22 dB drop between gusts makes every arrival abrupt.

    Weather remains, demoted to what it should always have been: an hour-scale
    baseline setting how much wind there is *between* gusts, not the gusts.

    **One envelope, both ears.** Each channel had its own gust briefly, and it
    was audible as the wind changing direction — a gust arriving left and not
    right swings the image. Weather reaches both ears together; what differs
    between them is how the room resonates, which is the filter bank, not the
    loudness. Channel correlation went from 0.0 to 0.86. Width now comes from
    the independent noise feeding each bank, plus a per-side trim of ±2 dB
    wandering over half a minute — far too slow to read as movement.

    **Loud wind is higher wind.** The same gust envelope drives the resonator
    blend, so a swell climbs in pitch as it arrives, the way wind does round an
    edge. That is why it is rendered once and passed to `render_with`: asking
    for its samples twice would advance it twice and decorrelate the two halves
    of one gesture.
    """
    # Thirteen generators, because the two banks must not share any: two sources
    # drawing from one generator interleave differently at a different block
    # size, and the render would stop being reproducible from its seed.
    r = dsp.spawn_rngs(seed, 14)
    centres = np.geomspace(140.0, 1500.0, 5)

    def bank(rngs):
        return [dsp.Sos(dsp.Noise(rngs[i], "brown"), dsp.resonator(c, 1.6, sr))
                for i, c in enumerate(centres)]

    # Both banks in the same order. The right one used to be built reversed, as
    # a way of decorrelating the sides, and that was fine only while each side
    # had its own modulator. Once they shared one it became systematic: at any
    # modulator position the left sat at a low resonance and the right at a high
    # one, mirrored, so the two channels held different sounds and swapped as
    # the modulator drifted. Decorrelation comes from the independent noise
    # feeding each bank, which does not need the frequencies to disagree.
    left = dsp.Morph(bank(r[0:5]), focus=2.2)
    right = dsp.Morph(bank(r[5:10]), focus=2.2)

    # One gust envelope, shared by both channels. Giving each side its own was a
    # mistake you could hear: a gust arriving left but not right swings the
    # stereo image, and the wind appears to change direction. Weather arrives at
    # both ears together. What differs between them is how the room resonates,
    # which is the filter bank below, not the loudness.
    #
    # Slow. The first version of this rose in 0.6-2.6 s and fell in 3.5-14, and
    # read as intensity changing too fast — a gust is a swell, not a stab.
    def swell(g):
        amp = float(g.uniform(0.35, 1.0)) ** 1.4
        return dsp.gust_shape(g, (1.5, 5.0), (6.0, 20.0), sr) * amp

    gusts = dsp.EnvelopeEvents(r[10], 1.0 / float(gust_s), swell, sr,
                               warmup_s=4.0 * float(gust_s))
    # A slow per-side trim, ±2 dB over half a minute. Enough width that the two
    # channels are not the same signal at different filters, far too slow and
    # too small to read as movement.
    tilt_l = dsp.SmoothNoise(r[11], 34.0, sr)
    tilt_r = dsp.SmoothNoise(r[13], 41.0, sr)
    # Weather is the hour-scale baseline: whether it is a blowy afternoon or a
    # still one. It sets how much wind there is between gusts, not the gusts.
    weather = dsp.SmoothNoise(r[12], gust_s * 8.0, sr)
    # Starts mid-range so the first fourteen seconds are not a slide up from the
    # bottom of the bank.
    pitch_lag = dsp.OnePole(14.0, sr, init=0.45)

    # Light wind is intermittent, strong wind is sustained and louder for it.
    # Only the floor moves: an earlier version also cut the gust gain as
    # strength rose and the two cancelled, moving the measured level by 0.6 dB.
    # The floor does not go below 0.10 — at 0.075 the lulls fell 22 dB and the
    # arrivals were abrupt rather than gradual.
    floor = 0.10 + 0.25 * float(strength)

    def render(n):
        w = np.clip(weather.render(n).astype(np.float64) * 1.4 - 0.2, 0.0, 1.0)
        base = floor * (0.35 + 0.65 * w)
        g = np.tanh(gusts.render(n).astype(np.float64) * 1.1)
        env = base + (1.0 - base) * g
        el = env * (0.9 + 0.2 * tilt_l.render(n).astype(np.float64))
        er = env * (0.9 + 0.2 * tilt_r.render(n).astype(np.float64))
        # Pitch follows the wind's trend, not each gust. Two separate fixes, in
        # the order they were needed:
        #
        # Range. At 0.10-1.00 every gust swept the resonance across the full
        # 140-1500 Hz span — a decade inside a two-second attack, which is a
        # filter sweep, not weather. A third of the bank spans roughly
        # 250-650 Hz, and the colour shifts without gliding.
        #
        # Rate. Narrowing the span shortened the journey without slowing it: the
        # modulator still tracked the gust envelope sample for sample, so it
        # still moved at the attack. A 14-second lag decouples them. Loudness
        # gusts; resonance drifts, because the resonance of a gap or a chimney
        # depends on the average flow rather than the instantaneous one.
        m = pitch_lag.filter(np.clip(0.28 + 0.34 * g, 0.0, 1.0))
        # 2.2, not 3.2: gusts stack, and the louder setting reached +4.4 dBFS
        # before the render's ceiling caught it. Relying on a limiter to hold a
        # layer down means the limiter is shaping every gust.
        return dsp.to_stereo(left.render_with(m, n) * el,
                             right.render_with(m, n) * er) * 2.2

    return Layer(render, gain_db, TRIM_DB['wind'])


# --------------------------------------------------------------------------- #


@recipe("fire", "A hearth — low roar under irregular crackle.",
        crackle=1.0, roar=0.6, gain_db=-20.0)
def fire(seed: int, sr: int, crackle: float, roar: float, gain_db: float) -> Layer:
    """Two populations of transient, not one.

    Fire is mostly small ticks with the occasional sharp pop, and modelling that
    as a single rate gives you a Geiger counter. The amplitudes are drawn from a
    heavy tail — cube of a uniform for the ticks, and a separate slow layer of
    louder snaps — which is what makes it sound like burning rather than static.
    """
    r = dsp.spawn_rngs(seed, 5)
    # Pink, wide, and starting well above the bottom of the spectrum. Two
    # earlier versions of this line both hummed, and the measurements say why.
    #
    #   brown -> lowpass 420    78% of the layer's power below 120 Hz
    #   brown -> bandpass 100-500   83% inside 80-320 Hz, peaking at 115 Hz,
    #                               spectral flatness 0.0000
    #
    # The second is quieter than the first and hums just as much, because the
    # problem was never the total low energy — it was that brown noise runs
    # -6 dB/octave, so inside any low band almost all the power piles up at the
    # bottom edge. A narrow concentration of energy is a tone, whatever it is
    # made of. Pink tilts half as steeply and a 250-3000 Hz span spreads it
    # across four octaves: flatness 0.0076, 190 times flatter, and 1% below
    # 160 Hz. A fire roars; it does not drone.
    #
    # The low weight a hearth does have belongs to the snaps below, where it
    # arrives as a transient and leaves again.
    roar_l, roar_r = _stereo_noise(r[:2], "pink",
                                   lambda: dsp.bandpass(250.0, 3000.0, 2, sr))
    breath = dsp.SmoothNoise(r[2], 6.5, sr)

    # Longer, darker and sparser than the first attempt, which read as crinkling
    # paper. Paper crinkle *is* a dense stream of very short, very bright, very
    # dry noise clicks — 3-15 ms bursts across 700 Hz-8 kHz, eleven a second,
    # which is what the first version generated. A fire pop is a slower event
    # with more low-mid in it, and there is air between them.
    tick_bands = [dsp.bandpass(lo, hi, 2, sr) for lo, hi in
                  ((250, 900), (400, 1600), (700, 2600), (1200, 4000),
                   (2000, 6500), (350, 2200))]
    # Non-pitched. A damped sine here was audible as an occasional drum beat,
    # because a decaying tone at 120-260 Hz is a drum however it was arrived at.
    # A narrow noise band gives the same weight with no pitch to latch onto.
    snap_sos = dsp.bandpass(500.0, 2600.0, 2, sr)
    thump_sos = dsp.bandpass(150.0, 400.0, 2, sr)

    def flurry(g):
        """One crackle: a few pops over a few hundred milliseconds.

        Fire does not tick at an even rate — it clusters, goes quiet, clusters
        again. A single Poisson stream of identical ticks gives you a Geiger
        counter, so each scheduled event is a small burst of them with their own
        spacing, timbre and weight.

        Amplitudes are drawn steeply (a 2.5 power) so that most pops are small
        and the occasional one is not. An even spread of loudness across many
        short events is the other half of what makes a crackle sound like paper.
        """
        spread_ms = float(g.uniform(40, 420))
        span = int((spread_ms + 220.0) * sr / 1000.0)
        buf = np.zeros(span, dtype=np.float32)
        for _ in range(int(g.integers(1, 5))):
            sos = tick_bands[int(g.integers(0, len(tick_bands)))]
            shot = dsp.burst(g, float(g.uniform(12, 70)), float(g.uniform(4, 9)),
                             sos, amp=float(g.uniform(0.15, 1.0)) ** 2.5, sr=sr)
            at = int(float(g.uniform(0.0, spread_ms)) * sr / 1000.0)
            room = min(shot.size, span - at)
            if room > 0:
                buf[at:at + room] += shot[:room]
        return buf

    def snap(g):
        """A log shifting: a noise burst with a low, unpitched thump under it."""
        shot = dsp.burst(g, float(g.uniform(35, 90)), 4.0, snap_sos,
                         amp=float(g.uniform(0.5, 1.0)), sr=sr)
        thump = dsp.burst(g, float(g.uniform(60, 140)), 5.0, thump_sos,
                          amp=float(g.uniform(0.3, 0.7)), sr=sr)
        span = max(shot.size, thump.size)
        out = np.zeros(span, dtype=np.float32)
        out[: shot.size] += shot
        out[: thump.size] += thump
        return out

    ticks = dsp.EventLayer(r[3], 3.2 * float(crackle), flurry, 0.7, sr)
    snaps = dsp.EventLayer(r[4], 0.55 * float(crackle), snap, 0.5, sr)

    def render(n):
        env = (0.7 + 0.6 * breath.render(n))[:, None]
        bed = dsp.to_stereo(roar_l.render(n), roar_r.render(n)) * env * float(roar) * 3.4
        return bed + ticks.render(n) * 1.5 + snaps.render(n) * 0.8

    return Layer(render, gain_db, TRIM_DB['fire'])


# --------------------------------------------------------------------------- #


@recipe("ocean", "Surf — long swells breaking at an uneven interval.",
        swell_s=11.0, brightness=0.5, gain_db=-16.0)
def ocean(seed: int, sr: int, swell_s: float, brightness: float, gain_db: float) -> Layer:
    """Pink noise shaped by an asymmetric swell.

    The envelope matters more than the spectrum. A sine would give you a washing
    machine; a real wave rises slowly, breaks, and drains for longer than it took
    to build. That shape comes from raising a smooth modulator to a power and
    running a brighter, quieter noise on top of the peak so the break has hiss in
    it that the trough does not.
    """
    r = dsp.spawn_rngs(seed, 6)
    body_l, body_r = _stereo_noise(r[:2], "pink", lambda: dsp.lowpass(1400.0, 4, sr))
    spray_l, spray_r = _stereo_noise(r[2:4], "white", lambda: dsp.bandpass(2000.0, 9000.0, 2, sr))
    swell = dsp.SmoothNoise(r[4], swell_s, sr)
    drift = dsp.SmoothNoise(r[5], swell_s * 3.4, sr)

    def render(n):
        s = swell.render(n).astype(np.float64)
        d = drift.render(n).astype(np.float64)
        # Steeper rise than fall: the wave breaks, then drains. The swell drives
        # the shape on its own and the slower drift varies how big each set is,
        # rather than multiplying into the exponent base — which held the wave
        # away from full scale and made the spray inaudible.
        # The 1.35 is what makes the peaks break. Smooth noise interpolated
        # between uniform draws almost never reaches 1.0 — measured over two
        # minutes it never passed 0.75 — so raising it to a power left the crest
        # of every wave in the middle of the range and the spray inaudible.
        # Overdriving into a clip gives a flat top, which is what a wave has.
        wave = np.clip(s * 1.35, 0.0, 1.0) ** 2.2
        sets = 0.70 + 0.55 * d
        body = dsp.to_stereo(body_l.render(n), body_r.render(n)) * (0.30 + 0.85 * wave)[:, None]
        spray = dsp.to_stereo(spray_l.render(n), spray_r.render(n)) * (wave ** 1.8)[:, None]
        out = body * 1.5 + spray * float(brightness) * 3.0
        return (out * sets[:, None]).astype(np.float32)

    return Layer(render, gain_db, TRIM_DB['ocean'])


# --------------------------------------------------------------------------- #


@recipe("room_tone", "The sound of an enclosed space that is not silent.",
        size=0.5, gain_db=-30.0)
def room_tone(seed: int, sr: int, size: float, gain_db: float) -> Layer:
    """Almost nothing, deliberately.

    This is the layer nobody notices and every mix needs: without it a soundscape
    sits in a vacuum, and the ear reads the silence between events as a dropout.
    `size` moves the cutoff down and the drift slower, so a small parlour and a
    great hall differ without either becoming audible as a sound in itself.

    There is no `hum_hz` here, and the reason is worth recording. Mains hum was a
    parameter of this recipe and could not be made to work: the bed is brown
    noise rolled off around 220 Hz, so effectively all of its energy sits in the
    same octave as a 50 Hz sine, and the sine is masked. Measured, lifting the
    50 Hz band by 8 dB took an amplitude that raised the whole layer by 8 dB
    as well — at which point the hum *is* the room tone. Pink noise and a
    highpass bought 5 dB for 3.6 dB of level, which is not a control either.

    Hum is a tonal element and belongs in the `tonal` layer role the design doc
    defines, where the mix can set its level against the bed rather than hiding
    it underneath one. A parameter that cannot be heard is worse than an absent
    one, because it invites tuning that does nothing.
    """
    r = dsp.spawn_rngs(seed, 3)
    cut = 320.0 - 200.0 * float(size)
    bed_l, bed_r = _stereo_noise(r[:2], "brown", lambda: dsp.lowpass(cut, 4, sr))
    drift = dsp.SmoothNoise(r[2], 20.0 + 40.0 * float(size), sr)

    def render(n):
        env = (0.75 + 0.5 * drift.render(n))[:, None]
        out = dsp.to_stereo(bed_l.render(n), bed_r.render(n)) * env * 2.2
        return out.astype(np.float32)

    return Layer(render, gain_db, TRIM_DB["room_tone"])
