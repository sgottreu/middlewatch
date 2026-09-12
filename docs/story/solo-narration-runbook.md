# Runbook: The Cracked Beam, solo narration

What to run, in order, to take the story you already recorded with a cast and
re-record it as a single reader. Roughly an hour, most of it waiting, and about
$7 in API spend.

Everything here runs **on the Mac, from the repo root, in the venv**:

```bash
cd ~/Development/claude/middlewatch
source .venv/bin/activate
```

---

## Where things stand

| | |
|---|---|
| The Cracked Beam | written, all 8 chapters approved, **recorded** (25,878 credits spent) |
| That recording | seven voices, one generation per segment — 182 of them, with a 400ms join stapled into each |
| The prose | written under the old rule that **forbade** `said Mrs Pike`, because the voices made it redundant |
| `config.yaml` | already `casting.mode: solo`, `elevenlabs.model: eleven_multilingual_v2` |
| Charlotte | mapped to `wScwPA1qCkWo5R2dmlS8` |
| The other six ids | still the shipped public-library defaults — **not yours** |

So: the code is ready, the voice ids are half done, and the prose needs
attribution before one reader can carry it.

---

## 1. Ship the code, and restart the review server

The prompts and `text.py` changed, and the review server runs the same code.

```bash
mw-ship "Solo narration, audio tags, attribution pass"
```

That commits, flushes the server's story approvals, rebases, pushes, and deploys.
Because `.py` files changed, it restarts `mw-review` itself. First run also
installs the deploy scripts on the server.

**Check:** it ends with `shipped <sha>` and the server reports `up on :8765`.

---

## 2. Finish the voice ids

Six names still point at ids your account does not have. One command:

```bash
python3 scripts/sync-voices.py --dry-run    # look first
python3 scripts/sync-voices.py              # write, keeping config.yaml.bak
```

It matches `Alice - Clear, Engaging Educator` to `Alice`, and leaves Charlotte
alone because she is already correct.

**Check:** no line comes back `! not in your account`. If one does, either add
that voice in the ElevenLabs dashboard or point the name at one you have:
`--map Brian=Bill`, or `--set Brian=<id>`.

Strictly, solo narration only needs Charlotte. Do the rest anyway — `cast` mode
is still there, and a half-mapped library is a trap for later.

---

## 3. Decide the model — before spending anything real

```bash
python3 -m story_pipeline.cli tts-check
```

A few hundred credits. It speaks one tagged line and prints the sentence spans
it got back. Two questions it settles that the documentation does not:

1. **Does the model return character alignment?** The timeline, the subtitles
   and the video sync are all built from it. If the output says
   `alignment returned: yes`, you are fine.
2. **Is `[quietly]` performed, or read out loud?** Listen. `eleven_multilingual_v2`
   does not perform tags; `eleven_v3` should.

**If you want tags performed**, set `elevenlabs.model: eleven_v3` in
`config.yaml` and run `tts-check` again. If v3 comes back without alignment,
**stay on v2 and skip tags** — the video pipeline needs the timings more than
the prose needs the direction.

Same credit rate either way: 1 credit per character.

---

## 4. Put attribution into the prose

**~$1–2, about ten minutes.**

```bash
python3 -m story_pipeline.cli attribute stories/the-cracked-beam --dry-run
python3 -m story_pipeline.cli attribute stories/the-cracked-beam
```

Each chapter goes back to the writer with one standing note: add attribution
wherever a listener would lose track, add a sparing `tag` where a line would be
misread, take the added words back out of restatement, change nothing else. The
editor judges each one as usual.

What it does to the bundle:

- Chapters with no dialogue are skipped, not paid for.
- Every rewritten chapter **lapses its approval and its narration**. That is
  correct — the words changed.
- Previous drafts are kept in `drafts/`, numbered after the ones already there.

**Check:** it ends `8 of 8 rewritten` (or names the ones that failed review).

