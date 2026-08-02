"""A lightweight collapsible section: a clickable header that shows/hides a body.

Used to dock a secondary view (the detected-notes table) below the piano roll so
it is available on demand without permanently competing for vertical space. The
header shows a disclosure triangle, a title, and an optional live suffix (e.g. a
count); clicking it toggles the body.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """A titled section whose body can be collapsed to just its header row."""

    toggled = Signal(bool)  # emits the new expanded state

    def __init__(self, title: str, body: QWidget, *, expanded: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._suffix = ""

        self.header = QToolButton()
        self.header.setObjectName("collapsibleHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(self._arrow(expanded))
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.clicked.connect(self._on_clicked)

        self.body = body
        self.body.setVisible(expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.header)
        layout.addWidget(self.body, 1)

        self._refresh_text()

    @staticmethod
    def _arrow(expanded: bool) -> Qt.ArrowType:
        return Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow

    def _on_clicked(self, checked: bool) -> None:
        self.set_expanded(checked)
        self.toggled.emit(checked)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)
        self.header.setArrowType(self._arrow(expanded))
        self.body.setVisible(expanded)

    def set_suffix(self, suffix: str) -> None:
        """Set a live suffix appended to the title (e.g. a count), then refresh."""

        self._suffix = suffix
        self._refresh_text()

    def _refresh_text(self) -> None:
        self.header.setText(f"{self._title}{self._suffix}")
