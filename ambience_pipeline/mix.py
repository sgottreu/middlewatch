"""The block-streaming mixer: a concept's layers to one stereo 16-bit wav.

Procedural layers only (generated layers are blocked on ElevenLabs rights). Each
layer is a recipe from `synth.recipes.REGISTRY` with its `gain_db`, plus the
optional per-layer shaping `concept.json` allows: `pan`, `hpf_hz` / `lpf_hz`,
and an LFO `movement`. An **accent** is the same recipe gated by
Poisson-scheduled swells at `density_per_min` (scaled by the hours tier), so a
procedural accent is an occasional gust or flare rather than a constant layer.

The three synth invariants hold through the mix, and the tests check them:

* **Block invariance.** Filters carry their state, the LFO and the fade read
  the absolute sample index, and accent swells use `EnvelopeEvents`, which
  carries its tail. Any block size renders identical bytes.
* **Determinism.** Each layer's seed is derived from the concept seed and the
  layer's *id*, not its position — so adding or reordering a layer leaves the
  others' sound unchanged, and stacked layers of the same recipe decorrelate.
* **Streaming.** One block in memory at a time, so eight hours costs the same
  RAM as eight seconds.

Level: `gain_db` is each layer's approximate loudness in LUFS (see synth.md),
so the concept sets the *balance*. The overall level is then trimmed to the
middle of the -20 to -23 LUFS window from a probe render of the opening minute
(`dsp.lufs`, no subprocess), and the finished file is gated with ffmpeg's
`ebur128` — the conforming meter. Outside the window it raises, the way
`price()` raises on an unknown model rather than recording zero.
"""

from __future__ import annotations

import shutil
import subprocess
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy import signal

from .synth import dsp
from .synth.recipes import REGISTRY
from .synth.wav import WavWriter

BLOCK_S = 10.0
LUFS_WINDOW = (-23.0, -20.0)
TARGET_LUFS = -21.5
TRUE_PEAK_MAX = -3.0
CEILING_DB = -4.0          # soft limit on the sum; leaves room for inter-sample peaks
FADE_IN_S, FADE_OUT_S = 10.0, 20.0
PROBE_S = 60.0
MAX_TRIM_DB = 18.0
DEFAULT_EVENT_S = 8.0


class LoudnessError(RuntimeError):
    """The render landed outside the loudness window, or could not be measured."""


def layer_seed(seed: int, layer_id: str, salt: int = 0) -> int:
    ss = np.random.SeedSequence([int(seed), zlib.crc32(layer_id.encode()), salt])
    return int(ss.generate_state(1)[0])


def _swell(event_s: float, sr: int):
    """One accent: a smooth rise and a longer fall, about `event_s` long."""
    def make(rng: np.random.Generator) -> np.ndarray:
        n = max(int(event_s * float(rng.uniform(0.7, 1.3)) * sr), 8)
        t = np.linspace(0.0, 1.0, n)
        a = 0.3
        rise = np.clip(t / a, 0, 1)
        rise = rise * rise * (3 - 2 * rise)
        fall = np.clip((1 - t) / (1 - a), 0, 1)
        fall = fall * fall * (3 - 2 * fall)
        return (np.minimum(rise, fall) * float(rng.uniform(0.6, 1.0))).astype(np.float32)
    return make


