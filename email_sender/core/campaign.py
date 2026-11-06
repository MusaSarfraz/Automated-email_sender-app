"""The campaign state machine, send loop, and power-cut failsafe.

No Qt here — this module is fully unit-testable. The GUI drives it from a
:class:`QThread` worker (Phase 4), calling :meth:`Campaign.run` and the
:meth:`Campaign.request_pause` / :meth:`Campaign.request_stop` controls.

Timing is injectable via a :class:`Clock`, and every wait is *interruptible*:
Pause and Stop take effect within a fraction of a second, but never mid-send —
an email already being transmitted is always finished first (spec §7).

Correctness guarantees (spec §6):
* The 80-second gap is measured from ``last_sent_at`` persisted in
  ``progress.json``, so restarts/pauses/resumes can never send two emails
  closer than 80 s.
* A row is marked SENDING **and saved** before the send, with a pre-generated
  message id. After a crash, that id is looked up in the Sent folder to decide
  SENT vs. retry — so at most one in-flight email is ever uncertain, and it is
  never silently duplicated.
* Network errors never mark a row FAILED; the same row is retried with backoff.
* Three consecutive auth errors pause the campaign for the user to reconnect.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import make_msgid
from pathlib import Path
from typing import Callable, Protocol

from . import progress as P
from . import template
from .config import SEND_GAP_SECONDS, Status
from .readers import Row, SheetResult
from .senders.base import AuthError, NetworkError, PermanentError, Sender

# Loop outcomes (what ended the current run()).
RESULT_PAUSED = "paused"
RESULT_STOPPED = "stopped"
RESULT_COMPLETED = "completed"
RESULT_AUTH_PAUSED = "auth_paused"

_DAY = timedelta(hours=24)
_NET_BACKOFF_START = 5.0
_NET_BACKOFF_MAX = 60.0
_AUTH_RETRY_WAIT = 5.0


# --------------------------------------------------------------------------- #
# Clock abstraction (injectable for tests)                                    #
# --------------------------------------------------------------------------- #
class Clock(Protocol):  # pragma: no cover - interface
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    """Wall-clock time in the machine's local timezone (offset-aware)."""

    def now(self) -> datetime:
        return datetime.now().astimezone()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# --------------------------------------------------------------------------- #
# Sheet reload diff                                                           #
# --------------------------------------------------------------------------- #
@dataclass
class SheetDiff:
    """Summary of how a reloaded sheet differs from the running campaign."""

    new: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0

    def summary(self) -> str:
        return f"{len(self.new)} new · {len(self.edited)} edited · {len(self.removed)} removed"

    @property
    def is_empty(self) -> bool:
        return not (self.new or self.edited or self.removed)


