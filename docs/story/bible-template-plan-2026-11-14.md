# Plan: Expand the Series Bible Template

Goal: extend the Middle Watch pipeline's series bible template so a bible can hold full worldbuilding, add a working-draft format with the same sections, and add a converter that promotes a draft into the pipeline bible, runnable from the review UI. First real use: Mapping the Broken Edges (`bibles/broken-edges/`).

## Context the implementing agent needs

- Bibles live at `bibles/<name>/<name>.md`, with portraits in `<name>.cast/` and the changelog in `<name>.changelog.md` (`story_pipeline/bibles.py`, `path_for`).
- A bible is YAML frontmatter (series, genre, canon, recurring cast with voice and portrait) plus prose the agents read (`prompt_block`). `TEMPLATE` and `EXAMPLE` in `bibles.py` are what `scaffold` and the CLI's new-bible command write.
- `available()` only counts `<name>/<name>.md`, so `broken-edges.draft.md` is currently ignored by the pipeline. Keep that behaviour for drafts.
- `casting_apply.py` edits `recurring_cast[].voice` line by line, and `bible check` validates bibles. Neither may break.
- Amberlight (`bibles/amberlight/amberlight.md`) must keep parsing and passing `bible check` unchanged.
- Python conventions: `python3`, `python3 -m pip`, venv steps in any install instructions.

## Step 1: Read before changing

Read `bibles.py` (TEMPLATE, EXAMPLE, `_parse`, `prompt_block`, check logic, `scaffold`), `changelog.py`, `casting_apply.py`, the CLI bible commands in `cli.py`, and how the review UI registers and runs job types (the Casting page and its Apply flow are the closest model).

## Step 2: Expand the template

Keep the existing frontmatter fields and add optional ones, so older bibles stay valid:

- `recurring_cast[]`: add optional `role` and `region` alongside the existing name, description, voice and portrait.
- Optional `regions[]` (name, one-line summary) and `factions[]` (name, region, focus), for agents and tooling that want structure.
- Optional `assets` listing `map` and `layout` images in the series folder.

Add these prose sections to the template body, each as a `##` heading:

1. Premise
2. Setting, with `###` subsections: Technology, Magic, Flora and Fauna, Travel and Trade (including barriers and hazards)
3. Regions, one entry per region
4. Map layout
5. Factions and Documenting Groups (or a genre-neutral name such as Institutions)
6. Recurring Cast, one `###` per character
7. What each story does on its own (existing section, keep)

Working-only sections that live in the draft but are NOT sent to the writing agents:

- Open Questions
- Story Ideas (optionally passed to the ideator agent only)
- Minor Names Register

## Step 3: Agent-facing output

Update `prompt_block` so agents receive the premise, canon, setting, regions, factions and cast, and never the open questions. Decide whether Story Ideas go to the ideator agent; default is no.

## Step 4: Draft format and converter

- Draft file: `bibles/<name>/<name>.draft.md`, same section headings, no frontmatter required. This is the format Claude Docs exports.
- New CLI command, for example `python3 -m story_pipeline.cli bible promote <name>`:
  1. Parse the draft by headings.
  2. Canon bullets become `canon`; each `###` under Recurring Cast becomes a `recurring_cast` entry. Keep any existing voice and portrait values already in `<name>.md`.
  3. Copy the prose sections, minus the working-only ones.
  4. Show a diff against the current `<name>.md` and write only on confirmation.
  5. Run `bible check` before writing; refuse on failure.
  6. Append a changelog entry via `changelog.append`.
- Must be idempotent: re-running on an unchanged draft changes nothing.

## Step 5: Review UI

Add a Promote Draft action for a series, as a job type in the existing runner, mirroring the Casting Apply flow: diff preview, confirm, write, validate, then commit and push only the touched files, never force-pushing.

## Step 6: Assets

Make `bible check` accept `<name>.map.jpg` and `<name>.layout.png` in the series folder without warnings.

## Step 6b: Continuity log (agents never edit the bible directly)

The bible stays the curated source of truth; agents must not rewrite it. Instead:

- After each story is finalised, its agent appends an entry to an append-only continuity log, for example `bibles/<name>/<name>.continuity.md`: new names, places and facts the story established, anything it changed, and threads it left open, each tagged with the story slug.
- Scott reviews entries and promotes the ones worth keeping into the bible (by editing the draft and re-running promote). Promoted and rejected entries are marked, never deleted.
- Writing agents read the bible plus a trimmed summary of recent or relevant unpromoted log entries, not the full log, to keep context small.
- Add new minor names to the log so they can feed the Minor Names Register and avoid reuse.

## Step 7: Tests

Test against synthetic data first, then:

- Amberlight parses and passes `bible check` unchanged.
- The Broken Edges draft promotes to a valid `broken-edges.md` with all eight cast members and empty voices.
- A second promote is a no-op.
- A promote does not overwrite voices set later by casting apply.

## Decisions for Scott

- Genre value for Broken Edges (a new fantasy genre file may be needed).
- Whether Story Ideas feed the ideator agent.
- The genre-neutral name for the factions section.
