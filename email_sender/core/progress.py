"""Per-campaign progress persistence with crash-safe atomic writes.

``progress.json`` is the single source of truth for a campaign's state and every
row's status. It is saved before and after each send, so a crash loses at most
one in-flight email — and that one is reconciled against the Sent folder on
restart (see :mod:`campaign`).

Atomic save strategy (spec §6.9): write to a temp file in the *same* directory,
``flush`` + ``os.fsync``, then ``os.replace``. A power cut mid-write leaves the
previous file fully intact.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _replace_with_retry(src: str, dst: str | Path, *, attempts: int = 10, delay: float = 0.05) -> None:
    """``os.replace`` with a short retry.

    On Windows, antivirus or the search indexer can briefly hold a just-created
    file, making ``os.replace`` raise ``PermissionError`` (WinError 5/32). The
    data is already safely on disk, so retrying for a moment is correct.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)

# Campaign-level states persisted across restarts.
STATE_READY = "ready"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_STOPPED = "stopped"
STATE_COMPLETED = "completed"
ALL_STATES = (STATE_READY, STATE_RUNNING, STATE_PAUSED, STATE_STOPPED, STATE_COMPLETED)

SOURCE_EXCEL = "excel"
SOURCE_GSHEET = "gsheet"


@dataclass
class Progress:
    """In-memory mirror of ``progress.json``.

    ``rows`` maps a normalised email address to a per-row record, e.g.::

        {"john@acme.com": {"status": "SENT", "sent_at": "...",
                            "message_id": "...", "error": None}}
    """

    campaign_id: str
    source: str
    source_type: str  # "excel" | "gsheet"
    #: Full path (Excel) or URL (Google Sheet) to the ORIGINAL source, so Reload
    #: can re-read what the user edited (the campaign folder keeps only a copy).
    source_path: str = ""
    state: str = STATE_READY
    last_sent_at: str | None = None
    sends_last_24h: list[str] = field(default_factory=list)
    rows: dict[str, dict] = field(default_factory=dict)
    #: Campaign display name (defaults to the source file/sheet name).
    name: str = ""
    #: ISO-8601 creation timestamp (set by the caller).
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.source_type not in (SOURCE_EXCEL, SOURCE_GSHEET):
            raise ValueError(f"Unknown source_type: {self.source_type!r}")
        if self.state not in ALL_STATES:
            raise ValueError(f"Unknown state: {self.state!r}")
        if not self.name:
            self.name = self.campaign_id

    # --- row helpers ----------------------------------------------------------
    def row(self, email: str) -> dict | None:
        """Return the record for ``email`` (already normalised) or ``None``."""
        return self.rows.get(email)

    def set_row(self, email: str, **fields: object) -> dict:
        """Create or update a row record, returning the merged record."""
        record = self.rows.setdefault(email, {})
        record.update(fields)
        return record

    def count_by_status(self) -> dict[str, int]:
        """Tally row records by their ``status`` value."""
        counts: dict[str, int] = {}
        for record in self.rows.values():
            status = str(record.get("status", ""))
            counts[status] = counts.get(status, 0) + 1
        return counts


def progress_path(campaign_dir: str | Path) -> Path:
    """Return the ``progress.json`` path inside a campaign directory."""
    return Path(campaign_dir) / "progress.json"


def load_progress(path: str | Path) -> Progress:
    """Load a :class:`Progress` from disk.

    Unknown keys are ignored so files written by a newer build still load after
    a downgrade; known-but-absent keys fall back to dataclass defaults.
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    known = set(Progress.__dataclass_fields__)  # type: ignore[attr-defined]
    filtered = {k: v for k, v in raw.items() if k in known}
    return Progress(**filtered)


def save_progress(path: str | Path, progress: Progress) -> None:
    """Atomically write ``progress`` to ``path`` (crash-safe)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(asdict(progress), indent=2, ensure_ascii=False)

    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix="progress", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp_name, p)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
