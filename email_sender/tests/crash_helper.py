"""Subprocess used by the crash test.

Runs a tiny real campaign whose "sender" records each recipient to a file and
then sleeps, creating a window to kill the process *after* the email went out
but *before* the SENT state is saved. On the next run it must reconcile that
in-flight row against the "Sent folder" (the same file) and never resend it.

Usage::

    python -m email_sender.tests.crash_helper <progress_dir> <sent_file> <send_sleep> <gap>
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from email_sender.core import campaign as campaign_mod
from email_sender.core import progress as P
from email_sender.core.campaign import Campaign
from email_sender.core.readers import build_result
from email_sender.core.senders.base import Sender, TestResult

RECIPIENTS = ["a@x.com", "b@x.com", "c@x.com"]


class FileSender(Sender):
    """Appends the recipient to a shared file on send; that file *is* the Sent folder."""

    provider = "File"

    def __init__(self, sent_file: Path, send_sleep: float):
        self.sent_file = Path(sent_file)
        self.send_sleep = send_sleep
        self._n = 0

    def test_connection(self):
        return TestResult(ok=True, address="file@x", message="ok")

    def new_message_id(self):
        self._n += 1
        return f"<{self._n}@file>"

    def send(self, *, to, subject, body, message_id):
        # 1) "Transmit" — record that this recipient's email went out, durably.
        with open(self.sent_file, "a", encoding="utf-8") as f:
            f.write(to + "\n")
            f.flush()
            os.fsync(f.fileno())
        # 2) Linger, so the parent can kill us here (before SENT is saved).
        if self.send_sleep:
            time.sleep(self.send_sleep)
        return message_id

    def find_in_sent(self, *, message_id, recipient, subject=None):
        if not self.sent_file.exists():
            return False
        lines = self.sent_file.read_text(encoding="utf-8").splitlines()
        return recipient in lines


def _rows():
    headers = ["name", "company", "email", "subject", "body"]
    data = [["N", "C", r, "Hi {{ name }}", "Body"] for r in RECIPIENTS]
    return build_result(headers, data).rows


def main(argv):
    progress_dir = Path(argv[0])
    sent_file = Path(argv[1])
    send_sleep = float(argv[2])
    gap = float(argv[3])

    progress_dir.mkdir(parents=True, exist_ok=True)
    ppath = P.progress_path(progress_dir)

    # Shrink the fixed gap for the test only (module-global, no spec change).
    campaign_mod.SEND_GAP_SECONDS = gap

    if ppath.exists():
        prog = P.load_progress(ppath)
    else:
        prog = P.Progress(campaign_id="crash", source="s.xlsx", source_type="excel")

    camp = Campaign(
        progress_path=ppath,
        progress=prog,
        rows=_rows(),
        sender=FileSender(sent_file, send_sleep),
        daily_cap=1000,
    )
    camp.run()


if __name__ == "__main__":
    main(sys.argv[1:])
