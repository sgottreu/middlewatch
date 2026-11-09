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

if [[ ! -d .venv ]]; then
    echo "No .venv — creating it and installing requirements."
    python3 -m venv .venv
    source .venv/bin/activate
    python3 -m pip install -r requirements.txt
else
    source .venv/bin/activate
fi

exec python3 -m story_pipeline.cli review "$@"
