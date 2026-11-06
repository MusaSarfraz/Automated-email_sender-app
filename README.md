# EmailSender

A free Windows desktop app that sends personalised emails **one at a time** from
an Excel file or Google Sheet, through **your own** Gmail or Outlook account —
waiting 80 seconds between emails and **resuming safely after a crash or power
cut** with no duplicate sends.

Built for a small team (5–8 people): each person installs their own copy and
sends from their own mailbox. No servers, no database, no code for end users.

> 📖 **Full user + admin + developer guide:** [`email_sender/README.md`](email_sender/README.md)

---

## Highlights

- **Your own mailbox** — Gmail (App Password) or Outlook (secure Microsoft sign-in).
- **Spreadsheet-driven** — columns `email` (or `To`/`Recipient`), `subject`, `body`
  (plus optional `name`, `company`, and any extras for `{{ placeholders }}`).
- **Safe pacing** — a fixed 80-second gap and a configurable daily cap.
- **Crash/power-cut proof** — progress is saved before and after every send; on
  restart it resumes from the next unsent row and never double-sends.
- **Simple UI** — Settings, New campaign, Progress (live log + countdown), History
  (export results), and a system-tray icon. Closing the window keeps sending.

## How it works

The PySide6 UI never sends mail itself — it drives a background `QThread` worker
around a fully-tested, UI-free `core/` engine:

```
email_sender/
  app.py            entry: single-instance guard, Qt app, tray, auto-resume
  worker.py         QThread wrapping core.campaign
  ui/               PySide6 screens + tray
  core/             NO Qt — unit-tested engine
    readers · template · progress · campaign · senders (gmail/outlook)
  tools/            CLIs: send_test, run_campaign, make_icon
  tests/            82 tests incl. a real crash/power-cut test
  installer/EmailSender.iss
```

## For end users (the other PCs)

Users do **not** need this repo, Git, or Python. They only need the installer:

1. Download **`EmailSender-Setup.exe`** (from the repo's **Releases** page, or sent to them directly).
2. Run it → open **EmailSender** → **Settings** → connect their own Gmail/Outlook.
3. **New campaign** → choose a sheet → **Start**.

## For developers

```powershell
# Run from source (Python 3.12)
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r email_sender\requirements.txt
.\.venv\Scripts\python.exe -m email_sender.app

# Tests
.\.venv\Scripts\python.exe -m pip install -r email_sender\requirements-dev.txt
cd email_sender; ..\.venv\Scripts\python.exe -m pytest

# Build the app + installer
.\.venv\Scripts\pyinstaller.exe EmailSender.spec --noconfirm --clean
iscc email_sender\installer\EmailSender.iss     # -> Output\EmailSender-Setup.exe
```

See [`email_sender/README.md`](email_sender/README.md) for mailbox setup, the
one-time admin steps (Microsoft Entra app + Google service account), sheet
format details, and daily sending limits.

## Status & scope

v0.1.0. Gmail works out of the box. Outlook needs a one-time Microsoft app
(client) ID (entered in Settings). Out of scope for v1: suppression lists,
scheduled sending, multiple concurrent campaigns, attachments, CC/BCC, tracking,
and any server/web UI.

---

*Created by Musa Sarfraz.* Built with free, open-source tools (Python, PySide6/LGPL).
