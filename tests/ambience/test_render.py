"""synth/render.py: block rendering, fades, headroom, ceiling."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from ambience_pipeline.synth import dsp
from ambience_pipeline.synth.render import BLOCK_S, render_to_wav

SR = 1000


def load(path):
    with wave.open(str(path), "rb") as w:
        d = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2)
    return d.astype(np.float64) / 32767.0


def test_default_block_length():
    assert BLOCK_S == 10.0


def test_length_and_returns_closed_writer(tmp_path, const_layer):
    w = render_to_wav(const_layer(0.5), tmp_path / "a.wav", 2.5, sr=SR,
                      fade_in=0, fade_out=0, headroom_db=0)
    assert w.frames == 2500 and len(load(tmp_path / "a.wav")) == 2500
    assert w.peak == pytest.approx(0.5)


def test_blocks_requested_in_block_sized_chunks(tmp_path, const_layer):
    layer = const_layer()
    render_to_wav(layer, tmp_path / "a.wav", 2.5, sr=SR, block_s=1.0)
    assert layer.calls == [1000, 1000, 500]


def test_zero_seconds(tmp_path, const_layer):
    layer = const_layer()
    w = render_to_wav(layer, tmp_path / "a.wav", 0, sr=SR)
    assert w.frames == 0 and layer.calls == []


def test_tiny_block_s_clamped_to_one_sample(tmp_path, const_layer):
    layer = const_layer()
    render_to_wav(layer, tmp_path / "a.wav", 0.003, sr=SR, block_s=0.0, fade_in=0, fade_out=0)
    assert layer.calls == [1, 1, 1]


def test_headroom_is_constant_attenuation(tmp_path, const_layer):
    render_to_wav(const_layer(0.5), tmp_path / "a.wav", 1, sr=SR,
                  fade_in=0, fade_out=0, headroom_db=6.0)
    d = load(tmp_path / "a.wav")
    assert d.max() == pytest.approx(0.5 * dsp.db_to_gain(-6.0), abs=1e-3)
    assert d.min() == pytest.approx(d.max(), abs=1e-3)


def test_headroom_default_is_6db(tmp_path, const_layer):
    render_to_wav(const_layer(0.5), tmp_path / "a.wav", 1, sr=SR, fade_in=0, fade_out=0)
    assert load(tmp_path / "a.wav").max() == pytest.approx(0.25, abs=1e-3)


def test_headroom_prevents_clipping_of_hot_layer(tmp_path, const_layer):
    w = render_to_wav(const_layer(1.5), tmp_path / "a.wav", 1, sr=SR,
                      fade_in=0, fade_out=0, headroom_db=6.0)
    assert w.clipped == 0
    w2 = render_to_wav(const_layer(1.5), tmp_path / "b.wav", 1, sr=SR,
                       fade_in=0, fade_out=0, headroom_db=0.0)
    assert w2.clipped == 2000   # 1000 frames x 2 ch


def test_negative_headroom_boosts(tmp_path, const_layer):
    render_to_wav(const_layer(0.1), tmp_path / "a.wav", 1, sr=SR,
                  fade_in=0, fade_out=0, headroom_db=-20.0)
    assert load(tmp_path / "a.wav").max() == pytest.approx(1.0, abs=1e-3)


def test_fades_shape(tmp_path, const_layer):
    render_to_wav(const_layer(0.8), tmp_path / "a.wav", 10, sr=SR,
                  fade_in=2.0, fade_out=3.0, headroom_db=0)
    d = load(tmp_path / "a.wav")[:, 0]
    assert d[0] == pytest.approx(0, abs=1e-3)
    assert d[1000] == pytest.approx(0.4, abs=2e-3)          # halfway up the 2s fade-in
    assert d[2000:7000] == pytest.approx(np.full(5000, 0.8), abs=1e-3)
    assert d[8500] == pytest.approx(0.4, abs=2e-3)          # halfway down the 3s fade-out
    assert d[-1] == pytest.approx(0, abs=2e-3)
    assert np.all(np.diff(d[:2000]) >= -1e-3) and np.all(np.diff(d[7000:]) <= 1e-3)


def test_fade_independent_of_block_size(tmp_path, const_layer):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    render_to_wav(const_layer(0.8), a, 6, sr=SR, block_s=10.0)
    render_to_wav(const_layer(0.8), b, 6, sr=SR, block_s=0.7)
    assert a.read_bytes() == b.read_bytes()


def test_fade_longer_than_render_min_of_both(tmp_path, const_layer):
    render_to_wav(const_layer(1.0), tmp_path / "a.wav", 1, sr=SR, fade_in=5, fade_out=5, headroom_db=0)
    d = load(tmp_path / "a.wav")[:, 0]
    assert d.max() < 0.25


def test_ceiling_soft_limits(tmp_path, const_layer):
    w = render_to_wav(const_layer(5.0), tmp_path / "a.wav", 1, sr=SR,
                      fade_in=0, fade_out=0, headroom_db=0, ceiling_db=-3.0)
    c = dsp.db_to_gain(-3.0)
    assert w.peak <= c + 1e-6 and w.peak > 0.99 * c
    assert w.clipped == 0


def test_no_ceiling_by_default_leaves_dynamics(tmp_path, const_layer):
    w = render_to_wav(const_layer(0.9), tmp_path / "a.wav", 1, sr=SR,
                      fade_in=0, fade_out=0, headroom_db=0)
    assert w.peak == pytest.approx(0.9)


def test_progress_callback(tmp_path, const_layer):
    seen = []
    render_to_wav(const_layer(), tmp_path / "a.wav", 2.5, sr=SR, block_s=1.0,
                  progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1000, 2500), (2000, 2500), (2500, 2500)]


def test_sr_written_to_header(tmp_path, const_layer):
    render_to_wav(const_layer(), tmp_path / "a.wav", 0.1, sr=SR)
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert w.getframerate() == SR


def test_real_recipe_short_render_deterministic(tmp_path):
    from ambience_pipeline.synth.recipes import REGISTRY
    out = []
    for i in range(2):
        layer = REGISTRY["room_tone"].make(3, sr=8000)
        p = tmp_path / f"{i}.wav"
        render_to_wav(layer, p, 1, sr=8000, fade_in=0.1, fade_out=0.1)
        out.append(p.read_bytes())
    assert out[0] == out[1]
