"""Small helper threads for the UI (keep blocking calls off the GUI thread)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal


class FuncWorker(QThread):
    """Run a blocking callable on a background thread and emit its result.

    Used for mailbox connection tests and interactive sign-in so the window
    never freezes.
    """

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            self.failed.emit(str(exc))
            return
        self.done.emit(result)