class Voice:
    """One concept layer, ready to render: recipe, gain, shaping, gating."""

    def __init__(self, layer: dict[str, Any], seed: int, sr: int,
                 accent_scale: float = 1.0):
        self.id = layer["id"]
        self.role = layer["role"]
        rec = dict(layer["recipe"])
        kind = rec.pop("type")
        rec["gain_db"] = float(layer["gain_db"])
        self.src = REGISTRY[kind].make(layer_seed(seed, self.id), rec, sr=sr)
        self.sr = sr
        self.t = 0

        pan = float(layer.get("pan") or 0.0)
        self.lr = np.array([np.sqrt((1 - pan) / 2) * np.sqrt(2),
                            np.sqrt((1 + pan) / 2) * np.sqrt(2)], dtype=np.float32)

        sos = []
        if layer.get("hpf_hz"):
            sos.append(dsp.highpass(float(layer["hpf_hz"]), 2, sr))
        if layer.get("lpf_hz"):
            sos.append(dsp.lowpass(float(layer["lpf_hz"]), 4, sr))
        self.sos = np.vstack(sos) if sos else None
        self.zi = (np.zeros((self.sos.shape[0], 2, 2)) if self.sos is not None else None)

        mv = layer.get("movement") or None
        self.lfo = ((float(mv["depth_db"]), float(mv["period_s"]))
                    if mv and float(mv.get("depth_db", 0)) > 0 else None)
        # A fixed phase per layer, so two LFO'd layers don't breathe together.
        self.lfo_phase = (layer_seed(seed, self.id, 2) % 10_000) / 10_000 * 2 * np.pi

        self.gate = None
        if self.role == "accent":
            rate = float(layer["density_per_min"]) * accent_scale / 60.0
            rng = np.random.default_rng(layer_seed(seed, self.id, 1))
            ev = float(layer.get("event_s") or DEFAULT_EVENT_S)
            self.gate = dsp.EnvelopeEvents(rng, rate, _swell(ev, sr), sr=sr)

    def render(self, n: int) -> np.ndarray:
        x = self.src.render(n).astype(np.float64)
        if self.sos is not None:
            for ch in range(2):
                x[:, ch], self.zi[:, ch, :] = signal.sosfilt(self.sos, x[:, ch],
                                                              zi=self.zi[:, ch, :])
        x *= self.lr
        if self.lfo:
            depth, period = self.lfo
            t = np.arange(self.t, self.t + n, dtype=np.float64) / self.sr
            db = depth * np.sin(2 * np.pi * t / period + self.lfo_phase)
            x *= (10.0 ** (db / 20.0))[:, None]
        if self.gate is not None:
            x *= np.clip(self.gate.render(n), 0.0, 1.0)[:, None]
        self.t += n
        return x


def voices(concept: dict, seed: int, sr: int = dsp.SR,
           accent_scale: float = 1.0) -> list[Voice]:
    return [Voice(L, seed, sr, accent_scale) for L in concept["layers"]]


def _sum(vs: list[Voice], n: int) -> np.ndarray:
    out = np.zeros((n, 2), dtype=np.float64)
    for v in vs:
        out += v.render(n)
    return out


def probe_lufs(concept: dict, seed: int, seconds: float, sr: int = dsp.SR,
               accent_scale: float = 1.0, block_s: float = BLOCK_S) -> float:
    """Loudness of the opening of the mix at unity, before any trim or fade."""
    vs = voices(concept, seed, sr, accent_scale)
    total = int(min(seconds, PROBE_S) * sr)
    blocks, done = [], 0
    while done < total:
        n = min(max(int(block_s * sr), 1), total - done)
        blocks.append(_sum(vs, n))
        done += n
    return float(dsp.lufs(np.concatenate(blocks), sr))


@dataclass
class MixResult:
    path: Path
    seconds: float
    trim_db: float
    probe_lufs: float
    peak_dbfs: float
    clipped: int
    lufs: float | None = None
    true_peak: float | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "seconds": self.seconds,
                "trim_db": round(self.trim_db, 2), "probe_lufs": round(self.probe_lufs, 2),
                "peak_dbfs": round(self.peak_dbfs, 2), "clipped": self.clipped,
                "lufs": self.lufs, "true_peak": self.true_peak,
                "warnings": self.warnings}


