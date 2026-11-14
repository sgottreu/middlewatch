# Clean-up — for review

*2026-08-28. Notes from the ambience session. Not a plan; a list of things that
need your judgement rather than mine.*

Ordered by what blocks the most downstream work. Anything marked **mine** is a
decision I made without asking, which means it is the kind of thing most worth a
second opinion.

---

## 0. Do this before anything else

**Commit.** Nothing from this session is in a commit. Everything is on disk and
the working copy is the only copy.

**`.gitignore` was hiding `docs/ambience/`.** Line 11 was `ambience/` — intended
for the output bundles at the repo root, but gitignore patterns without a leading
slash apply at *every* depth, so it also matched `docs/ambience/`. All five
ambience documents were invisible to `git status` and had never been tracked.
They would have gone with the working copy.

Fixed to `/ambience/`, and the docs now show as untracked. Swept the rest of the
file for the same hazard: only `__pycache__/` matches at depth, which is intended.
`auditions/` is unanchored but there is only one, and it is output either way.

Worth a habit: a bare directory name in `.gitignore` is a pattern, not a path.

```bash
git status --short          # docs/ambience/ should appear as ??
git add -A && git commit
```

---

## 1. One decision unblocks the most

**The ElevenLabs plan.** Free tier grants no commercial rights, and that single
fact gates two separate halves of the ambience pipeline: the generated SFX
accents (bells, carriages, page turns) and music-forward videos. Both caches are
designed to be permanent, so filling either before this is settled means
discarding work. `docs/story/setup.md` already flags the narration side of the
same problem.

Nothing else in the pipeline is waiting on anything external.

---

## 2. Your ears — nothing else can settle these

| Recipe | State |
|---|---|
| `wind` | Four rounds, **last iteration unheard**. Stereo mirroring and the silent-start bug were both fixed after your last listen. |
| `fire` | Four rounds, unsigned. Low hum and the drum are fixed; whether the pops read as fire is open. |
| `rain` | Never heard. Most likely to land first — closest to pure filtered noise. |
| `ocean` | Never heard. |
| `room_tone` | Never heard. |

```bash
python3 -m ambience_pipeline.synth rain --seconds 120 --out auditions/rain.wav
```

Auditions are written 6 dB down for headroom — turn it up. `auditions/` is
gitignored; everything in it regenerates from a seed in one command.

**If fire needs a fifth round, stop.** It is the one recipe on the wrong side of
the routing rule — events rather than filtered noise — and a fifth tuning pass is
a signal to reclassify it as a generated layer with a procedural bed under it.
Detail in `docs/ambience/calibration.md`.

---

## 3. Decisions I made that you may disagree with

Each is a one-line change if you want it the other way.

| Decision | Why | Where |
|---|---|---|
| `gain_db` means **LUFS**, not RMS | K-weighting favours the highs: rain at −15 dBFS RMS is −8.7 LUFS. Levelling by RMS put two layers 6 dB from where their numbers claimed. | `synth/recipes.py` |
| Auditions use **6 dB headroom, no limiter** | Wind has a 17 dB crest factor; limiting flattened the gusts the recipe exists to create — 2 dB of swing lost. | `synth/render.py` |
| **Removed `hum_hz`** from `room_tone` | Could not be made audible. The bed is brown noise under 220 Hz, so a 50 Hz sine is masked by its own bed; lifting it 8 dB took an amplitude that raised the whole layer 8 dB. Mains hum belongs in the `tonal` role. | `synth/recipes.py` |
| **Inverted `strength`** on wind | Light wind is intermittent (20 dB swing), strong wind sustained (15 dB) — a gale roars continuously, a breeze comes and goes. If you want it to mean "more dramatic", that is one line. | `synth/recipes.py` |
| `assets/sfx_cache/` **gitignored** | Unbounded binary git cannot forget, and every entry regenerates from a prompt. Ignoring is reversible in one line; committing is not. Needs a backup that is not git. | `.gitignore` |
| **stdlib `wave`**, not `soundfile` | ffmpeg is already a hard dependency and handles FLAC and encoding, so the only format to write natively is the cheapest one. One fewer C library on two machines. | `synth/wav.py` |
| `pipeline` on cost rows **raises if missing**, never defaults | A blank `genre` costs one report line; a blank `pipeline` merges two channels' economics into a number nothing downstream could tell was wrong. | `ledger/ledger.py` |
| **Duplicate `Bundle`** rather than abstract it | The manifest half is ~50 identical lines; the path half is entirely different. Two pipelines is not enough evidence for a shared abstraction. Revisit at a third. | recommendation only, in `pipeline-design.md` |
| `LOW_SHARE` bounds | There is no universal test for "hums when it shouldn't" — what made fire hum is what makes room tone correct. So the bounds encode intent, and the numbers are my guesses. | `tests/test_synth.py` |

