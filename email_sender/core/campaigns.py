"""Create, load, and list campaigns on disk.

A campaign lives in ``%APPDATA%\\EmailSender\\campaigns\\<id>\\`` and owns a
copy of the source sheet plus ``progress.json``. Working on a *copy* means the
user can keep the original Excel file open while sending.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from pathlib import Path

from . import config, progress as P
from .campaign import Campaign, Clock
from .readers import SheetResult, build_result, read_excel
from .senders.base import Sender
from .senders.factory import build_sender

SHEET_XLSX = "sheet.xlsx"


def generate_campaign_id(source_name: str, *, now: _dt.datetime | None = None) -> str:
    """Make a filesystem-safe, date-prefixed campaign id from a source name."""
    now = now or _dt.datetime.now()
    stem = Path(source_name).stem
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower() or "campaign"
    return f"{now:%Y-%m-%d}_{slug}"


def create_excel_campaign(
    xlsx_path: str | Path, *, name: str | None = None, now: _dt.datetime | None = None
) -> P.Progress:
    """Copy an .xlsx into a new campaign folder and write its progress.json.

    Validates the sheet first; raises ``ValueError`` if required columns are
    missing so callers can show the problem before anything is created.
    """
    src = Path(xlsx_path)
    result = read_excel(src)
    if not result.ok:
        raise ValueError(f"Sheet is missing required columns: {', '.join(result.missing_columns)}")

    campaign_id = generate_campaign_id(src.name, now=now)
    cdir = config.campaign_dir(campaign_id)
    shutil.copy2(src, cdir / SHEET_XLSX)

    prog = P.Progress(
        campaign_id=campaign_id,
        source=src.name,
        source_type=P.SOURCE_EXCEL,
        source_path=str(src.resolve()),
        state=P.STATE_READY,
        name=name or src.stem,
        created_at=(now or _dt.datetime.now()).astimezone().isoformat(),
    )
    P.save_progress(P.progress_path(cdir), prog)
    return prog


def reload_source(prog: P.Progress) -> SheetResult:
    """Re-read the user's ORIGINAL source and refresh the campaign's copy.

    For Excel, copies the (possibly edited) original over the stored copy and
    reads it back. Raises ``FileNotFoundError`` if the original is gone.
    """
    cdir = config.campaign_dir(prog.campaign_id)
    if prog.source_type == P.SOURCE_EXCEL:
        src = Path(prog.source_path)
        if not src.exists():
            raise FileNotFoundError(f"The original file is no longer at:\n{src}")
        shutil.copy2(src, cdir / SHEET_XLSX)
        return read_excel(cdir / SHEET_XLSX)
    return build_result([], [])


def export_results(prog: P.Progress, out_path: str | Path) -> Path:
    """Write the campaign's rows plus result columns to an .xlsx file.

    Adds ``status``, ``sent_at``, ``message_id`` and ``error`` to whatever the
    sheet originally held, matched to each row by email address.
    """
    import pandas as pd

    sheet = read_campaign_rows(prog)
    records: list[dict] = []
    for row in sheet.rows:
        rec = prog.row(row.email) or {}
        out = dict(row.data)
        out["status"] = rec.get("status", row.status)
        out["sent_at"] = rec.get("sent_at", "")
        out["message_id"] = rec.get("message_id", "")
        out["error"] = rec.get("error", row.error or "")
        records.append(out)
    df = pd.DataFrame.from_records(records)
    out = Path(out_path)
    df.to_excel(out, index=False, engine="openpyxl")
    return out


def read_campaign_rows(prog: P.Progress) -> SheetResult:
    """Read the stored sheet copy for a campaign back into rows."""
    cdir = config.campaign_dir(prog.campaign_id)
    if prog.source_type == P.SOURCE_EXCEL:
        return read_excel(cdir / SHEET_XLSX)
    # Google Sheet snapshots are added in a later phase; fall back to empty.
    return build_result([], [])


def load_progress(campaign_id: str) -> P.Progress:
    cdir = config.campaign_dir(campaign_id)
    return P.load_progress(P.progress_path(cdir))


def list_campaigns() -> list[P.Progress]:
    """Return all known campaigns, newest id first."""
    out: list[P.Progress] = []
    base = config.campaigns_dir()
    for cdir in sorted(base.iterdir(), reverse=True) if base.exists() else []:
        pj = P.progress_path(cdir)
        if pj.exists():
            try:
                out.append(P.load_progress(pj))
            except Exception:  # noqa: BLE001 - skip unreadable campaigns
                continue
    return out


def should_auto_resume(prog: P.Progress) -> bool:
    """A campaign resumes automatically on startup only if it was running."""
    return prog.state == P.STATE_RUNNING


def make_campaign(
    prog: P.Progress,
    *,
    settings: config.Settings | None = None,
    sender: Sender | None = None,
    clock: Clock | None = None,
    dry_run: bool = False,
    on_event=None,
) -> Campaign:
    """Build a ready-to-run :class:`Campaign` from a progress record."""
    settings = settings or config.load_settings()
    rows = read_campaign_rows(prog).rows
    if sender is None and not dry_run:
        sender = build_sender(settings)
    return Campaign(
        progress_path=P.progress_path(config.campaign_dir(prog.campaign_id)),
        progress=prog,
        rows=rows,
        sender=sender,
        daily_cap=settings.daily_cap,
        clock=clock,
        dry_run=dry_run,
        on_event=on_event,
    )
