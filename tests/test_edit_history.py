"""Headless tests for the undo/redo edit history."""

from __future__ import annotations

from notegrabber.gui.edit_history import EditHistory


def test_new_history_has_nothing_to_undo_or_redo() -> None:
    h = EditHistory()
    assert not h.can_undo
    assert not h.can_redo
    assert h.undo(["current"]) is None
    assert h.redo(["current"]) is None


def test_record_then_undo_restores_previous_state() -> None:
    h = EditHistory()
    state0 = ["a", "b"]
    # edit turns state0 -> state1; record the before-state.
    h.record(state0)
    state1 = ["a", "b", "c"]
    assert h.can_undo
    restored = h.undo(state1)
    assert restored == state0
    assert h.can_redo


def test_undo_then_redo_round_trip() -> None:
    h = EditHistory()
    s0 = ["x"]
    h.record(s0)
    s1 = ["x", "y"]
    undone = h.undo(s1)
    assert undone == s0
    redone = h.redo(s0)
    assert redone == s1
    assert h.can_undo
    assert not h.can_redo


def test_multiple_edits_undo_in_reverse_order() -> None:
    h = EditHistory()
    s0, s1, s2 = ["0"], ["0", "1"], ["0", "1", "2"]
    h.record(s0)  # before edit 1
    h.record(s1)  # before edit 2
    current = s2
    assert h.undo(current) == s1
    assert h.undo(s1) == s0
    assert h.undo(s0) is None  # back at baseline


def test_recording_after_undo_discards_redo() -> None:
    h = EditHistory()
    h.record(["a"])
    h.undo(["a", "b"])  # now redo is available
    assert h.can_redo
    h.record(["a"])  # a new edit branches; redo must be cleared
    assert not h.can_redo


def test_begin_resets_history() -> None:
    h = EditHistory()
    h.record(["a"])
    h.begin(["fresh"])
    assert not h.can_undo
    assert not h.can_redo


def test_history_is_bounded() -> None:
    h = EditHistory(limit=3)
    for i in range(10):
        h.record([str(i)])
    # Only the last 3 before-states are kept.
    seen = []
    current = ["current"]
    while h.can_undo:
        current = h.undo(current)
        seen.append(current)
    assert seen == [["9"], ["8"], ["7"]]


def test_snapshots_are_independent_copies() -> None:
    h = EditHistory()
    mutable = ["a"]
    h.record(mutable)
    mutable.append("mutated")  # mutating the original must not affect history
    assert h.undo(["current"]) == ["a"]
