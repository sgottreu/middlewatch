# Arc structure

This document governs how stories relate to each other. `style.md` governs how
they sound. The two are read together and neither overrides the other.

An **arc** is four stories that share a world, a cast, and a place. It is not a
serial. Each of the four is a complete story that a listener can watch first,
last, or alone without confusion.

Arcs are numbered volumes inside an open-ended series. *Amberlight* does not end.
*Amberlight, Volume One* is four stories long and then it is finished.

---

## The one rule everything else follows from

**Every story stands alone.**

A viewer arriving at episode three, having never seen episodes one or two, must
be able to follow it completely and reach a satisfying ending. This is not a
stylistic preference. YouTube surfaces individual videos rather than playlists,
so most arrivals at any given episode will be first-time arrivals with no
context. A story that depends on a prior episode loses them inside thirty
seconds.

Continuity is a **reward for the returning viewer**, never a **requirement for
the new one**.

If a beat only lands for someone who watched an earlier episode, that beat is
decoration. If a beat cannot be understood without an earlier episode, that beat
is a defect.

---

## The arc bible

Every arc has one file: `arcs/<arc-slug>/bible.json`. It is written before the
first story is outlined and updated after each story is finalized.

The ideator reads it before producing an outline. The writer reads it before
producing prose. The editor reads it before reviewing. Nothing about the arc is
passed between agents through context. The file on disk is the only shared state,
for the same reason every other handoff in this pipeline is a file.

```json
{
  "arc_slug": "amberlight-vol-1",
  "series": "Amberlight",
  "volume": 1,
  "setting": {
    "place": "One-paragraph description of the village, house, or district.",
    "season_and_year": "Autumn, 1813",
    "the_constraint": "The social or material fact that generates plots here."
  },
  "cast": [
    {
      "name": "",
      "role_in_arc": "recurring | protagonist-of-N | mentioned-only",
      "appearance": "Inherited verbatim from the first story that introduced them.",
      "dress": "",
      "voice_note": "",
      "portrait_ref": "arcs/<arc-slug>/cast/<name>.png",
      "known_facts": ["Facts established on screen. Nothing else is true."]
    }
  ],
  "locations": [
    { "name": "", "description": "", "ref": "arcs/<arc-slug>/locations/<name>.png" }
  ],
  "threads": [
    {
      "id": "curate-in-bath",
      "planted_in": 1,
      "pays_off_in": 3,
      "planted_as": "How it appears in episode 1. Must read as incidental there.",
      "payoff": "What it turns out to mean.",
      "status": "planted | paid | abandoned"
    }
  ],
  "episodes": [
    { "n": 1, "slug": "", "protagonist": "", "one_line": "", "status": "planned | written | rendered | published" }
  ]
}
```

Facts enter the bible only after they appear in a finished story. The bible
records what has been established, not what someone intends to establish.

---

## How the four connect

Use these three devices. They create continuity without creating dependency.

**Rotating protagonist.** A minor character in one story becomes the protagonist
of a later one. The listener who saw the earlier story recognizes them. The
listener who did not simply meets someone new. Both experiences work.

**Deferred payoff.** A detail is dropped in an early story where it reads as
incidental colour, and turns out to matter later. The rule: the payoff episode
must restate whatever it needs. The earlier planting adds recognition, not
information.

**Persistent place.** The same house, village, assembly room, and shop across all
four. This is what makes the arc feel like a world instead of an anthology, and
it is also what lets the designer reuse location references.

Do not use: cliffhangers, "previously on" recaps, unresolved endings, plots that
resume mid-motion, or a mystery whose solution is withheld across episodes.

---

## Instructions by agent

### Ideator

Before outlining, read the bible.

- Take the protagonist and premise from the `episodes` entry for this number if
  one exists. If the slot is empty, propose one and write it back.
- Any recurring character you use must be consistent with their `known_facts`.
  You may add facts. You may not contradict them.
- Reuse existing `locations` wherever the story allows. Introduce at most one new
  named location per story.
- Check `threads` for anything with `pays_off_in` equal to this episode number.
  You must build the payoff into the outline, and the outline must carry enough
  information for the payoff to land cold.
- You may plant one new thread. Not more. A thread that reads as significant on
  first appearance has failed as a plant.
- The ending resolves this story's central question. An arc-level question may
  remain open only if this story's own question is closed.

Output the outline as normal, plus a `bible_delta` object listing new characters,
new locations, new facts about existing characters, and any thread you planted or
paid off.

### Writer

Before writing, read the bible.

- Recurring characters get a fresh introduction every time, written as though the
  listener has never met them. One clause of physical detail and one of manner is
  usually enough. Never write "as you will remember" or any equivalent.
- Physical description of a recurring character must match their bible entry. The
  illustrator is working from a pinned reference portrait and prose that
  contradicts it will produce a visible mismatch.
- Where a planted thread is being paid off, the payoff scene carries its own
  setup. Assume the plant was never seen.
- Do not reference the events of another episode by name, number, or as "last
  time". You may reference the *events* as things that happened in this world,
  the way any character refers to the past.

### Editor

Read the bible. Fail a chapter for any of the following, in addition to the
existing `style.md` conditions.

- **Dependency.** Any passage that a first-time listener could not follow.
- **Contradiction.** Any statement inconsistent with a `known_facts` entry or a
  character's recorded appearance.
- **Recap.** Any explicit reference to a previous episode as a previous episode.
- **Open ending.** This story's central question left unresolved.
- **Unearned payoff.** A thread paid off without local setup sufficient to carry
  it alone.
- **Overplanting.** More than one new thread introduced.

Report contradictions with the bible path and the conflicting value, so the fix
is unambiguous.

---

## Production consequences

**Cast portraits are generated once per arc, not once per story.** The designer's
cast sheet phase runs against `arcs/<arc-slug>/cast/` and every story in the arc
passes the same reference portraits into every scene call. This is the main cost
saving and the main reason four episodes look like one thing.

**The art style string is pinned at arc level** in the bible and inherited by
every story manifest in the arc. Changing the default afterwards does not move a
published arc.

**A fifth asset exists.** When all four are rendered, concatenate them into a
single feature-length upload. Every input already exists, so this costs an ffmpeg
concat. It is a separate video and often the one that performs best, because this
audience prefers one long file to four short ones.

**Ambient tie-in.** Four ambient tracks from the arc's locations, released
alongside, reusing the location references. This is what the cross-channel badge
system exists to promote.

---

## Titling

Three levels of hierarchy do not fit in a title bar alongside the words that
drive discovery.

- **Video title:** carries the search terms. The story's own hook.
- **Playlist name:** carries series and volume. *Amberlight, Volume One*.
- **Thumbnail badge:** carries the series mark and the episode number.

The house brand lives in the channel name and appears in no title.

---

## Operational note (not for agents)

Do not produce all four before publishing one.

The shared cast makes it tempting to plan and generate the whole arc up front,
which commits four stories of spend to an untested premise. Write the bible,
generate the cast portraits, produce episode one, publish it, and watch the
retention curve for two weeks. Then decide whether to continue, revise the arc,
or abandon it.

The pipeline is file-based and resumable so that stopping here is cheap. Stopping
here is the point.

---

## House rules for this document

This file sits beside `style.md` and obeys it. No em-dash asides, no "not X but
Y", no trailing participial analysis. If you edit it, keep it that way.
