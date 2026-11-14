# Monetizing the RPG To-Do App and Hedgerow Farm

*Brainstorm notes, November 2026*

## Big picture

Treat both apps as one small studio rather than two separate businesses. They share a tile engine, and the Middle Watch domain (rpg.middlewatch.co) already ties them together.

## RPG To-Do App (habit apps make money over time)

- **Free core, paid cosmetics:** sell building skins, seasonal village themes and decorations. Never sell XP or progress, because paying to level up breaks the motivation the app is built on.
- **Small subscription ($2–4/mo):** cloud sync across devices, history and stats, and extra village plots. Habitica and Finch both make most of their money from a subscription plus cosmetics.
- **Family mode:** parents assign chores and kids grow a shared village. People pay more for family plans, and those plans are harder to cancel.
- **One-time "Founder" pack ($10):** for early users, sold before the subscription is built. Tests whether anyone will pay while costing almost nothing.
- **Launch as a web app first** at rpg.middlewatch.co to avoid app store fees at the start (Apple charges $99 a year, Google $25 once).

**Open question:** is the RPG app still mainly a personal project, or a product? The answer decides whether sync and accounts are worth building early.

## Hedgerow Farm (management-sim players prefer to buy once)

- **Pay-what-you-want web build on itch.io first.** Costs nothing to list, and shows quickly whether people play it.
- **Steam release ($5–15)** once soil health, animals and the scrolling map are working. Offer a free demo, and add DLC later (new map regions, more animal types, seasons).
- **Avoid free-to-play with ads.** Sim players tend to leave over it.

## Income both projects can share

- **Sell the tilesets** on itch.io as asset packs. The art is being made anyway.
- **Build-in-public devlogs.** The fact that AI agents built the whole app is a strong hook for YouTube and blog posts, and it brings players to both games. A Patreon could come later.
- **Set both apps in the Broken Edges world** so the stories, novellas, apps and game all feed one audience.

## Things to watch

- **Verizon Ethics:** apps and games weren't among the three activities HR cleared (books, YouTube short stories, dyslexia stories). Send Ethics a short note before charging anyone. Devlogs about agentic AI are close to the day job, so mention those too.
- **Time:** with about 5 hours a week, do one of these at a time. Start with the farm game on itch.io, because a one-time sale with no backend to run is the lowest-maintenance way to find out whether anyone will pay.

---

# Getting Hedgerow Farm onto Steam

Getting onto Steam takes $100 and about a month of waiting, which is the easy part. The real work is turning the prototype into a finished desktop game with a store page that gets people to wishlist it.

## 1. Make it a desktop app

- If the game runs in a browser, wrap it as a desktop app with Electron, Tauri or NW.js.
- Target Windows first. Mac and Linux are optional.
- It needs things players expect: save and load, a settings menu, a tutorial or first-season guide, and a clean way to quit.
- Optionally, add the Steamworks SDK for achievements and cloud saves. Not required, but players like having them.

## 2. Set up Steamworks

- **Account and paperwork:** create the Steamworks account with a Steam account that has spent at least $5, then complete the tax questionnaire, add bank details and verify identity. A W-9 as a sole proprietor is fine.
- **Fee:** $100 per game, refunded after the game earns $1,000.
- **Revenue split:** Valve keeps 30%, so a $10 sale pays about $7.

## 3. Build the store page

- **Required content:** app name, description, screenshots, trailer, tags and age rating. The trailer is the biggest lift. A 60-second screen capture of fields changing season by season would work well.
- **AI disclosure:** Steam's content survey asks about AI-generated content, so disclose any AI-made art or text. Code written with AI tools is generally treated differently, but read the current wording when filling it in.
- **Asset licenses:** check that everything in the LICENSES folder allows commercial use.

## 4. Follow the release timeline

- **Earliest release:** 30 days after paying the fee.
- **Coming Soon page:** must be public for at least 2 weeks before release. Four to eight weeks of wishlists gives Steam's algorithm more signal.
- **Reviews:** Valve reviews the store page and the build separately, each typically in 3 to 5 business days.
- **Early Access:** a good fit, since the roadmap (lime, weeds, manure, animals) is still growing.

## Realistic order at 5 hours a week

1. Get Verizon Ethics sign-off.
2. Ship a free itch.io build to get feedback.
3. Pay the Steam fee and put up the Coming Soon page as soon as there are good screenshots, so wishlists build while work continues.
4. Launch in Early Access once the core loop (fields, soil health and one animal type) feels finished.

## Sources

- [Steamworks: Getting Started / Onboarding](https://partner.steamgames.com/doc/gettingstarted/onboarding)
- [Summer Engine: How to Publish a Game on Steam (2026)](https://www.summerengine.com/blog/how-to-publish-game-on-steam)
- [Ziva: Publish a game on Steam](https://ziva.sh/blogs/publish-game-steam)
- [Summer Engine docs: Publish to Steam](https://docs.summerengine.com/knowledge-base/publish-steam)
- [Summer Engine: Unity on Steam guide](https://www.summerengine.com/make-great-games/sell-a-unity-game-on-steam)
