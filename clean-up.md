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

---

# Clean-up — asset sync / script rename session audit (checked 2026-11-14)

## L. Merge state — all committed

| Work | Where |
|---|---|
| `scripts/mw-asset-sync` (rsync over ssh, laptop pulls `audio/`, `images/`, `video/` straight off the server) | `dc51893` "Rename sync scripts", 2026-10-20 |
| `mw-sync` → `scripts/deploy/mw-s3-archive`, with `.service` / `.timer` renamed to match | `dc51893` |
| `mw-ship` install lines, `README.md`, `docs/story/aws.md` §5 + §7, `review-ui-roadmap.md` | `dc51893` |
| `push-casting` / `pull-casting` added to `mw-s3-archive` by the casting session | `e57c908` — builds on the rename, no conflict |

Nothing from this session is uncommitted, and no other session duplicated it,
so no timestamp-suffixed copies were needed. The first draft, `mw-pull.sh`
(wrong server path `/home/ubuntu/middlewatch`, its own env vars), was never in
the repo and is superseded.

## M. To do

- **Server: swap the systemd units if not done yet.** `mw-ship` installs the
  new script but not the units, so the old `mw-sync.timer` keeps running
  `/usr/local/bin/mw-sync` (pre-casting, so it does not back up auditions).
  Check with `systemctl list-timers | grep mw-`; the swap is in `aws.md` §5
  "Renamed from mw-sync". Delete that subsection once done.
- **`mw-asset-sync` has never run against the real server.** Syntax-checked
  only. First use: dry run, confirm `images/scenes.json` and the mp3s list.
- **`aws.md` §7 step 3 is stale.** Still says render on the laptop and
  `unzip` the zip; rendering now happens on the server via the Render button
  (oakmere-will rendered fine locally too). Same pass as section 9's
  server-size fix.
- **`deploy.md` §5 install list** names the original six scripts only; also
  `mw-deploy` prints `install scripts/deploy/*`, which would put the `.service`
  and `.timer` files into `/usr/local/bin`. Use one explicit list (the one in
  `mw-ship`) everywhere.

## N. Merge candidates / delete

- **Three ways to get a story's media onto the laptop:** Assets .zip,
  `mw-asset-sync` (live, from the server) and `mw-s3-archive pull` (S3, up to
  an hour old). Consider an `--from s3` flag on `mw-asset-sync` and retiring
  the zip, so there is one command.
- **Section D row `working/files1/mw-sync.sh`: do not move it into `scripts/`.**
  It is the older per-arc/episode design, superseded by
  `scripts/deploy/mw-s3-archive`. Delete it with the rest of `working/files1/`.
- **Stale `mw-sync` mentions:** `docs/story/review-ui-handoff-2026-09-13.md`
  (lines ~193, 237 — historical, fine to leave or annotate) and
  `WHERE-THINGS-GO-files1-2026-08-27.md` (already marked delete in B).
- **`commit-audit.sh` is untracked in the root.** Its own header says delete
  it once it has run; the tree is otherwise clean, so check its commits landed
  and remove it.

---

# Clean-up — Amberlight story review session audit (2026-11-10/11, checked 2026-11-14)

*The session that reviewed the six unwritten Amberlight outlines against the
bible, added Oakmere canon, fixed how the ideator reads the bible, and planned
every revision. Same rule: things needing your judgement.*

## O. Merge state

| Work | Where |
|---|---|
| Oakmere Will canon (10 lines) and six minor names in `bibles/amberlight/amberlight.md`, changelog `sha=13f6fec4` | `333227a` |
| `anchor_year: 1817`, `aliases: [Mrs Judith Rowe]`, changelog `sha=dab39361` | `333227a` |
| `story_pipeline/bibles.py` (minor names shown to the ideator, aliases, `anchor_year`, portrait keyed on the canonical name, validation) and `agents/text.py` (`story_year()`, ages shifted by year, station pinned only up to the anchor) | `333227a` |
| `docs/story/bible-format.md` (`anchor_year`, `minor_names`, `aliases`, *Ages and stations across years*) | `333227a` |
| Hand-written *Lit Windows* 1818 outline, then the pipeline revisions of all five stories | `333227a`, `6399654` |
| Deletion of `stories/amberlight/the-letter-at-fenwick-cottage/` (merged into the Bell) | `360e45c` |
| Revise failing at 16,000 tokens: ideator ceiling raised to 32,000 in the Mac's `config.yaml`, and streaming so the SDK accepts it | `config.yaml` (untracked), `360e45c` |

