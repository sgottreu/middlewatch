"""Procedural audio for the ambience pipeline.

No credentials, no network, no cost. Rain, wind, fire, surf and room tone are
generated rather than sampled, so nothing here loops and nothing here is licensed
by anybody.

    python3 -m ambience_pipeline.synth list
    python3 -m ambience_pipeline.synth rain --seconds 60 --out rain.wav
    python3 -m ambience_pipeline.synth fire -p crackle=1.8 -p roar=0.4 --out fire.wav

The parameters are guesses until you have listened to them. That is what the
CLI is for.
"""

from .dsp import SR, db_to_gain, peak_db, rms_db
from .recipes import REGISTRY, Layer, Recipe
from .render import render_to_wav
from .wav import WavWriter

__all__ = [
    "SR", "REGISTRY", "Layer", "Recipe", "WavWriter",
    "render_to_wav", "db_to_gain", "peak_db", "rms_db",
]
