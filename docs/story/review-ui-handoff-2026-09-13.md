# Review UI — Pipeline Expansion Handoff

Turning the review screen into a full pipeline dashboard for the stories
channel: every slow stage becomes a job with live CLI output, and generated
assets link out from inside that output.

Written 13 September 2026. This is a design plan, not a record of what is
built. `docs/story/review-ui.md` and `docs/story/review-ui-roadmap.md`
already exist and are the authority on current state — reconcile against
them before acting on any section here.

---

## 0. Where this goes

    docs/story/review-ui-handoff-2026-09-13.md   this file
    story_pipeline/review/                       the UI app
    story_pipeline/cli.py                        the CLI the jobs shell out to
    bibles/, stories/                            text assets
    /mnt/work/arcs/<arc>/ep<n>/                  audio, graphics, video
    s3://middlewatch-assets/arcs/...             same tree, after sync

Job state (§1) needs a home. The repo already has a top-level `runs/` —
either reuse that or put job dirs under `story_pipeline/review/runs/`.
Whichever it is, gitignore it.

---

## 1. Job runner — the foundation

Nothing long-running happens inside a request handler.

### Contract

    POST /jobs          {arc, ep, stage, scope?}  ->  {job_id}
    GET  /jobs/{id}                               ->  meta.json contents
    GET  /jobs/{id}/stream?from=<byte_offset>     ->  SSE, see §2
    POST /jobs/{id}/cancel                        ->  kills the process group

`scope` is optional and narrows the run — see §6 on per-chapter regeneration.

### On-disk state

Each job gets a directory. File-based, same reasoning as `bible.json`:
modular, inspectable, survives a restart.

    review/runs/<job_id>/
      meta.json        stage, arc, ep, scope, pid, status, started,
                       finished, exit_code, cost_before
      output.log       combined stdout + stderr, append-only
      artifacts.jsonl  one JSON object per emitted artifact

`status` is one of: `running`, `done`, `failed`, `cancelled`.

### Rules that matter

- **Detach the subprocess** (`setsid`, new process group). Closing the browser
  tab must not kill a twenty-minute render.
- **Kill the group, not the pid**, on cancel — ffmpeg and the AWS CLI spawn
  children.
- **Lock per `arc/ep/stage`.** A lock file in the job dir prevents a
  double-click from launching two chapter runs against the same episode.
- **Re-discover on server restart.** Scan `runs/` for `status: running`,
  check pid liveness, mark the dead ones `failed`. Otherwise a crashed server
  leaves ghost jobs spinning in the UI forever.
- **Cancel early.** Build it in phase one, not phase five. A bad chapter run
  or a wrong render costs real money.

---

## 2. The output window

Server-Sent Events, tailing `output.log`. SSE rather than WebSockets because
the traffic is one-way and SSE reconnects natively.

- Support `?from=<byte_offset>` so a refresh or a dropped connection resumes
  where it left off instead of replaying four thousand lines.
- Because the log is a file rather than an in-memory buffer, a finished job's
  output is still readable days later. Job history comes free.
- Render the last N lines on load, then attach the stream. Don't block first
  paint on the whole file.

---

## 3. Artifact links inside the output

**Do not regex the log for filenames.** Have the pipeline scripts emit a
structured marker line:

    ::artifact {"kind":"audio","path":"arcs/cracked-beam/ep1/audio/ch01.mp3","label":"Chapter 1"}

Kinds: `audio`, `image`, `video`, `text`.

The UI renders marker lines as a player, a thumbnail, or a link. Every other
line renders as plain log text. Also append each marker to `artifacts.jsonl`
so the job's assets can be listed without re-parsing the log.

One shared helper does the emitting:

```python
def emit_artifact(kind, path, label=None):
    """Print an artifact marker the review UI will render as a link."""
    rec = {"kind": kind, "path": path}
    if label:
        rec["label"] = label
    print("::artifact " + json.dumps(rec), flush=True)
```

`flush=True` matters — buffered output means the link appears minutes late.
Run the pipeline scripts with `PYTHONUNBUFFERED=1` from the job runner for the
same reason.

The alternative (watching the output directory for new files) needs no script
changes but is fragile with temp files and partial writes. The marker is worth
the small edit per script.

### Resolving a path to a URL

    GET /asset?path=arcs/cracked-beam/ep1/audio/ch01.mp3

1. Look for it under `/mnt/work`. Serve directly if present.
2. Otherwise presign the S3 object and redirect.
3. Reject anything containing `..` or resolving outside the work root.

Step 2 is what keeps links in old job logs alive after the episode has been
swept off the working disk.

---

## 4. Stage state — derived, not stored

Per episode:

    manifest -> ideate -> lead -> outline -> chapters -> edit -> review
             -> audio -> designs -> thumbnail -> render -> package -> upload

