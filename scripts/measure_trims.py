"""Measure TRIM_DB in ambience_pipeline/synth/recipes.py.

`gain_db` on a recipe names the layer's approximate integrated loudness in LUFS,
so that a mix can be written down before it is rendered. That only holds if each
recipe carries a constant correcting for however many components it happens to
sum. This measures those constants.

Loudness, not RMS. K-weighting favours the highs, so bright content measures
hotter than its RMS: rain at -15 dBFS RMS is -8.7 LUFS while fire at -20.7 is
-21.3. Trimmed by RMS, two layers whose gain_db differed by 6 dB arrived 12.6 dB
apart — which would make every mix written in the concept file wrong.

    python3 scripts/measure_trims.py            # report
    python3 scripts/measure_trims.py --write    # report and patch recipes.py

Run it after changing a recipe's internal balance. Averaged over several seeds,
because one seed of a sparse event layer is not a level.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ambience_pipeline.synth import dsp                      # noqa: E402
from ambience_pipeline.synth.recipes import REGISTRY, TRIM_DB  # noqa: E402

SECONDS = 20.0
SEEDS = (0, 1, 2, 3, 4)


def measure(name: str) -> float:
    """dB by which this recipe misses the loudness its gain_db names."""
    r = REGISTRY[name]
    target = float(r.params["gain_db"])
    n = int(SECONDS * dsp.SR)
    # Average in the energy domain, not the dB domain: the mean of two loudness
    # figures is not the loudness of the two together.
    energy = [10.0 ** (dsp.lufs(r.make(seed).render(n)) / 10.0) for seed in SEEDS]
    got = 10.0 * np.log10(float(np.mean(energy)))
    return target - got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="patch recipes.py in place")
    args = ap.parse_args()

    print(f"{SECONDS:g}s per seed, {len(SEEDS)} seeds\n")
    print(f"  {'recipe':<11} {'target':>8} {'current':>9} {'trim':>8}")
    trims = {}
    for name in sorted(REGISTRY):
        delta = measure(name)
        new = round(TRIM_DB.get(name, 0.0) + delta, 1)
        trims[name] = new
        print(f"  {name:<11} {REGISTRY[name].params['gain_db']:>8.1f} "
              f"{REGISTRY[name].params['gain_db'] - delta:>9.1f} {new:>8.1f}")

    if not args.write:
        print("\nRe-run with --write to patch TRIM_DB.")
        return 0

    path = Path(__file__).resolve().parent.parent / "ambience_pipeline/synth/recipes.py"
    body = "\n".join(f'    "{k}": {v},' for k, v in sorted(trims.items()))
    src = path.read_text()
    patched, n = re.subn(r"TRIM_DB = \{.*?\n\}", "TRIM_DB = {\n" + body + "\n}",
                         src, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("could not find the TRIM_DB block in recipes.py")
    path.write_text(patched)
    print(f"\nPatched {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
