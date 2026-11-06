"""Settings screen: choose and connect a mailbox, set the daily cap."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import config, secrets
from ..core.senders.gmail_smtp import GmailSender
from ..core.senders.outlook_graph import OutlookSender
from .workers import FuncWorker


class SettingsPage(QWidget):
    """Lets the user connect Gmail or Outlook and set the daily cap."""

    def __init__(self, settings: config.Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._worker: FuncWorker | None = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel("<h2>Settings</h2>"))

        # --- Provider choice --------------------------------------------------
        provider_box = QGroupBox("Mailbox")
        prow = QHBoxLayout(provider_box)
        self.rb_gmail = QRadioButton("Gmail")
        self.rb_outlook = QRadioButton("Outlook")
        self._group = QButtonGroup(self)
        self._group.addButton(self.rb_gmail)
        self._group.addButton(self.rb_outlook)
        prow.addWidget(self.rb_gmail)
        prow.addWidget(self.rb_outlook)
        prow.addStretch(1)
        root.addWidget(provider_box)

        # --- Stacked provider settings ---------------------------------------
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_gmail())
        self._stack.addWidget(self._build_outlook())
        root.addWidget(self._stack)

        # --- Shared settings --------------------------------------------------
        shared = QGroupBox("Sending")
        form = QFormLayout(shared)
        self.cap_spin = QSpinBox()
        self.cap_spin.setRange(1, 100000)
        self.cap_spin.setValue(settings.daily_cap)
        self.cap_spin.valueChanged.connect(self._on_cap_changed)
        form.addRow("Daily cap (rolling 24 h):", self.cap_spin)
        form.addRow("Gap between emails:", QLabel(f"{config.SEND_GAP_SECONDS} seconds (fixed)"))

        gsa = config.google_service_account_email()
        gsa_row = QHBoxLayout()
        self.gsa_label = QLabel(gsa or "(not configured in this build)")
        self.gsa_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        copy_btn = QPushButton("Copy")
        copy_btn.setEnabled(bool(gsa))
        copy_btn.clicked.connect(lambda: self._copy_to_clipboard(gsa))
        gsa_row.addWidget(self.gsa_label, 1)
        gsa_row.addWidget(copy_btn)
        gsa_wrap = QWidget()
        gsa_wrap.setLayout(gsa_row)
        form.addRow("Share Google Sheets with:", gsa_wrap)
        root.addWidget(shared)
        root.addStretch(1)

        # --- Wire provider switching -----------------------------------------
        self.rb_gmail.toggled.connect(self._on_provider_changed)
        self.rb_outlook.toggled.connect(self._on_provider_changed)
        if settings.sender_type == "outlook":
            self.rb_outlook.setChecked(True)
        else:
            self.rb_gmail.setChecked(True)
        self._refresh_outlook_status()

    # --- builders ------------------------------------------------------------
    def _build_gmail(self) -> QWidget:
        w = QGroupBox("Connect Gmail")
        form = QFormLayout(w)
        self.gmail_email = QLineEdit(self.settings.gmail_address)
        self.gmail_email.setPlaceholderText("you@gmail.com")
        self.gmail_pw = QLineEdit()
        self.gmail_pw.setEchoMode(QLineEdit.Password)
        self.gmail_pw.setPlaceholderText("16-character App Password")
        if secrets.get_secret(secrets.GMAIL_APP_PASSWORD):
            self.gmail_pw.setText("")
            self.gmail_pw.setPlaceholderText("•••• (saved — type to replace)")
        self.gmail_test_btn = QPushButton("Test connection")
        self.gmail_test_btn.clicked.connect(self._test_gmail)
        self.gmail_status = QLabel("")
        self.gmail_status.setWordWrap(True)
        form.addRow("Email:", self.gmail_email)
        form.addRow("App Password:", self.gmail_pw)
        form.addRow("", self.gmail_test_btn)
        form.addRow(self.gmail_status)
        return w

    def _build_outlook(self) -> QWidget:
        w = QGroupBox("Connect Outlook")
        v = QVBoxLayout(w)

        # Microsoft app (client) ID — a one-time admin value (see README).
        form = QFormLayout()
        self.outlook_client_id = QLineEdit(self.settings.outlook_client_id)
        self.outlook_client_id.setPlaceholderText("Microsoft app (client) ID — see README admin setup")
        self.outlook_client_id.editingFinished.connect(self._on_client_id_changed)
        form.addRow("App (client) ID:", self.outlook_client_id)
        v.addLayout(form)

        self.outlook_signin_btn = QPushButton("Sign in with Microsoft")
        self.outlook_signin_btn.clicked.connect(self._sign_in_outlook)
        self.outlook_signout_btn = QPushButton("Sign out")
        self.outlook_signout_btn.clicked.connect(self._sign_out_outlook)
        self.outlook_status = QLabel("")
        self.outlook_status.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.outlook_signin_btn)
        row.addWidget(self.outlook_signout_btn)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(self.outlook_status)
        self._note = QLabel(
            "Enter the Microsoft app (client) ID once to enable Outlook sign-in. "
            "Your admin creates it in Microsoft Entra (see README)."
        )
        self._note.setWordWrap(True)
        v.addWidget(self._note)
        return w

    def _on_client_id_changed(self) -> None:
        self.settings.outlook_client_id = self.outlook_client_id.text().strip()
        config.save_settings(self.settings)
        self._refresh_outlook_status()

    # --- handlers ------------------------------------------------------------
    def _on_provider_changed(self) -> None:
        if self.rb_outlook.isChecked():
            self.settings.sender_type = "outlook"
            self._stack.setCurrentIndex(1)
        else:
            self.settings.sender_type = "gmail"
            self._stack.setCurrentIndex(0)
        config.save_settings(self.settings)

    def _on_cap_changed(self, value: int) -> None:
        self.settings.daily_cap = value
        config.save_settings(self.settings)

    def _copy_to_clipboard(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)

    def _test_gmail(self) -> None:
        email = self.gmail_email.text().strip()
        # App Passwords are shown as "abcd efgh ijkl mnop"; ignore ALL whitespace.
        typed = "".join(self.gmail_pw.text().split())
        pw = typed or (secrets.get_secret(secrets.GMAIL_APP_PASSWORD) or "")
        if not email or not pw:
            self.gmail_status.setText("Enter your Gmail address and App Password first.")
            return
        self.gmail_test_btn.setEnabled(False)
        self.gmail_status.setText("Testing…")
        sender = GmailSender(email, pw)
        self._worker = FuncWorker(sender.test_connection)
        self._worker.done.connect(lambda r: self._on_gmail_tested(r, email, pw))
        self._worker.failed.connect(lambda m: self._on_gmail_tested(None, email, pw, error=m))
        self._worker.start()

    def _on_gmail_tested(self, result, email, pw, error: str | None = None) -> None:
        self.gmail_test_btn.setEnabled(True)
        if error or result is None or not result.ok:
            msg = error or (result.message if result else "Connection failed.")
            self.gmail_status.setText(f"❌ {msg}")
            return
        # Save on success.
        secrets.set_secret(secrets.GMAIL_APP_PASSWORD, pw)
        self.settings.gmail_address = email
        config.save_settings(self.settings)
        self.gmail_status.setText(f"✅ {result.message}")

    def _sign_in_outlook(self) -> None:
        self.outlook_signin_btn.setEnabled(False)
        self.outlook_status.setText("Opening Microsoft sign-in in your browser…")
        sender = OutlookSender(client_id=self.settings.effective_outlook_client_id())
        self._worker = FuncWorker(sender.sign_in)
        self._worker.done.connect(self._on_outlook_signed_in)
        self._worker.failed.connect(lambda m: self._on_outlook_signed_in(None, error=m))
        self._worker.start()

    def _on_outlook_signed_in(self, result, error: str | None = None) -> None:
        self.outlook_signin_btn.setEnabled(True)
        if error or result is None or not result.ok:
            self.outlook_status.setText(f"❌ {error or (result.message if result else 'Sign-in failed.')}")
            return
        self.settings.outlook_address = result.address
        config.save_settings(self.settings)
        self._refresh_outlook_status()

    def _sign_out_outlook(self) -> None:
        OutlookSender(client_id=self.settings.effective_outlook_client_id()).sign_out()
        self.settings.outlook_address = ""
        config.save_settings(self.settings)
        self._refresh_outlook_status()

    def _refresh_outlook_status(self) -> None:
        has_client_id = bool(self.settings.effective_outlook_client_id())
        self.outlook_signin_btn.setEnabled(has_client_id)
        addr = self.settings.outlook_address
        if addr:
            self.outlook_status.setText(f"✅ Signed in as {addr}")
            self.outlook_signout_btn.setEnabled(True)
        else:
            self.outlook_status.setText(
                "Not signed in." if has_client_id else "Enter the app (client) ID above to enable sign-in."
            )
            self.outlook_signout_btn.setEnabled(False)
