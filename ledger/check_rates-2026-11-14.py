#!/usr/bin/env python3
"""Check provider pricing pages against rates.json and flag differences.

Session draft, 2026-11-14 -- NOT wired to the real rates.json layout yet.
The real file nests rates under providers.<provider>.models.<model> and
records a single top-level rates_verified date. See clean-up.md section AA.
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

RATES_PATH = Path(__file__).with_name("rates.json")
UA = "Mozilla/5.0 (middlewatch rate check)"

# Each check: provider, rates.json key path, page URL, regex with one
# capture group for the number. Tune patterns against the live pages.
CHECKS = [
    {
        "provider": "elevenlabs",
        "key": ["elevenlabs", "sound_effects", "usd_per_minute"],
        "url": "https://elevenlabs.io/pricing/api",
        "pattern": r"Sound Effects\s*\$([0-9.]+)\s*per minute",
    },
    {
        "provider": "gemini",
        "key": ["gemini", "image", "usd_per_image"],
        "url": "https://ai.google.dev/gemini-api/docs/pricing",
        "pattern": r"\$([0-9.]+)\s*per image",
    },
    {
        "provider": "higgsfield",
        "key": ["higgsfield", "image", "usd_per_image"],
        "url": "https://higgsfield.ai/pricing",
        "pattern": r"\$([0-9.]+)\s*per image",
    },
    {
        "provider": "higgsfield",
        "key": ["higgsfield", "video", "usd_per_second"],
        "url": "https://higgsfield.ai/pricing",
        "pattern": r"\$([0-9.]+)\s*per second",
    },
]


def fetch(url, cache):
    if url not in cache:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        text = re.sub(r"<[^>]+>", " ", html)
        cache[url] = re.sub(r"\s+", " ", text)
    return cache[url]


def get_path(data, keys):
    for k in keys:
        if not isinstance(data, dict) or k not in data:
            return None
        data = data[k]
    return data


def set_path(data, keys, value):
    for k in keys[:-1]:
        data = data.setdefault(k, {})
    data[keys[-1]] = value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="confirm and write changes")
    args = ap.parse_args()

    rates = json.loads(RATES_PATH.read_text()) if RATES_PATH.exists() else {}
    cache, changes = {}, []

    for c in CHECKS:
        label = ".".join(c["key"])
        try:
            text = fetch(c["url"], cache)
        except Exception as e:
            print(f"[error]     {label}: could not fetch ({e})")
            continue
        m = re.search(c["pattern"], text, re.IGNORECASE)
        if not m:
            print(f"[not found] {label}: pattern missed; check {c['url']}")
            continue
        found = float(m.group(1))
        current = get_path(rates, c["key"])
        if current == found:
            print(f"[ok]        {label}: {found}")
        else:
            print(f"[changed]   {label}: rates.json={current} page={found}")
            changes.append((c, found))

    if not changes or not args.apply:
        return 1 if changes else 0

    for c, found in changes:
        label = ".".join(c["key"])
        if input(f"Apply {label} = {found}? [y/N] ").strip().lower() == "y":
            set_path(rates, c["key"], found)
            set_path(rates, [c["provider"], "_checked"], date.today().isoformat())
    RATES_PATH.write_text(json.dumps(rates, indent=2) + "\n")
    print("rates.json updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
