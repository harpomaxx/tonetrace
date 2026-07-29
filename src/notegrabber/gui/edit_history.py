"""Undo/redo history for note editing.

Qt-free and dependency-free so it can be unit-tested headlessly and reused by
non-Qt frontends, mirroring ``state.py``. The GUI drives it: it records a
snapshot of the note list before each committed edit, and ``undo``/``redo`` walk
between snapshots. ``GuiMidiNote`` is a frozen dataclass, so a shallow copy of
the list is a safe immutable snapshot.
"""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")

# Cap the number of undo steps kept, to bound memory on long editing sessions.
DEFAULT_HISTORY_LIMIT = 100


class EditHistory:
    """A bounded undo/redo stack of note-list snapshots.

    Usage from the GUI:

    - ``begin(notes)`` once when a fresh analysis establishes the baseline.
    - ``record(before)`` with the note list *as it was before* each committed
      edit (delete / committed drag / inspector apply / retune).
    - ``undo(current)`` / ``redo(current)`` return the note list to restore, or
      ``None`` when there is nothing to undo/redo.
    """

    def __init__(self, limit: int = DEFAULT_HISTORY_LIMIT) -> None:
        self._limit = max(1, limit)
        self._undo: list[list[T]] = []
        self._redo: list[list[T]] = []

    def begin(self, notes: Sequence[T]) -> None:
        """Reset history to a fresh baseline (e.g. after a new analysis)."""

        self._undo = []
        self._redo = []

    def record(self, before: Sequence[T]) -> None:
        """Record the note list as it was *before* a committed edit.

        Any redo history is discarded, since a new edit branches from here.
        """

        self._undo.append(list(before))
        if len(self._undo) > self._limit:
            # Drop the oldest snapshot to stay within the memory bound.
            self._undo.pop(0)
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, current: Sequence[T]) -> list[T] | None:
        """Return the previous snapshot to restore, or None if none.

        ``current`` (the present note list) is pushed onto the redo stack so a
        following :meth:`redo` can return to it.
        """

        if not self._undo:
            return None
        self._redo.append(list(current))
        return self._undo.pop()

    def redo(self, current: Sequence[T]) -> list[T] | None:
        """Return the next snapshot to restore, or None if none.

        ``current`` is pushed back onto the undo stack.
        """

        if not self._redo:
            return None
        self._undo.append(list(current))
        return self._redo.pop()
