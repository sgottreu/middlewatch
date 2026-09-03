# Content boundaries

`story_pipeline/prompts/boundaries.md` is inherited by the ideator, writer, editor and designer. Hard
lines: no sexual content, no graphic violence, no suicide or self-harm or
disordered eating in any form, no method detail, no slurs or caricature, no real
people, no profanity, and every character in a romantic role is a stated adult.
A section on handling historical atrocity, addiction, mental illness, children
and servants covers the cases that go wrong by accident rather than by intent.

Enforcement runs at three depths:

- **Structural**, in `_validate_outline`: romantic roles need an integer age of
  18+, or the outline is rejected before anything is written.
- **Editorial**: any entry in the editor's `boundary_breaches` fails the chapter
  outright, with the same authority as a lint error and regardless of score.
- **Human**: the review gate prints every under-18 cast member with their role,
  because the structural check keys off `role` and a mislabelled minor would slip
  past it.

The last section of that file is the one that matters most. The constraint isn't
that nothing bad happens — a story with nothing at risk isn't worth twelve
minutes of an evening. It's that the badness is social and recoverable, which is
the constraint the entire comedy-of-manners tradition was built under.


---

[← Style and genres](style.md)  ·  [Index](index.md)  ·  [Narration →](narration.md)
