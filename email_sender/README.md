# EmailSender

A free Windows desktop app that sends personalised emails **one at a time** from
an Excel file or Google Sheet, through **your own** Gmail or Outlook account. It
waits 80 seconds between emails, and **resumes safely after a crash or power
cut** — continuing from the next unsent row with no duplicates.

- 100% free — no paid servers, services, or licences.
- Each person runs their own copy on their own PC and sends from their own mailbox.
- No database. Progress is saved in a small file per campaign.
- Nothing to configure in code — everything happens in the app window.

---

## For users

### 1. Install
Run **EmailSender-Setup.exe** and follow the prompts. It adds a desktop
shortcut, a Start-menu entry, and (optionally) starts the app automatically at
sign-in, minimised to the system tray.

> If Windows SmartScreen or your antivirus warns about the unsigned app, choose
> *More info → Run anyway*, or ask your IT admin to add a folder exclusion for
> the install folder. (Code signing costs money and is out of scope.)

### 2. Connect your mailbox (one time)

**Gmail (free Gmail or Google Workspace)**
1. Turn on **2-Step Verification** for your Google account.
2. Create an **App Password** at <https://myaccount.google.com/apppasswords> and
   copy the 16-character code.
3. In the app: **Settings → Gmail →** enter your address and the App Password →
   **Test connection**. A green ✓ means you're ready.

> Workspace admins can block App Passwords. If the option is missing, ask your
> admin to allow it.

**Outlook (Outlook.com or Microsoft 365)**
1. In the app: **Settings → Outlook → Sign in with Microsoft**.
2. Sign in once in the browser window that opens. The app keeps you signed in.

> Some Microsoft 365 organisations require an admin to approve the app the first
> time. (Outlook also requires the app to be configured with a Microsoft client
> ID at build time — see *For admins*.)

### 3. Prepare your sheet
The first worksheet needs a header row. Header names are matched ignoring case
and spaces, and extra columns are allowed and can be used as placeholders.

**Required:** `email`, `subject`, `body`.
The email column may instead be named `To`, `Recipient`, or `Email Address`.

**Optional:** `name`, `company` (and any other columns) — only needed if your
subject/body actually references them as `{{ name }}` / `{{ company }}`.

| Column    | Required | Example                          | Notes                                            |
|-----------|----------|----------------------------------|--------------------------------------------------|
| `email`   | Yes (or `To`/`Recipient`) | john@acme.com       | Must be valid; used as the row's identity        |
| `subject` | Yes      | `Quick question, {{ name }}`     | May use `{{ placeholders }}`                      |
| `body`    | Yes      | `Hi {{ name }}, about {{ company }}…` | Plain text; line breaks kept; may use placeholders |
| `name`    | Optional | John                             | Only if used as `{{ name }}`                      |
| `company` | Optional | Acme Ltd                         | Only if used as `{{ company }}`                   |

Placeholders use `{{ columnname }}` (lower-case, no spaces). A row whose
placeholder has no value is marked **FAILED** (never sent blank). The same email
twice → the first is sent, the rest are marked **DUPLICATE**. Invalid addresses
are marked **INVALID**.

### 4. Send
1. **New campaign →** choose your `.xlsx` → check the preview (first 10 rows with
   placeholders filled in) → **Start**.
2. Watch **Progress**: a live countdown to the next email, counts, a progress
   bar, today's total vs. the daily cap, and a log.
3. Controls:
   - **Pause** — finishes the current email, then holds. (Good for shift end or
     editing data.)
   - **Reload sheet** (while paused) — re-reads your file and shows what changed
     (*"12 new · 3 edited · 1 removed"*) before applying. Already-sent rows are
     never touched.
   - **Resume** — re-reads the sheet and continues, keeping the 80-second gap.
   - **Stop** — ends the run; the campaign moves to **History**.

Closing the window with **✕** hides the app to the tray and **keeps sending**.
Only **Quit** (tray menu) exits — and it asks first if a campaign is running.

### 5. After a power cut
When the PC comes back on and you sign in, the app starts in the tray and
automatically resumes a campaign that was running — from the next unsent row,
with no duplicates. A paused or stopped campaign stays as it was.

> While a PC or its internet is off, that PC's sending pauses. A small UPS or a
> laptop on battery keeps it going through short outages.

### Results
**History → Export results** writes an `.xlsx` with extra columns: `status`,
`sent_at`, `message_id`, `error`.