# --------------------------------------------------------------------------- #
# Campaign                                                                    #
# --------------------------------------------------------------------------- #
class Campaign:
    """One campaign: its rows, its progress, and the loop that sends them."""

    def __init__(
        self,
        *,
        progress_path: str | Path,
        progress: P.Progress,
        rows: list[Row],
        sender: Sender | None,
        daily_cap: int,
        clock: Clock | None = None,
        dry_run: bool = False,
        on_event: Callable[[str, dict], None] | None = None,
        wait_slice: float = 0.25,
    ) -> None:
        self.progress_path = Path(progress_path)
        self.progress = progress
        self.sender = sender
        self.daily_cap = int(daily_cap)
        self.clock = clock or RealClock()
        self.dry_run = dry_run
        self._on_event = on_event
        self._wait_slice = wait_slice

        self._rows: list[Row] = list(rows)
        self._by_email: dict[str, Row] = {r.email: r for r in self._rows if r.email}

        self._stop = threading.Event()
        self._pause = threading.Event()
        self._auth_failures = 0
        self._net_backoff = _NET_BACKOFF_START

        self._seed_progress_rows()

    # --- public controls ------------------------------------------------------
    def request_pause(self) -> None:
        """Ask the loop to pause after the current email (if any) finishes."""
        self._pause.set()

    def request_stop(self) -> None:
        """Ask the loop to stop after the current email (if any) finishes."""
        self._stop.set()

    @property
    def rows(self) -> list[Row]:
        return self._rows

    # --- main loop ------------------------------------------------------------
    def run(self) -> str:
        """Run until paused, stopped, completed, or auth-paused. Returns the reason.

        If the saved state is already paused/stopped (and no fresh resume was
        requested), returns immediately without sending.
        """
        self._stop.clear()
        self._pause.clear()
        self._auth_failures = 0

        self.progress.state = P.STATE_RUNNING
        self._save()
        self._emit("state", state=P.STATE_RUNNING)

        reason = self._recover_sending()
        if reason:
            return self._finish(reason)

        while True:
            if self._stop.is_set():
                return self._finish(RESULT_STOPPED)
            if self._pause.is_set():
                return self._finish(RESULT_PAUSED)

            row = self._next_pending()
            if row is None:
                return self._finish(RESULT_COMPLETED)

            if self._cap_reached():
                r = self._wait_for_cap()
                if r:
                    return self._finish(r)
                continue

            r = self._wait_for_gap()
            if r:
                return self._finish(r)

            outcome = self._send_row(row)
            if outcome == RESULT_AUTH_PAUSED:
                return self._finish(RESULT_AUTH_PAUSED)
            # On "retry" or success we simply loop again; _next_pending picks the
            # same row again on retry, or the next one after a success/failure.

    # --- restart reconciliation ----------------------------------------------
    def _recover_sending(self) -> str | None:
        """Reconcile any rows left in SENDING by a crash (spec §6.2)."""
        for row in self._rows:
            if self._status(row.email) != Status.SENDING:
                continue
            rec = self.progress.row(row.email) or {}
            mid = rec.get("message_id") or ""
            subject = rec.get("subject")

            if self.dry_run or self.sender is None:
                # Nothing was really transmitted in a dry run — safe to resend.
                self._set(row.email, status=Status.PENDING)
                self._save()
                continue

            while True:
                if self._stop.is_set():
                    return RESULT_STOPPED
                if self._pause.is_set():
                    return RESULT_PAUSED
                try:
                    found = self.sender.find_in_sent(
                        message_id=mid, recipient=row.raw_email, subject=subject
                    )
                except NetworkError as exc:
                    self._emit("network_error", email=row.email, error=str(exc), phase="recover")
                    if self._backoff_wait():  # interrupted
                        return RESULT_STOPPED if self._stop.is_set() else RESULT_PAUSED
                    continue
                except AuthError as exc:
                    self._emit("auth_error", email=row.email, error=str(exc), phase="recover")
                    return RESULT_AUTH_PAUSED
                break

            self._net_backoff = _NET_BACKOFF_START
            if found:
                self._mark_sent(row.email, mid, subject or "", recovered=True)
                self._emit("row_recovered", email=row.email, result=Status.SENT)
            else:
                self._set(row.email, status=Status.PENDING)
                self._save()
                self._emit("row_recovered", email=row.email, result=Status.PENDING)
        return None

    # --- the send step --------------------------------------------------------
    def _send_row(self, row: Row) -> str | None:
        email = row.email
        try:
            subject, body = template.render_email(
                row.data.get("subject", ""), row.data.get("body", ""), row.data
            )
        except template.TemplateError as exc:
            self._set(email, status=Status.FAILED, error=str(exc))
            self._save()
            self._emit("row_failed", email=email, error=str(exc))
            return None

        mid = self._new_message_id()
        self._set(email, status=Status.SENDING, message_id=mid, subject=subject, error=None)
        self._save()
        self._emit("sending", email=email, subject=subject)

        if self.dry_run or self.sender is None:
            self._mark_sent(email, mid, subject)
            self._emit("row_sent", email=email, subject=subject, dry_run=True)
            return None

        try:
            returned = self.sender.send(to=row.raw_email, subject=subject, body=body, message_id=mid)
        except NetworkError as exc:
            self._set(email, status=Status.PENDING, error=None)
            self._save()
            self._emit("network_error", email=email, error=str(exc))
            self._backoff_wait()
            return "retry"
        except AuthError as exc:
            self._auth_failures += 1
            self._set(email, status=Status.PENDING, error=None)
            self._save()
            self._emit("auth_error", email=email, error=str(exc), count=self._auth_failures)
            if self._auth_failures >= 3:
                return RESULT_AUTH_PAUSED
            self._interruptible_wait(_AUTH_RETRY_WAIT)
            return "retry"
        except PermanentError as exc:
            self._set(email, status=Status.FAILED, error=str(exc))
            self._save()
            self._emit("row_failed", email=email, error=str(exc))
            return None

        self._auth_failures = 0
        self._net_backoff = _NET_BACKOFF_START
        self._mark_sent(email, returned or mid, subject)
        self._emit("row_sent", email=email, subject=subject)
        return None

    # --- waiting --------------------------------------------------------------
    def _wait_for_gap(self) -> str | None:
        """Wait out the remainder of the 80 s gap since the last send."""
        last = self.progress.last_sent_at
        if not last:
            return None
        target = _parse(last) + timedelta(seconds=SEND_GAP_SECONDS)
        return self._interruptible_wait_until(target, kind="gap")

    def _wait_for_cap(self) -> str | None:
        """Wait until the rolling-24h window drops below the cap (spec §6.4)."""
        while True:
            self._prune_window()
            if len(self.progress.sends_last_24h) < self.daily_cap:
                return None
            oldest = min(_parse(t) for t in self.progress.sends_last_24h)
            target = oldest + _DAY
            self._emit(
                "cap_reached",
                cap=self.daily_cap,
                count=len(self.progress.sends_last_24h),
                until=target.isoformat(),
            )
            r = self._interruptible_wait_until(target, kind="cap")
            if r:
                return r

    def _interruptible_wait_until(self, target: datetime, *, kind: str) -> str | None:
        last_emit = -1
        while True:
            if self._stop.is_set():
                return RESULT_STOPPED
            if self._pause.is_set():
                return RESULT_PAUSED
            remaining = (target - self.clock.now()).total_seconds()
            if remaining <= 0:
                return None
            secs = int(remaining)
            if secs != last_emit:
                self._emit("waiting", kind=kind, seconds_remaining=secs)
                last_emit = secs
            self.clock.sleep(min(self._wait_slice, remaining))

    def _interruptible_wait(self, seconds: float) -> str | None:
        return self._interruptible_wait_until(self.clock.now() + timedelta(seconds=seconds), kind="wait")

    def _backoff_wait(self) -> bool:
        """Wait the current network backoff, then grow it. Returns True if interrupted."""
        delay = self._net_backoff
        self._net_backoff = min(self._net_backoff * 2, _NET_BACKOFF_MAX)
        return self._interruptible_wait(delay) is not None

    # --- reload / diff --------------------------------------------------------
    def diff(self, new_result: SheetResult) -> SheetDiff:
        """Compute how a freshly-read sheet differs (for the reload preview)."""
        d = SheetDiff()
        new_by = {r.email: r for r in new_result.rows if r.email}
        for email, r in new_by.items():
            if email not in self._by_email:
                d.new.append(email)
            elif self._status(email) != Status.SENT and r.data != self._by_email[email].data:
                d.edited.append(email)
            else:
                d.unchanged += 1
        for email in self._by_email:
            if email not in new_by and self._status(email) != Status.SENT:
                d.removed.append(email)
        return d

    def apply_reload(self, new_result: SheetResult) -> SheetDiff:
        """Apply a reloaded sheet: SENT rows are never touched (spec §7)."""
        d = self.diff(new_result)
        new_by = {r.email: r for r in new_result.rows if r.email}

        # Drop unsent rows that disappeared from the sheet.
        for email in list(self._by_email):
            if email not in new_by and self._status(email) != Status.SENT:
                self.progress.rows.pop(email, None)

        new_rows: list[Row] = []
        for r in new_result.rows:
            if not r.email:
                continue
            email = r.email
            if email in self.progress.rows and self._status(email) == Status.SENT:
                # Keep the content that was actually sent; ignore edits.
                new_rows.append(self._by_email.get(email, r))
                continue
            if email not in self.progress.rows:
                self.progress.set_row(email, status=r.status, error=r.error)
            elif r.status in (Status.INVALID, Status.DUPLICATE):
                self.progress.set_row(email, status=r.status, error=r.error)
            else:
                # Edited (or unchanged) unsent row → ensure it is PENDING again.
                self.progress.set_row(email, status=Status.PENDING, error=None)
            new_rows.append(r)

        self._rows = new_rows
        self._by_email = {r.email: r for r in new_rows if r.email}
        self._save()
        self._emit("reloaded", summary=d.summary())
        return d

    # --- counts / status ------------------------------------------------------
    def counts(self) -> dict[str, int]:
        return self.progress.count_by_status()

    def remaining_pending(self) -> int:
        return sum(1 for r in self._rows if self._status(r.email) == Status.PENDING)

    # --- internal helpers -----------------------------------------------------
    def _seed_progress_rows(self) -> None:
        """Ensure every current sheet row has a progress record (preserve existing)."""
        for row in self._rows:
            if not row.email:
                continue
            if row.email not in self.progress.rows:
                self.progress.set_row(row.email, status=row.status, error=row.error)

    def _status(self, email: str) -> str:
        rec = self.progress.row(email)
        return str(rec.get("status")) if rec else Status.PENDING

    def _set(self, email: str, **fields: object) -> None:
        self.progress.set_row(email, **fields)

    def _next_pending(self) -> Row | None:
        for row in self._rows:
            if self._status(row.email) == Status.PENDING:
                return row
        return None

    def _new_message_id(self) -> str:
        if self.sender is not None:
            return self.sender.new_message_id()
        return make_msgid(domain="emailsender.local")

    def _mark_sent(self, email: str, message_id: str, subject: str, *, recovered: bool = False) -> None:
        now = self.clock.now()
        iso = now.isoformat()
        self._set(
            email,
            status=Status.SENT,
            sent_at=iso,
            message_id=message_id,
            subject=subject,
            error=None,
        )
        self.progress.last_sent_at = iso
        self.progress.sends_last_24h.append(iso)
        self._prune_window()
        self._save()

    def _prune_window(self) -> None:
        cutoff = self.clock.now() - _DAY
        self.progress.sends_last_24h = [
            t for t in self.progress.sends_last_24h if _parse(t) > cutoff
        ]

    def _cap_reached(self) -> bool:
        self._prune_window()
        return len(self.progress.sends_last_24h) >= self.daily_cap

    def _finish(self, reason: str) -> str:
        if reason == RESULT_COMPLETED:
            self.progress.state = P.STATE_COMPLETED
        elif reason == RESULT_STOPPED:
            self.progress.state = P.STATE_STOPPED
        else:  # paused or auth_paused
            self.progress.state = P.STATE_PAUSED
        self._save()
        self._emit("state", state=self.progress.state, reason=reason)
        if reason == RESULT_AUTH_PAUSED:
            self._emit("auth_paused")
        return reason

    def _save(self) -> None:
        P.save_progress(self.progress_path, self.progress)

    def _emit(self, event: str, **data: object) -> None:
        if self._on_event is not None:
            self._on_event(event, data)


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)
