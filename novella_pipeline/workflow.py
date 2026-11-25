"""Chapter stages and the rules for moving between them.

Ported unchanged from kindle-novellas `storyflow/workflow.py`: the stage names,
the redraft rule and send-back are what Scott already works to.
"""

from __future__ import annotations

from enum import Enum


class Stage(str, Enum):
    OUTLINED = "outlined"
    DRAFTED = "drafted"
    AUTHOR_EDITED = "author_edited"
    EDITOR_REVIEWED = "editor_reviewed"
    APPROVED = "approved"

    @property
    def label(self) -> str:
        return LABELS[self]

    @property
    def index(self) -> int:
        return ORDER.index(self)


ORDER = list(Stage)

LABELS = {
    Stage.OUTLINED: "Outlined",
    Stage.DRAFTED: "Drafted",
    Stage.AUTHOR_EDITED: "Author edited",
    Stage.EDITOR_REVIEWED: "Editor reviewed",
    Stage.APPROVED: "Approved",
}

NEXT_ACTION = {
    Stage.OUTLINED: "Run the writer",
    Stage.DRAFTED: "Edit the draft",
    Stage.AUTHOR_EDITED: "Run the editor",
    Stage.EDITOR_REVIEWED: "Revise or approve",
    Stage.APPROVED: "Done",
}


class TransitionError(ValueError):
    pass


def can_draft(stage: Stage) -> bool:
    # Redrafting is allowed until the author has started editing; after that the
    # chapter must be sent back to Outlined first, so edits are never lost by
    # accident. (A redraft is a new numbered version; the old one stays on disk.)
    return stage in (Stage.OUTLINED, Stage.DRAFTED)


def can_edit(stage: Stage) -> bool:
    return stage in (Stage.DRAFTED, Stage.AUTHOR_EDITED, Stage.EDITOR_REVIEWED)


def can_review(stage: Stage) -> bool:
    return stage in (Stage.AUTHOR_EDITED, Stage.EDITOR_REVIEWED)


def can_approve(stage: Stage) -> bool:
    return stage == Stage.EDITOR_REVIEWED


def stage_after_edit(stage: Stage) -> Stage:
    """Saving edits moves a fresh draft to Author edited; later edits keep the
    stage, so revising after a review doesn't force another review."""
    if not can_edit(stage):
        raise TransitionError(f"Cannot edit a chapter that is {stage.label}.")
    return Stage.AUTHOR_EDITED if stage == Stage.DRAFTED else stage


def send_back(current: Stage, target: Stage) -> Stage:
    if target.index >= current.index:
        raise TransitionError(f"{target.label} is not earlier than {current.label}.")
    return target