Derive each stage's status from the filesystem — does `bible.json` have this
episode, does `outline.md` exist, how many `ch*.md` files are there, is there
an mp4 in `video/`. Do **not** keep a separate status field; it drifts out of
sync with reality the first time you do something by hand on the box.

Buttons enable or grey out from that derived state.

### Instant actions vs jobs

Not everything is a job. These write immediately and need no output window:

- **manifest** — genre, flavor, series, arc slug, volume, episode. Must happen
  first, or the ledger has nothing to attribute costs to.
- **lead** — set narrator or cast lead. A form write to the bible.
- **review** — the approve/reject you already have.

Keep instant actions and jobs visually distinct, so you learn by shape which
buttons will open an output window.

---

## 5. Stages that were missing

Added to the list above, called out here because they weren't in the original
four:

- **ideate** — picking which planned episode slot in the bible you're filling,
  and the premise. Runs before the outline. `arc.md` defines the agent.
- **edit** — `arc.md` specifies three agents and only the writer was wired in.
  The editor pass is its own job; show the result as a diff against the
  writer draft, not as a fresh document.
- **package** — title, description, tags, YouTube chapter markers. Generated,
  reviewed, copied out at upload.
- **thumbnail** — split out from designs. It's the one asset where you want
  two candidates side by side to choose between.
- **upload** — even if it stays manual, the dashboard needs to know it
  happened. It's the trigger for the S3 lifecycle move off Standard.

---

## 6. Arc-level surfaces

These have no episode to hang off and need their own screen:

- **Cast portraits** — generated once per arc, reused by every episode. Lives
  in `shared/`, never tiered to Glacier.
- **Feature concat** — the fifth upload, available once all four episodes
  have rendered.
- **Bible editing** — threads, `planted_in` / `pays_off_in`, cast roles. You
  will want this visible *while* reviewing chapter drafts, so consider a
  side panel on the episode screen rather than a separate page.

---

## 7. Operational pieces

- **Auto-push on stage success.** `mw-sync push-audio` / `push-graphics` fires
  when the stage completes, not when you remember. The cost-integrity
  argument is the whole point: a lost asset that gets regenerated writes a
  duplicate row to `costs.csv`, and break-even goes silently wrong.
- **Scoped regeneration.** Polly mispronounces one name and you need chapter
  three re-recorded, not all six. Hence `scope` on the job contract —
  `{"chapters": [3]}`.
- **Cost delta per run.** Snapshot the ledger total into `meta.json` as
  `cost_before` at job start; diff on completion and show the delta plus the
  episode running total. Puts the number where the spending decision is.
- **`rates.json` staleness.** The 90-day warning already exists in the ledger
  code. Surface it as a banner in the UI rather than leaving it in report
  output nobody runs.
- **Revenue import.** Monthly YouTube Analytics CSV drop. Channel-level, not
  episode-level — give it a home in the UI so it doesn't stay a terminal
  chore.

---

## 8. Build order

1. **Job runner + SSE window, wired to `outline` only.** Fastest and cheapest
   stage to iterate against. Includes cancel and restart recovery.
2. **Remaining generation stages** (ideate, chapters, edit) **plus the
   derived-state dashboard.**
3. **Artifact markers** — `emit_artifact()` into the scripts, inline audio
   player and image thumbnails in the UI.
4. **Audio, designs, thumbnail, render** wired in, with the S3 presign
   fallback and auto-push.
5. **Cost deltas, job history, packaging, rates banner.**

---

## 9. The ambience channel

Getting its own UI. The rule is *separate UI, shared library*.

**Share:**

- Job runner, SSE streaming, cancel, restart recovery. None of it knows what
  a chapter is.
- The `::artifact` marker format and the path-to-URL resolver.
- Ledger writes and cost deltas — one `costs.csv`, so break-even and the
  genre/flavor rollups keep working across both channels.
- `mw-sync` and the S3 tree.

**Don't share:**

- Stage definitions and derived-state rules. Ambience has no bible, no cast,
  no arc. Probably closer to: concept → source audio → loop and mix →
  still or slow-motion visual → render → package → upload.
- The review surface. Reviewing an hour of ambient audio is scrubbing and
  checking the loop seam, not reading prose.

**Decide before starting:** whether ambience items get slugs in the same
namespace as stories. Same namespace means the ledger and S3 tree need no
changes and `report.py` groups by channel for free. A separate key space
means reconciling two ledgers at tax time.

**Sequencing:** build the stories UI first and let the shared pieces fall out
of it. You'll know what's genuinely common after one real episode has gone
end to end — designing the abstraction up front will get it wrong.

---

## 10. Open question before any code

What the current review UI is built on.

- Already a Python web app → all of the above is stdlib plus a routes file.
- A static HTML file opened locally → you need a small server process first,
  and that's step zero.

Either way, keep to the project conventions: `python3` never `python`, and if
anything needs installing, `python3 -m venv .venv` and
`source .venv/bin/activate` first.