Every recipe parameter default is also mine and unheard. `docs/ambience/calibration.md`
lists which are assumed rather than measured.

---

## 4. Facts I could not verify

**Gemini image pricing contradicts itself on Google's own page.** The table says
$30 per 1M output tokens and quotes $0.050 for a 2K image; a footnote below says
$60 and $0.101. The table is internally consistent (1,680 tokens × $30/1M =
$0.0504) and is what `rates.json` uses. **Check against a real invoice** — if the
footnote is right, every image costs double what the ledger will record.

**ElevenLabs SFX at ~200 credits per generation** comes from third-party pricing
write-ups, not official docs. Verify before trusting an estimate.

**Lyria 3's licensing position is unsettled** and there is active litigation over
its training data. Platform commercial rights are not the same as owning the
commercial rights YouTube's monetisation policy requires.

`rates.json` carries `rates_verified`, and `report.py` warns when it is more than
90 days old. That warning is the mechanism for all of the above.

---

## 5. Wrong in the repo right now

- **`CLAUDE.md`: "No stories have been generated yet."** There are 8 bundles under
  `stories/`. Yours to correct.
- **`rates.json` has no `lyria` entry.** If you use Lyria, `price()` raises on the
  first billed call — the identical failure the Gemini image model already had.
- **`ambience_pipeline/ambience-pipeline-design.md`** is a pointer stub, not a
  real doc. It is in `cleanup.sh`; run `bash cleanup.sh --dry-run` then
  `bash cleanup.sh`. I cannot delete files.
- **Date seam.** This session's docs are stamped 2026-08-28 as asked, but
  `rates_verified`, the bible changelog and all 8 story manifests are 2026-08-29.
  I left the real event records alone rather than rewrite history.

---

## 6. The largest gap, unchanged

**Nothing calls the ledger.** `add_spend` writes unit counts into `manifest.json`;
no stage writes a costed row. `costs.csv` does not exist, so `report` has nothing
to read — and the schema work this session (the `pipeline` column,
`Bundle.ledger_story()`, the pins, `migrate.py`) is correct and entirely
unexercised. 8 stories have been generated with no record of what they cost.

One line per paid call, documented in `ledger/README.md`.

---

## 7. Doc centralisation — hazards

Two failure modes that are silent:

**`story_pipeline/prompts/` must not move.** `config.py` and `genres.py` resolve
it from `Path(__file__)` deliberately, so it follows the package and never
depends on cwd. The files read like documentation and are shipped code — editing
them changes story output. `docs/story/index.md` already draws that line.

**Relative links break without erroring.** 22 files in `docs/story/`, 5 in
`docs/ambience/`, all with footer nav and cross-references. Check after moving:

```bash
for f in docs/**/*.md; do d=$(dirname $f); \
  grep -o '](\.\./*[^)#]*\.md\|](\([a-z-]*\.md\)' $f | sed 's/](//' | \
  while read l; do [ -e "$d/$l" ] || echo "BROKEN $f -> $l"; done; done
```

### Overlap worth merging

| Candidate | Why |
|---|---|
| `docs/ambience/pipeline-design.md` (545 lines) | Three documents in one: architecture, a defect log, and a rate-table verification snippet. The defect log is history and could move to a changelog. |
| `docs/story/cost.md` vs `ledger/README.md` | Likely restate each other. |
| `CLAUDE.md` Known state | Restates facts that now live properly in the docs. This is the duplication that goes stale first — two of its lines already have. Consider pointing at the docs instead of summarising them. |
| `docs/ambience/synth.md` vs recipe docstrings | Deliberate, not accidental: the docstrings carry *why each number is what it is* beside the code, the doc carries the reference. Worth keeping separate — the docstrings are the only thing that cannot drift from the code. |

---

## 8. Worth keeping through the pass

One section I would argue against cutting for length:
`docs/ambience/calibration.md` → **"Measuring the right thing"**. Four metrics
reported a working control dead or a broken one healthy during this session, and
each mistake is available again the next time a parameter is added. A percentile
threshold cannot measure density; a spectral centroid cannot see a change in
rate; a window shorter than the modulation period measures nothing; a measurement
over the wrong span answers the wrong question.

---

## 9. Render button session (2026-10-07) — checked 2026-11-14

