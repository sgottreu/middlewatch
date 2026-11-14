# ElevenLabs API key permissions

*2026-11-14. From a session that hit voices and models not working through the
API. Timestamp-suffixed because nothing in the repo covered it; merge into
`setup.md` §3 (see `clean-up.md` section V) and delete this file.*

## Models need no enabling

There is no per-model switch. The model is chosen on each request with
`model_id` (here, `elevenlabs.model` in `config.yaml`), and any voice works with
any model. `GET /v1/models` lists what the key can use.

## Library voices must be added before use

A Voice Library voice has to be in **My Voices** before the API will speak with
it — `POST /v1/voices/add/{public_user_id}/{voice_id}`, or "Add to My Voices"
on the site. Each one takes a voice slot on the plan, so the add fails at the
limit. Casting runs and `check_voices.py --add-missing` do this automatically;
a "couldn't audition" voice is usually this step failing.

## Where to change a key's permissions

elevenlabs.io → **Developers** (left sidebar) → **API Keys** → **…** next to the
key → **Edit**. New keys are restricted by default. Editing doesn't change the
key string, so `.env` and the server need no update.

Service-account keys: profile icon → **Workspace settings → Service Accounts**.

## What this pipeline needs

| Permission | Level | Why |
|---|---|---|
| Text to Speech | Access | Recording and auditions |
| Voices | **Write** | Read uses voices already in My Voices; Write is needed to add library voices (casting runs, `--add-missing`) |
| Models | Read | Optional — only for `GET /v1/models` |

Also check the key's **credit quota** and **IP allowlist**. An allowlisted key
returns 403 from any other IP, so it can work on the Mac and fail on the
Lightsail box, or the reverse.

Sources: [ElevenLabs — How do I authorize myself using an API key?](https://elevenlabs.io/docs/help-center/technical/how-do-i-authorize-myself-using-an-api-key.md),
[ElevenLabs — API Keys](https://elevenlabs.io/docs/overview/administration/workspaces/api-keys),
[ElevenLabs — Add shared voice](https://elevenlabs.io/docs/api-reference/voices/voice-library/share).
