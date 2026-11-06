"""New-campaign screen: pick a sheet, validate, preview, then Start."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import campaigns, config, template
from ..core.config import Status
from ..core.readers import SheetResult, read_excel


class CampaignPage(QWidget):
    """Load and validate a sheet, preview rendered rows, and start a campaign."""

    #: Emitted with a Progress when the user clicks Start.
    startRequested = Signal(object)

    def __init__(self, settings: config.Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._xlsx_path: Path | None = None
        self._result: SheetResult | None = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel("<h2>New campaign</h2>"))

        src = QGroupBox("1. Choose your data")
        sv = QVBoxLayout(src)
        row = QHBoxLayout()
        self.choose_btn = QPushButton("Choose Excel file (.xlsx)…")
        self.choose_btn.clicked.connect(self._choose_excel)
        self.path_label = QLabel("No file chosen")
        row.addWidget(self.choose_btn)
        row.addWidget(self.path_label, 1)
        sv.addLayout(row)

        gs_row = QHBoxLayout()
        self.gsheet_url = QLineEdit()
        self.gsheet_url.setPlaceholderText("…or paste a Google Sheet link")
        self.gsheet_btn = QPushButton("Load sheet")
        self.gsheet_btn.clicked.connect(self._load_gsheet)
        gs_row.addWidget(self.gsheet_url, 1)
        gs_row.addWidget(self.gsheet_btn)
        sv.addLayout(gs_row)
        root.addWidget(src)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        prev_box = QGroupBox("2. Preview (first 10 rows, placeholders filled in)")
        pv = QVBoxLayout(prev_box)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Recipient", "Subject", "Body / problem"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        pv.addWidget(self.table)
        root.addWidget(prev_box, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.start_btn = QPushButton("Start campaign")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        bottom.addWidget(self.start_btn)
        root.addLayout(bottom)

    # --- loading -------------------------------------------------------------
    def _choose_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose spreadsheet", "", "Excel files (*.xlsx)")
        if not path:
            return
        try:
            result = read_excel(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Could not read file", f"That file could not be read:\n{exc}")
            return
        self._xlsx_path = Path(path)
        self._result = result
        self.path_label.setText(path)
        self._show_result(result)

    def _load_gsheet(self) -> None:
        QMessageBox.information(
            self,
            "Google Sheets",
            "Google Sheet loading uses the bundled service account configured at install "
            "time. If Settings shows no service-account address, this build cannot read "
            "Google Sheets yet — use an Excel file for now.",
        )

    # --- presentation --------------------------------------------------------
    def _show_result(self, result: SheetResult) -> None:
        if not result.ok:
            self.summary.setText(
                f"❌ The sheet is missing required columns: <b>{', '.join(result.missing_columns)}</b>. "
                "Required: email (or a 'To'/'Recipient' column), subject, body."
            )
            self.table.setRowCount(0)
            self.start_btn.setEnabled(False)
            return

        c = result.counts()
        self.summary.setText(
            f"✅ {len(result.rows)} rows — "
            f"<b>{c.get(Status.PENDING, 0)}</b> ready to send, "
            f"{c.get(Status.INVALID, 0)} invalid, {c.get(Status.DUPLICATE, 0)} duplicate."
        )
        self._fill_preview(result)
        self.start_btn.setEnabled(c.get(Status.PENDING, 0) > 0)

    def _fill_preview(self, result: SheetResult) -> None:
        rows = result.rows[:10]
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            recipient = row.raw_email or "(blank)"
            if row.status in (Status.INVALID, Status.DUPLICATE):
                subject, body = row.status, row.error or ""
            else:
                try:
                    subject, body = template.render_email(
                        row.data.get("subject", ""), row.data.get("body", ""), row.data
                    )
                except template.TemplateError as exc:
                    subject, body = "⚠ will fail", str(exc)
            self.table.setItem(i, 0, QTableWidgetItem(recipient))
            self.table.setItem(i, 1, QTableWidgetItem(subject))
            self.table.setItem(i, 2, QTableWidgetItem(body[:120].replace("\n", " ")))

    # --- start ---------------------------------------------------------------
    def _start(self) -> None:
        if not self._mailbox_ready():
            QMessageBox.warning(
                self,
                "Connect a mailbox first",
                "Open the Settings tab and connect your Gmail or Outlook account before starting.",
            )
            return
        if not self._xlsx_path or not self._result or not self._result.ok:
            return
        try:
            prog = campaigns.create_excel_campaign(self._xlsx_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Could not start", str(exc))
            return
        self.startRequested.emit(prog)

    def _mailbox_ready(self) -> bool:
        from ..core import secrets

        if self.settings.sender_type == "gmail":
            return bool(self.settings.gmail_address and secrets.get_secret(secrets.GMAIL_APP_PASSWORD))
        return bool(self.settings.outlook_address)
