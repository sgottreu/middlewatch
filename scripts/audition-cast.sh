#!/usr/bin/env bash
# Audition every candidate voice for every character in a story, on Eleven v4.
#
#   scripts/audition-cast.sh                       # The Cracked Beam
#   scripts/audition-cast.sh stories/amberlight/the-shuttered-wing
#   scripts/audition-cast.sh <story> [extra check_voices.py flags]
#
# Each character's own line is rendered on their current voice (configured
# model and v4) and on every candidate (v4). Candidates come from the arc's
# stories/<arc>/_casting/candidates.json. Finished takes are reused on re-runs,
# so adding a candidate later only pays for the new takes.
#
# Everything lands in stories/<arc>/_casting/:
#   audio/<story>/index.html   listen side by side (audio is git-ignored;
#                              back it up with `mw-s3-archive push-casting`)
#   auditions.json, CASTING.md who was tried, for whom, and the verdicts — commit these
# Record verdicts with:
#   python3 -m story_pipeline.casting verdict <arc> "<character>" <voice> cast|maybe|no
#
# Candidates that are only in the Voice Library get added to My Voices, which
# uses voice slots on your plan. Remove the ones you don't cast afterwards.
set -euo pipefail

cd "$(dirname "$0")/.."

STORY="${1:-stories/amberlight/the-cracked-beam}"
[ $# -gt 0 ] && shift
STORY="${STORY%/}"
CANDIDATES="$(dirname "$STORY")/_casting/candidates.json"

[ -d "$STORY/chapters" ] || { echo "No chapters in $STORY" >&2; exit 1; }
[ -f "$CANDIDATES" ]     || { echo "No candidates file at $CANDIDATES" >&2; exit 1; }

if [ -z "${ELEVENLABS_API_KEY:-}" ] && [ -f .env ]; then
  set -a; . ./.env; set +a
fi
[ -n "${ELEVENLABS_API_KEY:-}" ] || { echo "Set ELEVENLABS_API_KEY or add it to .env" >&2; exit 1; }

PY=python3
[ -x .venv/bin/python3 ] && PY=.venv/bin/python3

exec "$PY" check_voices.py \
  --config config.yaml \
  --sample \
  --story "$STORY" \
  --candidates "$CANDIDATES" \
  --add-missing \
  "$@"
