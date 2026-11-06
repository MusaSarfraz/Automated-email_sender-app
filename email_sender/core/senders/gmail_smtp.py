"""Gmail backend: send via SMTP (STARTTLS) and verify via IMAP.

Uses an App Password (2-Step Verification required on the Google account).
Sending: ``smtp.gmail.com:587`` with STARTTLS. Sent-folder check:
``imap.gmail.com:993`` with the same App Password.
"""

from __future__ import annotations

import imaplib
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .base import AuthError, NetworkError, PermanentError, Sender, SenderError, TestResult

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Network/socket failures that mean "try again later", not "give up".
_NETWORK_EXC = (
    socket.gaierror,
    socket.timeout,
    TimeoutError,
    ConnectionError,
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
)


def build_message(*, from_addr: str, to: str, subject: str, body: str, message_id: str) -> EmailMessage:
    """Build a plain-text UTF-8 email that preserves the body's line breaks."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = formatdate(localtime=True)
    # set_content keeps the body as-is (incl. newlines) and picks a safe encoding.
    msg.set_content(body)
    return msg


def classify_smtp_error(exc: Exception) -> SenderError:
    """Map an smtplib/socket exception to the sender error taxonomy."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return AuthError(_friendly_auth(exc))
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        # All recipients refused — permanent for this address.
        reasons = "; ".join(f"{code} {msg.decode(errors='replace') if isinstance(msg, bytes) else msg}"
                            for code, msg in exc.recipients.values())
        return PermanentError(f"Recipient refused: {reasons}")
    if isinstance(exc, (smtplib.SMTPSenderRefused, smtplib.SMTPDataError, smtplib.SMTPResponseException)):
        code = getattr(exc, "smtp_code", 0) or 0
        detail = _decode(getattr(exc, "smtp_error", b"")) or str(exc)
        if 500 <= code < 600:
            return PermanentError(f"{code} {detail}")
        # 4xx and anything else transient → retry.
        return NetworkError(f"{code} {detail}")
    if isinstance(exc, _NETWORK_EXC):
        return NetworkError(f"Could not reach Gmail: {exc}")
    # Unknown — treat as transient so we don't wrongly burn a recipient as FAILED.
    return NetworkError(f"Unexpected sending error: {exc}")


class GmailSender(Sender):
    """Send and verify through a user's Gmail account."""

    provider = "Gmail"

    def __init__(self, address: str, app_password: str, *, timeout: float = 30.0) -> None:
        self.address = address.strip()
        self._password = app_password
        self._timeout = timeout

    # --- interface ------------------------------------------------------------
    def new_message_id(self) -> str:
        domain = self.address.split("@", 1)[-1] or "gmail.com"
        return make_msgid(domain=domain)

    def test_connection(self) -> TestResult:
        try:
            with self._smtp() as smtp:
                smtp.login(self.address, self._password)
        except smtplib.SMTPAuthenticationError:
            return TestResult(
                ok=False,
                message=(
                    "Gmail rejected the email address or App Password. Check the "
                    "16-character App Password and that 2-Step Verification is on."
                ),
            )
        except _NETWORK_EXC as exc:
            return TestResult(ok=False, message=f"Could not reach Gmail. Check your internet. ({exc})")
        except smtplib.SMTPException as exc:
            return TestResult(ok=False, message=f"Gmail connection failed: {exc}")
        return TestResult(ok=True, address=self.address, message="Connected to Gmail successfully.")

    def send(self, *, to: str, subject: str, body: str, message_id: str) -> str:
        msg = build_message(
            from_addr=self.address, to=to, subject=subject, body=body, message_id=message_id
        )
        try:
            with self._smtp() as smtp:
                smtp.login(self.address, self._password)
                smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001 - re-raised as a classified SenderError
            raise classify_smtp_error(exc) from exc
        return message_id

    def find_in_sent(self, *, message_id: str, recipient: str, subject: str | None = None) -> bool:
        """Search the Gmail Sent folder for a message with ``message_id``."""
        try:
            imap = self._imap()
        except _NETWORK_EXC as exc:
            raise NetworkError(f"Could not reach Gmail IMAP: {exc}") from exc
        try:
            try:
                imap.login(self.address, self._password)
            except imaplib.IMAP4.error as exc:
                raise AuthError(f"Gmail IMAP login failed: {exc}") from exc
            folder = self._sent_folder(imap)
            status, _ = imap.select(folder, readonly=True)
            if status != "OK":
                return False
            # Primary match: the exact Message-ID header we generated.
            status, data = imap.search(None, "HEADER", "Message-ID", message_id)
            if status == "OK" and data and data[0].split():
                return True
            # Fallback: match by recipient (covers rows sent before we saved an id).
            status, data = imap.search(None, "TO", recipient)
            return status == "OK" and bool(data and data[0].split())
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass

    # --- helpers --------------------------------------------------------------
    def _smtp(self) -> smtplib.SMTP:
        context = ssl.create_default_context()
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=self._timeout)
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        return smtp

    def _imap(self) -> imaplib.IMAP4_SSL:
        context = ssl.create_default_context()
        return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=context, timeout=self._timeout)

    @staticmethod
    def _sent_folder(imap: imaplib.IMAP4_SSL) -> str:
        """Find the Sent mailbox via its special-use flag, else a sane default."""
        try:
            status, boxes = imap.list()
            if status == "OK" and boxes:
                for raw in boxes:
                    line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
                    if "\\Sent" in line:
                        # The quoted mailbox name is the last token on the line.
                        if '"' in line:
                            return line.rsplit('"', 2)[-2]
                        return line.split()[-1]
        except Exception:  # noqa: BLE001
            pass
        return "[Gmail]/Sent Mail"


def _friendly_auth(exc: Exception) -> str:
    return (
        "Gmail rejected the App Password. Open Settings and re-enter a valid "
        "16-character App Password."
    )


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value) if value else ""
