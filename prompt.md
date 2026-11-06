# Task: Build "EmailSender" — a Windows desktop app that sends personalised emails from a spreadsheet

You are a senior Python developer. Build a production-quality Windows desktop app exactly to the spec below. Work in phases (see "Build order"), and after each phase stop, summarise what you built, and show me how to test it before continuing.

## 1. Context and goals
- 5–8 non-technical users. Each installs the app on their own Windows PC and sends from their OWN Gmail or Outlook account.
- Users never touch code or a terminal: everything happens through the app's window.
- 100% free: no paid services, servers or licences.
- No database. Progress is stored in a JSON file per campaign.
- One campaign runs at a time per PC.
- Exactly one email every 80 seconds (fixed; shown in Settings but not editable).
- Must survive power cuts and crashes: on restart, resume from the next unsent row with NO duplicate sends.
- Typical run length: one to a few hours during a shift. No scheduled/automatic sending hours.

## 2. Fixed tech stack (do not substitute)
| Layer | Use |
|---|---|
| Language | Python 3.12 |
| UI | PySide6 (LGPL) + QSystemTrayIcon |
| Background work | QThread worker communicating via Qt signals |
| Excel input | pandas + openpyxl (.xlsx) |
| Google Sheets input | gspread with a service account (users share the sheet with its email as Viewer) |
| Gmail sending | smtplib, smtp.gmail.com:587 STARTTLS, App Password |
| Gmail Sent check | imaplib, imap.gmail.com:993, same App Password |
| Outlook sending | MSAL (public client, interactive browser login, token cache) + Microsoft Graph `POST /me/sendMail` |
| Outlook Sent check | Graph query on Sent Items |
| Secrets | keyring (Windows Credential Manager) — never store passwords/tokens in plain files |
| Templates | Jinja2 with StrictUndefined |
| Email validation | email-validator |
| Logging | logging + RotatingFileHandler |
| Packaging | PyInstaller `--onedir --windowed` |
| Installer | Inno Setup script (desktop + Start-menu shortcut, Startup entry, in-place upgrade) |

Do NOT use basic-auth SMTP for Outlook. Do NOT use Streamlit, Flask, FastAPI, or a browser UI.

## 3. Project layout
```
email_sender/
  app.py                 # entry: single-instance guard, Qt app, tray, worker
  worker.py              # QThread wrapping core.campaign
  ui/                    # PySide6 only
    main_window.py  settings_page.py  campaign_page.py  progress_page.py  history_page.py
  core/                  # NO Qt imports; fully unit-testable
    config.py            # paths, settings.json load/save
    readers.py           # Excel / Google Sheet -> list[dict]
    template.py          # Jinja2 render + validation
    progress.py          # atomic load/save of progress.json
    campaign.py          # state machine + send loop logic
    senders/
      base.py            # Sender interface: send(), find_in_sent(), test_connection()
      gmail_smtp.py
      outlook_graph.py
  tests/
  installer/EmailSender.iss
  requirements.txt
  README.md
```
All user data lives in `%APPDATA%\EmailSender\` (settings.json, campaigns\<id>\ with a copy of the sheet + progress.json, logs\). Nothing user-specific in the program folder.

## 4. Input sheet format
- First tab, headers in row 1, matched case-insensitively ignoring spaces.
- Required columns: `name`, `company`, `email`, `subject`, `body`. Extra columns allowed.
- `subject` and `body` may contain Jinja2 placeholders like `{{ name }}`, `{{ company }}`, or any column name.
- Body is plain text; preserve line breaks.
- Row identity = email address lower-cased and trimmed (NOT row number), so inserting/deleting/reordering rows is safe.
- Duplicate email in the same sheet → first is sent, later rows marked DUPLICATE.
- Invalid email → INVALID. Missing placeholder value → FAILED with a clear reason (never send blanks).
- Excel: always work on a copy so the user can keep the file open in Excel.

## 5. Row statuses
PENDING, SENDING, SENT, FAILED, INVALID, DUPLICATE. Results export adds columns: `status`, `sent_at`, `message_id`, `error`.

## 6. Send loop and failsafe (critical — implement exactly)
1. On start/restart, load progress.json. If saved campaign state is `paused` or `stopped`, stay that way and wait for the user. If `running`, resume automatically.
2. For every row stuck in SENDING: check the sender's Sent folder for that recipient (Gmail via IMAP, Outlook via Graph). Found → SENT. Not found → PENDING.
3. Pick the next PENDING row. Validate address + render template.
4. Daily cap: count sends in a rolling 24 hours (store timestamps). Default 450 (editable in Settings). If reached, wait until the oldest send in the window is 24 h old.
5. Enforce the 80 s gap from `last_sent_at` stored in progress.json — restarts, pauses and resumes must never send two emails closer than 80 s.
6. Mark row SENDING and SAVE. Send. On success mark SENT with `sent_at` + `message_id` and SAVE.
7. Network errors (no internet, timeout, DNS) → put row back to PENDING and retry with backoff once connectivity returns; never mark FAILED for these.
8. Permanent rejections (5xx, invalid recipient) → FAILED with error text. 3 consecutive auth errors → pause campaign and tell the user to reconnect their mailbox.
9. Atomic saves: write to a temp file in the same folder, flush + os.fsync, then os.replace. A power cut mid-write must leave the previous file intact.
10. The waiting between sends must be interruptible (Pause/Stop react within 1 second).

progress.json shape (extend as needed):
```json
{"campaign_id": "...", "source": "...", "source_type": "excel|gsheet", "state": "ready|running|paused|stopped|completed",
 "last_sent_at": "ISO-8601 with offset", "sends_last_24h": ["ISO-8601", "..."],
 "rows": {"john@acme.com": {"status": "SENT", "sent_at": "...", "message_id": "...", "error": null}}}
