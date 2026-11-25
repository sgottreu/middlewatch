"""synth/dsp.py helpers with simple numeric checks (recipe-level behaviour lives
in ambience_pipeline/tests/test_synth.py)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import signal

from ambience_pipeline.synth import dsp

SR = 8000


def tone(freq, seconds=1.0, amp=1.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ---- level conversions


def test_db_gain_roundtrip():
    assert dsp.db_to_gain(0) == 1.0
    assert dsp.db_to_gain(-6.0206) == pytest.approx(0.5, rel=1e-4)
    assert dsp.db_to_gain(20) == pytest.approx(10)
    for db in (-60, -12.5, 0, 3, 18):
        assert dsp.gain_to_db(dsp.db_to_gain(db)) == pytest.approx(db)


def test_gain_to_db_floor_and_sign():
    assert dsp.gain_to_db(0) == pytest.approx(-240.0)
    assert dsp.gain_to_db(-0.5) == dsp.gain_to_db(0.5)


def test_rms_and_peak_db():
    x = tone(100, amp=0.5)
    assert dsp.peak_db(x) == pytest.approx(-6.02, abs=0.01)
    assert dsp.rms_db(x) == pytest.approx(-6.02 - 3.01, abs=0.02)
    assert dsp.rms_db(np.full(10, 1.0)) == pytest.approx(0)


def test_peak_db_empty_is_floor():
    assert dsp.peak_db(np.array([])) == pytest.approx(-240.0)


# ---- loudness


def test_lufs_997hz_full_scale_sine_reference():
    # A 997 Hz sine at -20 dBFS peak: -23 dBFS RMS per channel, +3 dB for two
    # channels summed, ~0 dB K-weighting at 997 Hz => about -20 LUFS.
    x = tone(997, 3.0, amp=dsp.db_to_gain(-20), sr=dsp.SR)
    st = np.stack([x, x], 1)
    assert dsp.lufs(st, dsp.SR) == pytest.approx(-20.0, abs=0.3)


def test_lufs_scales_with_gain():
    x = np.random.default_rng(0).standard_normal((dsp.SR, 2)) * 0.1
    assert dsp.lufs(x * 2) - dsp.lufs(x) == pytest.approx(6.02, abs=0.01)


def test_lufs_accepts_channels_first_and_mono():
    x = np.random.default_rng(0).standard_normal((dsp.SR, 2)) * 0.1
    assert dsp.lufs(x.T) == pytest.approx(dsp.lufs(x))
    m = x[:, 0]
    assert dsp.lufs(m) == pytest.approx(dsp.lufs(np.stack([m, m], 1)) - 3.01, abs=0.01)


def test_lufs_silence_is_very_low():
    assert dsp.lufs(np.zeros((1000, 2))) < -200


def test_k_weighting_favours_highs_over_lows():
    lo = dsp.lufs(tone(100, 2, 0.1, dsp.SR), dsp.SR)
    hi = dsp.lufs(tone(4000, 2, 0.1, dsp.SR), dsp.SR)
    assert hi > lo + 3


# ---- rng


def test_spawn_rngs_deterministic_independent_ordered():
    a = [r.random() for r in dsp.spawn_rngs(5, 3)]
    b = [r.random() for r in dsp.spawn_rngs(5, 3)]
    assert a == b and len(set(a)) == 3
    assert [r.random() for r in dsp.spawn_rngs(6, 3)] != a
    # prefix stability: asking for more sources does not disturb earlier streams
    assert [r.random() for r in dsp.spawn_rngs(5, 5)][:3] == a


# ---- noise


@pytest.mark.parametrize("colour", ["white", "pink", "brown"])
def test_noise_unit_ish_rms_and_zero_mean(colour):
    n = dsp.Noise(np.random.default_rng(1), colour)
    x = n.render(SR * 20)
    assert x.dtype == np.float32 and x.shape == (SR * 20,)
    assert abs(x.mean()) < 0.1 * x.std() + 0.1
    assert 0.6 < x.std() < 1.6      # normalisation constants aim at unit RMS


def test_noise_spectral_slopes():
    def band_power(x, lo, hi):
        f, p = signal.welch(x, SR, nperseg=2048)
        return p[(f >= lo) & (f < hi)].mean()
    w = dsp.Noise(np.random.default_rng(2), "white").render(SR * 30)
    p = dsp.Noise(np.random.default_rng(2), "pink").render(SR * 30)
    b = dsp.Noise(np.random.default_rng(2), "brown").render(SR * 30)
    tilt = lambda x: band_power(x, 100, 200) / band_power(x, 1600, 3200)
    assert 0.5 < tilt(w) < 2
    assert tilt(p) > 8            # ~ -3 dB/oct over 4 octaves = 12x
    assert tilt(b) > 3 * tilt(p)


def test_noise_unknown_colour_raises():
    with pytest.raises(ValueError, match="unknown noise colour"):
        dsp.Noise(np.random.default_rng(0), "blue")


def test_noise_block_invariant():
    a = dsp.Noise(np.random.default_rng(3), "pink")
    b = dsp.Noise(np.random.default_rng(3), "pink")
    whole = a.render(1000)
    parts = np.concatenate([b.render(333), b.render(333), b.render(334)])
    assert np.allclose(whole, parts, atol=1e-6)


# ---- filters


def gain_at(sos, f, sr=SR):
    _, h = signal.sosfreqz(sos, worN=[f], fs=sr)
    return abs(h[0])


def test_lowpass_highpass_bandpass_responses():
    lp = dsp.lowpass(500, sr=SR)
    hp = dsp.highpass(500, sr=SR)
    bp = dsp.bandpass(400, 800, sr=SR)
    assert gain_at(lp, 50) == pytest.approx(1, abs=0.02) and gain_at(lp, 2000) < 0.05
    assert gain_at(hp, 2000) == pytest.approx(1, abs=0.02) and gain_at(hp, 50) < 0.05
    assert gain_at(bp, 600) > 0.9 and gain_at(bp, 50) < 0.05 and gain_at(bp, 3000) < 0.05


def test_filter_cutoffs_clamped_below_nyquist():
    sos = dsp.lowpass(1e9, sr=SR)
    assert np.isfinite(sos).all()
    assert np.isfinite(dsp.bandpass(0, 1e9, sr=SR)).all()
    assert np.isfinite(dsp.highpass(0, sr=SR)).all()


def test_resonator_peaks_at_centre():
    r = dsp.resonator(1000, 8, sr=SR)
    assert gain_at(r, 1000) > gain_at(r, 500) * 5
    assert gain_at(r, 1000) > gain_at(r, 2000) * 5


def test_sos_source_block_invariant_and_filters():
    mk = lambda: dsp.Sos(dsp.Noise(np.random.default_rng(4), "white"), dsp.lowpass(300, sr=SR))
    a, b = mk(), mk()
    whole = a.render(2000)
    parts = np.concatenate([b.render(700), b.render(1300)])
    assert np.allclose(whole, parts, atol=1e-5)


# ---- modulators


def test_sine_frequency_and_continuity():
    s = dsp.Sine(100, sr=SR)
    x = np.concatenate([s.render(333), s.render(667)])
    ref = tone(100, 1.0)[:1000]
    assert np.allclose(x, ref, atol=1e-4)
    assert x.max() == pytest.approx(1.0, abs=1e-3)


def test_sine_phase():
    s = dsp.Sine(10, phase=np.pi / 2, sr=SR)
    assert s.render(1)[0] == pytest.approx(1.0)


def test_smooth_noise_range_and_block_invariance():
    mk = lambda: dsp.SmoothNoise(np.random.default_rng(5), 0.1, sr=SR)
    a, b = mk(), mk()
    whole = a.render(5000)
    parts = np.concatenate([b.render(1234), b.render(1), b.render(3765)])
    assert np.allclose(whole, parts, atol=1e-6)
    assert whole.min() >= 0 and whole.max() <= 1
    # smooth: no sample-to-sample jump anywhere near a full-range step
    assert np.abs(np.diff(whole)).max() < 0.01


def test_smooth_noise_hits_grid_values_at_nodes():
    rng = np.random.default_rng(6)
    first = [float(r) for r in np.random.default_rng(6).random(2)]
    s = dsp.SmoothNoise(rng, 0.1, sr=SR)
    x = s.render(801)
    assert x[0] == pytest.approx(first[0], abs=1e-6)
    assert x[800] == pytest.approx(first[1], abs=1e-6)  # exactly at node 1
    assert s._vals[1] == pytest.approx(first[1])


def test_one_pole_step_response():
    op = dsp.OnePole(0.01, sr=SR)           # tau = 80 samples
    y = op.filter(np.ones(800))
    assert y[0] == pytest.approx(op.alpha)
    assert y[79] == pytest.approx(1 - np.exp(-1), abs=0.01)
    assert y[-1] == pytest.approx(1.0, abs=1e-3)
    assert np.all(np.diff(y) > 0)


def test_one_pole_state_carries_and_init():
    a, b = dsp.OnePole(0.01, sr=SR, init=0.5), dsp.OnePole(0.01, sr=SR, init=0.5)
    x = np.ones(400)
    assert np.allclose(a.filter(x), np.concatenate([b.filter(x[:150]), b.filter(x[150:])]))
    assert dsp.OnePole(0.01, sr=SR, init=1.0).filter(np.ones(3)) == pytest.approx([1, 1, 1])


# ---- shapes


def test_gust_shape_asymmetry_and_range():
    rng = np.random.default_rng(7)
    g = dsp.gust_shape(rng, (0.5, 0.5), (1.0, 1.0), sr=SR)
    assert g.dtype == np.float32 and len(g) == int((0.5 + 4.0) * SR)
    assert g[0] == 0 and 0.9 < g.max() <= 1.0 + 1e-6
    peak = int(g.argmax())
    assert peak == pytest.approx(0.5 * SR, abs=5)
    assert peak < len(g) - peak          # fast attack, long tail
    assert g[-1] < 0.05


def test_burst_length_floor_and_decay():
    rng = np.random.default_rng(8)
    sos = dsp.bandpass(500, 2000, sr=SR)
    b = dsp.burst(rng, 100, 6.0, sos, sr=SR)
    assert len(b) == 800
    assert np.abs(b[:100]).max() > np.abs(b[-100:]).max()
    assert len(dsp.burst(rng, 0, 1.0, sos, sr=SR)) == 4
    assert np.abs(dsp.burst(np.random.default_rng(8), 100, 6.0, sos, amp=2.0, sr=SR)).max() == \
        pytest.approx(2 * np.abs(b).max(), rel=1e-4)


def test_to_stereo_shape_dtype():
    s = dsp.to_stereo(np.zeros(5), np.ones(5))
    assert s.shape == (5, 2) and s.dtype == np.float32
    assert (s[:, 1] == 1).all() and (s[:, 0] == 0).all()


# ---- fade / limit


def test_fade_ramps_and_float32():
    x = np.ones((1000, 2), np.float32)
    y = dsp.fade(x, 0, 1000, 0.1, 0.2, sr=1000)
    assert y.dtype == np.float32 and y.shape == x.shape
    assert y[0, 0] == 0 and y[50, 0] == pytest.approx(0.5)
    assert y[100:800].min() == pytest.approx(1.0)
    assert y[900, 0] == pytest.approx(0.5, abs=0.01)
    assert y[-1, 0] == pytest.approx(0.005, abs=0.001)


def test_fade_zero_lengths_is_identity():
    x = np.random.default_rng(0).standard_normal((100, 2)).astype(np.float32)
    assert np.array_equal(dsp.fade(x, 0, 100, 0, 0, sr=1000), x)


def test_fade_block_position_matches_whole():
    x = np.ones((1000, 2), np.float32)
    whole = dsp.fade(x, 0, 1000, 0.3, 0.3, sr=1000)
    parts = np.concatenate([dsp.fade(x[:400], 0, 1000, 0.3, 0.3, 1000),
                            dsp.fade(x[400:], 400, 1000, 0.3, 0.3, 1000)])
    assert np.allclose(whole, parts)


def test_fade_applies_to_both_channels_equally():
    y = dsp.fade(np.ones((100, 2), np.float32), 0, 100, 0.05, 0.05, sr=1000)
    assert np.array_equal(y[:, 0], y[:, 1])


def test_soft_limit_ceiling_and_transparency():
    c = dsp.db_to_gain(-3)
    y = dsp.soft_limit(np.array([100.0, -100.0, 0.0], np.float32), -3)
    assert y[0] == pytest.approx(c, rel=1e-5) and y[1] == pytest.approx(-c, rel=1e-5) and y[2] == 0
    small = np.array([0.001, -0.001], np.float32)
    assert dsp.soft_limit(small, -3) == pytest.approx(small, rel=1e-3)
    ramp = np.linspace(-5, 5, 101).astype(np.float32)
    assert np.all(np.diff(dsp.soft_limit(ramp)) >= 0)          # monotonic
    assert np.abs(dsp.soft_limit(ramp)).max() <= dsp.db_to_gain(-3.0) + 1e-6
    assert dsp.soft_limit(ramp).dtype == np.float32
