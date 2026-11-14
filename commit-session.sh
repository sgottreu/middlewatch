#!/usr/bin/env bash
# The five commits left from the ambience session, plus the cleanup git needs
# first.
#
# Why this is a script rather than something that already happened: the sandbox
# mount this session ran in denies unlink. git cannot work that way — it creates
# HEAD.lock and index.lock for every write and removes them afterwards — so the
# first commit landed and then left a lock behind that blocked every commit
# after it, including yours. Run this on the machine where deletion works.
#
#   bash commit-session.sh --dry-run    # show what each commit would contain
#   bash commit-session.sh              # commit
#
# Commits 3-5 are your work, not mine. The messages are written from the diffs
# alone, so read them before running — or drop those blocks and commit by hand.
#
# Delete this file afterwards. It is in cleanup.sh.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

# --- git's leftovers ------------------------------------------------------
# Stale, not active: all written by the session's own commit and orphaned when
# the unlink failed. HEAD.lock is the one that blocks everything.
if (( DRY )); then
    echo "would remove $(find .git -name '*.lock' -o -name 'tmp_obj_*' | wc -l | tr -d ' ') stale lock/temp files under .git/"
else
    find .git \( -name '*.lock' -o -name 'tmp_obj_*' \) -delete
    echo "==> cleared stale .git locks and temp objects"
fi

commit () {
    local msg="$1"; shift
    if (( DRY )); then
        printf '\n--- would commit: %s\n' "${msg%%$'\n'*}"
        git --no-pager diff --stat --cached -- "$@" 2>/dev/null \
          || git --no-pager diff --stat HEAD -- "$@"
        printf '    paths: %s\n' "$*"
        return
    fi
    git add -- "$@"
    git diff --cached --quiet && { echo "==> nothing staged for: ${msg%%$'\n'*}"; return; }
    git -c user.name="Scott Gottreu" -c user.email="sgottreu@gmail.com" \
        commit -q -m "$msg"
    echo "==> $(git log --oneline -1)"
}

# --- 1. ambience documentation (mine) -------------------------------------
commit "Ambience: synth docs, calibration record, and a wiki index

Documents the half of the ambience pipeline that is built, and records how
settled it is.

- synth.md: the three invariants the module rests on — sample continuity across
  block boundaries, determinism from one seed, no time-varying IIR filters —
  plus the primitive reference, why levels are LUFS rather than RMS, and why
  auditions use headroom instead of a limiter.
- calibration.md: the dial, the by-ear verdicts with the diagnosis behind each
  change, measured baselines, and the four ways a metric lied during tuning.
  Fire and wind are not signed off; rain, ocean and room tone are unheard.
- index.md: nav and an \"I want to change X -> edit this\" table.
- pipeline-design.md: states that the procedural recipes are not a fallback for
  lacking a sound library, and that fire sits on the wrong side of the routing
  rule and may need reclassifying rather than more tuning.
- music.md: dated, and scoped against music-as-furniture up front.

ambience_pipeline/ambience-pipeline-design.md reduced to a pointer; it is a
duplicate and is listed in cleanup.sh. README and CLAUDE.md no longer describe
the pipeline as unbuilt." \
    docs/ambience ambience_pipeline/ambience-pipeline-design.md README.md CLAUDE.md

# --- 2. review notes (mine) -----------------------------------------------
commit "Add clean-up.md — review notes before the doc consolidation

What needs a decision rather than another pass: the ElevenLabs plan that gates
both SFX and music, the recipes nobody has heard yet, nine choices made without
asking and where to reverse each, and three external facts that could not be
verified — including Google's image pricing contradicting itself by 2x on its
own page." \
    clean-up.md commit-session.sh

# --- 3. deploy documentation (yours) --------------------------------------
commit "Add deploy.md: how review.middlewatch.co exists

aws.md owns storage and compute and opens its section 7 with \"Open
review.middlewatch.co\" without ever saying how. This is that missing half —
DNS, TLS, the Google OAuth client, oauth2-proxy, nginx, and the SSH bootstrap —
ported from the session that first stood the box up and deliberately not
re-verified since, so every heading carries the date it was last checked.

aws.md now says what its own restore does not cover: a box rebuilt from that
section alone answers on 127.0.0.1:8765 and nothing else." \
    docs/story/deploy.md docs/story/aws.md docs/story/index.md

# --- 4. service file (yours) ----------------------------------------------
commit "mw-review.service: python3

Per the project rule. The unit invoked the venv's \`python\`, which is not
guaranteed to exist in a venv built by python3 -m venv." \
    scripts/deploy/mw-review.service

# --- 5. review UI handoff (yours) -----------------------------------------
commit "Add review UI pipeline-expansion handoff

Design plan for turning the review screen into a full pipeline dashboard: every
slow stage becomes a job with live CLI output and generated assets link out from
inside it. Dated 13 September 2026 and explicitly not a record of what is built
— review-ui.md and review-ui-roadmap.md remain the authority on current state." \
    docs/story/review-ui-handoff-2026-09-13.md

# --- anything left --------------------------------------------------------
echo
if (( DRY )); then
    echo "Dry run. Nothing written."
else
    left=$(git status --short | wc -l | tr -d ' ')
    if [[ "$left" == "0" ]]; then
        echo "Clean. Nothing uncommitted."
    else
        echo "$left path(s) still uncommitted:"
        git status --short | sed 's/^/  /'
    fi
fi
