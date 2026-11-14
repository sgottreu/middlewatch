# Middle Watch — Ambience Pipeline (session export, 2026-11-14)

> **Session export, not yet merged.** Exported from the claude.ai doc
> "Middle Watch — Ambience Pipeline" (https://claude.ai/artifact/3R64x7TMNHet2N3Ys663E2).
> Written in a voice session that did not read [pipeline-design.md](pipeline-design.md)
> first, so parts of it conflict with that design. See clean-up.md section AA
> for what to merge and what to drop. pipeline-design.md remains the source of truth
> until that review is done.

How ambience tracks are designed, generated, mixed and rendered. Covers audio (ElevenLabs) and video (Gemini or Higgsfield), with ffmpeg running locally on the Mac for now.

## Overview

Every ambience starts from a **design doc**, whatever its origin:

- **Story-tied** — the design doc is pre-filled from the arc's `bible.json` (place, era, season, weather, time of night), then edited by hand.
- **Standalone** — the design doc is written from scratch.

From there, both follow the same stages: design → generate stems → generate visuals → mix audio → render video → review → upload → archive to S3.

Ambience gets its own review UI, separate from the stories UI, but it follows the same pattern: stages stream CLI output into a window, with links to generated clips and images.

## Design doc template

One file per ambience, kept in the git repo, for example `ambience/<slug>/design.json`.

- **slug** — unique id, also the ledger key
- **origin** — `story` or `standalone`; if story, include `arc_slug` and `episode`
- **title** — working YouTube title
- **scene** — a short prose description of the place and moment
- **target_length** — e.g. 1h, 3h, 8h
- **stems** — list of sound layers (see below)
- **mix** — overall loudness target and fade in/out lengths
- **visuals** — provider, mode and prompt (see below)
- **genre / flavor** — pinned to the manifest schema at creation, same as stories

### Stem fields

- **name** — e.g. `rain_on_deck`, `hull_creak`, `ships_bell`
- **role** — `bed` (continuous, looped) or `event` (one-off, scattered)
- **prompt** — ElevenLabs sound effect prompt
- **variants** — how many takes to generate (events benefit from 3–6 so repeats aren't obvious)
- **gain_db** — level in the mix
- **events only:** `min_gap` / `max_gap` in seconds, for random spacing

### Visual fields

- **provider** — `gemini` or `higgsfield`
- **mode** — `still` or `motion` (motion is Higgsfield only)
- **prompt** — scene prompt, using the Regency look and the Prussian and amber palette where it fits
- **overlay** — optional: `none`, `candle_flicker`, `snow`, `rain`, `embers`

## Audio stages

### 1. Generate stems

- For each stem, call the ElevenLabs sound effects API once per variant.
- Save to `ambience/<slug>/stems/<name>_<n>.wav` locally. The repo keeps a manifest only; audio goes to S3.
- Log each call as a ledger row (provider `elevenlabs`, stage `stems`).
- Review step: approve or reject each variant in the ambience UI; rejected ones get regenerated.

### 2. Build the bed

- Short generated clips are looped with crossfades (`acrossfade`) to hide the seams, then extended to `target_length`.
- If there are several bed stems (e.g. rain plus wind), each one is looped separately and they're mixed together with slightly different loop lengths so they never line up identically.

### 3. Scatter events

- A Python script builds a timeline: for each event stem, choose random times between `min_gap` and `max_gap` and pick a random variant each time.
- Use a fixed random seed stored in the design doc so a render can be reproduced exactly.
- ffmpeg places the events with `adelay` and mixes them over the bed with `amix`.

### 4. Master

- Normalise to the loudness target with `loudnorm`, then apply fade in and fade out.
- Output: `renders/<slug>/audio.m4a` (AAC) plus a short preview clip for the review UI.

## Video stages

### Still mode (Gemini or Higgsfield)

- Generate one image at 1920×1080 or higher; log the cost row.
- Optional overlay: a generated short looping clip, on a black background, blended over the still. Overlays are generated, not licensed, and can be reused across ambiences.
- Optional slow push-in using `zoompan`, kept very subtle.
- Render at a low frame rate (e.g. 1–5 fps for a pure still, ~24 fps if there's an overlay) to keep render time and file size down.

### Motion mode (Higgsfield)

- Generate a short clip; log the cost row.
- Make it loop smoothly by playing it forwards then backwards (`reverse` + `concat`), then loop that to the audio length with `-stream_loop`.
- Check the loop in review before committing to a long render.

### Final render

- Combine the video and `audio.m4a` with `-shortest`, using H.264 video and AAC audio.
- Output: `renders/<slug>/final.mp4`.

## Local setup (Mac)

- Install ffmpeg (e.g. via Homebrew); set its path in `ambience/config.json` so scripts can move to Lightsail later unchanged.
- Python environment:
  - `python3 -m venv .venv`
  - `source .venv/bin/activate`
  - `python3 -m pip install requests`
- API keys come from environment variables (`ELEVENLABS_API_KEY`, `GEMINI_API_KEY`, `HIGGSFIELD_API_KEY`), never from the repo.

## Ledger integration

- Add rate entries to `rates.json` for ElevenLabs sound effects and for Higgsfield image and video generation.
- Every generation call writes a row keyed on the ambience slug, so `report.py` can show break-even per ambience and compare story-tied with standalone.

### Provider A/B tests

- Each visual or overlay generation row is tagged with its provider (`gemini` or `higgsfield`) and a `test_id` that links the paired outputs.
- Run both providers on the same prompt, then pick the winner in the review UI. Record the pick, a quality note and generation time on the test.
- `report.py` groups by provider to compare cost per asset and win rate across tests.

## After upload

- Push stems, visuals and `final.mp4` to S3; keep the design doc, timeline and seed in git.

## Rate check script

Saved separately as `ledger/check_rates-2026-11-14.py`. It scrapes each provider's pricing page, compares the prices it finds with `rates.json`, and flags any differences. It never changes `rates.json` unless run with `--apply` and each change is confirmed. Standard library only.

None of the three providers offers a pricing API, so this relies on scraping. The regex patterns will need tuning against the live pages. If a page is rendered by JavaScript (Higgsfield's probably is), the script reports "not found" and doesn't guess.

- `python3 ledger/check_rates-2026-11-14.py` shows a report only
- `python3 ledger/check_rates-2026-11-14.py --apply` asks before applying each change

Notes:

- The key paths are placeholders and do **not** match the real `rates.json`, which nests rates under `providers.<provider>.models.<model>`. See clean-up.md AA.
- The script exits with code 1 when it finds a change but `--apply` isn't set, so it could also run as a scheduled check.
- Higgsfield sells credits, so its rows may need a credits-to-dollars conversion instead of a direct dollar price.

## Open questions

- Exact rates for ElevenLabs and Higgsfield, to confirm against current pricing.
- Overlay and visual provider: A/B test Higgsfield against Gemini on the same prompts, comparing loop quality, cost per asset and generation time. Also decide whether overlays live in a shared library.
- Standard target lengths per channel.