def mix_to_wav(concept: dict, path: str | Path, seconds: float, seed: int,
               sr: int = dsp.SR, accent_scale: float = 1.0,
               normalize: bool = True, fade_in: float | None = None,
               fade_out: float | None = None, block_s: float = BLOCK_S,
               ceiling_db: float | None = CEILING_DB,
               progress: Callable[[int, int], None] | None = None) -> MixResult:
    """Render the concept to `path`. Deterministic for (concept, seed, sr).

    `normalize` trims the sum to TARGET_LUFS from a probe of the first minute;
    off, the concept's own gains decide the level and the gate is the judge.
    Fades default to 10 s in / 20 s out, shortened to a tenth of a short
    preview each so the fades do not dominate what the gate measures.
    """
    total = int(seconds * sr)
    if total <= 0:
        raise ValueError("seconds must be positive")
    fi = FADE_IN_S if fade_in is None else fade_in
    fo = FADE_OUT_S if fade_out is None else fade_out
    fi, fo = min(fi, seconds * 0.1), min(fo, seconds * 0.1)

    probed = probe_lufs(concept, seed, seconds, sr, accent_scale, block_s)
    trim = 0.0
    if normalize:
        trim = float(np.clip(TARGET_LUFS - probed, -MAX_TRIM_DB, MAX_TRIM_DB))
    g = dsp.db_to_gain(trim)

    vs = voices(concept, seed, sr, accent_scale)
    block = max(int(block_s * sr), 1)
    done = 0
    with WavWriter(path, sr) as w:
        while done < total:
            n = min(block, total - done)
            x = (_sum(vs, n) * g).astype(np.float32)
            x = dsp.fade(x, done, total, fi, fo, sr)
            if ceiling_db is not None:
                x = dsp.soft_limit(x, ceiling_db)
            w.write(x)
            done += n
            if progress:
                progress(done, total)
    res = MixResult(Path(path), seconds, trim, probed,
                    dsp.gain_to_db(w.peak) if w.peak > 0 else -120.0, w.clipped)
    if normalize and abs(TARGET_LUFS - probed) > MAX_TRIM_DB:
        res.warnings.append(f"trim capped at {MAX_TRIM_DB:g} dB; the concept's gains "
                            f"put the mix at {probed:.1f} LUFS")
    return res


# --------------------------------------------------------------------------- #
# The loudness gate
# --------------------------------------------------------------------------- #


def measure(path: str | Path) -> dict[str, float]:
    """Integrated loudness and true peak from ffmpeg's ebur128 summary."""
    if not shutil.which("ffmpeg"):
        raise LoudnessError("ffmpeg is not on PATH, so the loudness gate cannot run. "
                            "Install ffmpeg; the gate is not skipped silently.")
    proc = subprocess.run(
        ["ffmpeg", "-nostats", "-hide_banner", "-i", str(path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True)
    lines = proc.stderr.splitlines()
    try:
        start = max(i for i, l in enumerate(lines) if "Summary:" in l)
    except ValueError:
        raise LoudnessError(f"ffmpeg ebur128 produced no summary for {path}:\n"
                            + "\n".join(lines[-5:])) from None
    out: dict[str, float] = {}
    section = ""
    for l in lines[start:]:
        s = l.strip()
        if s.startswith("Integrated loudness"):
            section = "I"
        elif s.startswith("True peak"):
            section = "TP"
        elif s.startswith("I:") and "LUFS" in s and section == "I":
            out["lufs"] = float(s.split()[1])
        elif s.startswith("Peak:") and section == "TP":
            out["true_peak"] = float(s.split()[1])
    if "lufs" not in out:
        raise LoudnessError(f"could not read integrated loudness for {path}")
    return out


def gate(res: MixResult, window: tuple[float, float] = LUFS_WINDOW) -> MixResult:
    """Measure the file and raise LoudnessError if it is outside the window."""
    m = measure(res.path)
    res.lufs = m["lufs"]
    res.true_peak = m.get("true_peak")
    if res.true_peak is not None and res.true_peak > TRUE_PEAK_MAX:
        res.warnings.append(f"true peak {res.true_peak:.1f} dBTP is above "
                            f"{TRUE_PEAK_MAX:g} dBTP")
    lo, hi = window
    if not lo <= res.lufs <= hi:
        raise LoudnessError(
            f"loudness gate failed: {res.lufs:.1f} LUFS is outside {lo:g} to {hi:g} "
            f"LUFS (probe {res.probe_lufs:.1f}, trim {res.trim_db:+.1f} dB). "
            "Change the layer gains, or check the trim was not capped.")
    return res
