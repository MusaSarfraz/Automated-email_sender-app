"""Progress screen: live status, counts, log, and the Pause/Stop/Reload controls."""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Status

# Campaign-level UI states that drive which buttons are enabled.
UI_READY = "ready"
UI_RUNNING = "running"
UI_PAUSED = "paused"
UI_STOPPED = "stopped"
UI_COMPLETED = "completed"
UI_CAP = "cap"


class ProgressPage(QWidget):
    """Shows what the worker is doing and lets the user control it."""

    pauseClicked = Signal()
    resumeClicked = Signal()
    stopClicked = Signal()
    reloadClicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state = UI_READY
        self._counts: dict[str, int] = {s: 0 for s in Status.ALL}
        self._last24 = 0
        self._cap = 0

        root = QVBoxLayout(self)

        header = QGroupBox()
        hv = QVBoxLayout(header)
        self.title_label = QLabel("<h2>No campaign running</h2>")
        self.sender_label = QLabel("")
        self.status_label = QLabel("Status: —")
        self.status_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        hv.addWidget(self.title_label)
        hv.addWidget(self.sender_label)
        hv.addWidget(self.status_label)
        root.addWidget(header)

        self.counts_label = QLabel("")
        root.addWidget(self.counts_label)

        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        root.addWidget(self.bar)

        self.cap_label = QLabel("")
        root.addWidget(self.cap_label)

        controls = QHBoxLayout()
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stopClicked.emit)
        self.reload_btn = QPushButton("Reload sheet")
        self.reload_btn.clicked.connect(self.reloadClicked.emit)
        controls.addWidget(self.pause_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.reload_btn)
        controls.addStretch(1)
        root.addLayout(controls)

        log_box = QGroupBox("Activity")
        lv = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        lv.addWidget(self.log)
        root.addWidget(log_box, 1)

        self.set_ui_state(UI_READY)

    # --- setup ---------------------------------------------------------------
    def begin(self, name: str, sender_address: str, counts: dict[str, int], cap: int) -> None:
        self.title_label.setText(f"<h2>{name}</h2>")
        self.sender_label.setText(f"Sending from: {sender_address}")
        self._counts = {s: counts.get(s, 0) for s in Status.ALL}
        self._cap = cap
        self._last24 = counts.get(Status.SENT, 0)
        self.log.clear()
        self._refresh_counts()

    def set_counts(self, counts: dict[str, int], cap: int | None = None) -> None:
        """Replace the displayed counts (e.g. after a reload)."""
        self._counts = {s: counts.get(s, 0) for s in Status.ALL}
        if cap is not None:
            self._cap = cap
        self._refresh_counts()

    # --- event handling ------------------------------------------------------
    def handle_event(self, kind: str, data: dict) -> None:
        if kind == "row_sent":
            self._counts[Status.SENT] += 1
            self._counts[Status.PENDING] = max(0, self._counts[Status.PENDING] - 1)
            self._last24 += 1
            tag = "WOULD SEND" if data.get("dry_run") else "SENT"
            self._append(tag, data.get("email", ""), data.get("subject", ""))
            self._refresh_counts()
        elif kind == "row_failed":
            self._counts[Status.FAILED] += 1
            self._counts[Status.PENDING] = max(0, self._counts[Status.PENDING] - 1)
            self._append("FAILED", data.get("email", ""), data.get("error", ""))
            self._refresh_counts()
        elif kind == "row_recovered":
            if data.get("result") == Status.SENT:
                self._counts[Status.SENT] += 1
                self._last24 += 1
            self._append("RECOVERED", data.get("email", ""), str(data.get("result", "")))
            self._refresh_counts()
        elif kind == "sending":
            self.status_label.setText(f"SENDING to {data.get('email', '')}…")
        elif kind == "waiting":
            secs = int(data.get("seconds_remaining", 0))
            if data.get("kind") == "cap":
                self.status_label.setText(f"DAILY CAP REACHED — resumes in {_fmt(secs)}")
            else:
                self.status_label.setText(f"WAITING — next email in {_fmt(secs)}")
        elif kind == "network_error":
            self._append("NETWORK", data.get("email", ""), f"{data.get('error', '')} (will retry)")
        elif kind == "auth_error":
            self._append("AUTH", data.get("email", ""), f"{data.get('error', '')} ({data.get('count')}/3)")
        elif kind == "cap_reached":
            self.set_ui_state(UI_CAP)
        elif kind == "reloaded":
            self._append("RELOAD", "", data.get("summary", ""))

    # --- state / buttons -----------------------------------------------------
    def set_ui_state(self, state: str) -> None:
        self._state = state
        running = state == UI_RUNNING
        paused = state == UI_PAUSED
        cap = state == UI_CAP

        self.pause_btn.setText("Resume" if paused else "Pause")
        self.pause_btn.setEnabled(running or paused or cap)
        self.stop_btn.setEnabled(running or paused or cap)
        self.reload_btn.setEnabled(paused)

        labels = {
            UI_READY: "Status: ready",
            UI_RUNNING: "Status: RUNNING",
            UI_PAUSED: "Status: PAUSED",
            UI_STOPPED: "Status: STOPPED",
            UI_COMPLETED: "Status: COMPLETED ✅",
            UI_CAP: "Status: DAILY CAP REACHED — waiting",
        }
        if state in labels:
            self.status_label.setText(labels[state])

    # --- helpers -------------------------------------------------------------
    def _on_pause_clicked(self) -> None:
        if self._state == UI_PAUSED:
            self.resumeClicked.emit()
        else:
            self.pauseClicked.emit()

    def _refresh_counts(self) -> None:
        c = self._counts
        self.counts_label.setText(
            f"Sent {c[Status.SENT]}   Pending {c[Status.PENDING]}   "
            f"Failed {c[Status.FAILED]}   Invalid {c[Status.INVALID]}   Duplicate {c[Status.DUPLICATE]}"
        )
        total = c[Status.SENT] + c[Status.PENDING] + c[Status.FAILED]
        done = c[Status.SENT] + c[Status.FAILED]
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)
        cap_txt = f" / {self._cap} cap" if self._cap else ""
        self.cap_label.setText(f"Last 24 h: {self._last24}{cap_txt}")

    def _append(self, tag: str, recipient: str, detail: str) -> None:
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        line = f"{ts}  {tag:<10} {recipient}"
        if detail:
            line += f"   {detail}"
        self.log.appendPlainText(line)


def _fmt(seconds: int) -> str:
    m, s = divmod(max(0, seconds), 60)
    return f"{m}:{s:02d}"
