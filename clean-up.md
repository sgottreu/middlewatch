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
