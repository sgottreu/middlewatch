# Bible Promote — Review UI Plan

Addendum to `docs/story/bible-template-plan-2026-11-14.md`. Save as `docs/story/bible-promote-ui-plan-2026-11-19.md`. Builds the `bible promote` converter (plan Step 4) and its review UI flow (plan Step 5), with Scott's decisions from 2026-11-19.

## Decisions (settled)

- **Genre:** Broken Edges uses `fantasy`, North Star uses `scifi`. Both genre files already exist.
- **Story Ideas feed the ideator.** A bible's Story Ideas section goes to the ideator agent (not the writer or editor). Open Questions and the Minor Names Register stay working-only and are never sent to agents.
- **Factions section is named "Institutions",** with a one-line description under the heading, for example: *Factions, guilds, households, companies — any group that shapes the world.*
- **Validation errors block Apply.** The UI turns each error into an inline field so the missing detail can be filled in on the spot.
- **Filled-in details are saved to both the draft and the promoted bible,** so the draft stays the source of truth and re-promoting never loses them.

## Mandatory screenshot checkpoints

The implementing agent must stop after **every** change to the review interface and send Scott a screenshot of the result, then wait for his go-ahead before continuing. This applies to each new button, dialog, panel and state, including error and empty states, and to both desktop and phone widths when layout differs. Do not batch several UI changes behind one screenshot. If Scott asks for changes, make them and screenshot again before moving on.

## The flow

1. **Entry point.** Drafts already appear greyed out in the New outline dialog (`app.html`, `GET /api/bibles`). Add a **Promote** button beside each draft. Optionally also a Bibles section reachable from the Stories/Casting tabs. *Screenshot checkpoint.*
2. **Preview (free).** Promote runs the converter as a runner job (`runner.start_command`, keyed by series) in dry-run mode and shows: canon facts found, recurring cast extracted, sections kept and sections dropped as working-only, the diff against any existing `<name>.md`, and the `bible check` result. *Screenshot checkpoint.*
3. **Errors block Apply.** If `bibles.validate` returns any fatal problem, Apply stays disabled. Each problem renders as an inline input next to what it refers to: a genre dropdown, cast fields (age, gender, station), required front-matter fields. *Screenshot checkpoint, including the error state.*
4. **Fill in.** Saving a field writes the value into the draft (`<name>.draft.md`) and re-runs the preview, so the bible is generated from the updated draft. Re-validate after every save. *Screenshot checkpoint.*
5. **Apply.** Once clean: write `<name>.md`, append a changelog entry via `changelog.append`, then commit and push only the touched files (draft, bible, changelog). Never force-push. Mirror the Casting Apply flow. *Screenshot checkpoint.*
6. **Ready.** The series becomes selectable in New outline with no further change. *Screenshot checkpoint of the dialog showing it enabled.*

## Converter rules (`python3 -m story_pipeline.cli bible promote <name>`)

- Parse the draft by headings. Canon bullets become `canon`; each `###` under Recurring Cast becomes a `recurring_cast` entry.
- Preserve any `voice` and portrait values already in `<name>.md`; casting apply must never be overwritten.
- Copy prose sections, omitting Open Questions and the Minor Names Register. Keep Story Ideas, marked for the ideator only.
- Run `bible check` before writing; refuse on failure.
- Idempotent: promoting an unchanged draft changes nothing.
- Same logic backs both the CLI and the UI.

## Server endpoints (suggested)

- `POST /api/bibles/<name>/preview` — dry-run job; returns diff, extracted fields, problems.
- `POST /api/bibles/<name>/fill` — `{field, value}`; writes to the draft, re-previews.
- `POST /api/bibles/<name>/apply` — writes bible + changelog, commits and pushes touched files.

## Tests

- Amberlight parses and passes `bible check` unchanged.
- Broken Edges and North Star drafts preview without crashing; their errors map to fields.
- Filling a field updates both the draft and the bible.
- A second promote is a no-op.
- Promote does not overwrite voices set by casting apply.
- Story Ideas reach the ideator prompt and no other agent; Open Questions reach none.

## Conventions

`python3` and `python3 -m pip` only; venv creation and activation in any install steps. Deploy with `scripts/deploy/mw-ship`.
