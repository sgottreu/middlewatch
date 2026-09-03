"""Checks on the synth that do not need ears.

Ears decide whether a recipe sounds like rain. These decide whether it is the
same rain twice, whether it survives being rendered in blocks, and whether every
parameter is connected to anything — the three failures that are invisible to
listening and fatal to a long render.

    python3 -m ambience_pipeline.tests.test_synth

Deliberately not pytest. The repo has no test dependency yet and this needs
none; adding one to run five assertions would be the wrong trade.
"""

from __future__ import annotations

import sys

import numpy as np

from ..synth import dsp
from ..synth.recipes import REGISTRY

SR = dsp.SR
# Long enough to contain several cycles of the slowest modulator any recipe has —
# the ocean's set drift runs on a 37-second period. Six seconds was not: it
# judged an eleven-second swell on a fragment of one wave and called a working
# control dead. Kept as low as that constraint allows, because the recipes
# render at 28-170x realtime and this file should stay cheap enough to run.
WINDOW_S = 60.0
LEVEL_SEEDS = (0, 1, 2)
STREAM_MINUTES = 2

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)
    return ok


def test_deterministic_and_block_invariant() -> None:
    print("\nsame seed renders the same audio, at any block size")
    n = int(12.0 * SR)
    for name in sorted(REGISTRY):
        r = REGISTRY[name]
        once = r.make(7).render(n)
        check(np.array_equal(once, r.make(7).render(n)), f"{name}: seed 7 twice")

        # Ragged blocks, none of them divisors of n.
        src, parts, left = r.make(7), [], n
        for blk in (1, SR, 7919, 3, 2 * SR, n):
            take = min(blk, left)
            if take <= 0:
                break
            parts.append(src.render(take))
            left -= take
        if left:
            parts.append(src.render(left))
        check(np.array_equal(once, np.concatenate(parts)[:n]),
              f"{name}: one call == six ragged blocks")
        check(not np.array_equal(once, r.make(8).render(n)),
              f"{name}: seed 8 differs from seed 7")


def _centroid_hz(x: np.ndarray) -> float:
    mag = np.abs(np.fft.rfft(x.mean(1)))
    freq = np.fft.rfftfreq(x.shape[0], 1 / SR)
    return float((freq * mag).sum() / mag.sum())


