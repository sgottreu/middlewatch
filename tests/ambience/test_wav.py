"""synth/wav.py: streaming 16-bit stereo WavWriter."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from ambience_pipeline.synth.wav import WavWriter


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return params, data.reshape(-1, params.nchannels)


def test_header_is_16bit_stereo(tmp_path):
    p = tmp_path / "a.wav"
    with WavWriter(p, 22050) as w:
        w.write(np.zeros((100, 2), np.float32))
    params, data = read_wav(p)
    assert (params.nchannels, params.sampwidth, params.framerate, params.nframes) == (2, 2, 22050, 100)
    assert params.comptype == "NONE"
    assert not data.any()


def test_scaling_is_32767(tmp_path):
    p = tmp_path / "a.wav"
    with WavWriter(p, 8000) as w:
        w.write(np.array([[1.0, -1.0], [0.5, -0.5], [0.0, 0.0]], np.float32))
    _, d = read_wav(p)
    assert d[0].tolist() == [32767, -32767]
    assert d[1].tolist() == [int(0.5 * 32767), -int(0.5 * 32767)]
    assert d[2].tolist() == [0, 0]


def test_blocks_are_concatenated(tmp_path):
    x = np.linspace(-0.9, 0.9, 1000, dtype=np.float32)
    whole, split = tmp_path / "w.wav", tmp_path / "s.wav"
    with WavWriter(whole, 8000) as w:
        w.write(np.stack([x, x], 1))
    with WavWriter(split, 8000) as w:
        for a in range(0, 1000, 130):
            b = x[a:a + 130]
            w.write(np.stack([b, b], 1))
        assert w.frames == 1000
    assert whole.read_bytes() == split.read_bytes()


def test_clipping_counted_and_clamped(tmp_path):
    p = tmp_path / "a.wav"
    blk = np.array([[1.5, 0.0], [-2.0, 1.0], [1.0000001, 0.2]], np.float32)
    with WavWriter(p, 8000) as w:
        w.write(blk)
    assert w.clipped == 3          # 1.5, -2.0, 1.0000001 (exactly 1.0 is not clipped)
    assert w.peak == pytest.approx(2.0)
    _, d = read_wav(p)
    assert d.max() == 32767 and d.min() == -32767


def test_exact_full_scale_not_counted_as_clipped(tmp_path):
    with WavWriter(tmp_path / "a.wav", 8000) as w:
        w.write(np.array([[1.0, -1.0]], np.float32))
    assert w.clipped == 0 and w.peak == 1.0


def test_peak_accumulates_across_blocks_and_rms(tmp_path):
    with WavWriter(tmp_path / "a.wav", 8000) as w:
        w.write(np.full((10, 2), 0.25, np.float32))
        w.write(np.full((10, 2), 0.75, np.float32))
    assert w.peak == pytest.approx(0.75)
    assert w.rms == pytest.approx(np.sqrt((0.25 ** 2 + 0.75 ** 2) / 2))


def test_rms_zero_when_empty(tmp_path):
    with WavWriter(tmp_path / "a.wav", 8000) as w:
        pass
    assert w.rms == 0.0 and w.frames == 0 and w.peak == 0.0
    assert read_wav(tmp_path / "a.wav")[0].nframes == 0


def test_empty_block_ok(tmp_path):
    with WavWriter(tmp_path / "a.wav", 8000) as w:
        w.write(np.zeros((0, 2), np.float32))
    assert w.frames == 0


def test_mono_block_is_duplicated_to_stereo(tmp_path):
    p = tmp_path / "a.wav"
    with WavWriter(p, 8000) as w:
        w.write(np.array([0.5, -0.25], np.float32))
    _, d = read_wav(p)
    assert (d[:, 0] == d[:, 1]).all() and d.shape == (2, 2)


def test_wrong_channel_count_raises(tmp_path):
    with WavWriter(tmp_path / "a.wav", 8000) as w:
        with pytest.raises(ValueError, match="expected 2 channels, got 3"):
            w.write(np.zeros((4, 3), np.float32))


def test_accepts_float64_input(tmp_path):
    with WavWriter(tmp_path / "a.wav", 8000) as w:
        w.write(np.full((4, 2), 0.1, np.float64))
    assert w.frames == 4


def test_creates_parent_dirs(tmp_path):
    p = tmp_path / "x" / "y" / "a.wav"
    with WavWriter(p, 8000):
        pass
    assert p.exists()


def test_custom_channel_count(tmp_path):
    p = tmp_path / "a.wav"
    with WavWriter(p, 8000, channels=1) as w:
        w.write(np.array([0.5, 0.5], np.float32))
    assert read_wav(p)[0].nchannels == 1


def test_context_manager_closes_on_error(tmp_path):
    p = tmp_path / "a.wav"
    with pytest.raises(RuntimeError):
        with WavWriter(p, 8000) as w:
            w.write(np.zeros((8, 2), np.float32))
            raise RuntimeError
    assert read_wav(p)[0].nframes == 8   # header finalised, file readable
