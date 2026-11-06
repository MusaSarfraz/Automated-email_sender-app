"""The sender interface and its error taxonomy.

The campaign loop (see :mod:`email_sender.core.campaign`) relies on the *type*
of error raised by :meth:`Sender.send` to decide what to do:

* :class:`NetworkError`   — transient (no internet, DNS, timeout). Keep the row
  PENDING and retry once connectivity returns. **Never** marks a row FAILED.
* :class:`PermanentError` — the server rejected the message (5xx, bad
  recipient). Mark the row FAILED with the reason.
* :class:`AuthError`      — authentication failed. Three in a row pause the
  campaign and ask the user to reconnect their mailbox.

To make the power-cut failsafe deterministic, the caller generates a message id
*before* sending (via :meth:`new_message_id`), saves it with the SENDING state,
then passes it to :meth:`send`. On restart, :meth:`find_in_sent` can look that
exact id up in the Sent folder to decide SENT vs. retry.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


class SenderError(Exception):
    """Base class for all send failures."""


class NetworkError(SenderError):
    """Transient connectivity problem; safe to retry the same row later."""


class AuthError(SenderError):
    """Authentication/authorisation failed; the user must reconnect."""


class PermanentError(SenderError):
    """The message was rejected for a reason retrying won't fix."""


@dataclass
class TestResult:
    """Outcome of :meth:`Sender.test_connection`."""

    ok: bool
    #: The verified account address when ``ok`` (else empty).
    address: str = ""
    #: A friendly, non-technical message to show the user.
    message: str = ""


class Sender(abc.ABC):
    """Common interface implemented by the Gmail and Outlook backends."""

    #: Human-friendly provider label, e.g. "Gmail".
    provider: str = "mailbox"

    @abc.abstractmethod
    def test_connection(self) -> TestResult:
        """Verify credentials/connectivity without sending a real campaign email."""

    @abc.abstractmethod
    def new_message_id(self) -> str:
        """Generate a unique message id (with angle brackets) to use for a send."""

    @abc.abstractmethod
    def send(self, *, to: str, subject: str, body: str, message_id: str) -> str:
        """Send one plain-text email and return its message id.

        Must raise :class:`NetworkError`, :class:`AuthError`, or
        :class:`PermanentError` — never a bare provider exception.
        """

    @abc.abstractmethod
    def find_in_sent(self, *, message_id: str, recipient: str, subject: str | None = None) -> bool:
        """Return True if a matching message is already in the Sent folder.

        Used on restart to reconcile a row left in SENDING by a crash.
        """

    def close(self) -> None:  # pragma: no cover - default no-op
        """Release any held connections. Safe to call more than once."""
        return None

    # Context-manager sugar so callers can use ``with build_sender(...) as s:``.
    def __enter__(self) -> "Sender":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
