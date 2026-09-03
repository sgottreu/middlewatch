# Setup

Installing, and the three accounts you need.

## Install

Run everything from the repo root, and install into a virtualenv — never into
system Python.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env          # then fill in the three keys
ffmpeg -version               # must be on PATH
```

The CLI reads `.env` from the repo root on every command, so there is nothing
to source. An already-exported variable wins over the file, which is how you
override one key for a single run.

`.venv/` is gitignored. Reactivate it in each new shell before running the
pipeline; if `python3 -m pip install` ever complains about an externally managed
environment, the venv isn't active.

Three credentials: `ANTHROPIC_API_KEY` for the writing agents, `GEMINI_API_KEY`
for images, `ELEVENLABS_API_KEY` for narration. The accounts section below covers
which plans you actually need and the two licensing traps: ElevenLabs grants no
commercial rights on its free tier, and enabling Gemini billing removes the free
allowance rather than adding to it.

Polly still works as an alternative provider; set `tts_provider: polly` and
supply AWS credentials instead. `boto3` is only needed on that path.

Before the first record, run `cli voices` and correct `elevenlabs.voice_library`
in `config.yaml` against your account. The shipped IDs are the common public
voices and may not match what you have.

Three accounts, three API keys. None of them are the consumer chat subscription —
a Claude Pro plan, a Google AI Pro plan, and an ElevenLabs UI plan give you
nothing here. API access is billed separately from all of them.

```
ANTHROPIC_API_KEY     ideator, writer, editor, designer's shot planning
GEMINI_API_KEY        designer's image generation
ELEVENLABS_API_KEY    actor
```

Everything below was checked in August 2026. Pricing and tier names move; verify
before committing to a plan.

---

## 1. Anthropic — the writing agents

**Account:** console.anthropic.com. Separate from claude.ai even if you use the
same email; a Claude Pro subscription does not carry over.

**Steps**

1. Sign up at console.anthropic.com and verify the email.
2. Add a payment method under **Billing** and buy prepaid credits. There's no
   ongoing free tier — new accounts get a small credit grant to start, and after
   that a zero balance means every call fails.
3. **API keys → Create key.** Copy it immediately; it isn't shown again.
4. Set a **monthly spend limit** while you're on that page. The writer runs Opus
   and the edit loop can retry a chapter twice, so a runaway batch is the one
   place this pipeline could surprise you.

**Cost here:** cents per story. `models.writer` is the only setting where model
choice materially changes output, and Opus over Sonnet is worth it for chapter
coherence. Everything else is structured extraction on Sonnet.

**Key format:** starts `sk-ant-`.

---

## 2. Google — Gemini / Nano Banana images

**Account:** aistudio.google.com, using any Google account.

**Steps**

1. Go to aistudio.google.com and accept the terms.
2. **Get API key → Create API key**, attached to a Google Cloud project. It will
   make one for you if you don't have a project.
3. Decide free tier or paid, then set `GEMINI_API_KEY`.

**Free tier vs paid — this one matters.**

The free tier needs no credit card and does allow commercial use. The catch is
that **your prompts and the images that come back may be used to improve Google's
products, with human review.** For this pipeline that means your story text and
character descriptions. If that's fine, the free tier is genuinely usable for
getting started.

Paid tier stops that: prompts and responses aren't used for training and are
covered by a data processing addendum. To enable it, click **Set up billing** in
AI Studio and attach a Cloud Billing account. You may be required onto a Prepay
plan with a positive credit balance.

Two traps worth knowing before you flip the switch:

- **Enabling billing removes the free allowance entirely.** It doesn't become
  "free up to the limit, then billed." Every call is billed from that point,
  including ones that would have fit inside the free quota.
- **The $300 Google Cloud welcome credit does not pay for Gemini API or AI Studio
  usage** for billing accounts opened after 2 March 2026. Don't plan around it.

Also, extra API keys don't add quota — rate limits are per project, not per key.

**Cost here:** about 20 image generations per story (roughly 5 cast portraits
plus 3 scenes × 5 chapters). Cast portraits are generated once and reused across
every chapter, so a re-run of the design stage costs far less than the first.
The Batch API is roughly half price for up to 24-hour turnaround, which suits a
queue of stories better than a single one.

**Watermarking:** every image is embedded with SynthID. It's invisible and
doesn't affect the video, but it is detectable, and it's one reason to be
straightforward about disclosure (see below).

**Key format:** starts `AIza`.

---

## 3. ElevenLabs — narration

**Account:** elevenlabs.io.

**Steps**

1. Sign up at elevenlabs.io.
2. **Subscribe to at least the Starter plan.** See the licensing note below —
   this is not optional for a monetized channel.
3. Profile icon → **API Keys → Create API Key.** Copy it immediately.
4. Run `python3 -m story_pipeline.cli voices` and correct
   `elevenlabs.voice_library` in `config.yaml` against what your account
   actually has. The IDs shipped in the example config are the common public
   voices and may not match.

### The licensing trap

**The free tier grants no commercial rights.** Audio generated on it is
watermarked, requires visible ElevenLabs attribution wherever it's published, and
cannot legally be used in monetized content. A YouTube channel running ads is
monetized content. This is the single most common way people get caught out with
this service.

**Starter, around $5–6/month, is the minimum tier that grants a commercial
license** and removes the attribution requirement. Everything from Starter up
includes it.

Two useful details:

- Commercial rights attach **at generation time and are permanent**. Audio you
  generate while subscribed stays commercially licensed even if you later cancel.
  What you lose on downgrade is the right to generate new commercial audio.
- Free-tier audio does not become licensed retroactively by upgrading later. If
  you test on free, re-record before publishing.

### Which plan

| Plan | Credits/mo | Why you'd pick it |
|---|---|---|
| Free | 10,000 | Evaluation only. No commercial use. |
| Starter ~$5–6 | 30,000 | Minimum legal tier. ~2–3 stories/month. |
| Creator ~$22 | ~100–121,000 | ~9–11 stories/month. Professional voice cloning. |
| Pro ~$99 | ~500–600,000 | 44.1 kHz PCM output; higher API concurrency. |

A 2,000-word story is roughly 11,000 characters, and the standard models bill one
credit per character. Turbo and Flash bill half that if you'll trade some quality.

**Starter gets you two or three stories a month. Creator is the realistic tier
for a publishing schedule.** Unused credits roll forward about two cycles on paid
plans, so a light month builds a buffer.

Overage runs around $0.10 per 1,000 characters, which is *below* the effective
rate of your monthly allowance — so the config's `usd_per_1k_credits: 0.22`
(Creator's rate at full utilization) is the conservative figure. Adjust it to
whatever your plan actually works out to.

**Why the config uses `pcm_24000`:** 44.1 kHz PCM requires a Pro plan. 24 kHz
needs no particular tier and is ample for narration over stills. PCM rather than
mp3 keeps the stitch lossless until the final encode.

**Refunds are narrow:** within 14 days of payment *and* only if none of that
period's credits have been used. Once you generate anything, treat the month as
spent.

**Key format:** starts `sk_`.

---

## Optional: AWS, only for the Polly path

Not needed unless you set `tts_provider: polly`. If you do: an AWS account, an
IAM user with `polly:SynthesizeSpeech`, and either `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` in the environment or a named profile set as
`polly.profile` in `config.yaml`. `boto3` is only required on this path.

Polly costs roughly a tenth of ElevenLabs, which makes it useful for test runs
where you only care that the pipeline completes.

---

## Putting it together

```bash
cp .env.example .env     # then fill in the three keys
set -a; source .env; set +a
cp config.example.yaml config.yaml
```

`.env` and `config.yaml` are both gitignored. Keep them that way — an API key in
a public repo gets scraped within minutes.

**Verify each key before spending anything:**

```bash
python3 -m story_pipeline.cli genres                      # no key needed; checks the install
python3 -m story_pipeline.cli bibles                      # no key needed; lists series bibles
python3 -m story_pipeline.cli voices                      # proves the ElevenLabs key
python3 -m story_pipeline.cli new --genre regency         # proves the Anthropic key, ~1 cent
python3 -m story_pipeline.cli record <story> --dry-run    # cost estimate, no key needed
```

The Gemini key isn't exercised until the design stage, which is the most
expensive one to discover a bad key in. Worth generating one throwaway image in
AI Studio first to confirm the key works.

---

## Two things that aren't credentials

**YouTube disclosure.** Videos with synthetic voice or generated imagery need to
be marked as altered or synthetic content in YouTube Studio at upload. It's a
checkbox, it doesn't affect monetization for this kind of content, and skipping
it risks a strike. The Gemini SynthID watermark means the images are detectable
regardless.

**Terms on voice.** Don't clone a real person's voice without their written
permission, and don't use a stock voice in a way that implies a real person said
something. The stock library is licensed for exactly this use; that's what you're
paying Starter or Creator for.

---

[Index](index.md)  ·  [Running the pipeline →](running.md)
