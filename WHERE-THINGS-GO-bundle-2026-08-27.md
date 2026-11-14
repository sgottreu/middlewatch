# Where these files go

Produced 28 August 2026. Nothing here has been merged into your repo — this is
a staging bundle, laid out to mirror where each piece belongs.

    ledger/                     -> repo root, beside pipeline/
    pipeline/prompts/arc.md     -> beside style.md, inherited by
                                   ideator, writer, and editor
    brand/                      -> reference only, not code.
                                   Keep it out of the repo if you prefer.

## Before the ledger runs

Add `genre` and `flavor` to the manifest.json schema and pin them at creation,
the way art style and voice casting already are. Every ledger row reads those
two fields, and if they stay editable after the fact the comparison data rots
without telling you.

Then add one recording call per paid API call. See ledger/README.md.

## Before the arc doc does anything

Move cast portrait generation up from per-story to per-arc:

    stories/<slug>/cast/   ->   arcs/<arc-slug>/cast/

That path change is what lets four episodes share one cast sheet, and it is
the single biggest cost saving in the arc design.

## Not done, and needs you

- ledger/videos.csv does not exist yet. Create it when you publish your first
  video. Two columns: story_slug, video_id.
- rates.json was verified 2026-08-28 against list pricing. Check it against
  your actual invoices before trusting any total.

---

## Added since the first bundle (1 September)

    site/index.html              landing page for middlewatch.co.
                                 Single file, no build step, no dependencies
                                 beyond Google Fonts.
    brand/launch-checklist.html  clickable and printable version of the
                                 checklist, with copy buttons for the bios
    brand/icon/                  JPG and PNG renders of your icon.svg

The icon renders came from your own icon.svg, which is NOT in this bundle
because you already have the original. Keep the SVG as the master and
re-render from it rather than editing these.

Known inconsistency: the icon is #14140f near-black with #c9a227 gold, the
landing page is #131E2B prussian with #E8A44C amber. Close enough to read as
a mistake rather than a contrast. Pick one and change the other.
