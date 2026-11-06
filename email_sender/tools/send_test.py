"""CLI: test a mailbox connection and optionally send one real test email.

Examples
--------
Test the currently-configured mailbox (from settings.json + Credential Manager)::

    python -m email_sender.tools.send_test --test

Send one email with inline Gmail credentials (handy before wiring up the UI)::

    python -m email_sender.tools.send_test \
        --gmail you@gmail.com --app-password "xxxx xxxx xxxx xxxx" \
        --to friend@example.com

Sign in to Outlook interactively and send a test (needs a configured client ID
via EMAILSENDER_OUTLOOK_CLIENT_ID)::

    python -m email_sender.tools.send_test --outlook --to friend@example.com
"""

from __future__ import annotations

import argparse
import sys

from ..core import config
from ..core.senders import SenderError
from ..core.senders.factory import NotConnectedError, build_sender
from ..core.senders.gmail_smtp import GmailSender
from ..core.senders.outlook_graph import OutlookSender


def _build_sender(args: argparse.Namespace):
    if args.gmail:
        if not args.app_password:
            raise SystemExit("--gmail requires --app-password")
        return GmailSender(args.gmail, args.app_password)
    if args.outlook:
        sender = OutlookSender(client_id=config.outlook_client_id())
        result = sender.sign_in()
        if not result.ok:
            raise SystemExit(f"Outlook sign-in failed: {result.message}")
        print(f"Signed in: {result.address}")
        return sender
    # Fall back to the configured mailbox.
    try:
        return build_sender()
    except NotConnectedError as exc:
        raise SystemExit(str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test a mailbox and optionally send one email.")
    parser.add_argument("--gmail", metavar="ADDRESS", help="Gmail address to use")
    parser.add_argument("--app-password", metavar="PW", help="Gmail App Password")
    parser.add_argument("--outlook", action="store_true", help="Use Outlook (interactive sign-in)")
    parser.add_argument("--to", metavar="ADDRESS", help="Recipient; if omitted, only tests the connection")
    parser.add_argument("--subject", default="EmailSender test")
    parser.add_argument("--body", default="This is a test email from EmailSender. If you received it, sending works.")
    parser.add_argument("--test", action="store_true", help="Only run the connection test")
    args = parser.parse_args(argv)

    sender = _build_sender(args)

    print(f"Testing {sender.provider} connection...")
    result = sender.test_connection()
    print(("  OK: " if result.ok else "  FAILED: ") + (result.message or ""))
    if not result.ok:
        return 2
    if args.test or not args.to:
        return 0

    msg_id = sender.new_message_id()
    print(f"Sending test email to {args.to} (message id {msg_id})...")
    try:
        sender.send(to=args.to, subject=args.subject, body=args.body, message_id=msg_id)
    except SenderError as exc:
        print(f"  Send failed: {exc}")
        return 3
    print("  Sent. Checking the Sent folder...")
    try:
        found = sender.find_in_sent(message_id=msg_id, recipient=args.to, subject=args.subject)
        print(f"  Found in Sent: {found}")
    except SenderError as exc:
        print(f"  Sent-folder check failed (non-fatal): {exc}")
    finally:
        sender.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
