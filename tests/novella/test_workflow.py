"""Stage rules, ported from kindle-novellas tests/test_workflow.py."""
import itertools

import pytest

from novella_pipeline.workflow import (LABELS, NEXT_ACTION, ORDER, Stage, TransitionError,
                                       can_approve, can_draft, can_edit, can_review,
                                       send_back, stage_after_edit)

O, D, A, R, P = ORDER


def test_order_labels_next_actions():
    assert [s.value for s in ORDER] == ["outlined", "drafted", "author_edited",
                                        "editor_reviewed", "approved"]
    assert [s.label for s in ORDER] == ["Outlined", "Drafted", "Author edited",
                                        "Editor reviewed", "Approved"]
    assert set(LABELS) == set(NEXT_ACTION) == set(Stage)


@pytest.mark.parametrize("stage,expected", [(O, True), (D, True), (A, False), (R, False), (P, False)])
def test_can_draft(stage, expected):
    assert can_draft(stage) is expected


@pytest.mark.parametrize("stage,expected", [(O, False), (D, True), (A, True), (R, True), (P, False)])
def test_can_edit(stage, expected):
    assert can_edit(stage) is expected


@pytest.mark.parametrize("stage,expected", [(O, False), (D, False), (A, True), (R, True), (P, False)])
def test_can_review(stage, expected):
    assert can_review(stage) is expected


@pytest.mark.parametrize("stage,expected", [(O, False), (D, False), (A, False), (R, True), (P, False)])
def test_can_approve(stage, expected):
    assert can_approve(stage) is expected


@pytest.mark.parametrize("stage,expected", [(D, A), (A, A), (R, R)])
def test_stage_after_edit_allowed(stage, expected):
    assert stage_after_edit(stage) == expected


@pytest.mark.parametrize("stage", [O, P])
def test_stage_after_edit_refused(stage):
    with pytest.raises(TransitionError, match="Cannot edit a chapter that is"):
        stage_after_edit(stage)


@pytest.mark.parametrize("cur,tgt", [(c, t) for c, t in itertools.product(ORDER, ORDER)
                                     if t.index < c.index])
def test_send_back_allowed(cur, tgt):
    assert send_back(cur, tgt) == tgt


@pytest.mark.parametrize("cur,tgt", [(c, t) for c, t in itertools.product(ORDER, ORDER)
                                     if t.index >= c.index])
def test_send_back_refused(cur, tgt):
    with pytest.raises(TransitionError, match="is not earlier than"):
        send_back(cur, tgt)
