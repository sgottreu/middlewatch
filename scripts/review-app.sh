#!/usr/bin/env bash
#
# Start the review UI (queue at http://localhost:8765, casting at /casting).
# Free: it reads bundles and calls no model until you press a paid button.
#
#   scripts/review-app.sh                 # open a browser on :8765
#   scripts/review-app.sh --no-open       # any `cli review` flag passes through
#   scripts/review-app.sh --port 9000 --reload
#
# Works from any directory: it cds to the repo root first, because the ledger,
# stories/ and config.yaml all resolve against cwd.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Pull the latest code first. Fast-forward only, so it never makes a merge
# commit; if it can't (offline, local changes in the way, branch diverged)
# it warns and starts the UI on what you have.
echo "Pulling latest from $(git rev-parse --abbrev-ref HEAD)..."
if ! git pull --ff-only; then
    echo "WARNING: git pull failed; starting the review UI on the current checkout." >&2
fi

if [[ ! -d .venv ]]; then
    echo "No .venv — creating it."
    python3 -m venv .venv
fi
source .venv/bin/activate

# Reinstall requirements when requirements.txt has changed since the last
# install (e.g. after the pull above). The last-installed hash lives in .venv.
REQ_STAMP=".venv/.requirements.sha256"
REQ_HASH="$(shasum -a 256 requirements.txt | cut -d' ' -f1)"
if [[ ! -f "$REQ_STAMP" || "$(cat "$REQ_STAMP")" != "$REQ_HASH" ]]; then
    echo "requirements.txt changed — installing requirements."
    python3 -m pip install -r requirements.txt
    echo "$REQ_HASH" > "$REQ_STAMP"
fi

exec python3 -m story_pipeline.cli review "$@"