def _envelope_autocorr(x: np.ndarray, lag_s: float = 5.0) -> float:
    """How much the layer's loudness at t predicts its loudness 5 seconds later.

    A parameter that changes how fast something breathes — `gust_s`, mainly —
    is invisible to both RMS and spectral centroid: the distribution and the
    spectrum are identical, only the rate differs. An envelope-spectrum centroid
    does not see it either, because broadband noise fluctuation swamps a 0.07 Hz
    gust. Correlation at a fixed lag separates them cleanly: measured across
    gust_s of 4, 14 and 40 seconds it reads -0.08, +0.41, +0.60.
    """
    env = np.abs(x.mean(1))
    k = SR // 4                                   # 4 Hz envelope
    env = env[: len(env) // k * k].reshape(-1, k).mean(1)
    env = env - env.mean()
    lag = int(lag_s * 4)
    return float(np.corrcoef(env[:-lag], env[lag:])[0, 1])


def _onsets(x: np.ndarray, thr_db: float = -34.0) -> int:
    """Transients per window, counted against an absolute threshold.

    Absolute deliberately. Every adaptive threshold tried here — a percentile,
    a median plus MADs — moves with the density it is supposed to be measuring,
    and reported the same sixty onsets whether the crackle was turned down to a
    fifth or up two and a half times.
    """
    thr = dsp.db_to_gain(thr_db)
    env = np.abs(x.mean(1))
    k = SR // 100
    env = env[: len(env) // k * k].reshape(-1, k).max(1)
    return int(np.sum((env[1:] > thr) & (env[:-1] <= thr)))


def _swells(x: np.ndarray) -> int:
    """Count arrivals in the loudness envelope — gusts, waves, swells.

    Distinct from `_onsets`, which counts transients against an absolute
    threshold. A swell has no attack sharp enough to cross a threshold and no
    fixed level to cross it at; what it has is a shape, so it is found as a peak
    with prominence relative to the layer's own range.
    """
    from scipy.signal import find_peaks
    env = np.abs(x.mean(1))
    k = SR // 4
    env = env[: len(env) // k * k].reshape(-1, k).mean(1)
    peaks, _ = find_peaks(env / np.max(env), prominence=0.12, distance=8)
    return len(peaks)


def test_parameters_are_connected() -> None:
    print(f"\nevery parameter moves the output ({WINDOW_S:g}s window)")
    # metric: "level" | "tone" | "rate" | "density" — whichever the parameter is
    # meant to move. Asserting on the wrong one calls a working control dead,
    # which is how gust_s was nearly "fixed" by breaking it.
    #
    # `fixed` holds other parameters steady so the one under test is not masked.
    # Crackle needs the bed down: at its default level the roar sits above the
    # onset threshold continuously and the count is zero at every density.
    cases = [
        ("rain", "intensity", 0.1, 0.95, "level", {}, WINDOW_S),
        ("rain", "surface", "leaves", "glass", "tone", {}, WINDOW_S),
        ("wind", "strength", 0.1, 1.0, "level", {}, WINDOW_S),
        # `gust_s` sets how often a gust arrives, so it is counted, not
        # correlated. It used to be a "rate" case, back when the level wandered
        # continuously; now that gusts are discrete events with their own decay,
        # changing the interval barely moves an autocorrelation — 0.07 apart —
        # while the arrivals themselves go from 8 a minute to 1.5. Three minutes,
        # because at the slow end a one-minute window holds one and a half gusts.
        ("wind", "gust_s", 4.0, 40.0, "swell", {}, 180.0),
        ("fire", "crackle", 0.2, 2.5, "density", {"roar": 0.05}, WINDOW_S),
        ("fire", "roar", 0.1, 1.0, "level", {}, WINDOW_S),
        ("ocean", "brightness", 0.0, 1.0, "tone", {}, WINDOW_S),
        ("ocean", "swell_s", 5.0, 25.0, "rate", {}, WINDOW_S),
        ("room_tone", "size", 0.1, 0.9, "tone", {}, WINDOW_S),
    ]
    for name, key, lo, hi, metric, fixed, secs in cases:
        n = int(secs * SR)
        a = REGISTRY[name].make(5, {**fixed, key: lo}).render(n)
        b = REGISTRY[name].make(5, {**fixed, key: hi}).render(n)
        if metric == "level":
            got = abs(dsp.rms_db(a) - dsp.rms_db(b))
            ok, detail = got > 0.8, f"{got:.1f} dB apart"
        elif metric == "tone":
            got = abs(_centroid_hz(a) - _centroid_hz(b))
            ok, detail = got > 30, f"centroid {got:.0f} Hz apart"
        elif metric == "density":
            na, nb = _onsets(a), _onsets(b)
            got = max(na, nb) / max(min(na, nb), 1)
            ok, detail = got > 2.0, f"{na} vs {nb} onsets, {got:.1f}x"
        elif metric == "swell":
            na, nb = _swells(a), _swells(b)
            got = max(na, nb) / max(min(na, nb), 1)
            ok, detail = got > 2.5, f"{na} vs {nb} swells, {got:.1f}x"
        else:
            got = abs(_envelope_autocorr(a) - _envelope_autocorr(b))
            ok, detail = got > 0.15, f"envelope autocorr {got:.2f} apart"
        check(ok, f"{name}.{key} ({metric})", detail)


def test_levels_and_stereo() -> None:
    print("\nloudness lands near what gain_db names, and the channels differ")
    # Averaged over seeds and over a long window, the same way the trims were
    # measured. A 30-second window on the ocean spans less than one cycle of its
    # 37-second set drift and reads 12 dB apart across seeds — which says
    # nothing about whether the trim is right.
    n = int(WINDOW_S * SR)
    seeds = LEVEL_SEEDS
    for name in sorted(REGISTRY):
        energy, corrs = [], []
        for s in seeds:
            x = REGISTRY[name].make(s).render(n)
            energy.append(10.0 ** (dsp.lufs(x) / 10.0))
            corrs.append(float(np.corrcoef(x[:, 0], x[:, 1])[0, 1]))
        got = 10.0 * np.log10(float(np.mean(energy)))
        target = float(REGISTRY[name].params["gain_db"])
        check(abs(got - target) < 3.0, f"{name}: loudness within 3 LU of gain_db",
              f"target {target:.1f}, measured {got:.1f} LUFS")
        worst = max(corrs, key=abs)
        check(abs(worst) < 0.5, f"{name}: L/R decorrelated", f"r = {worst:+.3f}")


def test_long_render_is_stable() -> None:
    print(f"\nno NaN and no drift over a {STREAM_MINUTES}-minute streamed render")
    for name in sorted(REGISTRY):
        src, worst = REGISTRY[name].make(1), 0.0
        finite = True
        for _ in range(STREAM_MINUTES * 12):
            blk = src.render(int(5 * SR))
            finite &= bool(np.all(np.isfinite(blk)))
            worst = max(worst, float(np.max(np.abs(blk))))
        check(finite and worst < 4.0, f"{name}: {STREAM_MINUTES} minutes streamed",
              f"peak {dsp.gain_to_db(worst):.1f} dBFS")


def test_no_narrow_tone() -> None:
    """Nothing should ring at a single frequency.

    The fire shipped a damped 120-260 Hz sine under every snap, and it was
    audible as an occasional drum beat — a decaying tone is a drum however it
    was arrived at. Nothing else in this file could see it: it moved neither the
    level, nor the spectral balance, nor any parameter response.

    Prominence against a local background, on a Welch estimate so per-bin noise
    does not read as structure. The window matters. At 357 Hz wide it followed
    the peak and reported the drum at the same +6 dB as the wind's resonators,
    which are legitimately narrow. At 44 Hz it smooths over a tone two bins wide
    while still tracking a resonance fifteen bins wide, and the two separate:
    every recipe sits under 2.4 dB, the drum reads 5.5.

    A quiet enough tone still hides — the drum at half level reads 2.9. This
    catches gross ones, not all of them.
    """
    print("\nnothing rings at a single frequency")
    from scipy import ndimage, signal as sig
    for name in sorted(REGISTRY):
        x = REGISTRY[name].make(3).render(int(40.0 * SR)).mean(1)
        freq, power = sig.welch(x, SR, nperseg=16384)
        band = (freq > 40) & (freq < 12000)
        db = 10 * np.log10(power[band] + 1e-30)
        background = ndimage.median_filter(db, size=15, mode="nearest")
        prominence = float(np.max(db - background))
        where = float(freq[band][int(np.argmax(db - background))])
        check(prominence < 4.0, f"{name}: no narrow tone",
              f"worst +{prominence:.1f} dB at {where:.0f} Hz")


# Share of power below 320 Hz that each recipe is meant to have. These encode
# intent, not a general law — there is no universal test for "hums when it
# shouldn't", because the thing that made the fire hum is exactly what makes the
# room tone correct. Both measure 0.0000 spectral flatness; one is a defect and
# one is the entire point. So the bound is per recipe, and the fire's is the one
# with history: its bed measured 96% below 320 Hz as a lowpassed brown noise and
# 84% as a bandpassed one, and hummed audibly both times. It is 12% now.
LOW_SHARE = {           # (min %, max %)
    "rain": (0.0, 5.0),
    "fire": (2.0, 30.0),
    "ocean": (35.0, 85.0),
    "wind": (10.0, 80.0),
    "room_tone": (90.0, 100.0),
}


def test_spectral_balance() -> None:
    print("\nlow-end share is where each recipe intends it")
    for name in sorted(REGISTRY):
        lo, hi = LOW_SHARE[name]
        worst = None
        for s in (0, 1, 2, 3):
            x = REGISTRY[name].make(s).render(int(30.0 * SR))
            mag = np.abs(np.fft.rfft(x.mean(1))) ** 2
            freq = np.fft.rfftfreq(x.shape[0], 1 / SR)
            pct = float(100 * mag[freq < 320].sum() / mag.sum())
            if worst is None or not (lo <= pct <= hi):
                worst = pct
        check(lo <= worst <= hi, f"{name}: <320 Hz share in {lo:.0f}-{hi:.0f}%",
              f"{worst:.1f}%")


def test_bad_parameter_is_rejected() -> None:
    print("\nan unknown parameter fails rather than being ignored")
    try:
        REGISTRY["rain"].make(1, {"nope": 1})
        check(False, "rain: unknown parameter rejected")
    except KeyError as e:
        check("nope" in str(e), "rain: unknown parameter rejected", str(e)[:52])


def main() -> int:
    for fn in (test_deterministic_and_block_invariant,
               test_parameters_are_connected,
               test_levels_and_stereo,
               test_long_render_is_stable,
               test_no_narrow_tone,
               test_spectral_balance,
               test_bad_parameter_is_rejected):
        fn()
    print(f"\n{len(failures)} failure(s)" + (": " + ", ".join(failures) if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
