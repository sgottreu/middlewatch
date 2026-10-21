# Casting — voice auditions per arc

Every arc keeps a record of the voices auditioned for its characters, so a
recast, or a new character, starts from voices already heard rather than from
the 17,500-voice library.

## Where it lives

`stories/<arc>/_casting/`:

| File | What it is | Where it goes |
|---|---|---|
| `candidates.json` | Voices to audition, per character. Edit by hand or from the Casting page. | git |
| `auditions.json` | Every take: voice, id, gender/age/accent, character, story, model, line, file. Verdicts too. | git |
| `CASTING.md` | Readable summary, regenerated on every write: who's cast, auditions by character, the voice pool. | git |
| `audio/<story>/` | The takes, plus an `index.html` to listen side by side. | S3 (`mw-s3-archive push-casting`; `push-all` includes it) |

`_casting` has no `manifest.json`, so nothing that lists story bundles treats it
as a story. `mw-commit-stories` commits its text with the rest of `stories/`.

## The Casting page

`cli review`, then **Casting** at the top of the queue (or `/casting`).

- **Arc** and **Lines from** pick the arc and the story whose lines are used.
  Each character speaks one of their own lines, about 240 characters.
- Every character shows their current voice (on the configured model and on
  v4), every candidate (on v4), the takes heard so far, and the verdicts.
- **Run** buttons: the whole arc, one character, or one voice. Each shows the
  estimate and arms on the first press; the second press spends. Takes
  already on disk are reused, so a run only pays for what's new. The estimate
  is an upper bound — voices that turn out to be missing from the library
  cost nothing.
- Runs are jobs like Record and Design: detached, in `runs/_casting-<arc>/`,
  cancellable, and they survive a server restart. The record is updated after
  every take, so a stopped run keeps what it paid for.
- **Cast / Maybe / No**, with a note, saves as you click. Click the lit button
  again to clear it. Casting one voice moves an earlier cast for that character
  to Maybe.
- **Add candidate** takes a voice id (20 characters), a name and a note.
  Library voices are added to My Voices on their first run, which uses a voice
  slot on the plan.
- **Voice pool** lists every voice heard in the arc that isn't cast as anyone.

A verdict on its own doesn't change anything outside `_casting/`. **Apply
casting…** does that, once the choices are made.

## Applying the cast

**Apply casting…** in the page header shows every change before anything is
written, then writes the cast everywhere a character's voice is decided:

| Where | What changes |
|---|---|
| `config.yaml` | New voices added to `elevenlabs.voice_library`; optionally `casting.mode: cast` (one voice per character, with `max_named_voices` raised to fit) and `elevenlabs.model: eleven_v4`. A backup is left as `config.yaml.<time>.bak`. |
| The bible | `recurring_cast[].voice` for every cast character; `voices.narrator` only if the narrator differs from the genre default. |
| Each story's `outline.json` | `cast[].voice`, which an unrecorded story records with. |
| Each story's `manifest.json` | `pinned.voices`, which a recorded story re-records with. A changed voice marks that narration out of date, which puts the story in the review queue's **Narration out of date** group. Re-recording from there is the step that costs. |
| The bible changelog | One entry listing the cast and any model change. |
| `_casting/auditions.json` | An `applied` entry with the cast, model and mode. |

Edits to hand-written YAML are made line by line, so comments and ordering
survive, and each is re-parsed and checked before anything is written. The
bible must still pass `cli bible check`; if the edit would break it, nothing is
changed. The running review server reloads its config in place.

It then commits exactly the files it changed — nothing else that happens to be
staged — and pushes without force. A refused push leaves the commit on disk and
says so, the same way `mw-commit-stories` behaves.

Things it deliberately leaves to you, and says so in the preview:

- **`config.yaml` is per machine.** It isn't in git. Copy the new
  `voice_library` lines to any other machine that narrates.
- **Bible prose** that names an old voice, such as a voice casting note.
- **Minor characters who aren't cast.** In cast mode they fall back to the
  narrator's voice.
- **The genre's voice pool** for unnamed characters, if it still lists Default
  voices that expire on 2026-12-31.

Apply refuses while auditions or any of the arc's stories are running, and
when the cast is already applied everywhere.

## From a terminal

```bash
scripts/audition-cast.sh stories/amberlight/the-cracked-beam     # every candidate, every character
python3 check_voices.py --config config.yaml --sample --story <story> \
    --speaker "Mrs Hale" --voice AXdMgz6evoL7OPd7eU12 --add-missing   # just one
python3 -m story_pipeline.casting verdict amberlight "Mrs Hale" Elizabeth cast --note "brisk"
python3 -m story_pipeline.casting pool amberlight --gender female
python3 -m story_pipeline.casting show amberlight
```

The page and the command line write the same files.
