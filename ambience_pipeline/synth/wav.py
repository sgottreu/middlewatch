"""Streaming 16-bit PCM wav, written a block at a time.

Stdlib `wave`, no `soundfile`. ffmpeg is already a hard dependency of this repo
and does FLAC, encoding and loudness measurement, so the only format that has to
be written natively is the one it is cheapest to write — and doing it here means
one fewer C library that has to be installed correctly on two machines.

Blocks matter more than the format. A three-hour stereo render at float32 is
about 2 GB held in memory and an eight-hour one is 5.5; written as it is
generated, neither figure appears anywhere. That is the constraint the whole
synth module is shaped around.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


class WavWriter:
    """Append float32 stereo blocks in [-1, 1]. Clipped, not normalised.

    Normalising would need the whole render in hand, which is the thing being
    avoided. Clipping is announced instead: `clipped` counts the samples that hit
    the rail so the CLI can say so rather than let a too-hot recipe pass quietly.
    """

    def __init__(self, path: str | Path, sr: int, channels: int = 2):
        self.path = Path(path)
        self.sr = sr
        self.channels = channels
        self.frames = 0
        self.clipped = 0
        self.peak = 0.0
        self._sumsq = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._w = wave.open(str(self.path), "wb")
        self._w.setnchannels(channels)
        self._w.setsampwidth(2)
        self._w.setframerate(sr)

    def write(self, block: np.ndarray) -> None:
        x = np.asarray(block, dtype=np.float32)
        if x.ndim == 1:
            x = np.stack([x] * self.channels, axis=1)
        if x.shape[1] != self.channels:
            raise ValueError(f"expected {self.channels} channels, got {x.shape[1]}")

        self.peak = max(self.peak, float(np.max(np.abs(x))) if x.size else 0.0)
        self._sumsq += float(np.sum(np.square(x, dtype=np.float64)))
        self.clipped += int(np.count_nonzero(np.abs(x) > 1.0))
        self.frames += x.shape[0]

        # -32768 is representable and +32768 is not, so scale by 32767 and clip.
        self._w.writeframes(
            (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        )

    @property
    def rms(self) -> float:
        n = self.frames * self.channels
        return float(np.sqrt(self._sumsq / n)) if n else 0.0

    def close(self) -> None:
        self._w.close()

    def __enter__(self) -> "WavWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