```

## 7. Campaign states and controls
States: Ready → Running ⇄ Waiting; Pause (if mid-send: Pausing → finish current email → Paused); Paused → Reviewing (Reload sheet) → Paused; Paused → Resume → Waiting (honours remaining 80 s); CapReached ⇄ Waiting; Stop from Running/Waiting/Paused → Stopped; Stopped → Continue (from History) → Paused; Waiting → Completed when no rows remain.

| State | Start | Pause | Resume | Stop | Reload |
|---|---|---|---|---|---|
| Ready | ✓ | | | | ✓ |
| Running / Waiting | | ✓ | | ✓ | |
| Pausing | | | | | |
| Paused | | | ✓ | ✓ | ✓ |
| Cap reached | | ✓ | | ✓ | |
| Stopped / Completed | (Continue / Export in History) |

Reload sheet (paused only): re-read the source and show a diff dialog ("X new · Y edited · Z removed") with Apply / Cancel. New rows → PENDING. Edited unsent rows → use new content. SENT rows are never re-sent and edits to them are ignored. Removed unsent rows are dropped. Resume also re-reads the sheet.

## 8. GUI (PySide6)
- **Settings:** choose Gmail or Outlook. Gmail: email + App Password fields + "Test connection". Outlook: "Sign in with Microsoft" button (MSAL interactive) + signed-in address + "Sign out". Daily cap field. Read-only 80 s gap. Shows the Google service-account email to share sheets with (copy button).
- **New campaign:** pick .xlsx or paste a Google Sheet URL → validation summary (missing columns, invalid, duplicates) → preview first 10 rows with placeholders rendered → Start.
- **Progress:** campaign name, sender address, status + live countdown to next email, counts (Sent/Pending/Failed/Invalid/Duplicate), progress bar, "last 24 h: N / cap", live log (time, status, recipient, subject or error), Pause/Resume, Stop, Reload buttons enabled per the table above.
- **History:** past campaigns with counts, Continue (stopped ones), Export results to .xlsx.
- **Tray:** Open, Pause/Resume, Quit. Clicking ✕ hides to tray and keeps sending; Quit asks for confirmation if a campaign is running.
- Single instance only: launching again focuses the existing window.
- Plain, clean, readable UI; friendly error messages written for non-technical users (no stack traces in the UI; full details go to the log).

## 9. Packaging and startup
- PyInstaller spec producing `dist/EmailSender/` (`--onedir --windowed`, app icon).
- Inno Setup script: installs to Program Files, desktop + Start-menu shortcuts, Startup entry that launches the app minimised to tray (`--minimized` flag), upgrade in place without touching `%APPDATA%\EmailSender`.
- On startup with `--minimized`: go straight to tray and resume a `running` campaign automatically.

## 10. One-time admin setup (document in README)
- Microsoft Entra app registration: multi-tenant + personal accounts, public client, redirect `http://localhost`, delegated `Mail.Send`, `Mail.Read`, `offline_access`. Client ID goes in config.
- Google Cloud service account with Sheets API enabled; JSON key bundled with the build (read-only Sheets scope).
- Gmail users: enable 2-Step Verification, create an App Password.

## 11. Out of scope (do not build)
Do-not-send/suppression lists, scheduled sending hours, multiple concurrent campaigns, attachments, CC/BCC, tracking, any server or web UI, auto-update.

## 12. Quality requirements
- Type hints, docstrings, no Qt imports in `core/`.
- pytest unit tests for: readers (header matching, duplicates, invalid emails), template rendering (missing placeholder), progress atomic save + reload, campaign loop with a fake sender (80 s gap via injectable clock, pause/resume, stop/continue, cap, SENDING recovery, network error retry, reload diff).
- A `--dry-run` mode in core that logs instead of sending, for testing.
- A crash test: kill the process mid-send, restart, confirm no duplicate and correct resume.

## 13. Build order (stop for review after each)
1. `core/` readers, template, progress (+ tests)
2. Senders: Gmail SMTP/IMAP and Outlook MSAL/Graph, with `test_connection()` (+ a CLI script to send one test email)
3. `core/campaign.py` loop + failsafe, runnable from CLI with `--dry-run` (+ tests incl. crash test)
4. PySide6 UI, worker thread, tray, single instance
5. PyInstaller spec, Inno Setup script, README with user and admin setup steps

Acceptance: a non-technical user can install, connect their mailbox, load a sheet, start, pause, edit the sheet, reload, resume, survive a forced shutdown, and export results — without ever seeing code.