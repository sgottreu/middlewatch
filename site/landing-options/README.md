# middlewatch.co landing page options

Eight landing page directions, made 2026-11-20/21 on the design canvas
"Middle Watch landing page options" (https://claude.ai/artifact/PDUqQd6iMqBHJwUiudW3DV).

These are the canvas's source files, not standalone pages. Each `.dc.html` loads
`./support.js` from the canvas runtime and uses its template syntax (`{{holes}}`,
`<sc-for>`, `<sc-if>`), so opening one directly in a browser won't render it.
Use them as the reference when building the chosen direction into `site/index.html`
(single self-contained file, no build step).

`canvas.json` is the canvas layout (positions, titles), kept so the set can be
re-published to a canvas as-is.

## Round 1: leaning into the Middle Watch theme

| File | Option | Look |
|---|---|---|
| `Main.dc.html` | A · The Watch Band | Current Prussian/amber identity, live 8pm–8am watch band |
| `Almanac.dc.html` | B · The Almanac | Light Regency broadsheet, numbered departments, 24-hour dial |
| `ListeningRoom.dc.html` | C · The Listening Room | Candlelit window hero with a scene switcher (candle, snow, rain, sea) |
| `Harbour.dc.html` | D · The Harbour Chart | Night nautical chart, sweeping lighthouse, lit berths |

## Round 2: lighter on the theme, studio framing

| File | Option | Look |
|---|---|---|
| `Studio.dc.html` | E · Studio Index | Swiss-style, vermilion accent, numbered index of formats |
| `Imprint.dc.html` | F · The Imprint | Modern literary press, each format a paperback-style cover |
| `Maker.dc.html` | G · Small Studio, Many Things | Playful indie-maker cards and stickers |
| `Channel.dc.html` | H · The Channel | Dark streaming-app hub, mock player, tile shelves |

E, F and H carry the revised copy:

- **Structure:** Listen / Read / Play. Only Listen (audio stories, ambience) is on the
  page now; Read and Play are added when books, games and apps launch.
- **Voice:** each format is described by the moment it's for ("for an evening in",
  "for falling asleep").
- **About:** no "one-person studio" or "I make everything" claims. It uses the line
  "Middle Watch is a small studio that makes stories, sound and games using modern
  tools, including AI, with a lot of human direction."
- **Headline:** "Something for every quiet hour."

A–D and G still have the earlier copy, including one-person-studio wording in G.
