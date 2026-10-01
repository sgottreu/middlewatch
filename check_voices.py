#!/usr/bin/env python3
"""Compare the repo's elevenlabs.voice_library against the ElevenLabs account,
and optionally A/B each voice on eleven_multilingual_v2 vs eleven_v4.

Stdlib only (PyYAML used if the config is YAML).

Usage:
  export ELEVENLABS_API_KEY=...
  python3 check_voices.py --config /srv/middlewatch/<config file>
  python3 check_voices.py --config ... --sample          # renders audio, costs credits
  python3 check_voices.py --config ... --sample --text "Your own test line."

Samples land in ./voice-samples/ — scratch audio, don't commit it.
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.elevenlabs.io"
DEFAULT_LINE = ("The watch bell rang twice, and somewhere below the lantern "
                "swung against the beam.")
MODELS_TO_COMPARE = ["eleven_multilingual_v2", "eleven_v4"]


def call(method, path, key, params=None, body=None, raw=False):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
            return resp.status, payload if raw else json.loads(payload)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def load_config(path):
    p = pathlib.Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml  # PyYAML, already in the pipeline venv if config is YAML
        return yaml.safe_load(text)
    if p.suffix == ".toml":
        import tomllib
        return tomllib.loads(text)
    return json.loads(text)


def voice_library(cfg):
    lib = (cfg.get("elevenlabs") or {}).get("voice_library")
    if lib is None:
        sys.exit("No elevenlabs.voice_library found in that config.")
    items = lib.items() if isinstance(lib, dict) else ((v.get("name"), v) for v in lib)
    out = {}
    for name, v in items:
        out[name] = v if isinstance(v, str) else (v.get("voice_id") or v.get("id"))
    return out


def account_voices(key):
    voices, token = [], None
    while True:
        params = {"page_size": 100}
        if token:
            params["next_page_token"] = token
        status, data = call("GET", "/v2/voices", key, params)
        if status != 200:
            sys.exit(f"/v2/voices failed: {status} {data}")
        voices += data.get("voices", [])
        if not data.get("has_more"):
            return voices
        token = data.get("next_page_token")


def name_matches(name, voices):
    n = name.lower()
    return [v for v in voices if v["name"].lower().split(" - ")[0].strip() == n
            or v["name"].lower().startswith(n)]


def library_matches(name, key):
    status, data = call("GET", "/v1/shared-voices", key,
                        {"search": name, "page_size": 5})
    if status != 200:
        return []
    return [v for v in data.get("voices", [])
            if v.get("name", "").lower().startswith(name.lower())]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--key-env", default="ELEVENLABS_API_KEY")
    ap.add_argument("--sample", action="store_true",
                    help="render the test line on v2 and v4 per voice (costs credits)")
    ap.add_argument("--text", default=DEFAULT_LINE)
    ap.add_argument("--out", default="voice-samples")
    args = ap.parse_args()

    key = os.environ.get(args.key_env)
    if not key:
        sys.exit(f"Set {args.key_env} first.")

    configured = voice_library(load_config(args.config))
    voices = account_voices(key)
    by_id = {v["voice_id"]: v for v in voices}

    status, models = call("GET", "/v1/models", key)
    model_ids = {m["model_id"] for m in models} if status == 200 else set()

    print(f"\nAccount has {len(voices)} voices; repo configures {len(configured)}.\n")
    missing, ok = [], []
    for name, vid in configured.items():
        acct = by_id.get(vid)
        if acct:
            ok.append((name, vid))
            hq = acct.get("high_quality_base_model_ids")
            v4 = "" if hq is None else ("  v4-optimized" if "eleven_v4" in hq else "  (not listed as v4-optimized)")
            print(f"  OK       {name:<18} {vid}  -> {acct['name']} [{acct.get('category')}]{v4}")
        else:
            missing.append((name, vid))
            print(f"  MISSING  {name:<18} {vid}")

    for name, vid in missing:
        print(f"\n  {name}: not in account.")
        local = name_matches(name, voices)
        for v in local:
            print(f"    in account under a different id: {v['voice_id']}  {v['name']}")
        if not local:
            lib = library_matches(name, key)
            for v in lib:
                print(f"    in Voice Library (add to My Voices first): "
                      f"{v['voice_id']}  {v['name']}  owner={v.get('public_owner_id')}")
            if not lib:
                print("    no match in account or Voice Library — recast.")

    unused = [v for v in voices if v["voice_id"] not in set(configured.values())]
    if unused:
        print(f"\nIn the account but not in the repo ({len(unused)}):")
        for v in unused:
            print(f"  {v['voice_id']}  {v['name']} [{v.get('category')}]")

    print("\nModels:")
    for m in MODELS_TO_COMPARE:
        print(f"  {m:<24} {'available' if m in model_ids else 'NOT available to this key'}")

    if args.sample and ok:
        out = pathlib.Path(args.out)
        out.mkdir(exist_ok=True)
        print(f"\nRendering {len(ok)} voices x {len(MODELS_TO_COMPARE)} models "
              f"({len(args.text) * len(ok) * len(MODELS_TO_COMPARE)} chars) into {out}/")
        for name, vid in ok:
            for model in MODELS_TO_COMPARE:
                if model not in model_ids:
                    continue
                status, audio = call(
                    "POST", f"/v1/text-to-speech/{vid}", key,
                    {"output_format": "mp3_44100_128"},
                    {"text": args.text, "model_id": model}, raw=True)
                dest = out / f"{name.lower().replace(' ', '-')}__{model}.mp3"
                if status == 200:
                    dest.write_bytes(audio)
                    print(f"  wrote {dest}")
                else:
                    print(f"  FAILED {name} on {model}: {status} {audio[:200]}")


if __name__ == "__main__":
    main()