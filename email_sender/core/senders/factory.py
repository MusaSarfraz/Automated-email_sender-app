"""Build the configured :class:`Sender` from settings + stored secrets."""

from __future__ import annotations

from .. import config, secrets
from .base import Sender
from .gmail_smtp import GmailSender
from .outlook_graph import OutlookSender


class NotConnectedError(Exception):
    """The chosen mailbox has no stored credentials yet."""


def build_sender(settings: config.Settings | None = None) -> Sender:
    """Return a ready-to-use sender for the configured provider.

    Raises :class:`NotConnectedError` if the mailbox hasn't been connected.
    """
    settings = settings or config.load_settings()

    if settings.sender_type == "gmail":
        password = secrets.get_secret(secrets.GMAIL_APP_PASSWORD)
        if not settings.gmail_address or not password:
            raise NotConnectedError("Gmail is not connected. Open Settings and add your App Password.")
        return GmailSender(settings.gmail_address, password)

    if settings.sender_type == "outlook":
        return OutlookSender(client_id=settings.effective_outlook_client_id())

    raise NotConnectedError(f"Unknown sender type: {settings.sender_type!r}")
