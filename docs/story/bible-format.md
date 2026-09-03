# Writing a series bible by hand

The reference for authoring `bibles/<name>.md` yourself — either preloading one
before the ideator has ever seen it, or editing a scaffold before the first story
in the series. [Series bibles](bibles.md) covers what a bible is *for*; this
covers what the loader accepts and what each field actually reaches.

The safe window is **before the first `new --bible <name>`**. Until then every
field is free to change with no consequences. After it, some fields stop mattering
for stories already created — the last section says which.

---

## Creating one

```bash
python3 -m story_pipeline.cli bible new amberlight --genre regency \
    --series "Middle Watch: Amberlight"
```

Writes `bibles/amberlight.md` from a commented template and refuses if the file
already exists. `--genre` is required and must be one of the installed genres;
`--series` is optional and defaults to the filename with hyphens turned to spaces
and title-cased.

Add `--example` for a scaffold that ships **filled in** rather than commented
out — two complete cast members, real canon entries, nothing to uncomment. The
YAML shape is then unambiguous and `bible check` passes on it immediately, which
makes it the better starting point if you are writing one by hand. The content is
generic on purpose: it is a shape to edit, not a series to keep.

You do not have to use the command. A hand-written file that parses is
indistinguishable from a scaffolded one — `bible new` only saves you typing.

The **filename stem is the bible's name**. `bibles/amberlight.md` is
`--bible amberlight`. Nothing inside the file changes that.

---

## The shape

```markdown
---
series: "Middle Watch: Amberlight"
genre: regency

canon:
  - "A fact a story could contradict."

recurring_cast:
  - name: Mrs Honoria Pike
    age: 58
    gender: female
    station: widow of a naval surgeon; three hundred a year
    appearance: Short and broad, grey hair pinned under a lace cap.
    dress: Black bombazine with a white fichu, ten years out of fashion.
    voice: Matilda
    note: Knows everything first and is wrong about a third of it.

art_style: >
  Optional. Overrides the genre's art direction for this series.
voices:
  narrator: Charlotte
---

# Middle Watch: Amberlight

Prose from here down. Everything below the second `---` is handed to the agents
verbatim, ahead of the canon and cast lists.
```

**The file must start with `---`.** `_parse` splits on the first two `---` and
raises `has no frontmatter block` otherwise. A `---` used as a horizontal rule in
the prose body is fine; the split has already happened by then.

---

## Frontmatter fields

| Field | Required | What it does |
|---|---|---|
| `genre` | **yes** | Must match an installed genre. `Bible.genre` raises without it, and `--genre` on `new` must agree with it or be omitted. |
| `series` | no | Display name. Defaults to the filename stem. |
| `canon` | no | List of strings. Shown to all four agents as *Established facts*; the editor treats a contradiction as a continuity failure. |
| `recurring_cast` | no | List of characters. `[]` is valid and is what the scaffold ships. |
| `art_style` | no | Overrides the genre's art direction. Pinned per story at creation. |
| `voices` | no | Overrides the genre's casting, e.g. `narrator:`. Pinned per story at creation. |

### Writing canon

Facts a story could contradict — geography, money, who is related to whom, what
happened that everyone knows. Not atmosphere; atmosphere belongs in the prose
body. The agents are told not to re-explain canon at length, because a viewer may
be watching this story first.

```yaml
canon:
  - "The living at Amberlight is worth two hundred and forty a year."
  - "The London coach stops at The Bell on Tuesdays and Fridays, and no other day."
```

---

## Cast members: what each field reaches

This is the part worth reading closely, because the fields do not all travel the
same route and two of them never reach the ideator at all.

| Field | Required | Where it goes |
|---|---|---|
| `name` | **yes** | The join key for everything below. Also shown to the text agents. |
| `age` | no | Shown to the text agents; used in the portrait prompt. |
| `station` | no | Shown to the text agents. |
| `note` | no | Shown to the text agents only. Never reaches the image or voice models. |
| `gender` | no | **Not shown to any agent.** Forced onto the outline, where it drives voice casting by gender. |
| `appearance` | no | **Not shown to any agent.** Forced onto the outline, then used verbatim in the portrait prompt. |
| `dress` | no | Same as `appearance`. Appears as `Wearing: <dress>`. |
| `voice` | no | **Not shown to any agent.** Forced onto the outline, where the actor honours it as an explicit casting override. |
| `portrait` | no | Filename inside `bibles/<name>.cast/`. Manual override only — see below. |

### Only four fields reach the ideator

`prompt_block()` emits **name, age, station and note** — nothing else. The
ideator never sees a character's appearance, dress, gender or voice, and cannot
reason about them.

They still end up correct. `_apply_bible_casting()` runs after generation and
forces every bible-stated field back over whatever the model produced, before the
outline is validated. So the ideator invents an appearance, and yours overwrites
it.

Two consequences for authoring:

- **Write `appearance` and `dress` as image prompts, not as prose.** They are
  interpolated straight into the portrait request with no rewriting. Concrete and
  visual — age, build, hair, colouring, the specific garment. No personality
  adjectives; the image model cannot draw *shrewd*.
- **Anything you want the ideator to actually think about goes in `note` or in
  the prose body.** A character's temperament belongs there, not in `appearance`.

### Names must match exactly

`member()` compares on the full name, case-insensitively:

```python
if c["name"].lower() == name.lower():
```

If the bible says `Mrs Honoria Pike` and the ideator writes `Honoria Pike`, that
is **not a match**, and nothing warns you. The character silently gets no forced
fields, no pinned voice, and a fresh portrait rather than the series one.

