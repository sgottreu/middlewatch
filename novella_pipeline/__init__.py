"""Kindle novellas: one bundle per book under novellas/<series>/<slug>/.

Ported from the kindle-novellas FastAPI app (storyflow/). Chapters move through
outlined -> drafted -> author_edited -> editor_reviewed -> approved; the writer
and editor go through story_pipeline.llm and record spend like every other
stage. See docs/novella/plan-2026-11-24.md.
"""
