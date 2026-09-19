# Troubleshooting

| Symptom | Cause |
|---|---|
| `Could not resolve authentication method` | `ANTHROPIC_API_KEY` is not set. The CLI reads `.env` at the repo root — check it exists, has the key, and that you are running from the root. |
| `<model> hit the N-token ceiling` (`TruncatedReply`) | Ceilings live in `config.yaml` under `max_tokens`, per agent — raise the one named. But read the block breakdown first: `1x text` cut mid-sentence means it needed room; `1x thinking` and no text means the budget went to reasoning, not the answer, and a bigger ceiling may not help. A 16,000-token verdict on a 560-word chapter means the model is repeating itself either way. |
| A write stopped and the story looks stuck | `write: running` used to persist after a crash. It is now reset to `pending`, and completed chapters stay on disk — just run it again. |
| `unbalanced JSON in model reply` | The reply hitting `max_tokens` is raised earlier as `TruncatedReply`, so anything reaching this point is a reply the model *chose* to end. If it was short only its closing `}` or `]`, the parser now puts them back and carries on rather than discarding a chapter already paid for. What still raises ended mid-sentence, mid-key or mid-number, where repairing would hide the loss. The error prints both ends of the reply plus `stop_reason` and the tokens used against the ceiling. |
| `FutureWarning: You are using a Python version 3.9 past its end of life` | google-auth, on every call. Harmless, but it buries real output — delete `.venv` and rebuild it under python3.10+. |
| `does not support assistant message prefill` (400) | The 4.6/5 model generations removed prefill. `llm.complete()` drops it automatically; if you see this, the code predates that fix. |
| `anthropic-workspace-id is required` (400) | Your key is identity-linked and reaches more than one workspace, so it must name one per request. Set `ANTHROPIC_WORKSPACE_ID` in `.env`, or create a key scoped to a single workspace. See below. |
| Reading a chapter, you cannot tell who is speaking | Fixed in `render_markdown`: a paragraph per speaker and a `**Name**` label on each turn. Chapters written before that render flat — re-render with `cli render <story>`, no API calls and no cost. |
| `voice X is not in elevenlabs.voice_library` | Run `cli voices` and fix the mapping; caught before any paid call |
| Female character in a male voice | Outline is missing `gender`; add it, or set `voice` on that cast member |
| 429s partway through a record | Concurrency cap, not quota; `tts.py` backs off and retries |
| Wrong accent in narration | Casting comes from the genre file's `voices`, pinned at ideation |
| `chapter N segment M is empty` | No longer raised. An empty segment carries no prose and no audio, so it is dropped and the count recorded on the chapter as `dropped_empty_segments`; the editor's beat coverage is what judges whether anything was actually lost. Only a chapter where *every* segment is empty still fails. |
| Asterisks read aloud in narration | A `**Mrs Pike**` label copied out of the context the writer is shown. Stripped at validation, but only when the bolded text is a cast name — bold prose is left alone. |
| Unknown speaker error in `write` | A shortened name (`Vernon Hartwell`, `Hartwell`, `Miss Vane`) is scored part-by-part against the cast and repaired in place. What still raises is a name that fits two cast members equally — two sisters both answering to `Miss Smith` — or one that fits nobody, the writer inventing a walk-on. The error says which, and quotes the line. The rejected draft is kept at `drafts/NN.rejected.json` — it was generated and billed, so it is there to correct by hand rather than pay for twice. |
| Video ends before the narration | Crossfade timing — every clip but the first needs the fade back |
| Protagonist's face changes mid-story | Cast sheet missing or scene shots didn't list `characters` |
| Text or lettering in a generated image | Prompt overrode the negative clause; keep captions out of prompts |
| `no timeline` on design | `record` hasn't run; the designer needs timings to place shots |
| Prose sounds like an LLM | Run `cli lint`; if clean, the problem is generic writing, not vocabulary — check the editor's `generic_prose` |
| Linter flags a legitimate period usage | Add the phrase to `CONTEXT_EXEMPT` in `lint.py` |
| Chapters loop to the revision limit | A lint rule is firing on something the writer can't drop; check `reviews/NN.json` `lint.by_rule` |
| ffmpeg error after a long render | ffmpeg missing — the actor checks first, the director doesn't |
| Chapters read as summaries | Writer given too small a `target_words` for the beats assigned |
| Write says it is starting at chapter 1 when chapters exist | It was genuinely rewriting them, and billing for it. It now resumes, and the progress list marks kept chapters `already written, kept`. Use `--restart`, or the dialog's checkbox, to rewrite deliberately. |
| `write` refuses to start | Outline not approved; `approve` it or pass `--force` |
| Outline rejected on tropes | Model invented one or stacked four; it regenerates on retry |
| `--genre` refused beside `--bible` | They disagree; drop `--genre`, the bible declares one |
| Recurring character's face changed | No portrait in `bibles/<name>/<name>.cast/` yet; the first story to run `design` creates it |
| Every story feels the same | Raise `trope_sample`, or the genre needs more tropes |
| Wrong accent in narration | Casting comes from the genre file's `voices`, pinned at ideation |

