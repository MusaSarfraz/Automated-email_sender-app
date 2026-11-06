"""Mailbox senders: a common interface plus Gmail and Outlook backends."""

from .base import (
    AuthError,
    NetworkError,
    PermanentError,
    Sender,
    SenderError,
    TestResult,
)

__all__ = [
    "Sender",
    "SenderError",
    "AuthError",
    "NetworkError",
    "PermanentError",
    "TestResult",
]
