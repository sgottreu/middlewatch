# Style and genres

What the stories sound like, and where each rule lives.


Four files feed every text agent, and they're split by how far each one travels.

| File | Scope |
|---|---|
| `prompts/house.md` | Structure, listenability, trope rules. All genres. |
| `prompts/boundaries.md` | Content lines. All genres, absolute. |
| `prompts/ai-tells.md` | Machine register. All genres, linter-enforced. |
| `prompts/genres/<name>.md` | Voice, diction, subgenres, tropes, art direction, casting. |

`text.py:_render()` assembles them in one place so the writer and editor always
judge against identical text. The moment those diverge, the editor starts failing
chapters for rules the writer was never shown.

A genre file is YAML frontmatter plus a prose voice guide. Frontmatter carries
the subgenres, the trope menu, the art style, and the Polly casting; the prose
below it is what the agents read. One file rather than a table plus separate
prose, because those two drift the instant they live apart — someone adds a trope
and never touches the guide explaining how that genre handles one. Adding a genre
is adding one file; nothing else changes.

## Scene breaks

A jump in time or place inside a chapter is a segment — `{"speaker": "break"}`,
no text — and it renders three ways: `* * *` on the page, two seconds of
silence in the narration (`casting.break_ms`), and nothing at all to the voice.

It is a segment rather than a flag on the segment after it because every path
that rebuilds a segment as `{speaker, text}` would drop a flag — the review
editor does exactly that — and because `chapter_hash` hashes speaker/text
pairs, so a break as a segment makes moving one count as a change and correctly
marks the narration out of date.

**The mark is not the cue.** A reader sees asterisks; a listener gets silence
and no other information, so the sentence after a break has to say when or
where we now are. That rule is in `house.md` for the writer, checked by the
editor, and approximated by the linter: `break_into_dialogue` is an error, and
`break_unanchored` warns when the first sentence after a break names no time
and no place.

`_validate_chapter` repairs placement rather than failing a paid draft: a break
at either end of a chapter marks nothing and is dropped, two in a row collapse
to one, and a segment whose whole text is asterisks — the writer copying the
mark out of the context it was given — becomes a real break. Breaks dropped
this way are counted in `dropped_breaks`.

Anything longer than a night or wider than the parish is usually a chapter, not
a break. Three or more in one chapter is a `break_count` warning for the same
reason.

**Tropes** are a promise to the viewer, so they're structural rather than
advisory. The ideator picks two or three from its genre's list and records them
in the outline; `_validate_outline` rejects four or more, and rejects any trope
that isn't in that genre's menu. The writer gets them in every chapter prompt,
the editor reports in `trope_note` whether the chapter served them or quietly
grew a fourth, and the prose never names one aloud.

The ideator sees a rotating sample of the menu (`trope_sample: 8` of ~15) rather
than the whole thing. Shown all of them it reaches for the same high-probability
three every run, and a channel of stories that all rhyme is the failure mode
worth designing against.

**Art direction and voice casting come from the genre and are pinned per story**
at ideation. Editing a genre file never changes how an existing story re-renders.

# Not sounding like AI

Two files and one code path handle this.

`story_pipeline/prompts/ai-tells.md` is adapted from Wikipedia's *Signs of AI writing*
(CC BY-SA) and is inherited by both the writer and the editor. The source guide
is written for encyclopedic prose and explicitly leaves out the fiction-specific
tells, so roughly half of it — markup, citations, edit summaries — was dropped and
the rest rewritten for narrative.

`story_pipeline/lint.py` enforces the mechanical half. Prompt instructions don't hold
over a long generation: a model told not to write "underscoring" writes it anyway
two thousand tokens later. So the banned vocabulary, the trailing analytic
participles, the copula dodges, the negative parallelisms, the spaced em dashes,
and sentence-cadence uniformity are regexes instead. The linter runs on every
draft, its errors force a revision even when the LLM editor passed the chapter,
and its findings go to the writer as quoted fixes rather than as general advice.

```bash
python3 -m story_pipeline.cli lint stories/<slug>   # free, no API calls, exits nonzero on errors
```

The rules are tuned against period prose rather than generic English, so the
concrete senses survive: *he boasted of his horses*, *the clerk marking the
ledger*, *last will and testament*, and *she sat by the window, sewing* are all
clean, while *her attention to the guests underscoring her commitment* is not.
Add rules in `lint.py`; `CONTEXT_EXEMPT` is where you put the innocent senses of
a flagged word.

**What none of this catches** is the thing that actually matters. The Wikipedia
guide's own warning is that treating the signs as the problem just makes
detection harder, and that's right — the underlying failure is regression to the
mean, generic writing where specific writing belongs. A chapter can pass the
linter clean and still be about nobody. That judgement is the editor's, and the
editor prompt is told to ignore what the linter already covers and spend its
attention on `generic_prose` instead: every sentence that would still be true of
a different story in a different county. More than three of those fails the
chapter on its own.

# The edit loop

The editor fails a chapter on uncovered beats, a missing turn, a setting
violation, borrowed text, a boundary breach, or a lint error, then hands the
writer specific fixes. Two revision passes by default. A chapter that exhausts
them is kept and flagged in `reviews/NN.json` rather than blocking the run.


