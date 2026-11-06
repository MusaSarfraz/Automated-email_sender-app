"""Tests for the campaign loop and failsafe, using a fake sender + manual clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from email_sender.core import progress as P
from email_sender.core.campaign import (
    RESULT_AUTH_PAUSED,
    RESULT_COMPLETED,
    RESULT_PAUSED,
    RESULT_STOPPED,
    Campaign,
)
from email_sender.core.config import Status
from email_sender.core.readers import build_result
from email_sender.core.senders.base import AuthError, NetworkError, PermanentError, Sender
from email_sender.core.senders.base import TestResult as ConnResult

TZ = timezone(timedelta(hours=5))
START = datetime(2026, 11, 4, 12, 0, 0, tzinfo=TZ)


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #
class ManualClock:
    def __init__(self, start=START):
        self.t = start
        self.on_sleep = None  # optional one-shot hook(clock)

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)
        if self.on_sleep is not None:
            self.on_sleep(self)


class FakeSender(Sender):
    provider = "Fake"

    def __init__(self, *, fail_seq=None, preloaded_sent=None):
        self.sent = []  # list of (to, subject, message_id)
        self.in_sent = set(preloaded_sent or [])
        self._fail_seq = list(fail_seq or [])
        self._i = 0
        self._n = 0

    def test_connection(self):
        return ConnResult(ok=True, address="fake@x.com", message="ok")

    def new_message_id(self):
        self._n += 1
        return f"<{self._n}@fake>"

    def send(self, *, to, subject, body, message_id):
        exc = self._fail_seq[self._i] if self._i < len(self._fail_seq) else None
        self._i += 1
        if exc is not None:
            raise exc
        self.sent.append((to, subject, message_id))
        self.in_sent.add(message_id)
        return message_id

    def find_in_sent(self, *, message_id, recipient, subject=None):
        return message_id in self.in_sent


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def make_rows(specs):
    """specs: list of (name, company, email, subject, body)."""
    headers = ["name", "company", "email", "subject", "body"]
    return build_result(headers, [list(s) for s in specs]).rows


def make_campaign(tmp_path, rows, sender, *, daily_cap=450, clock=None, dry_run=False, events=None):
    prog = P.Progress(campaign_id="c1", source="s.xlsx", source_type="excel")
    on_event = (lambda k, d: events.append((k, d))) if events is not None else None
    return Campaign(
        progress_path=tmp_path / "progress.json",
        progress=prog,
        rows=rows,
        sender=sender,
        daily_cap=daily_cap,
        clock=clock or ManualClock(),
        dry_run=dry_run,
        on_event=on_event,
        wait_slice=10_000,  # one sleep jumps straight to the target (fast tests)
    )


def _sent_at(camp, email):
    return datetime.fromisoformat(camp.progress.row(email)["sent_at"])


# --------------------------------------------------------------------------- #
# 80-second gap                                                               #
# --------------------------------------------------------------------------- #
def test_80s_gap_enforced_between_sends(tmp_path):
    rows = make_rows(
        [("A", "C", "a@x.com", "Hi {{ name }}", "b"), ("B", "C", "b@x.com", "Hi {{ name }}", "b")]
    )
    sender = FakeSender()
    camp = make_campaign(tmp_path, rows, sender)
    assert camp.run() == RESULT_COMPLETED
    gap = (_sent_at(camp, "b@x.com") - _sent_at(camp, "a@x.com")).total_seconds()
    assert gap == 80
    assert len(sender.sent) == 2


def test_gap_honoured_across_restart(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "s", "b"), ("B", "C", "b@x.com", "s", "b")])
    sender = FakeSender()
    # Pretend a@x.com was sent 30s ago in a previous process.
    clock = ManualClock()
    camp = make_campaign(tmp_path, rows, sender, clock=clock)
    camp.progress.last_sent_at = (START - timedelta(seconds=30)).isoformat()
    camp.progress.set_row("a@x.com", status=Status.SENT, sent_at=(START - timedelta(seconds=30)).isoformat())
    camp.run()
    # b@x.com must wait the remaining 50s, not a fresh 80s.
    assert _sent_at(camp, "b@x.com") == START + timedelta(seconds=50)


# --------------------------------------------------------------------------- #
# Pause / resume and stop / continue                                          #
# --------------------------------------------------------------------------- #
def test_pause_during_wait_then_resume(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "s", "b"), ("B", "C", "b@x.com", "s", "b")])
    sender = FakeSender()
    clock = ManualClock()
    camp = make_campaign(tmp_path, rows, sender, clock=clock)

    def pause_once(c):
        camp.request_pause()
        c.on_sleep = None

    clock.on_sleep = pause_once

    assert camp.run() == RESULT_PAUSED
    assert camp.progress.state == P.STATE_PAUSED
    assert len(sender.sent) == 1  # only a@x.com went out
    assert camp.progress.row("b@x.com")["status"] == Status.PENDING

    # Resume: b@x.com sent, honouring the remaining gap.
    assert camp.run() == RESULT_COMPLETED
    assert len(sender.sent) == 2


def test_stop_then_continue(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "s", "b"), ("B", "C", "b@x.com", "s", "b")])
    sender = FakeSender()
    clock = ManualClock()
    camp = make_campaign(tmp_path, rows, sender, clock=clock)

    def stop_once(c):
        camp.request_stop()
        c.on_sleep = None

    clock.on_sleep = stop_once
    assert camp.run() == RESULT_STOPPED
    assert camp.progress.state == P.STATE_STOPPED
    assert len(sender.sent) == 1

    assert camp.run() == RESULT_COMPLETED
    assert len(sender.sent) == 2


# --------------------------------------------------------------------------- #
# Daily cap                                                                   #
# --------------------------------------------------------------------------- #
def test_daily_cap_waits_for_window(tmp_path):
    rows = make_rows(
        [("A", "C", "a@x.com", "s", "b"), ("B", "C", "b@x.com", "s", "b"), ("C", "C", "c@x.com", "s", "b")]
    )
    sender = FakeSender()
    clock = ManualClock()
    camp = make_campaign(tmp_path, rows, sender, daily_cap=2, clock=clock)
    assert camp.run() == RESULT_COMPLETED
    assert len(sender.sent) == 3
    # The 3rd send had to wait until the oldest send aged out (~24h after a@x.com).
    assert _sent_at(camp, "c@x.com") >= START + timedelta(hours=24)


# --------------------------------------------------------------------------- #
# SENDING recovery on restart                                                 #
# --------------------------------------------------------------------------- #
def test_recover_sending_found_marks_sent_without_resending(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "s", "b"), ("B", "C", "b@x.com", "s", "b")])
    sender = FakeSender(preloaded_sent=["<prev@fake>"])
    camp = make_campaign(tmp_path, rows, sender)
    # a@x.com was mid-send when the process died; its id IS in the Sent folder.
    camp.progress.set_row("a@x.com", status=Status.SENDING, message_id="<prev@fake>", subject="s")

    assert camp.run() == RESULT_COMPLETED
    assert camp.progress.row("a@x.com")["status"] == Status.SENT
    # a@x.com was NOT re-sent; only b@x.com went out this run.
    assert [to for (to, _, _) in sender.sent] == ["b@x.com"]


def test_recover_sending_not_found_resends(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "s", "b")])
    sender = FakeSender()  # nothing preloaded -> not found
    camp = make_campaign(tmp_path, rows, sender)
    camp.progress.set_row("a@x.com", status=Status.SENDING, message_id="<gone@fake>", subject="s")

    assert camp.run() == RESULT_COMPLETED
    assert camp.progress.row("a@x.com")["status"] == Status.SENT
    assert [to for (to, _, _) in sender.sent] == ["a@x.com"]


# --------------------------------------------------------------------------- #
# Network retry and permanent/auth errors                                     #
# --------------------------------------------------------------------------- #
def test_network_error_retries_same_row_never_failed(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "s", "b")])
    sender = FakeSender(fail_seq=[NetworkError("down"), NetworkError("down"), None])
    clock = ManualClock()
    camp = make_campaign(tmp_path, rows, sender, clock=clock)
    assert camp.run() == RESULT_COMPLETED
    assert camp.progress.row("a@x.com")["status"] == Status.SENT
    assert sender._i == 3  # two failures + one success, same row
    assert clock.now() > START  # backoff time elapsed


def test_permanent_error_marks_failed(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "s", "b"), ("B", "C", "b@x.com", "s", "b")])
    sender = FakeSender(fail_seq=[PermanentError("550 bad mailbox")])
    camp = make_campaign(tmp_path, rows, sender)
    assert camp.run() == RESULT_COMPLETED
    assert camp.progress.row("a@x.com")["status"] == Status.FAILED
    assert "550" in camp.progress.row("a@x.com")["error"]
    assert camp.progress.row("b@x.com")["status"] == Status.SENT


def test_three_auth_errors_pause_campaign(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "s", "b")])
    sender = FakeSender(fail_seq=[AuthError("x"), AuthError("x"), AuthError("x")])
    events = []
    camp = make_campaign(tmp_path, rows, sender, events=events)
    assert camp.run() == RESULT_AUTH_PAUSED
    assert camp.progress.state == P.STATE_PAUSED
    assert camp.progress.row("a@x.com")["status"] == Status.PENDING  # not FAILED
    assert any(k == "auth_paused" for k, _ in events)


# --------------------------------------------------------------------------- #
# Template failures                                                           #
# --------------------------------------------------------------------------- #
def test_missing_placeholder_marks_failed_not_sent(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "Hi {{ missing }}", "b")])
    sender = FakeSender()
    camp = make_campaign(tmp_path, rows, sender)
    assert camp.run() == RESULT_COMPLETED
    assert camp.progress.row("a@x.com")["status"] == Status.FAILED
    assert "missing" in camp.progress.row("a@x.com")["error"]
    assert sender.sent == []


# --------------------------------------------------------------------------- #
# Dry run                                                                     #
# --------------------------------------------------------------------------- #
def test_dry_run_sends_nothing_but_marks_sent(tmp_path):
    rows = make_rows([("A", "C", "a@x.com", "Hi {{ name }}", "b")])
    camp = make_campaign(tmp_path, rows, sender=None, dry_run=True)
    assert camp.run() == RESULT_COMPLETED
    assert camp.progress.row("a@x.com")["status"] == Status.SENT


# --------------------------------------------------------------------------- #
# Reload diff / apply                                                         #
# --------------------------------------------------------------------------- #
def test_reload_diff_and_apply(tmp_path):
    rows = make_rows(
        [
            ("A", "C", "a@x.com", "s", "b"),
            ("B", "C", "b@x.com", "s", "b"),
            ("D", "C", "d@x.com", "s", "b"),
        ]
    )
    sender = FakeSender()
    camp = make_campaign(tmp_path, rows, sender)
    # a@x.com already sent; must never be touched by a reload.
    camp.progress.set_row("a@x.com", status=Status.SENT, sent_at=START.isoformat())

    new = build_result(
        ["name", "company", "email", "subject", "body"],
        [
            ["A2", "C", "a@x.com", "s", "b"],   # edited but SENT -> ignored
            ["B2", "C", "b@x.com", "s2", "b2"],  # edited unsent
            ["E", "C", "e@x.com", "s", "b"],     # new
            # d@x.com removed
        ],
    )
    diff = camp.diff(new)
    assert set(diff.new) == {"e@x.com"}
    assert set(diff.edited) == {"b@x.com"}
    assert set(diff.removed) == {"d@x.com"}

    camp.apply_reload(new)
    assert camp.progress.row("a@x.com")["status"] == Status.SENT  # untouched
    assert "d@x.com" not in camp.progress.rows  # dropped
    assert camp.progress.row("e@x.com")["status"] == Status.PENDING
    # b@x.com now uses the edited content.
    b_row = next(r for r in camp.rows if r.email == "b@x.com")
    assert b_row.data["name"] == "B2"
    assert b_row.data["subject"] == "s2"
