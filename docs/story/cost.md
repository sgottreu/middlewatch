# Cost

Per ~2,000-word story, roughly:

| | |
|---|---|
| Writing (ideate + 5 chapters × up to 3 passes + reviews) | a few cents to ~$0.50 |
| ElevenLabs narration, ~11k characters | ~11k credits |
| Gemini images, ~5 cast + ~15 scenes | 20 image generations |

Rates move; `record --dry-run` computes from `usd_per_1k_credits` in your config
and the credit rates in `story_pipeline/tts.py`. On Polly the same story runs about
$0.18 on neural or $0.33 on generative, which is the reason that path is still
there.

Model choice matters in exactly one place: the writer. Sustained coherence over a
whole chapter is where Opus separates from Sonnet. Everything else is structured
extraction and Sonnet handles it.

See [Setup](setup.md) for which plan on each service you actually need.


---

[← Architecture](architecture.md)  ·  [Index](index.md)  ·  [Troubleshooting →](troubleshooting.md)