---

## 5. Read it, and re-approve

```bash
python3 -m story_pipeline.cli review
```

All eight chapters are back in the queue. This pass is not a formality — it is
where you find out whether the writer over-attributed. Watch for a column of
`said X` down the page, and for tags on lines that did not need one.

Fixing either is a Note to writer on that chapter. Approve from the foot of each
chapter as you finish it; phone works.

**Check:** the action bar reads `8 of 8 approved`.

---

## 6. Re-record

**~26,000 credits, about $5.**

```bash
python3 -m story_pipeline.cli record stories/the-cracked-beam --dry-run
python3 -m story_pipeline.cli record stories/the-cracked-beam
```

Every chapter re-records — the text changed *and* the casting did, and the
timeline stores both, so nothing is silently kept. The casting line should read:

```
provider: elevenlabs, solo narration by Charlotte
```

16 generations instead of 182.

---

## 7. Listen for five things

Everything up to here is mechanical. This is the part that decides whether the
approach is right.

1. **Seams at the joins.** The thing that started this. There should be
   essentially none left inside a chapter.
2. **Attribution.** Can you follow who is speaking with your eyes shut? Too much
   is as wrong as too little.
3. **The scene-break silence.** Two seconds, my guess, never heard. Too short
   reads as a paragraph; too long as the end of the chapter. Change
   `casting.break_ms` and re-record one chapter. If plain silence stays
   ambiguous, the ship's bell is the next move.
4. **Names.** Mangled ones are invisible in text and obvious in audio.
5. **Tags, if you went to v3.** Performed, ignored, or spoken aloud.

---

## 8. Then the video — one thing is missing

```bash
python3 -m story_pipeline.cli design stories/the-cracked-beam
python3 -m story_pipeline.cli direct stories/the-cracked-beam
```

`design` needs `GEMINI_API_KEY` in `.env`, which is still the placeholder. It
will stop you until that is set.

---

## Cost

| Step | Spend |
|---|---|
| `tts-check` | a few hundred credits, twice if you try v3 |
| `attribute` (8 chapters) | ~$1, up to $2 |
| `record` | ~25,900 credits ≈ $5 |
| **Total** | **≈ $7** |

One 30-minute story is ~26k credits. Starter (30k/mo) is one story; Creator
(121k/mo) is about four.

---

## If something goes wrong

| | |
|---|---|
| A voice 404s | The account check should stop you first. `scripts/sync-voices.py --dry-run` names it. |
| `config.yaml` looks wrong | `config.yaml.bak` is the version before the last sync. |
| A rewritten chapter is worse | `drafts/NN.*.json` holds every pass. Copy one back over `chapters/NN.json` and run `cli render`. |
| Solo does not work for you | `casting.mode: cast` in `config.yaml` restores the old behaviour. The attribution stays and does no harm. |
| `record` skips chapters you wanted redone | Delete `stories/<slug>/audio/NN.*`, or pass `--force`. |
| The review page offers a button the server 404s on | The server is running older code. `mw-restart` on it. |

---

## Command reference

| Command | Cost | What |
|---|---|---|
| `mw-ship "msg"` | free | commit, push, deploy, restart |
| `mw-restart` | free | restart the review server alone |
| `scripts/sync-voices.py` | free | write your voice ids into `config.yaml` |
| `cli voices` | free | list the account's voices and ids |
| `cli tts-check` | ~cents | verify alignment and tags on the configured model |
| `cli status <story>` | free | stages, approvals, spend |
| `cli review` | free | the review queue |
| `cli attribute <story>` | ~$0.09/ch | add dialogue attribution for solo narration |
| `cli critique <story>` | ~$0.02/ch | re-run the editor, rewriting nothing |
| `cli record <story>` | ~$5/story | narrate; `--dry-run` is free |
| `cli design <story>` | Gemini | cast sheet and scene images |
| `cli direct <story>` | free | assemble the video |
