"""Crash/power-cut test: kill the process mid-send, restart, verify no duplicate.

Launches :mod:`email_sender.tests.crash_helper` as a real subprocess, kills it
while the first email is "in flight", then runs it again to completion and
checks that every recipient was sent exactly once.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from email_sender.core import progress as P
from email_sender.core.config import Status

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECIPIENTS = ["a@x.com", "b@x.com", "c@x.com"]


def _run_helper(progress_dir: Path, sent_file: Path, send_sleep: float, gap: float) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "email_sender.tests.crash_helper",
            str(progress_dir),
            str(sent_file),
            str(send_sleep),
            str(gap),
        ],
        cwd=str(PROJECT_ROOT),
    )


def test_crash_mid_send_no_duplicate(tmp_path):
    progress_dir = tmp_path / "camp"
    sent_file = tmp_path / "sent.log"

    # Run 1: long per-send sleep so we can kill it mid-send.
    proc = _run_helper(progress_dir, sent_file, send_sleep=30.0, gap=0.1)
    try:
        # Wait until the first recipient has been "transmitted" (file has 1 line).
        deadline = time.time() + 20
        while time.time() < deadline:
            if sent_file.exists() and len(sent_file.read_text().splitlines()) >= 1:
                break
            time.sleep(0.05)
        else:
            proc.kill()
            pytest.fail("Helper never sent the first email")

        # Simulate a power cut: kill mid-send, before SENT was saved.
        proc.kill()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()

    # Exactly one email went out, and progress shows it stuck in SENDING.
    first_lines = sent_file.read_text().splitlines()
    assert len(first_lines) == 1
    prog = P.load_progress(P.progress_path(progress_dir))
    assert prog.row(first_lines[0])["status"] == Status.SENDING

    # Run 2: resume to completion (no sleep now).
    proc2 = _run_helper(progress_dir, sent_file, send_sleep=0.0, gap=0.1)
    proc2.wait(timeout=30)
    assert proc2.returncode == 0

    # Every recipient sent exactly once — no duplicate of the in-flight one.
    lines = sent_file.read_text().splitlines()
    assert sorted(lines) == sorted(RECIPIENTS)
    assert len(lines) == len(set(lines))  # no duplicates

    prog = P.load_progress(P.progress_path(progress_dir))
    assert prog.state == P.STATE_COMPLETED
    for r in RECIPIENTS:
        assert prog.row(r)["status"] == Status.SENT
