# Review UI — what is built, and what is next

Reconciled from a handoff written 13 September 2026, which planned turning the
review screen into a pipeline dashboard. The plan was sound; several of its
premises were not. This keeps what still applies, marks what was overtaken, and
says what is actually left.

The original is at `working/review-ui-handoff.md` and is left as written.

---

## What the handoff assumed, and what is true

It described a repo that never happened — the arc-and-working-disk design, not
the one in the tree. None of this is a small naming difference: the paths are
where the code looks, and the stage list is what the CLI actually has.

| Handoff | Reality |
|---|---|
| `review/`, job state in `review/runs/` | `story_pipeline/review/`, job state in `runs/` at the repo root |
| `arcs/<arc>/ep<n>/` | `stories/<slug>/` — arcs are designed but nothing reads or writes them |
| `/mnt/work` working disk, assets in `graphics/` | `stories/` on an EBS volume at `/srv/middlewatch/stories`, assets in `images/` |
| Polly | ElevenLabs (`tts_provider` still switches back) |
| Stages: manifest, lead, outline, chapters, thumbnail, package, upload | `ideate, write, edit, record, design, direct` — see `STAGES` in `bundle.py` |
| Feature concat as an arc surface | No feature stage exists |

One disagreement is real rather than stale. The handoff says stage state should
be **derived from the filesystem, never stored**. The repo stores it in
`manifest.json` and has since the beginning. Both are defensible and changing it
now would touch every agent, so the compromise in place is narrower: anything
that *can* drift is derived at read time — chapter approval is recomputed from
the per-chapter hashes, and job progress is counted off the files each stage
writes — while the stage field stays stored.

---

## Built

- **A job runner** (`review/runner.py`). Every paid stage runs as a detached
  child in its own process group, with `meta.json` and an append-only log per
  run under `runs/<slug>/<job_id>/`. This is §1 of the handoff, minus the
  per-`arc/ep/stage` lock: one job per story is enough, because a story is the
  unit everything else here is keyed by.
- **Cancel**, as the handoff insisted, in the first pass rather than the fifth.
  It signals the process group, so the provider call and ffmpeg stop too.
- **Restart recovery.** Jobs still alive are re-found by pid; dead ones are
  marked failed at startup and their stages released.
- **The output window**, by polling `GET /api/log/<slug>?from=<offset>` rather
  than SSE. Same byte-offset resume the handoff asked for; one fewer moving
  part. If the log ever needs to stream to several viewers at once, SSE is the
  upgrade and the offset contract already fits it.
- **Progress markers.** `::progress {...}` lines under `MW_PROGRESS`, which is
  the handoff's `::artifact` idea applied to progress first.
- **Record and design as UI actions**, each with its cost on screen before it
  starts, and the gates enforced server-side.
- **`cli redraft`**, which the page needed as a command to run.

---

## Next, in order

1. **Artifact links** — §3 of the handoff, unchanged and still right. Add
   `emit_artifact()` beside `_progress()` in `cli.py`, emit from the actor and
   designer as each file lands, and render markers in the log pane as an audio
   player or a thumbnail. Do not regex the log for filenames. The path-to-URL
   resolver is simpler here than in the handoff: files are local to the box, so
   it is one guarded static route under `stories_dir`, with the S3 presign
   fallback only once assets are swept off the volume.
2. **Auto-push on stage success** — the strongest operational argument in the
   handoff, and the reason to do it before anything cosmetic: a lost asset that
   gets regenerated writes a *second* ledger row for the same story, both rows
   legitimate, break-even quietly wrong. One `mw-sync push <slug>` at the tail
   of a successful record or design run closes the hour-wide window the timer
   leaves. See [running on AWS](aws.md).
3. **Cost delta per run.** Snapshot the manifest spend into `meta.json` at
   start, diff at the end, show the delta beside the story total. Blocked on
   nothing — but worth noting that until the ledger is actually called, the
   delta is unit counts, not dollars.
4. **`rates.json` staleness banner.** The 90-day check already exists in
   `ledger/report.py`; surface it in the page rather than in output nobody runs.
5. **Scoped regeneration.** `{"chapters": [3]}` on a record run, for the one
   chapter with a mispronounced name. Redraft already does this for prose.

## Deferred, with reasons

- **SSE.** Polling is enough for one viewer; see above for when it stops being.
- **`package`, `thumbnail`, `upload` as stages.** Real gaps — nothing generates
  a title, description or thumbnail today, and nothing records that an upload
  happened. They are pipeline work rather than UI work, and `upload` is the
  trigger for tiering video off Standard, so it wants the ledger wired first.
- **Arc-level surfaces.** Cast portraits per arc, the feature concat, bible
  editing beside the chapter. All blocked on arcs existing in code at all —
  `docs/story/continuity.md` has the state of that.
- **A second UI for ambience.** The rule from the handoff still holds and is
  worth keeping: *separate UI, shared library*. The runner is already the shared
  piece and knows nothing about chapters. What still has to be decided before
  any of it: whether ambience items share the story slug namespace, because the
  ledger and the S3 tree both key on slug.
