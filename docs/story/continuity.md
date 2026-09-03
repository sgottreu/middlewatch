# Consequences across a series

Design for a per-series `continuity` setting. **Not built.** This is the plan for
letting one series carry consequences forward while another stays an anthology.

The question that prompted it: if a recurring character marries in one story,
what stops them being courted and married in another?

Today, nothing does.

---

## What exists now

`canon` in the bible is the enforcement surface. `editor.md` tells the editor
that chapters "must agree with [the bible's] established facts and recurring
cast," and to list contradictions. So if the bible said *Mr Silas Rowe married
Miss Judith Vane*, a later courtship would be caught.

Three things stop that from working on its own.

**Nothing writes back.** The code only ever reads `.canon`. No stage appends to a
bible after a story is finished, and nothing prompts you to. The marriage exists
only in the story that contained it.

**`arc.md` designs the loop and has no code behind it.** It specifies
`known_facts` per character, says "Facts enter the bible only after they appear
in a finished story", and describes an arc bible "updated after each story is
finalized". No module reads or writes one; `arc_slug` survives only as a label on
a ledger row.

**`canon` is timeless.** It means *true in every story*. A marriage is not — it
is true from one story onward. Adding it flatly makes every earlier story wrong,
and the pipeline has no story order to reason about anyway.

---

## The real tension

`arc.md` is emphatic, and it is right for what it was written for:

> Continuity is a **reward for the returning viewer**, never a **requirement for
> the new one.**

A recognised face is a reward. A marriage is not — it changes how every scene
reads. Someone who watches episode five and then episode two does not get a
bonus; they get a contradiction.

So the two things genuinely conflict, and the resolution is not to pick a winner
globally. It is to let each series say which one it is.

---

## Two modes

Set in the bible's frontmatter. Unknown keys are already ignored by the loader
and the validator, so adding this breaks nothing and every existing bible keeps
working.

```yaml
continuity: standalone      # default — today's behaviour
continuity: chronological   # consequences persist
```

| | `standalone` | `chronological` |
|---|---|---|
| Watch order | Any. That is the point. | Publication order. |
| `canon` | Timeless facts only | Timeless facts **and** dated ones |
| Recurring cast | Fixed points. No irreversible change. | May change, permanently. |
| A marriage | Belongs to a guest character | May happen to anyone |
| New viewer arriving at #6 | Fully served | Sees consequences of stories they haven't watched |
| Playlist | Any order works | Ordered, and the order matters |

**`standalone` is the default and should stay the default.** It is what makes a
YouTube playlist work when most arrivals at any given video are first-time
arrivals — the reason `arc.md` argues for it. Choose `chronological` for a series
you intend people to watch through.

### What standalone should say that it currently doesn't

Nothing today tells the ideator that recurring characters are fixed points. That
is the actual bug behind your question — the pipeline never states the rule it
depends on.

In `standalone`, the bible block should carry a line to that effect: recurring
characters may be changed *by* a story emotionally, but end it in the same
social position they began. Marriages, deaths, departures and inheritances
belong to the guest cast, who exist for one story and can be spent freely.

That single sentence would have prevented the problem without any of the
machinery below.

---

## How a fact would enter canon

The loop, in `chronological` mode.

**1. The story proposes.** After `write` completes, one small call — the whole
story is already assembled for the editor's context — asks what is now true about
the recurring cast that was not true before. Sonnet, roughly $0.02.

Written to `canon_candidates.json` in the bundle. Nothing is committed yet.

```json
[
  { "who": "Mr Silas Rowe",
    "fact": "Married Miss Judith Vane at Amberlight church.",
    "chapter": 8,
    "quote": "…the curate was married on the Tuesday, quietly, with Mrs Pike in the second pew." }
]
```

**2. You approve.** The candidates appear in `cli review` beside the chapter
queue, each with the line that established it. Approving appends to the bible;
rejecting drops it. This is the same shape as every other gate here — the model
proposes, nothing changes until you say so.

**3. The bible records it, dated.**

```yaml
canon:
  - "The living at Amberlight is worth two hundred and forty a year."   # timeless
  - fact: "Mr Silas Rowe married Miss Judith Vane."
    since: 4
    established_in: the-bath-will
```

A plain string stays timeless, so every bible written so far parses unchanged. A
mapping is dated.

**4. The changelog records that it happened.** `changelog.py` already exists for
this, and a fact entering canon is exactly the kind of change that is invisible
six months later. Entry kind `canon`, attributed to the story that established
it — the same way portrait promotion is attributed today.

---

## Ordering

`chronological` needs stories to have an order, and nothing currently provides
one beyond a `created` timestamp.

The natural source is the approval itself: when a story is approved in a series,
it takes the next episode number, pinned into its manifest. That matches the rule
`arc.md` already states — facts enter after a story is *finished*, not when it is
imagined — and it means a rejected outline never consumes a number.

The ideator and editor then see only the canon true **as of this episode**:
timeless facts always, dated facts where `since` is at or below this story's
number. A story written later but set earlier is possible, and needs its episode
number set by hand.

---

## What it costs

Worth stating plainly, because the pipeline is currently optimised against it.

- **A viewer arriving mid-series is no longer fully served.** That is the whole
  trade. `standalone` exists because YouTube surfaces individual videos.
- **Order becomes load-bearing.** Republishing out of order, or deleting a
  middle episode, breaks the chain.
- **The bible accumulates.** Fifty stories of dated canon is a large prompt, and
  eventually needs summarising rather than listing.
- **A wrong fact is expensive.** Approving "Silas married Judith" when the story
  actually left it ambiguous constrains every later story. The review step is
  what protects against it, which is why it is a gate rather than automatic.

---

## Build order

Each step stands alone.

1. **The standalone rule, stated.** One sentence in the bible block telling the
   ideator that recurring characters end where they began. Free, no schema
   change, fixes the immediate problem. **Do this first regardless of the rest.**
2. **`continuity:` in the bible**, read and validated, defaulting to
   `standalone`. Changes no behaviour yet.
3. **Episode numbers**, assigned at approval, pinned in the manifest.
4. **Dated canon** — the mapping form, and filtering by episode when the block is
   rendered.
5. **`canon_candidates`** — the proposing call and the review gate.
6. **Changelog integration**, which is a few lines once 5 exists.

Step 1 is worth doing this week. Steps 2–6 are only worth building when you
actually want a series that carries consequences — and it is worth asking whether
that series should be a different format entirely, since a chronological serial
on YouTube is a different product from an anthology.

---

## What stays true in both modes

- Recurring characters keep their **face and voice** across stories. That is
  `bibles/<name>.cast/` and the `voice:` field, and it has nothing to do with
  plot consequences.
- The bible's `canon` is still the only place a fact is enforced.
- Nothing enters canon without you approving it.
- `history.py` still weights tropes away from what has been approved, which is a
  separate axis: it stops stories rhyming, not stories contradicting.
