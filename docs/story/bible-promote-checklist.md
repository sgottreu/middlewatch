# Bible promote checklist: draft → pipeline bible

Use this in voice sessions while working on a series draft (`bibles/<name>/<name>.draft.md`). It lists everything the review app's **Promote** needs, and how to write each item in the draft so the converter picks it up. Last checked against the code on 2026-11-20 (`story_pipeline/bible_promote.py`, `bibles.validate`).

**How to use it in voice:** pick a series, go through Part 1 (blocking) first, then Part 2 (strongly recommended), then Part 3 (optional). For each item, say what's there, ask Scott for what's missing, and give him the exact line to add to the draft. Don't invent facts; if Scott hasn't decided, leave it and note it under Open Questions.

---

## Part 1: Blocking. Promote refuses until these are done

- [ ] **Genre.** A line directly under the `#` title, before the first `##`:
  `Genre: fantasy` (or `scifi`, or `regency`). Only those three genres exist.
- [ ] **Series name.** Optional, but set it to avoid a derived name. Same place:
  `Series: Mapping the Broken Edges`. If missing, the working title is used.
- [ ] **Canon section** (`## Canon`) with at least one bullet. Each bullet is one plain fact true in every story: geography, who's related to whom, what everyone knows. Not atmosphere.
- [ ] **Every recurring character has a real, unique name.** Placeholders like "Juniper's father" don't count.
- [ ] **No two names sound alike out loud.** The narration validator rejects names that are too close by ear (e.g. two characters starting with the same sound). Rename one if they clash.
- [ ] **Gender, if given, is exactly** `female`, `male` or `neutral`.
- [ ] **Age, if given, is a whole number** (`Age: 45`, not "mid-forties").
- [ ] **Aliases point at one character only.** An alias can't match another character's name or alias.

## Part 2: Strongly recommended. Promote works, but quality drifts without them

For **each** recurring character, under their `###` heading (or `**Name**` bullet), add these lines:

- [ ] `- Gender: female` (male / neutral). Without it, voice casting falls back to the female pool. The app guesses from words like "father", but only a written line counts.
- [ ] `- Age: 34`
- [ ] `- Station: ` their role or social position in a few words (e.g. "Glasswood mapper", "independent courier").
- [ ] `- Appearance: ` what they look like. Goes straight into the image prompt; without it every story invents a new face.
- [ ] `- Dress: ` what they usually wear. Same reason.

If one heading covers two people, name who each line is for: `- Gender (Tess Ridley): female`.

- [ ] **Premise** section (`## Premise`): what the series is, in a short paragraph.
- [ ] **Story Ideas** section (`## Story Ideas`): bullets of story seeds. These go to the ideator only, so New outline can draw on them.

- [ ] **Story Engine** section (`## Story Engine`): what every story turns on, in one paragraph. Every agent reads it.
- [ ] **Not This Series** section (`## Not This Series`): the genre's stock moves this world rules out. The bible outranks the genre guide.
- [ ] **Narrow the genre**, under the title next to `Genre:`:
  `Subgenres: mystery, slice_of_life` (which of the genre's subgenres fit; New outline rotates through them) and
  `Exclude tropes: The hedge-witch, Magical debt` (names exactly as in the genre file; `cli genres --tropes` lists them).
- [ ] Optional **Series Tropes** section (`## Series Tropes`): `- **Name:** note` bullets, added to this series' trope menu only.

## Part 3: Optional structure

- [ ] `- Role: ` and `- Region: ` per character (fantasy series especially).
- [ ] `## Setting` with `### Technology`, `### Magic`, `### Flora and Fauna`, `### Travel and Trade`.
- [ ] `## Regions`: one entry per region.
- [ ] `## Map layout`.
- [ ] `## Institutions`: factions, guilds, households, companies, any group that shapes the world.
- [ ] Map/layout images beside the draft: `<name>.map.jpg`, `<name>.layout.png`.

## Never sent to agents (fine to keep, the converter drops them)

`## Open Questions`, `## Minor Names Register`, `## Changelog`. Park undecided things in Open Questions.

---

## Where each series stands (2026-11-20)

### Mapping the Broken Edges (`broken-edges`, fantasy)

- [ ] **Blocking:** add `Genre: fantasy` under the title.
- [ ] Optional: `Series: Mapping the Broken Edges`.
- [x] Canon: 4 facts.
- [x] Cast: 8 characters with their own headings (Garret Marsh, Wren Calloway, Maud Brennick, Jory Brennick, Bram Tolliver, Tobias Finch and Tess Ridley, Matthias Kettering).
- [ ] For all 8: Gender, Age, Station, Appearance, Dress lines (genders are only guessed so far).
- [x] Story Ideas section exists.
- [ ] Consider renaming `## The Documenters` to `## Institutions`. Not required; it's copied as prose either way.

### North Star (`north-star`, scifi)

- [ ] **Blocking:** add `Genre: scifi` under the title.
- [ ] **Blocking:** give Juniper's father and mother real names (both currently "Unnamed"). Check the new names don't sound like "Juniper" or each other.
- [ ] Optional: `Series: North Star`.
- [x] Canon: 6 facts.
- [ ] Decide whether **Captain** (the beagle) is a recurring cast member. If yes, give him `Gender: male` and Appearance; he won't need a narration voice unless he speaks.
- [ ] For each person: Gender, Age, Station, Appearance, Dress.
- [ ] Add a `## Story Ideas` section.
- [ ] Optional: Premise is there; Setting has only `### The Circuit` (Dorsey's, Okafor, the farming planet, the research station). Consider an `## Institutions` section (station co-ops, the Okafor family claim).

---

**When done:** commit the draft to the repo, then in the review app choose the series in the dropdown → **Promote…** → fix anything the preview flags → **Apply**.
