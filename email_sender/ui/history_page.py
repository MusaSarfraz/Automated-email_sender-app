"""History screen: past campaigns, Continue a stopped one, Export results."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import campaigns, progress as P
from ..core.config import Status


class HistoryPage(QWidget):
    """Lists all campaigns and offers Continue / Export."""

    continueRequested = Signal(object)  # Progress
    exportRequested = Signal(object)  # Progress

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._progs: list[P.Progress] = []

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("<h2>History</h2>"))
        header.addStretch(1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        cols = ["Campaign", "State", "Sent", "Failed", "Invalid", "Duplicate", "Pending"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.continue_btn = QPushButton("Continue")
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(self._continue)
        self.export_btn = QPushButton("Export results…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        buttons.addWidget(self.continue_btn)
        buttons.addWidget(self.export_btn)
        root.addLayout(buttons)

    def refresh(self) -> None:
        self._progs = campaigns.list_campaigns()
        self.table.setRowCount(len(self._progs))
        for i, prog in enumerate(self._progs):
            c = prog.count_by_status()
            values = [
                prog.name,
                prog.state,
                str(c.get(Status.SENT, 0)),
                str(c.get(Status.FAILED, 0)),
                str(c.get(Status.INVALID, 0)),
                str(c.get(Status.DUPLICATE, 0)),
                str(c.get(Status.PENDING, 0)),
            ]
            for j, v in enumerate(values):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self._update_buttons()

    def _selected(self) -> P.Progress | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        idx = rows[0].row()
        return self._progs[idx] if 0 <= idx < len(self._progs) else None

    def _update_buttons(self) -> None:
        prog = self._selected()
        self.export_btn.setEnabled(prog is not None)
        self.continue_btn.setEnabled(prog is not None and prog.state == P.STATE_STOPPED)

    def _continue(self) -> None:
        prog = self._selected()
        if prog:
            self.continueRequested.emit(prog)

    def _export(self) -> None:
        prog = self._selected()
        if prog:
            self.exportRequested.emit(prog)
