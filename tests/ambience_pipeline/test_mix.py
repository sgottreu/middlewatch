"""The mixer: determinism, block invariance, streaming, levels, the gate."""
from __future__ import annotations

import pytest

from ambience_pipeline import mix
from ambience_pipeline.synth import dsp
from tests.conftest import has_ffmpeg

from .conftest import layer, read_wav

SR = dsp.SR


def test_same_seed_same_bytes_different_seed_different(tmp_path, concept):
    a = mix.mix_to_wav(concept, tmp_path / "a.wav", 2.0, seed=1)
    b = mix.mix_to_wav(concept, tmp_path / "b.wav", 2.0, seed=1)
    c = mix.mix_to_wav(concept, tmp_path / "c.wav", 2.0, seed=2)
    assert a.path.read_bytes() == b.path.read_bytes()
    assert a.path.read_bytes() != c.path.read_bytes()


@pytest.mark.parametrize("block_s", [0.37, 1.0, 3.3])
def test_block_size_is_inaudible(tmp_path, block_s):
    c = {"layers": [layer("rain", "bed", "rain", -20.0, pan=-0.3, hpf_hz=120, lpf_hz=7000,
                          movement={"type": "lfo", "depth_db": 3, "period_s": 5}),
                    layer("fire", "accent", "fire", -24.0, density_per_min=6, event_s=1.5)]}
    ref = mix.mix_to_wav(c, tmp_path / "ref.wav", 3.0, seed=5, block_s=10.0)
    got = mix.mix_to_wav(c, tmp_path / "got.wav", 3.0, seed=5, block_s=block_s)
    assert got.path.read_bytes() == ref.path.read_bytes()


def test_streams_one_block_at_a_time(tmp_path, concept, monkeypatch):
    asked = []
    real = mix.Voice.render

    def spy(self, n):
        asked.append(n)
        return real(self, n)
    monkeypatch.setattr(mix.Voice, "render", spy)
    mix.mix_to_wav(concept, tmp_path / "s.wav", 3.0, seed=1, block_s=0.5)
    assert max(asked) <= int(0.5 * dsp.SR)
    # The probe is a minute at most, regardless of how long the render is.
    assert mix.PROBE_S <= 60


def test_layer_seed_follows_the_id_not_the_position(concept):
    alone = mix.voices(concept, 3)[1].render(4000)
    shifted = {"layers": [layer("extra", "bed", "rain", -30.0)] + concept["layers"]}
    again = mix.voices(shifted, 3)[2].render(4000)
    assert (alone == again).all()
    # Two layers of one recipe are decorrelated.
    twin = {"layers": [layer("a", "bed", "room_tone"), layer("b", "bed", "room_tone")]}
    va, vb = mix.voices(twin, 3)
    assert not (va.render(2000) == vb.render(2000)).all()


def test_normalised_mix_lands_on_target(tmp_path, concept):
    r = mix.mix_to_wav(concept, tmp_path / "n.wav", 6.0, seed=1)
    x, sr = read_wav(r.path)
    assert abs(dsp.lufs(x, sr) - mix.TARGET_LUFS) < 1.5
    assert r.clipped == 0 and r.peak_dbfs <= mix.CEILING_DB + 0.01


def test_gains_set_the_balance(concept):
    quiet = {"layers": [dict(L, gain_db=L["gain_db"] - 6) for L in concept["layers"]]}
    a = mix.probe_lufs(concept, 1, 3.0)
    b = mix.probe_lufs(quiet, 1, 3.0)
    assert 5.0 < a - b < 7.0


def test_fades_start_and_end_silent(tmp_path, concept):
    r = mix.mix_to_wav(concept, tmp_path / "f.wav", 4.0, seed=1)
    x, _ = read_wav(r.path)
    assert abs(x[0]).max() < 1e-3 and abs(x[-1]).max() < 1e-2


def test_sparse_accent_is_mostly_silent(tmp_path):
    c = {"layers": [layer("bed", "bed", "room_tone", -40.0),
                    layer("gust", "accent", "rain", -20.0, density_per_min=0.01)]}
    v = mix.voices(c, 9)[1]
    assert abs(v.render(dsp.SR * 2)).max() < 1e-6


@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_gate_passes_and_reports(tmp_path, concept):
    r = mix.gate(mix.mix_to_wav(concept, tmp_path / "g.wav", 6.0, seed=1))
    assert mix.LUFS_WINDOW[0] <= r.lufs <= mix.LUFS_WINDOW[1]
    assert r.true_peak is not None and r.true_peak < mix.TRUE_PEAK_MAX


@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_gate_fails_loudly_outside_the_window(tmp_path, concept):
    quiet = {"layers": [dict(L, gain_db=-48.0) for L in concept["layers"]]}
    r = mix.mix_to_wav(quiet, tmp_path / "q.wav", 4.0, seed=1, normalize=False)
    with pytest.raises(mix.LoudnessError, match="outside -23 to -20"):
        mix.gate(r)


def test_gate_without_ffmpeg_refuses(tmp_path, concept, monkeypatch):
    r = mix.mix_to_wav(concept, tmp_path / "x.wav", 1.0, seed=1)
    monkeypatch.setattr(mix.shutil, "which", lambda name: None)
    with pytest.raises(mix.LoudnessError, match="not skipped"):
        mix.gate(r)


@pytest.mark.slow
@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_ninety_seconds_with_every_recipe(tmp_path):
    c = {"layers": [layer("rain", "bed", "rain", -20.0), layer("surf", "bed", "ocean", -22.0),
                    layer("wind", "texture", "wind", -28.0),
                    layer("fire", "accent", "fire", -26.0, density_per_min=1.0),
                    layer("room", "tonal", "room_tone", -34.0)]}
    r = mix.gate(mix.mix_to_wav(c, tmp_path / "long.wav", 90.0, seed=4))
    assert r.path.stat().st_size == 90 * dsp.SR * 4 + 44
