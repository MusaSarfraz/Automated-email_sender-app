"""Paths, application settings, and shared constants.

All user data lives under ``%APPDATA%\\EmailSender\\`` so that reinstalling or
upgrading the program (which replaces the program folder) never touches the
user's settings, campaigns, or logs.

Tests and alternate deployments can override the data directory by setting the
``EMAILSENDER_DATA_DIR`` environment variable.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

APP_NAME = "EmailSender"

# --- Fixed, non-editable timing ------------------------------------------------
#: Seconds between two consecutive sends. Fixed by product spec; shown in the UI
#: but not user-editable.
SEND_GAP_SECONDS = 80

# --- Editable defaults ---------------------------------------------------------
#: Default rolling-24h send cap (safe default for free Gmail).
DEFAULT_DAILY_CAP = 450

#: Keyring service name under which secrets are stored in Credential Manager.
KEYRING_SERVICE = "EmailSender"

# --- Outlook / Microsoft Graph (MSAL) -----------------------------------------
#: Microsoft Entra application (client) ID for the public client. Filled in at
#: build time; overridable via ``EMAILSENDER_OUTLOOK_CLIENT_ID`` for dev/testing.
#: See README admin setup for how this app registration is created.
_DEFAULT_OUTLOOK_CLIENT_ID = ""
#: "common" = any Microsoft work/school or personal account.
OUTLOOK_AUTHORITY = "https://login.microsoftonline.com/common"
#: Delegated scopes requested interactively (offline_access is added by MSAL).
OUTLOOK_SCOPES = ("Mail.Send", "Mail.Read")
#: Microsoft Graph base URL.
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def outlook_client_id() -> str:
    """Return the configured Entra client ID (env override wins)."""
    return os.environ.get("EMAILSENDER_OUTLOOK_CLIENT_ID", _DEFAULT_OUTLOOK_CLIENT_ID)


# --- Google Sheets service account --------------------------------------------
#: Display-only: the service-account email users share their Google Sheet with.
#: Filled at build time from the bundled key, or via env for dev/testing.
_DEFAULT_GSA_EMAIL = ""


def google_service_account_email() -> str:
    """Return the service-account email to show in Settings ("" if unconfigured)."""
    return os.environ.get("EMAILSENDER_GSA_EMAIL", _DEFAULT_GSA_EMAIL)


# --- Row status values ---------------------------------------------------------
class Status:
    """The lifecycle states a single recipient row can be in.

    Defined here (not in :mod:`progress`) so both :mod:`readers` and
    :mod:`progress` can share them without a circular import.
    """

    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"

    ALL = (PENDING, SENDING, SENT, FAILED, INVALID, DUPLICATE)


# --- Paths ---------------------------------------------------------------------
def data_dir() -> Path:
    """Return the root data directory, creating it if necessary.

    Honours ``EMAILSENDER_DATA_DIR`` for tests; otherwise uses
    ``%APPDATA%\\EmailSender`` (falling back to the user profile if APPDATA is
    somehow unset).
    """
    override = os.environ.get("EMAILSENDER_DATA_DIR")
    if override:
        base = Path(override)
    else:
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        base = Path(appdata) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def campaigns_dir() -> Path:
    """Directory holding one sub-folder per campaign."""
    d = data_dir() / "campaigns"
    d.mkdir(parents=True, exist_ok=True)
    return d


def campaign_dir(campaign_id: str) -> Path:
    """Directory for a single campaign (sheet copy + progress.json)."""
    d = campaigns_dir() / campaign_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    """Directory for rotating log files."""
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    """Path to the global settings.json file."""
    return data_dir() / "settings.json"


# --- Settings ------------------------------------------------------------------
@dataclass
class Settings:
    """User-level application settings (persisted to settings.json).

    Secrets (Gmail App Password, Outlook token cache) are **not** stored here;
    they live in Windows Credential Manager via :mod:`keyring`.
    """

    #: "gmail" or "outlook"
    sender_type: str = "gmail"
    #: Connected Gmail address (if any).
    gmail_address: str = ""
    #: Connected Outlook address (if any).
    outlook_address: str = ""
    #: Microsoft Entra application (client) ID for Outlook, if entered in the UI.
    #: Falls back to the build-time default when empty.
    outlook_client_id: str = ""
    #: Rolling-24h send cap (editable in the UI).
    daily_cap: int = DEFAULT_DAILY_CAP

    def __post_init__(self) -> None:
        if self.sender_type not in ("gmail", "outlook"):
            raise ValueError(f"Unknown sender_type: {self.sender_type!r}")
        if int(self.daily_cap) < 1:
            raise ValueError("daily_cap must be at least 1")
        self.daily_cap = int(self.daily_cap)

    @property
    def send_gap_seconds(self) -> int:
        """Read-only send gap (always :data:`SEND_GAP_SECONDS`)."""
        return SEND_GAP_SECONDS

    def effective_outlook_client_id(self) -> str:
        """Client ID entered in the UI, else the build-time default."""
        return self.outlook_client_id.strip() or outlook_client_id()


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from disk, returning defaults if the file is absent.

    Unknown keys in the file are ignored so older files keep working after an
    upgrade adds new fields.
    """
    p = path or settings_path()
    if not p.exists():
        return Settings()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt settings file should not brick the app; fall back to defaults.
        return Settings()
    known = {f for f in Settings.__dataclass_fields__}  # type: ignore[attr-defined]
    filtered = {k: v for k, v in raw.items() if k in known}
    try:
        return Settings(**filtered)
    except (TypeError, ValueError):
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Atomically persist settings to disk."""
    p = path or settings_path()
    _atomic_write_text(p, json.dumps(asdict(settings), indent=2))


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically: temp file in the same dir, fsync, then os.replace.

    A crash mid-write leaves the previous file intact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Retry os.replace: on Windows, AV/indexer can briefly lock the new file.
        for attempt in range(10):
            try:
                os.replace(tmp_name, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    except BaseException:
        # Clean up the temp file on any failure so we don't leave litter behind.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
