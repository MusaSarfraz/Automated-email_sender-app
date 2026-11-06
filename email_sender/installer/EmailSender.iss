; Inno Setup script for EmailSender.
;
; Build the app first (from the project root):
;     pyinstaller EmailSender.spec
; Then compile this script with Inno Setup (iscc), run from the project root so
; the relative dist\ path resolves:
;     iscc email_sender\installer\EmailSender.iss
;
; Produces Output\EmailSender-Setup.exe.

#define MyAppName "EmailSender"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "EmailSender"
#define MyAppExeName "EmailSender.exe"

[Setup]
; A stable AppId makes in-place upgrades replace the previous version.
AppId={{B6E7B1E2-4C7A-4E2B-9E0B-EMAILSENDER01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Install into Program Files (requires admin). User data in %APPDATA% is untouched.
PrivilegesRequired=admin
OutputDir=..\..\Output
OutputBaseFilename=EmailSender-Setup
SetupIconFile=..\assets\EmailSender.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Close the running app during an upgrade so files can be replaced.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startupicon"; Description: "Start {#MyAppName} automatically at sign-in (minimised to the tray)"; GroupDescription: "Startup:"

[Files]
; The entire PyInstaller --onedir output.
Source: "..\..\dist\EmailSender\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; Start-menu shortcut.
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; Desktop shortcut (optional task).
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; Startup shortcut that launches minimised to the tray (optional task).
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--minimized"; Tasks: startupicon

[Run]
; Offer to launch after install.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
