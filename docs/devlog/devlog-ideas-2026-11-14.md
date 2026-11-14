> Exported 2026-11-14 from the claude.ai doc "Middle Watch Devlog Ideas" (https://claude.ai/artifact/2ppZJPL9hF5YCDqDfNDvYi). The doc is the source while ideas are still forming; re-export after edits.

# Middle Watch Devlog Ideas

Devlogs will live in their own playlist on the Middle Watch channel.

## 1. Story to Screen

- Pick one finished Middle Watch story as the example
- Walk through each pipeline stage: script, narration, visuals, audio mix and render
- Show the tooling and code briefly, without going too deep
- Finish with the final video playing side by side with the raw input

## 2. I Built an RPG App with AI

- What the app does, with a quick demo first
- How you split the work between yourself and the AI
- Best moments and worst failures
- Lessons for anyone building with AI

## 3. Why I Built a Farm Manager App

- The problem it solves
- A tour of the main features
- How it was built and what tech it uses
- What's next for it

## 4. Behind the Ambience

- What makes a good soundscape
- Layering the sounds, with before and after clips
- How the pipeline puts it together
- A short sample to close

## Wild Ideas

- **AI runs the channel for a week** (front-runner): decide how much control it gets, from just the videos up to titles, thumbnails and comments
- Viewers vs. the pipeline: the comments pick a prompt and you build the episode
- Inside the RPG: play the app on camera, with the bugs left in
- Farm manager meets fiction: a story set on a farm run by the app
- Middle Watch at 3 a.m.: a moody late-night devlog

## Notes from Past Sessions

### Story to Screen

- Stories live in bundles (e.g. The Cracked Beam in Amberlight) that move through stages from outline to render
- Human approval gates on outlines, chapters and the rendered video, then a Publish stage that records the YouTube URL
- Review app buttons: Record, Design, Render (live progress, Stop) and Publish
- Amberlight series bible keeps canon, recurring cast and voices consistent
- Cost ledger logs every API call and shows break-even for each story
- Subplot: ElevenLabs is retiring its default voices, so a Casting page was built for auditions, verdicts and a one-click Apply

#### The Agent Team Behind the Stories

- Stages run in order: ideate, write, edit, record, design, direct, approve, publish
- **Ideator** (`cli new`) writes the outline: premise, cast, tropes, chapter beats, a logline and a one-line promise to the viewer
  - Open mode: genre and subgenre only, and it invents the rest; generate five and keep one
  - Directed mode: your notes are binding, and the rest is its call
  - Priority: content boundaries, then your notes, then genre conventions; any conflict is reported, not dropped
  - For a series it reads the bible and runs the same checks as `bible check`
- Review gate after ideation: the writer won't run until you approve, and `revise` reruns the outline with your feedback
- Ideation costs a few cents against about a dollar further down, so it's the cheapest place to catch problems
- `cli all` skips the gates for batch runs
- **Writer** drafts the prose and **editor** reviews it
- The bible on disk is the shared state, so cast, places and planted threads stay consistent
- Core rule: every story stands alone, and continuity is a reward for returning viewers, not a requirement

### RPG To-Do App

- Runs at rpg.middlewatch.co on its own Lightsail box, with Caddy, Uvicorn and SQLite
- Magic-link sign-in and a village feature
- A PM agent merges to main and a deploy script redeploys it

### Farm Manager (Hedgerow Farm)

- Single-file HTML game; started with Kenney CC0 tiles, then moved to AI-generated sprite sheets
- Sub-agents worked on branches (sprites, bug batches) while Claude coordinated
- Features added by voice, e.g. a loan offer with game over after 6 months in the red
- UI polish: ledger dropdown, field tooltips, machines swinging wide at field ends

### Behind the Ambience

- Design doc drives each ambience, either tied to a story or standalone
- ElevenLabs sound stems: a looped bed plus one-off sounds scattered by ffmpeg
- ffmpeg runs locally on the Mac for now
- Visuals from Gemini or Higgsfield, still or motion, with generated overlays like candle flicker
