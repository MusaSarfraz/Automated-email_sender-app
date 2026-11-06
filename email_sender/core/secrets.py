"""Thin wrapper over :mod:`keyring` (Windows Credential Manager).

Secrets never touch plain files: the Gmail App Password and the Outlook MSAL
token cache are stored here, keyed by a stable account name.
"""

from __future__ import annotations

import keyring

from .config import KEYRING_SERVICE

# Account keys used within the single keyring service.
GMAIL_APP_PASSWORD = "gmail_app_password"
OUTLOOK_TOKEN_CACHE = "outlook_token_cache"


def set_secret(account: str, value: str) -> None:
    """Store (or overwrite) a secret under ``account``."""
    keyring.set_password(KEYRING_SERVICE, account, value)


def get_secret(account: str) -> str | None:
    """Return the stored secret for ``account`` or ``None`` if absent."""
    return keyring.get_password(KEYRING_SERVICE, account)


def delete_secret(account: str) -> None:
    """Remove a secret if present; silently ignore if it is already gone."""
    try:
        keyring.delete_password(KEYRING_SERVICE, account)
    except keyring.errors.PasswordDeleteError:
        pass
