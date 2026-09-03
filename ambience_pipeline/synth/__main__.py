"""Audition CLI. Render a recipe to a wav and listen to it.

    python3 -m ambience_pipeline.synth list
    python3 -m ambience_pipeline.synth rain --seconds 60 --out rain.wav
    python3 -m ambience_pipeline.synth rain -p surface=leaves -p intensity=0.8
    python3 -m ambience_pipeline.synth fire --seconds 600 --seed 42

Free, offline, and the only way to judge any of this. Every default in
`recipes.py` is a starting guess; change one, listen, and the numbers that
survive belong in a calibration note.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import dsp
from .recipes import REGISTRY
from .render import render_to_wav


def cmd_list(args) -> int:
    for name in sorted(REGISTRY):
        r = REGISTRY[name]
        print(f"\n{name}  —  {r.summary}")
        for k, v in r.params.items():
            print(f"    {k:<12} {v!r}")
    print("\nOverride with -p name=value, repeatable.")
    return 0


def _parse_params(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"-p expects name=value, got {p!r}")
        k, _, v = p.partition("=")
        out[k.strip()] = v.strip()
    return out


def _loudness(path: Path) -> float | None:
    """Integrated LUFS via ffmpeg, if it is on PATH.

    `dsp.lufs` computes the same figure without a subprocess, and is what the
    trims and the tests use. This reports ffmpeg's instead, because it is the
    conforming gated meter and this is the number you would quote in an argument
    about levels. They agree within 0.3 LU across all five recipes; if they ever
    stop agreeing, ffmpeg is right.
    """
    if not shutil.which("ffmpeg"):
        return None
    proc = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    for line in reversed(proc.stderr.splitlines()):
        if "I:" in line and "LUFS" in line:
            try:
                return float(line.split("I:")[1].split("LUFS")[0])
            except (IndexError, ValueError):
                return None
    return None


def cmd_render(args) -> int:
    if args.recipe not in REGISTRY:
        raise SystemExit(
            f"no recipe {args.recipe!r}. Available: {', '.join(sorted(REGISTRY))}"
        )
    recipe = REGISTRY[args.recipe]
    overrides = _parse_params(args.param)
    layer = recipe.make(args.seed, overrides, sr=args.sr)

    out = Path(args.out or f"{args.recipe}.wav")
    shown = dict(recipe.params)
    shown.update(overrides)
    print(f"{recipe.name} — {recipe.summary}")
    print(f"  seed {args.seed}   {args.seconds:g}s @ {args.sr} Hz")
    print("  " + "  ".join(f"{k}={v}" for k, v in shown.items()))

    started = time.monotonic()
    last = [-1]

    def progress(done: int, total: int) -> None:
        pct = int(100 * done / total)
        if pct != last[0] and (pct % 10 == 0 or done == total):
            last[0] = pct
            print(f"\r  rendering {pct:3d}%", end="", flush=True)

    w = render_to_wav(
        layer, out, args.seconds, sr=args.sr,
        fade_in=args.fade_in, fade_out=args.fade_out,
        headroom_db=args.headroom,
        ceiling_db=args.ceiling,
        progress=None if args.quiet else progress,
    )
    if not args.quiet:
        print()

    took = time.monotonic() - started
    speed = args.seconds / took if took else float("inf")
    print(f"\n  wrote {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"  {took:.1f}s to render {args.seconds:g}s of audio — {speed:.0f}x realtime")
    print(f"  peak {dsp.gain_to_db(w.peak):6.1f} dBFS in the file "
          f"({dsp.gain_to_db(w.peak) + args.headroom:.1f} at mix level)")
    if w.clipped:
        print(f"  ! {w.clipped:,} samples clipped — raise --headroom")
    lu = _loudness(out)
    if lu is not None:
        print(f"  {lu + args.headroom:6.1f} LUFS for the layer "
              f"(target {recipe.params['gain_db']:.1f})")
    if args.headroom:
        print(f"  written {args.headroom:.0f} dB down so the peaks fit without "
              f"limiting — turn it up to listen")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="ambience_pipeline.synth", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="show every recipe and its parameters")
    ls.set_defaults(fn=cmd_list)

    for name in sorted(REGISTRY):
        r = sub.add_parser(name, help=REGISTRY[name].summary)
        r.add_argument("--seconds", type=float, default=60.0)
        r.add_argument("--seed", type=int, default=0)
        r.add_argument("--out", help="output wav (default: <recipe>.wav)")
        r.add_argument("--sr", type=int, default=dsp.SR)
        r.add_argument("-p", "--param", action="append", metavar="NAME=VALUE",
                       help="override a recipe parameter; repeatable")
        r.add_argument("--fade-in", type=float, default=2.0)
        r.add_argument("--fade-out", type=float, default=3.0)
        r.add_argument("--headroom", type=float, default=6.0,
                       help="constant attenuation so peaks fit unlimited "
                            "(default 6 dB); dynamics are untouched")
        r.add_argument("--ceiling", type=float, default=None,
                       help="soft-limit at this dBFS. Off by default: a limiter "
                            "flattens the gusts and swells these recipes exist "
                            "to produce.")
        r.add_argument("--quiet", action="store_true")
        r.set_defaults(fn=cmd_render, recipe=name)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