---

## `anthropic-workspace-id is required`

```
anthropic.BadRequestError: Error code: 400 - {'type': 'error', 'error':
{'type': 'invalid_request_error', 'message': 'anthropic-workspace-id is
required when authenticating with an identity-linked API key; ...'}}
```

Nothing is wrong with the pipeline, the bible, or the outline. Anthropic API
keys come in two shapes and this is the one that needs a header:

| Key | Behaviour |
|---|---|
| Scoped to one workspace | Carries the workspace with it. Nothing else needed. |
| **Identity-linked** (personal or service account) reaching several workspaces | Must name the workspace on **every** request, or the API returns a 400. |

Two fixes, either is fine.

**Set the workspace id.** In `.env`:

```bash
ANTHROPIC_WORKSPACE_ID=wrkspc_01JwQvzr7rXLA5AGx3HKfFUJ
```

`llm.client()` sends it as the `anthropic-workspace-id` header on every request.
Find the id in the **ID** column of Settings → Workspaces in the Claude Console.

The Default Workspace's id is not listed there. If that is where you want to
work, read it from the `anthropic-workspace-id` response header of any request
that runs in it, or use the other fix.

**Or create a scoped key.** Settings → API keys → Create key, and set a
workspace on it. A scoped key needs no header and no `.env` entry, which is the
simpler option if you are making a key anyway.

The request is rejected before it runs, so a run that fails this way costs
nothing. `calibration-sweep.sh` detects it and stops rather than repeating the
same failure for every remaining run.

---

## `does not support assistant message prefill`

```
anthropic.BadRequestError: Error code: 400 - {'type': 'error', 'error':
{'type': 'invalid_request_error', 'message': 'This model does not support
assistant message prefill. The conversation must end with a user message.'}}
```

`complete_json()` used to seed the assistant's reply with `{` so the model had
no room for a preamble. Anthropic removed prefill in the 4.6 and 5 generations —
Sonnet 4.6 and 5, Opus 4.6 and 5, Haiku 4.5 — and both models in the shipped
`config.yaml` are affected.

`llm.complete()` now handles it: models known to refuse are never sent a
prefill, and any other model that refuses is remembered after one attempt, so
the wasted call is paid once per model rather than once per call.

Nothing replaced the guarantee prefill gave, because nothing needed to. The
`SYSTEM` instruction already tells the model to return only the object, and
`_parse_json()` strips code fences and falls back to the outermost balanced
object — which covers the preamble case the prefill existed to prevent.

If you pin an older model in `config.yaml`, prefill is attempted again for it
automatically. Anthropic's own recommendation for new work is structured
outputs (`output_config.format`) rather than either approach; that would be a
larger change and is not needed while the parser holds.

---

[← Cost](cost.md)  ·  [Index](index.md)
