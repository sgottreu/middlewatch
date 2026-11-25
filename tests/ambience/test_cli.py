"""ambience_pipeline.synth.__main__ CLI."""

from __future__ import annotations

import subprocess
import sys
import wave

import pytest

from ambience_pipeline.synth import __main__ as cli
from ambience_pipeline.synth.recipes import REGISTRY
from tests.conftest import REPO_ROOT, has_ffmpeg

RECIPES = sorted(REGISTRY)
FAST = ["--sr", "8000", "--quiet"]


@pytest.fixture(autouse=True)
def _no_ffmpeg_unless_marked(request, monkeypatch):
    """cli.main shells out to ffmpeg for a loudness line when it is installed;
    keep that to the tests marked needs_ffmpeg."""
    if request.node.get_closest_marker("needs_ffmpeg") is None:
        monkeypatch.setattr(cli, "_loudness", lambda path: None)


def wav_info(path):
    with wave.open(str(path)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


def test_registry_has_five_recipes():
    assert RECIPES == ["fire", "ocean", "rain", "room_tone", "wind"]


def test_list(capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    for name, r in REGISTRY.items():
        assert name in out and r.summary in out
        for k in r.params:
            assert k in out
    assert "-p name=value" in out


def test_no_command_is_an_error():
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


def test_unknown_recipe_is_argparse_error(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["thunder"])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_parse_params():
    assert cli._parse_params(["a=1", " b = x=y "]) == {"a": "1", "b": "x=y"}
    assert cli._parse_params(None) == {} and cli._parse_params([]) == {}


def test_parse_params_missing_equals():
    with pytest.raises(SystemExit, match="-p expects name=value"):
        cli._parse_params(["oops"])


def test_cmd_render_unknown_recipe_message():
    import argparse
    with pytest.raises(SystemExit, match="no recipe 'x'. Available: fire, ocean"):
        cli.cmd_render(argparse.Namespace(recipe="x"))


@pytest.mark.parametrize("name", RECIPES)
def test_render_small_each_recipe_8k(name, tmp_path, capsys):
    out = tmp_path / "o.wav"
    assert cli.main([name, "--seconds", "1", "--out", str(out), *FAST]) == 0
    assert wav_info(out) == (2, 2, 8000, 8000)
    text = capsys.readouterr().out
    assert name in text and "wrote" in text and "dBFS" in text


@pytest.mark.slow
@pytest.mark.parametrize("name", RECIPES)
def test_render_two_seconds_each_recipe(name, tmp_path, capsys):
    out = tmp_path / f"{name}.wav"
    assert cli.main([name, "--seconds", "2", "--out", str(out), "--quiet"]) == 0
    assert wav_info(out) == (2, 2, 48000, 96000)
    assert out.stat().st_size == 44 + 96000 * 4
    text = capsys.readouterr().out
    assert "clipped" not in text            # default headroom keeps every recipe clean
    assert "written 6 dB down" in text


def test_default_out_is_recipe_name_in_cwd(repo_root):
    cli.main(["room_tone", "--seconds", "0.5", *FAST])
    assert (repo_root / "room_tone.wav").exists()


def test_out_creates_parent_dirs(tmp_path):
    out = tmp_path / "a" / "b" / "x.wav"
    cli.main(["room_tone", "--seconds", "0.5", "--out", str(out), *FAST])
    assert out.exists()


def test_seed_determinism_and_difference(tmp_path):
    def run(seed, name):
        p = tmp_path / name
        cli.main(["rain", "--seconds", "0.5", "--seed", str(seed), "--out", str(p), *FAST])
        return p.read_bytes()
    assert run(1, "a.wav") == run(1, "b.wav")
    assert run(1, "a.wav") != run(2, "c.wav")


def test_param_override_changes_output_and_is_echoed(tmp_path, capsys):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    cli.main(["rain", "--seconds", "1", "--out", str(a), *FAST])
    cli.main(["rain", "--seconds", "1", "--out", str(b), "-p", "intensity=0.9",
              "-p", "surface=leaves", *FAST])
    assert a.read_bytes() != b.read_bytes()
    text = capsys.readouterr().out
    assert "intensity=0.9" in text and "surface=leaves" in text


def test_bad_param_name_is_keyerror(tmp_path):
    with pytest.raises(KeyError, match="no parameter 'bogus'"):
        cli.main(["rain", "--seconds", "0.5", "-p", "bogus=1", "--out", str(tmp_path / "x.wav"), *FAST])


def test_bad_param_value_errors(tmp_path):
    with pytest.raises(ValueError):
        cli.main(["rain", "--seconds", "0.5", "-p", "intensity=loud", "--out", str(tmp_path / "x.wav"), *FAST])


def test_bad_param_syntax_exits(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["rain", "-p", "intensity", "--out", str(tmp_path / "x.wav"), *FAST])


def test_failed_param_leaves_no_file(tmp_path):
    out = tmp_path / "x.wav"
    with pytest.raises(KeyError):
        cli.main(["rain", "-p", "bogus=1", "--out", str(out), *FAST])
    assert not out.exists()


def test_headroom_zero_hides_note_and_hot_headroom_reports_peak(tmp_path, capsys):
    cli.main(["fire", "--seconds", "1", "--headroom", "0", "--out", str(tmp_path / "a.wav"), *FAST])
    assert "written 0 dB" not in capsys.readouterr().out
    cli.main(["fire", "--seconds", "1", "--headroom", "12", "--out", str(tmp_path / "b.wav"), *FAST])
    assert "written 12 dB down" in capsys.readouterr().out


def test_clipping_reported_when_headroom_negative(tmp_path, capsys):
    cli.main(["fire", "--seconds", "1", "--headroom", "-40", "--out", str(tmp_path / "a.wav"), *FAST])
    assert "samples clipped — raise --headroom" in capsys.readouterr().out


def test_ceiling_option_accepted(tmp_path):
    cli.main(["wind", "--seconds", "1", "--ceiling", "-12", "--out", str(tmp_path / "a.wav"), *FAST])
    assert (tmp_path / "a.wav").exists()


def test_fade_options_zero(tmp_path):
    p = tmp_path / "a.wav"
    cli.main(["room_tone", "--seconds", "1", "--fade-in", "0", "--fade-out", "0", "--out", str(p), *FAST])
    with wave.open(str(p)) as w:
        first = w.readframes(400)
    assert first.strip(b"\x00")  # no fade-in: signal present from the start


def test_progress_printed_unless_quiet(tmp_path, capsys):
    cli.main(["room_tone", "--seconds", "1", "--sr", "8000", "--out", str(tmp_path / "a.wav")])
    assert "rendering 100%" in capsys.readouterr().out
    cli.main(["room_tone", "--seconds", "1", "--out", str(tmp_path / "b.wav"), *FAST])
    assert "rendering" not in capsys.readouterr().out


def test_loudness_none_without_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    assert cli._loudness(tmp_path / "x.wav") is None


@pytest.mark.slow
@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_loudness_via_ffmpeg_close_to_dsp_lufs(tmp_path):
    from ambience_pipeline.synth import dsp
    import numpy as np
    out = tmp_path / "r.wav"
    cli.main(["room_tone", "--seconds", "5", "--out", str(out), "--quiet"])
    lu = cli._loudness(out)
    with wave.open(str(out)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2) / 32767.0
    assert lu == pytest.approx(dsp.lufs(x), abs=1.0)


@pytest.mark.slow
@pytest.mark.needs_ffmpeg
@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")
def test_cli_reports_layer_lufs_line(tmp_path, capsys):
    cli.main(["room_tone", "--seconds", "3", "--out", str(tmp_path / "a.wav"), "--quiet"])
    assert "LUFS for the layer" in capsys.readouterr().out


def test_module_entrypoint_list():
    p = subprocess.run([sys.executable, "-m", "ambience_pipeline.synth", "list"],
                       cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "rain" in p.stdout
