"""Read an Excel file or Google Sheet into validated recipient rows.

The output is a :class:`SheetResult` describing every data row with a status
(PENDING / INVALID / DUPLICATE) plus a summary the UI uses for the pre-send
validation screen. Reading never touches the network for Excel, and always
works on the caller-provided copy of the file so the user can keep the original
open in Excel.

Row identity is the **email address, trimmed and lower-cased** — never the row
number — so inserting, deleting, or reordering rows between reloads is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from email_validator import EmailNotValidError, validate_email

from .config import Status

#: Columns every sheet must have (after alias resolution). ``name`` and
#: ``company`` are optional — they're only needed when a subject/body actually
#: references ``{{ name }}`` / ``{{ company }}`` (a missing value then marks that
#: row FAILED with a clear reason, rather than blocking the whole sheet).
REQUIRED_COLUMNS = ("email", "subject", "body")

#: Header names (normalised) that are accepted as the email column, in order of
#: preference. So "To", "Recipient", or "Email Address" all work as `email`.
EMAIL_HEADER_ALIASES = ("email", "emailaddress", "to", "recipient")


@dataclass
class Row:
    """One recipient row read from the sheet."""

    #: 1-based position among data rows (for display/export only — NOT identity).
    row_number: int
    #: Normalised identity: email trimmed + lower-cased (``""`` if blank/invalid).
    email: str
    #: All columns for this row, keyed by normalised header name. This is the
    #: context passed to the template engine.
    data: dict[str, str]
    #: One of :class:`~email_sender.core.config.Status`.
    status: str = Status.PENDING
    #: Human-readable reason when INVALID/DUPLICATE (else ``None``).
    error: str | None = None

    @property
    def raw_email(self) -> str:
        """The email exactly as typed (trimmed), for the actual ``To`` header."""
        return self.data.get("email", "").strip()


@dataclass
class SheetResult:
    """The outcome of reading and validating a sheet."""

    rows: list[Row]
    #: Normalised header names found in the sheet, in order.
    headers: list[str]
    #: Header names exactly as they appeared in the sheet.
    original_headers: list[str]
    #: Required columns that were not found (empty when the sheet is usable).
    missing_columns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no required columns are missing (sheet can be started)."""
        return not self.missing_columns

    def counts(self) -> dict[str, int]:
        """Count rows by status (only non-zero-friendly keys initialised)."""
        result = {s: 0 for s in Status.ALL}
        for row in self.rows:
            result[row.status] = result.get(row.status, 0) + 1
        return result

    @property
    def sendable(self) -> list[Row]:
        """Rows currently eligible to send (PENDING)."""
        return [r for r in self.rows if r.status == Status.PENDING]


def normalize_header(header: object) -> str:
    """Normalise a header for matching: lower-case and strip all whitespace."""
    return "".join(str(header).split()).lower()


def _is_valid_email(address: str) -> bool:
    try:
        validate_email(address, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def build_result(headers: Sequence[object], data_rows: Sequence[Sequence[Any]]) -> SheetResult:
    """Build a :class:`SheetResult` from a header row and data rows.

    Shared by the Excel and Google Sheet readers so validation behaves
    identically for both sources.
    """
    original_headers = [str(h) for h in headers]
    norm_headers = [normalize_header(h) for h in original_headers]

    # Map each normalised header to the FIRST column index that produces it, so
    # accidental duplicate headers don't shadow the intended column.
    index_by_header: dict[str, int] = {}
    for i, nh in enumerate(norm_headers):
        index_by_header.setdefault(nh, i)

    # Resolve the email column from its accepted aliases (prefer "email").
    email_key = next((a for a in EMAIL_HEADER_ALIASES if a in index_by_header), None)

    missing = [] if email_key else ["email"]
    missing += [c for c in ("subject", "body") if c not in index_by_header]
    if missing:
        return SheetResult(
            rows=[],
            headers=norm_headers,
            original_headers=original_headers,
            missing_columns=missing,
        )

    rows: list[Row] = []
    seen_valid: set[str] = set()
    data_row_number = 0

    for raw_values in data_rows:
        data: dict[str, str] = {}
        for nh, col_idx in index_by_header.items():
            value = raw_values[col_idx] if col_idx < len(raw_values) else ""
            data[nh] = "" if value is None else str(value)

        # Expose the resolved email column under the canonical "email" key so
        # identity, the To header, and {{ email }} all work regardless of alias.
        data["email"] = data.get(email_key, "")

        # Silently skip wholly blank rows (trailing empties in Excel are common).
        if all(v.strip() == "" for v in data.values()):
            continue

        data_row_number += 1
        norm_email = data["email"].strip().lower()
        row = Row(row_number=data_row_number, email=norm_email, data=data)

        if not norm_email:
            row.status = Status.INVALID
            row.error = "Email address is blank"
        elif not _is_valid_email(norm_email):
            row.status = Status.INVALID
            row.error = "Not a valid email address"
        elif norm_email in seen_valid:
            row.status = Status.DUPLICATE
            row.error = "Same email appears in an earlier row"
        else:
            seen_valid.add(norm_email)
            row.status = Status.PENDING

        rows.append(row)

    return SheetResult(
        rows=rows,
        headers=norm_headers,
        original_headers=original_headers,
        missing_columns=[],
    )


def read_excel(path: str | Path) -> SheetResult:
    """Read the first worksheet of an .xlsx file into a :class:`SheetResult`.

    Every cell is read as text (so ``01234`` and phone-like values are not
    mangled) and blanks become empty strings.
    """
    import pandas as pd  # imported lazily so core import stays light for tests

    df = pd.read_excel(
        path,
        sheet_name=0,
        header=0,
        dtype=str,
        keep_default_na=False,
        engine="openpyxl",
    )
    headers = list(df.columns)
    data_rows = df.values.tolist()
    return build_result(headers, data_rows)


def read_google_sheet(client: Any, url_or_key: str) -> SheetResult:
    """Read the first worksheet of a Google Sheet via an authorised gspread client.

    ``client`` is an authorised :mod:`gspread` client. The network/auth plumbing
    lives in the caller (Settings/senders); this function only turns the fetched
    grid into a :class:`SheetResult`.
    """
    if "/" in url_or_key:
        spreadsheet = client.open_by_url(url_or_key)
    else:
        spreadsheet = client.open_by_key(url_or_key)
    worksheet = spreadsheet.get_worksheet(0)
    grid = worksheet.get_all_values()
    if not grid:
        return SheetResult(rows=[], headers=[], original_headers=[], missing_columns=list(REQUIRED_COLUMNS))
    headers, *data_rows = grid
    return build_result(headers, data_rows)