### Approximate daily limits
| Account type      | Approx. limit/day | Set the app's cap a little below |
|-------------------|-------------------|----------------------------------|
| Free Gmail        | ~500              | e.g. 450                         |
| Google Workspace  | ~2,000            | e.g. 1,500                       |
| Outlook.com       | ~300              | e.g. 270                         |
| Microsoft 365     | ~10,000           | e.g. 2,000                       |

(Check your provider's current limits before go-live. The 80-second pace is
about 45/hour, up to ~1,080/day if run nonstop.)

---

## For admins (one-time setup before building)

These make Outlook sign-in and Google Sheets reading work for everyone. They are
configured **at build time** (see *Building* below) via environment variables or
by editing `email_sender/core/config.py`.

### Microsoft Entra app registration (for Outlook)
1. In the [Microsoft Entra admin center](https://entra.microsoft.com) →
   **App registrations → New registration**.
2. Supported account types: **Accounts in any organizational directory and
   personal Microsoft accounts**.
3. **Platform: Mobile and desktop applications**, redirect URI
   `http://localhost`. Mark it a **public client**.
4. **API permissions → Microsoft Graph → Delegated:** `Mail.Send`, `Mail.Read`,
   `offline_access`. Grant admin consent if your tenant requires it.
5. Copy the **Application (client) ID** and set it for the build:
   `EMAILSENDER_OUTLOOK_CLIENT_ID`, or edit `_DEFAULT_OUTLOOK_CLIENT_ID` in
   `core/config.py`.

### Google Cloud service account (for Google Sheets)
1. Create a Google Cloud project → enable the **Google Sheets API**.
2. Create a **service account**; download its **JSON key**.
3. Place the key at `email_sender/assets/service_account.json` (the PyInstaller
   spec bundles it automatically if present), and set the service-account email
   for display via `EMAILSENDER_GSA_EMAIL` (or edit `_DEFAULT_GSA_EMAIL`).
4. Users share their Google Sheet (**Viewer**) with that service-account address,
   shown in the app's **Settings**.

---

## For developers

### Run from source
```powershell
cd email_sender_app
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r email_sender\requirements.txt
.\.venv\Scripts\python.exe -m email_sender.app
```

### Run the tests
```powershell
.\.venv\Scripts\python.exe -m pip install -r email_sender\requirements-dev.txt
cd email_sender
..\.venv\Scripts\python.exe -m pytest
```
The suite includes a real power-cut **crash test** (kills a subprocess mid-send
and verifies no duplicate on restart).

### Try the loop without a mailbox
```powershell
.\.venv\Scripts\python.exe -m email_sender.tools.run_campaign --excel path\to\leads.xlsx --dry-run
```

### Send one real test email
```powershell
.\.venv\Scripts\python.exe -m email_sender.tools.send_test --gmail you@gmail.com --app-password "xxxx xxxx xxxx xxxx" --to friend@example.com
```

### Build the app and installer
```powershell
cd email_sender_app
.\.venv\Scripts\python.exe -m pip install -r email_sender\requirements-dev.txt
# (Re)generate the icon if you changed it:
.\.venv\Scripts\python.exe -m email_sender.tools.make_icon
# Build dist\EmailSender\EmailSender.exe (onedir, windowed):
.\.venv\Scripts\pyinstaller.exe EmailSender.spec --noconfirm --clean
# Compile the installer (needs Inno Setup's iscc on PATH):
iscc email_sender\installer\EmailSender.iss   # -> Output\EmailSender-Setup.exe
```

### Where data lives
Everything user-specific is under `%APPDATA%\EmailSender\` (settings, campaigns,
logs) and in **Windows Credential Manager** (Gmail App Password, Outlook token) —
never in the program folder, so upgrades keep it all.

### Project layout
```
email_sender/
  app.py            entry: single-instance guard, Qt app, tray, auto-resume
  worker.py         QThread wrapping core.campaign
  ui/               PySide6 only (settings / campaign / progress / history + tray)
  core/             NO Qt — fully unit-testable
    config.py  readers.py  template.py  progress.py  campaign.py  campaigns.py
    secrets.py  senders/ (base, gmail_smtp, outlook_graph, factory)
  tools/            CLIs: send_test, run_campaign, make_icon
  tests/            pytest suite incl. crash test
  installer/EmailSender.iss
```

## Out of scope (v1)
Suppression lists, scheduled sending hours, multiple concurrent campaigns,
attachments, CC/BCC, open/reply tracking, any server or web UI, and auto-update.