This is the single most likely way a hand-written bible fails to do anything.
Write the name in the form you expect to appear in the story, and check the first
outline: a matched character comes back with `"recurring": true` in
`outline.json`. If that flag is missing, the join failed.

### `voice` must name a voice you have

The value is a name from `elevenlabs.voice_library` in `config.yaml`, not a voice
ID. `resolve_voice` raises on an unknown name — at cast time, before any audio is
paid for, but after the story is written. Run `python3 -m story_pipeline.cli
voices` and check the name exists before committing it.

A bible `voice` outranks turn-count casting and does not consume one of the
`max_named_voices` slots.

### `portrait` is not filled in for you

`save_portrait()` copies the
generated image to `bibles/<name>.cast/<slugified-name>.png` and never edits the
YAML. Set `portrait:` only when you want a filename other than the slug — for
instance to point two bible entries at one image, or to drop in a portrait you
made yourself.

To preload a face by hand: put the file at
`bibles/<bible>.cast/<slugified-name>.png` — lowercase, every non-alphanumeric
character replaced with a hyphen. `Mrs Honoria Pike` becomes `mrs-honoria-pike.png`.
The designer finds it and skips generation.

---

## Editing an existing bible

Bibles are loaded fresh from disk on every run. Only the bible's *name* is pinned
into a story's manifest, never its contents — so an edit reaches every future
story and also changes what the writer and editor see if you re-run an old one.

What that means per field, for a story that has already been created:

| Edit | Effect on an existing story |
|---|---|
| `canon`, prose body, `note` | Takes effect on the next `write`. The editor judges against the new text. |
| `gender`, `appearance`, `dress`, `voice` | **No effect.** These are copied onto `outline.json` at ideate and read from there afterwards. Edit the outline instead, or start a new story. |
| `art_style`, `voices` | **No effect.** Pinned into the manifest at creation. |
| Adding a cast member | Available to the next story. Does not join retroactively. |

So the rule is simple: **structural decisions about a character — their look,
their voice, the spelling of their name — are worth getting right before the
first story runs.** Canon and prose can grow as the series does; that is what
they are for.

Portraits are the exception that works in your favour. The first story to
generate a face promotes it into `bibles/<name>.cast/`, and every later story
reuses it. Delete the png to force a regeneration.

---

## The changelog

Every bible has a sidecar at `bibles/<name>.changelog.md`, alongside
`<name>.cast/`. Nothing in the pipeline reads it — it is for you.

```bash
python3 -m story_pipeline.cli bible log amberlight -m "Added the docking curfew to canon."
python3 -m story_pipeline.cli bible log amberlight -m "..." --kind canon --author "Scott"
python3 -m story_pipeline.cli bible log amberlight          # show the history
```

Entries are newest first and each records who made the change:

```
## 2026-08-29 14:03 · Scott · canon · sha=4f2a9c1e

Added the docking-ring curfew to canon.

## 2026-08-28 09:12 · pipeline (the-hartfield-letter) · portrait · sha=1b7e0d33

Promoted portrait for Mrs Honoria Pike into `amberlight.cast/`.
```

**The pipeline writes to it too.** The first story to draw a recurring character
promotes that face into the series, and every later story reuses it — a real
change to the bible that nobody makes deliberately and that is otherwise
invisible. Those entries are attributed to `pipeline` and name the story that
caused them.

Author is resolved in this order: `--author`, then `author:` in `config.yaml`,
then `git config user.name`, then your login name.

### Why it notices when you forget

Each entry stores a short hash of the bible as it stood when the entry was
written. `bible check` compares it against the file:

```
amberlight: ok  (edited since the last changelog entry, 2026-08-29 14:03 by Scott)
```

That is a note, not an error — it never blocks a run. It exists because a
changelog nobody is reminded to update stops being true within a month, and a
log that is wrong is worse than none. A bible with no log at all reports
`(no changelog yet)` rather than nagging about history that was never kept.

The log is a plain markdown file. Editing it by hand is fine; a mangled one
parses as empty rather than raising, because a changelog is never worth failing
a run over.

---

## Checking your work

```bash
python3 -m story_pipeline.cli bible check <name>    # one
python3 -m story_pipeline.cli bible check           # all of them
```

Free, no API calls, and it runs the same error-level checks the ideator runs
before it spends anything. Exit code 1 if any error is found.

**Errors** stop `new --bible`. They are things that will raise, or that will
quietly produce a broken series:

| Check | Why it is fatal |
|---|---|
| Missing `---`, invalid YAML | The loader raises. |
| `genre` missing or not installed | `Bible.genre` raises. |
| A cast member with no `name` | Nothing can join to it. |
| Duplicate names | The join key must be unique. |
| Names too similar by ear | `_validate_outline` rejects the outline — *after* the ideator has been billed. |
| `gender` not female/male/neutral | Voice casting looks up the pool by this string. |
| `age` not a whole number | |
| `voice` not in `elevenlabs.voice_library` | Raises at cast time, once the story is already written. |
| `art_style` present but empty | |
| `voices` malformed, or an unknown narrator | |

**Warnings** do not stop anything, but each one is a way the series drifts:
a missing `appearance` or `dress` (the ideator invents one per story, so the face
changes), no `gender` (casting falls back to the female pool), no `series`, an
empty prose body, a leftover scaffold placeholder, a `portrait:` pointing at a
file that isn't there, or a png in `<name>.cast/` that no cast member claims —
usually the old face of a character you renamed.

Then read `bibles/amberlight.md`. It ships filled in rather than as a stub
precisely so there is a worked example of the format.

---

[← Series bibles](bibles.md)  ·  [Index](index.md)