All of it is committed, in `f20c68d` ("Add in new status for produced video and
button to produce video"): `server.py`, `runner.py`, `app.html`, `director.py`,
`review-ui.md`, `aws.md`. No later session duplicated it and nothing from it is
left uncommitted, so no files needed a timestamp suffix.

**To do**

- **Push.** `main` is 6 commits ahead of `origin/main`. The server deploys from
  GitHub, so the Render button does not exist on review.middlewatch.co until
  this is pushed.
- **Never run end to end.** The gate, `--force`, the progress markers and the
  job view were tested with a fake process. A real render through the button has
  not run yet. Do one on a short story and watch memory while it runs.
- **Server size: the docs disagree with each other.** The `direct` row in
  `docs/story/aws.md` now says the server renders. The same file still
  says t3.nano / 412 MiB at line 17 and the cost table, has "Do not run `direct`
  on the nano" near the end, and §8 (spot instance for rendering) and the
  laptop steps in §7. Project notes elsewhere call the box Lightsail. Pick the
  real instance, then fix all of it in one pass.
- **A crashed render leaves `direct: running`.** It is only reset on Stop or a
  server restart, which is the same as record and design today. If ffmpeg fails
  on its own, the button stays enabled but the manifest says running. A
  `try/finally` in `director.direct()` that resets the stage to pending on an
  exception would fix it, and the same fix applies to `actor` and `designer`.

**Leftovers from other sessions, seen while checking**

- Deleted but not committed: `ambience_pipeline/ambience-pipeline-design.md`
  (a 16-line "Moved" stub) and `commit-session.sh`. `cleanup.sh` lists both
  for deletion, so commit the deletions.
- Untracked, already timestamp-suffixed: `WHERE-THINGS-GO-bundle-2026-08-27.md`,
  `WHERE-THINGS-GO-files1-2026-08-27.md`, `brand/launch-checklist-2026-08-27.html`,
  `brand/icon/*-2026-08-27.*`. The bundle file is dated 28 August and says
  "nothing here has been merged". Most of it (ledger, arc.md, genre/flavor)
  has since landed, so check what is still open, then archive it or drop it.
- `brand/launch-checklist-2026-08-27.html` vs tracked `brand/launch-checklist.md`
  overlap. Keep one.
- Ignored but sitting in the root: `config.yaml.bak`, two dated
  `config.yaml.*.bak`, `calibration-sweep.sh`, `cleanup.sh`, `oauth.txt`.
  `oauth.txt` holds credentials, so rotate them if that has not been done and
  delete the file.


---

# Clean-up — branding/ledger session audit

*2026-11-14. Added after auditing the chat that produced the ledger, `arc.md`,
the landing page and the brand files against what is actually in the repo.
Same rule as above: this is a list of things needing your judgement.*

## A. What was already merged, and by whom

Earlier sessions had already landed most of this work. Verified by hash, not by
memory:

| File | State |
|---|---|
| `ledger/*` (6 files) | merged in `f239a33`, then **extended** — see C |
| `story_pipeline/prompts/arc.md` | merged in `f239a33`, byte-identical |
| `site/index.html` | tracked, byte-identical (last touched `ed0eedf`) |
| `brand/launch-checklist.md` | merged in `f239a33`, byte-identical |
| `brand/merch-reference.md` | merged in `f239a33`, byte-identical |

Ten of the seventeen session files were already in place. Nothing needed
re-adding.

## B. Added with a 2026-08-27 suffix — your call whether to keep

Never tracked anywhere. Suffixed rather than dropped in place, so nothing was
overwritten. None of it is referenced by code.

| File | Recommendation |
|---|---|
| `brand/launch-checklist-2026-08-27.html` | **Keep.** The clickable/printable checklist with copy buttons for the bio lines. Only `launch-checklist.md` was tracked. Rename to drop the suffix once you have looked at it. |
| `brand/icon/middlewatch-icon-{256,512,800,1024}-2026-08-27.jpg` and `brand/icon/middlewatch-icon-1024-2026-08-27.png` | **Probably delete.** Derived from `brand/icon.svg`, which is tracked, and regenerate in seconds. Five binaries git cannot forget, for no gain. |
| `WHERE-THINGS-GO-bundle-2026-08-27.md` | **Delete after reading.** A placement guide for a move that has since happened. Obsolete. |
| `WHERE-THINGS-GO-files1-2026-08-27.md` | **Delete after reading.** Different content from the one above — came from a different session's bundle. Preserved only because the two disagree. |

## C. Five ledger conflicts — checked, no action needed

`ledger/ledger.py`, `report.py`, `import_youtube.py`, `rates.json` and
`README.md` all differ from the chat's copies. **The repo versions are newer and
strictly better.** They add the `pipeline` cost column, `record_elevenlabs()`,
`migrate.py`, ElevenLabs and current Gemini/Anthropic model rates.

Diffed line by line to confirm nothing was lost: the only lines unique to the old
copies are docstrings and usage examples that were reworded. `flavor` survives in
both files at the same occurrence count; `COST_FIELDS` gained `pipeline` and lost
nothing. `rates.json` dropped only the stale `gemini/nano-banana-2` key.

**No timestamped duplicates were created for these** — a second importable copy of
working ledger code is a hazard, and nothing would have been preserved by it.
Flagging it because it is the one place the instruction was not followed
literally.

## D. Unmerged work in `working/`, from other sessions

`working/` is gitignored (`.gitignore:49`). These have **no counterpart anywhere
in the repo** and will go with the working copy if the disk goes:

| File | What it is |
|---|---|
| `working/files1/mw-sync.sh` | S3 asset sync — pull/push audio, graphics, video per arc and episode. Real tooling. Belongs in `scripts/`. |
| `working/files1/storage-and-backup.md` | 319-line Lightsail block-storage and S3 runbook. Belongs in `docs/story/`. |
| `working/revision-notes/*.md` (5 files) | Revision notes for `an-understanding-at-amberlight`, `the-letter-at-the-bell`, `the-lit-windows-of-amberlight`, `the-shuttered-wing`, `the-thatcher-s-bill`. Every one of those stories exists under `stories/amberlight/`. |

Left in place rather than moved, because where they belong is a judgement call —
particularly the revision notes, which could sit beside each story bundle or in
one notes directory.

## E. One duplicate to resolve

`working/review-ui-handoff.md` and `docs/story/review-ui-handoff-2026-09-13.md`
have **different content**. One is stale. The second is untracked. Decide which
is current before committing either.

## F. Uncommitted right now

You committed the earlier backlog at 10:23 (`21d2603` through `162814e`) and ran
`cleanup.sh` while this audit was in progress, so most of what was dirty is now
in. What remains:

- **Two staged deletions, not yet committed.** `cleanup.sh` removed
  `ambience_pipeline/ambience-pipeline-design.md` (the pointer stub section 5
  above called for) and `commit-session.sh`. Both are still tracked, so git shows
  them as `D` until you commit.
- **Everything in B**, untracked.
- `clean-up.md` itself, modified by this section.

## G. Brand inconsistency, unresolved since August

`brand/icon.svg` is `#14140f` near-black with `#c9a227` gold. `site/index.html`
is `#131E2B` prussian with `#E8A44C` amber. Side by side they read as a mistake
rather than a deliberate contrast. Pick one and change the other. The icon is the
older and more settled artifact, which argues for moving the site.

## H. A wrong path worth knowing about

The chat's manifest listed `arc.md` under `pipeline/prompts/`. The real location
is `story_pipeline/prompts/`, as section 7 above says it must stay. The file is
correct and in the right place; only the manifest was wrong.


---

# Clean-up — video approve/publish session audit

*2026-11-14. Checked the 2026-10-07 session that added the `approve` and
`publish` stages after `direct` against the repo. Same rule: things needing your
judgement.*

## I. Merge state — all committed

| Piece | Where |
|---|---|
| `bundle.py` (`STAGES`, `stages()`, `publish_info()`), `cli.py` (`approve --video`, `publish`), `server.py` (`/api/publish`, queue groups), `app.html` (Approve video, Publish…), `director.py`, `summary.py`, `running.md`, `index.md`, `CLAUDE.md` | `f20c68d`, 2026-10-07 |
| `story_pipeline/release.py` | `b67cf2d`, 2026-10-08 — byte-identical to the working copy |

No later session duplicated it. Nothing was left untracked, so **no files needed
a timestamp suffix.**

## J. Things to know

- **Section 9 above calls `f20c68d` the "Render button session".** That commit
  holds two sessions' work: the Render button *and* approve/publish. Both
  sessions were editing the same files uncommitted and one commit took both.
  Nothing is lost; the label is just incomplete.
- **`f20c68d` was broken on its own for ~22 hours.** It imports `release`, but
  `release.py` was untracked until `b67cf2d`. Any checkout or deploy of
  `f20c68d` alone fails at import for both the CLI and the review server. Fixed
  now. The habit worth taking from it: `git status` for `??` files before
  committing a session's work.
- **Doc gap, filled today, uncommitted:** `docs/story/review-ui.md` never got
  the three new queue groups or the Approve video / Publish buttons. Rows added
  to *The queue* and *Actions* tables. Commit with the rest.

## K. Merge candidates

- `docs/story/quick-reference.md`, `running.md` and `index.md` all list the
  publish command with slightly different flag sets (`quick-reference` omits
  `--force`). Fold into one during the doc consolidation and link to it.
- Section 9's crash-leaves-`running` fix applies here too: `director.direct()`
  now also sets `approve: awaiting_review` on success only, so a failed render
  leaves the previous approval in place. That is the correct behavior, but the
  same `try/finally` should keep it that way.