No other session duplicated any of it.

**Added today, with a date suffix because they were never tracked:**

- `docs/story/amberlight-review-2026-11-10.md`: a markdown export of the
  review doc (https://claude.ai/artifact/A7EaAW9kDhYjkDftKidfz7), with the
  later decisions noted inline. The doc itself only lived in claude.ai.
- `stories/amberlight/<slug>/drafts/revision-notes-2026-11-11.md` × 5: copies
  of `working/revision-notes/<slug>.md`, the notes each `revise` was run with.
  Beside the `outline.N.json` snapshots they produced.

## P. To do

- **Three outlines still awaiting review:** *The Shuttered Wing*, *An
  Understanding at Amberlight*, *The Thatcher's Bill*. Spot-checked: all
  three followed their notes (the wedding ends *The Shuttered Wing*; Margaret
  Selby and Robert Dene carry *An Understanding*; Rowe never learns who paid
  in *The Thatcher's Bill*), ages shifted correctly, `bible_conflict` null.
  *The Shuttered Wing* dropped Mrs Pike from the cast, though the notes gave
  her the wedding breakfast. Keep or ask for her back.
- **Guest names now collide across the unwritten slate.** "Augusta" twice:
  Mrs Augusta Ferris (*An Understanding*) and Miss Augusta Fairfax (*The Lit
  Windows*). Mr Edward Harker (*Lit Windows*) sits beside Sir Edward
  Marchmont. The ideator cannot see this: `minor_names` holds only finished
  stories, and unwritten outlines are not in it. Rename one Augusta before
  writing; consider having `history.avoid_block` list guest names from
  approved-but-unwritten outlines too.
- **`config.py` `DEFAULTS["max_tokens"]["ideator"]` is still 16000**, and
  `config.example.yaml` has no `max_tokens` block. Only the Mac's untracked
  `config.yaml` says 32000. Any other machine, the server included, will hit
  the same `TruncatedReply` on a 30-minute revise. Raise the default to 32000.
- **Server:** check the review site has `333227a` and `360e45c` (`mw-ship`), and
  that `/srv/middlewatch/config.yaml` has `ideator: 32000`, before revising
  from review.middlewatch.co.
- **Open bible questions,** listed at the end of the review export: the rector
  and whether the living is vacant; where the Rowes live after the wedding;
  whether Farr and Hester become recurring (and Farr's age: the outline says
  32, the chapters make him about 26 in 1816); the stale voice casting note
  that still names Charlotte and Lily.
- **Canon to add once stories are finished:** the wedding date (*The Shuttered
  Wing*); Fenwick Cottage, Eleanor and Sorrel (*The Letter at the Bell*); the
  1818 trustee in the main rooms (*The Lit Windows*).
- **Small code fixes noted in the review, not done:** reject non-null strings
  like `"omit"` in `bible_conflict`; refresh `genre_voices` in the unwritten
  manifests (still the retired Alice/Lily/Matilda/Daniel/George/Brian).

## Q. Merge candidates / delete

- **`working/revision-notes/*.md`** are now duplicated by the
  `drafts/revision-notes-2026-11-11.md` copies. Delete the `working/` copies
  (and section D's row for them).
- **The review export is stale in places** by design: its *Lit Windows*
  section names Eliza Combe and Calder, but the outline has since been
  revised twice more (now Phoebe Langton, Edward Harker, Augusta Fairfax).
  Treat it as the record of decisions, not of current outlines. It is not
  linked from `docs/story/index.md`; link it from `bibles.md` or leave it as an
  archive.
- **The claude.ai review doc** and the repo export will drift. The repo copy is
  the record; delete or ignore the doc.
- **Two commits carry this session's work under other labels.** `333227a`
  ("Add map for new fantasy book & new amberlight drafts") holds the bible and
  pipeline code changes; `360e45c` ("llm: stream every call…") holds the
  Fenwick deletion. Nothing is lost; the history just won't find them by name.
  Same lesson as section J.
- **`commit-audit.sh`** is still untracked in the root (section N).

---

# Clean-up — voice casting session audit (2026-10-20, checked 2026-11-14)

The session that compared voices for Eleven v4 and built per-character
auditions: `check_voices.py --sample --story`, `scripts/audition-cast.sh`, the
per-arc record in `stories/<arc>/_casting/`, the Casting page in the review UI,
and its Apply button.

## R. Merge state — all committed

| Work | Where |
|---|---|
| `check_voices.py` story auditions, `--candidates`, `--speaker` / `--voice`, progress markers, per-take record writes | `e57c908`; reworked for `voices.yaml` in `75eaa47` |
| `scripts/audition-cast.sh` | `e57c908` |
| `story_pipeline/casting.py` (record, verdicts, pool, `CASTING.md`) | `e57c908`, extended in `58f2d48` |
| `story_pipeline/casting_apply.py` (Apply: plan, line-by-line YAML edits, commit and push) | `e57c908`; moved to `voices.yaml` in `75eaa47` |
| Casting page: `review/casting.html`, `review/casting_api.py`, routes and Range-aware audio in `server.py`, `runner.start_command`, link in `app.html` | `e57c908`; UI refined by later sessions in `5c13e2a`, `1fc5166`, `32957f5` |
| `tts.py`: v4 in `TAG_MODELS` and `CREDIT_RATE` | `e57c908` |
| `mw-s3-archive push-casting` / `pull-casting` | `e57c908` (see L) |
| `docs/story/casting.md`, linked from `docs/story/index.md` | `e57c908`; updated for `voices.yaml` in `75eaa47` |
| Amberlight cast applied: `_casting/` record, bible, every outline and manifest | `236a5d6`, then recast and re-recorded in `af36a2c` |

Nothing from this session is uncommitted and no other session made a competing
version, so no timestamp-suffixed copies were needed. Later sessions built on
these files rather than beside them.

Resolved since: v4 narration with `previous_text` / `next_text` was untested
when the session ended. The Cracked Beam was re-recorded on 2026-11-09 under
`eleven_v4` with the full cast, so it works; leaving v4 out of
`NO_CONTEXT_MODELS` was right.

## S. To do

- **Default voices expire 2026-12-31 and every genre's pool still uses them.**
  `story_pipeline/prompts/genres/*.md` `voices:` blocks:
  regency `female: [Alice, Lily, Matilda]`, `male: [Daniel, George, Brian]`;
  fantasy narrator **Daniel** and pool `[Charlotte, Lily, Alice]`; sci-fi
  narrator **Rachel** and `[Rachel, Alice, Charlotte]` (Rachel is not in
  `voices.yaml` at all, so a sci-fi record would fail today). Any character not
  in a bible gets a voice from these pools. Re-point them at voices already
  auditioned (`python3 -m story_pipeline.casting pool amberlight`) before the
  next new story. Amberlight itself is clear: every outline and manifest uses
  the new cast.
- **Then prune `voices.yaml`.** It still maps Daniel, Alice, George, Lily, Brian
  and Matilda. Keep them until the genre pools above move, then remove them so
  nothing can reach for them after Dec 31.
- **Amberlight bible prose is stale** (lines ~155–158, "Mrs Hale is cast on
  Charlotte… Lily is free"). Same item as P; listed here only because Apply
  flags it and can't fix prose.
- **`ledger/rates.json` has no `eleven_v3` or `eleven_v4`,** and
  `rates_verified` is 2026-08-29, past the ledger's own 90-day window. Nothing
  fails today only because the pipeline doesn't write `costs.csv` yet; it will
  raise on the first v4 cost event once it does. Add both models (v4 full rate,
  v4 Turbo half) and re-verify against the plan.
- **Three candidates could not be auditioned** (IDs came from third-party
  listings): Alicia `DNYvvkdiu5NYK2rSLwDL` (Judith Vane), Jeanette
  `RILOU7YmBhvwJGDGjNmP` (Mrs Pike), Finley `uxut3IsZzAX3ycGl84pJ` (Silas
  Rowe — ElevenLabs' own named replacement for Daniel). Find the real IDs in
  the Voice Library or remove them from `_casting/candidates.json`; Jeanette is
  still listed there.
- **`docs/story/narration.md` predates v4.** The model table (~line 159) has
  only `eleven_multilingual_v2` and `eleven_v3`; the Casting section says solo
  is the default, which is true of the code but not of this repo's config
  (`mode: cast`). Add a v4 column and a pointer to `casting.md`.
- **`review-ui.md` and `review-ui-roadmap.md` don't mention the Casting page.**
  One line and a link each.
- **Do not re-run `commit-audit.sh`.** Its commit 4 stages `clean-up.md`, so a
  re-run would sweep this section into a commit titled for the branding/ledger
  audit. N already says to delete it.

## T. Merge candidates / delete

- **One list of Default voices, not two.** `check_voices.py` has `REPLACEMENTS`
  (Default → suggested replacement) and `casting_apply.py` has its own set of
  Default names for the expiry warning. Move one constant into
  `story_pipeline/casting.py` and import it in both. After Dec 31 the
  replacement map is history; the warning can stay.
- **`check_voices.py` lives in the repo root** but is now a pipeline component
  the review server shells out to (`casting_api.run`). Consider
  `story_pipeline/auditions.py` run as `python3 -m`, with a thin root wrapper or
  none. `casting_api.py` hard-codes `Path("check_voices.py")` and would need the
  same change.
- **`scripts/audition-cast.sh` is mostly superseded by the Casting page.** Keep
  it only if you audition from a terminal; otherwise delete it and the
  "From a terminal" lines in `casting.md` that use it.
- **Empty `casting/` directory in the root.** Left when the candidate list moved
  into `stories/amberlight/_casting/`; git doesn't track it. `rmdir casting`.
- **`config.yaml.20261102T200921.bak` and `config.yaml.20261109T143013.bak`**
  are Apply's backups (ignored by `config.yaml.*.bak`). Delete once the current
  `config.yaml` is known good; same pass as section 9's root clean-out.

## U. Review UI / CLI help-guide check — 2026-11-14

*From a session that asked for a quick guide to starting the review UI and the
CLI commands. Nothing new was written: the content already exists in the repo.*

- **Already in the repo, nothing to add.** `docs/story/quick-reference.md`
  covers starting the review UI (`scripts/review-app.sh`, `cli review
  [--port] [--no-open] [--reload]`) and every subcommand in `cli.py`. Checked
  against `cli.py` and all subcommands match. The README section "Running the
  review interface locally" came from the 2026-11-02 casting session's draft.
- **Merge: README duplicates quick-reference.** README's review section is a
  thinner copy (no `--port`/`--no-open`/`--reload`, no Casting page). Cut it to
  two lines plus a link to `docs/story/quick-reference.md` so there is one
  source.
- **To do: quick-reference has no server/ops section.** Add a short block or
  a link to `deploy.md` for `ssh middlewatch mw-deploy` (server-side only),
  `scripts/deploy/mw-ship`, `mw-restart`, `sudo systemctl restart mw-review`,
  and `scripts/mw-asset-sync <slug> --go`. Today these are only in `deploy.md`
  and `aws.md`.
- **To do: README's `mw-asset-sync` line omits the arguments.** It says "pull
  it down with `scripts/mw-asset-sync`", but `aws.md` documents
  `scripts/mw-asset-sync <slug> --go`. Match the README to that, or link to it.
- **Optional: keyboard shortcuts.** `review-ui.md` documents them (`j`/`k`,
  `a`, `r`, `g`, `v`, `p`), but they're spread out. A single line in
  quick-reference's Review UI section would make it a true one-pager.
