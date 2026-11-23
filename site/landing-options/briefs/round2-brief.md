# Middle Watch landing page, ROUND 2: shared brief for all four design agents

## IMPORTANT: what changed from round 1
Round 1 leaned too hard on the "middle watch" theme: ships, lighthouses, lamps, bells, nautical charts, midnight-to-4am clocks, Regency pastiche. The owner wants LESS of that. In this round:
- Do NOT use nautical imagery (ships, lighthouses, compasses, charts, bells, watches, lanterns, candles as a motif), clocks or time bands, or a "night watch" narrative.
- Treat Middle Watch as a modern, small, independent creative studio that makes stories and soundscapes (and later books, games and apps). The name is just a brand name.
- At most, ONE short line (in the footer or an about blurb) may say where the name comes from. You can also leave it out.
- Copy should be clear, confident and warm, the way a good indie studio site reads. Skip the purple prose, the archaic voice and the night-time metaphors in every line.
- The two offerings must be easy to scan: what each one is, where to find it, and what's coming.

## The brand
- Middle Watch (middlewatch.co) is a one-person creative studio run by Scott.
- (Background only: the name refers to the midnight-to-4am watch at sea. Don't build the design around this.)
- What it makes today, and the ONLY things this page may talk about:
  1. **Audio stories**: narrated original short fiction on YouTube. Historically grounded, often with a Regency-period feel. Every story stands alone, and some also connect across four-episode arcs. Genres in development include cozy sci-fi and post-cataclysm fantasy. Don't name specific series and don't build a "series" section.
  2. **Ambience**: long-form ambient soundscapes on YouTube (candlelight, snow, rain, the sea at night, and so on). Some are tied to the stories' places, and some stand alone.
- Things that come LATER and must NOT appear on the page yet (no "coming soon" teasers for them): Kindle books, itch.io games, iOS apps. Your information architecture (nav, section structure, card system) MUST be built so these three can be added later as sibling "shelves" or sections without a redesign. Explain how in your report.

## Facts and constraints
- The YouTube channels are not live yet. Show their handles as placeholders, like `[@STORIES_HANDLE]` and `[@AMBIENCE_HANDLE]`, with a short, tasteful "coming soon" note on the channels only.
- Contact: a plain mailto link to hello@middlewatch.co. No email signup form, newsletter or input fields.
- No invented stats, testimonials, view counts, prices or episode titles. Use [BRACKETED PLACEHOLDERS] for any missing fact.
- No emoji. No gradient washes. No left-border accent cards. No generic AI-startup look.
- The current site uses Libre Caslon and Archivo with deep Prussian blue and amber. You may keep it, evolve it or depart from it, depending on your assigned concept.

## Technical format (strict; each rule fails silently if broken)
Write ONE file: the path your prompt gives you. It is a "Design Component" (.dc.html) artboard for a design canvas.

Skeleton:
```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Middle Watch: [concept name]</title>
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=...&display=swap">
<style>
body{margin:0}
a{color:#...}a:hover{color:#...}
/* page basics, @keyframes and @media rules may live here */
</style>
</helmet>
<div style="...fluid root...">
  ... all markup ...
</div>
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{"$preview":{"width":1440,"height":H}}'>
class Component extends DCLogic {
  renderVals() { return { ... }; }
}
</script>
</body>
</html>
```

Rules:
- Keep `<script src="./support.js"></script>` in head EXACTLY as written.
- Close every non-void element and quote every attribute.
- This is a PAGE: a fluid root with no fixed px width. Use a `max-width` container and %/rem/fr/clamp. Use px/rem heights (`vh` only on a whole-screen hero, never `%` heights). It must work at phone width: nav wraps or stacks, and grids collapse (use @media in the helmet style, or `repeat(auto-fit, minmax(...))`).
- Put most styling inline (`style="..."`), because that's what the editor's properties panel edits. The `<helmet><style>` block is for body basics, link colors, @keyframes, @media and hover states.
- Lay out sibling groups with flex or grid plus `gap`. For grids use `grid-template-columns: repeat(N, minmax(0, 1fr))`.
- Holes: `{{name}}` is a dotted lookup into `renderVals()` only, never an expression. Compute everything in `renderVals()`.
- Events: `onClick="{{handler}}"`, where `renderVals()` returns the function. State uses `this.state` / `this.setState`, with React class lifecycle (`componentDidMount`, `componentWillUnmount`) available. Branches: `<sc-if value="{{flag}}" hint-placeholder-val="{{ true }}">...</sc-if>`. Loops: `<sc-for list="{{items}}" as="item" hint-placeholder-count="3">...{{item.x}}...</sc-for>`. Precompute per-item styles or flags in `renderVals()`.
- All UI is markup inside `<x-dc>`. Never build UI in script (no innerHTML, appendChild or document queries for building).
- Write copy as literal text in the markup, not as props. In `data-props`, declare at most 2–3 deliberate "tweaks" (for example an accent color or a variant enum), plus `$preview`. `data-props` is single-quoted JSON: use `&#39;` for a literal ' and `&amp;` for &.
- No network access except the one Google Fonts css2 `<link>` in helmet. No external images, no `<img>` with URLs, no `<iframe>`, `<object>` or `<embed>`, and no data: URIs. Build ALL imagery with CSS and inline SVG (decorative SVG gets `aria-hidden="true"`). Icons are inline stroke SVG.
- Accessibility: use real `<a href>` and `<button>`, add `aria-label` on icon-only buttons, keep text contrast at 4.5:1 or better (including small grey labels), and keep touch targets at 44px or larger. No global keydown handlers.
- Set `$preview.height` to your best estimate of the page's full height at 1440px wide.

## Process
- Write the file with your file-writing tool, in one go or by building it up and then rewriting it whole.
- Do NOT render, screenshot, open a browser, install anything or run a verification step. Just re-read your file once for well-formedness (closed tags, quoted attributes, no `{{expressions}}`).
- Copy should be original, clear and short. It should sound like a thoughtful indie studio, not a SaaS page and not a period drama.

## Your final reply (under 200 words)
1. Concept name and one-line pitch
2. Palette (hex) and fonts
3. The page's sections, top to bottom
4. How Books, Games and Apps slot in later
5. The `$preview` height you set