---

[← Series bibles](bibles.md)  ·  [Index](index.md)  ·  [Content boundaries →](boundaries.md)

---

## Not repeating yourself

`trope_sample` shows the ideator eight of the genre's tropes rather than all of
them, so it cannot reach for the same three every run. That works *within* a run
and does nothing *across* one: a uniform sample is as likely to offer a trope
used in the last four stories as one never used at all.

`history.py` closes that loop. It reads the bundles on disk and biases the next
sample away from what has already been made:

```bash
python3 -m story_pipeline.cli history --bible amberlight --genre regency
```

```
  trope                              used  weight  odds vs unused
  The disputed inheritance              3    0.25  ###
  The misdirected letter                2    0.33  ##
  The ball reveal                       0    1.00
```

Weight is `1 / (1 + uses)`. In practice a trope used three times goes from a 51%
chance of being offered to 23%, and an unused one rises to about 65%.

**Nothing is ever excluded.** A trope used twenty times still surfaces in roughly
3% of runs. Fifteen tropes cannot fill a channel without coming round again, and
a trope the audience liked is worth returning to — the goal is to stop the *next*
three stories rhyming, not to ban anything.

### Only approved stories count

An outline you read and rejected is not part of the channel and does not push
anything down the list. Rejecting at the review gate stays free, and the weights
become a feedback loop on your taste rather than on the model's first instinct.

### What sampling cannot reach

Tropes are handled by the weights, which the model never sees. Three things are
not, so the ideator is told about them directly: titles already used, which
recurring character has led the last several stories, and invented names reused
across unrelated stories. All three are phrased as preferences — sometimes the
right answer is to repeat.

Scope is per bible. Amberlight's repetitions say nothing about what a different
series should do next.

---

## Run-on sentences and clause chains

Nothing caught these until now, and one rule actively protected them:
`ai-tells.md` argues *for* the long balanced period, and says in as many words
that long sentences are not the problem. On the page they are not. Heard once,
a sentence that coordinates clause after clause loses its listener partway,
because the subject of the first clause is gone by the third.

The example that prompted this:

> He said the spring damp **had got in** above the north gable, and **had likely
> been getting in** since before the old rector died, and **that he had mentioned
> it once**.

Length is not its fault — 31 words is unremarkable here, where the median is 10.
Its fault is that the parallelism breaks: two members continue *"said [that] the
damp had…"*, and the third opens a new *that*-clause with a new subject. Three
coordinated members, two grammatical shapes.

### What counts it

Two lint rules, both **warnings** — period prose earns a long sentence, and an
error would fail the chapter for writing well:

| Rule | Fires on | Rate |
|---|---|---|
| `long_sentence` | over 40 words | 1.4 a chapter |
| `clause_chain` | 2+ comma-coordinated clauses past 25 words | 2.3 a chapter |

The thresholds were measured, not guessed: across the first two stories, 769
sentences with a median of 10 words and a maximum of 54. Over 40 words is 2.9%
of them; the pair together fire 3.7 times a chapter, which is often enough to
mean something and rare enough not to be noise. A 33-word sentence with a single
coordination is left alone.

### What judges it

The editor, on the half the count cannot reach: whether a chain is a deliberate
accumulating period whose members hold one shape — good period prose — or one
that changes shape partway and lost its thread. More than two of the second kind
is a style failure. Same division as the length check: the machine counts, the
editor judges.

`house.md` states the rule for every agent, so writer and editor are held to one
standard rather than two.

---

## Two faults a linter cannot see

Both came out of one sentence — the opening of *The Cracked Beam* chapter 2:

> It should be understood that Mr Rowe had a reason, and that he had kept it in
> good repair since November.

### The cold reference

*A reason* for what? For Rowe's coldness toward Judith, established in chapter
1's third beat and then buried under two more — Mrs Pike's arrival, the
Marchmont news, and the argument about a wife that closes the chapter. The next
paragraph explains it, so nothing dangles. On the page it is a one-line withhold.

Heard, it is not. A chapter opening is where a listener pauses, where an ad
break lands, and where somebody returns after a day away, so it is the one
sentence in a chapter that must stand entirely on its own. *Had not forgiven
Miss Vane for the business of the verse* costs six words and works cold.

Beat coverage passes this — the beat *is* covered. Nothing else was looking.

### The borrowed word

*Kept it in good repair* is a good sentence about a grievance in any other
story. In this one the schoolroom beam is cracked and the repair costs fifteen
pounds, so **repair** is spoken for, and a listener hearing it reaches for the
roof.

Whatever a plot turns on owns its words literally. Every figurative use of them
sends the listener somewhere else in the story.

### Why neither is a lint rule

Unlike sentence length and clause chains, there is nothing here to count. *A
reason* is unremarkable text; the fault is entirely in what surrounds it. And an
attempt to hand the editor the story's literal vocabulary automatically —
frequency over the title, logline and beats — returned `asks`, `together` and
`sister` while missing **repair**, which appears exactly once. A bad hint is
worse than none.

So both live in `house.md` for every agent, in `writer.md` as instructions, and
in `editor.md` as checks 8 and 9. The opening sentence is now part of the pass
condition: *"the one sentence in the chapter worth reading twice."*
