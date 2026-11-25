from __future__ import annotations

import numpy as np
import pytest

from ambience_pipeline.synth.recipes import Layer


class ConstLayer(Layer):
    """A layer emitting a constant stereo value, for exact numeric checks."""

    def __init__(self, value=0.5):
        super().__init__(lambda n: np.full((n, 2), value, dtype=np.float32))
        self.calls = []

    def render(self, n):
        self.calls.append(n)
        return super().render(n)


@pytest.fixture
def const_layer():
    return ConstLayer
