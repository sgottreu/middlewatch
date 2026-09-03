"""Drive a layer through a wav file, one block at a time.

Separated from the CLI because the mixer will want exactly this loop, with a
stack of layers instead of one, and the block-carrying fade is the fiddly part
worth having in only one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import dsp
from .recipes import Layer
from .wav import WavWriter

BLOCK_S = 10.0


def render_to_wav(
    layer: Layer,
    path: str | Path,
    seconds: float,
    sr: int = dsp.SR,
    fade_in: float = 2.0,
    fade_out: float = 3.0,
    headroom_db: float = 6.0,
    ceiling_db: float | None = None,
    block_s: float = BLOCK_S,
    progress: Callable[[int, int], None] | None = None,
) -> WavWriter:
    """Render `seconds` of `layer` to a wav, holding one block in memory.

    Returns the closed writer, which carries the measured peak, RMS and clip
    count — the numbers worth reading before deciding a recipe is wrong.

    **Headroom, not limiting.** A layer at its mix level does not fit in a file
    on its own: wind and ocean carry a 17 dB crest factor, so at -18 LUFS their
    peaks land at 0 dBFS and above. Limiting that back is the wrong answer — the
    peaks *are* the gusts and the breaking waves, and a limiter flattens them
    into exactly the constant texture the recipe was rewritten to avoid. Every
    wind audition came back with its peak pinned at -3.5 dBFS and 2 dB of its
    swing gone.

    So the whole render is scaled down by a constant instead. Dynamics survive
    untouched, and the level the CLI reports is the layer's own, with the
    headroom added back. `ceiling_db` is still available for the mix, where
    limiting a sum is a reasonable thing to do.
    """
    total = int(seconds * sr)
    block = max(int(block_s * sr), 1)
    trim = dsp.db_to_gain(-headroom_db)
    done = 0

    with WavWriter(path, sr) as w:
        while done < total:
            n = min(block, total - done)
            x = layer.render(n) * trim
            # The fade is applied per block from absolute position, so dividing
            # the render differently cannot change the envelope.
            x = dsp.fade(x, done, total, fade_in, fade_out, sr)
            if ceiling_db is not None:
                x = dsp.soft_limit(x, ceiling_db)
            w.write(x)
            done += n
            if progress:
                progress(done, total)
    return w
