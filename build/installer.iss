; Inno Setup script for Phantom Monitor.
;
; Produces PhantomMonitor-Setup.exe - the "download, double click, click yes"
; path for people who do not want a loose executable.
;
; Build: install Inno Setup (https://jrsoftware.org/isdl.php), then either open
; this file in the Inno Setup Compiler and press F9, or run:
;   iscc build\installer.iss
;
; Requires build\dist\PhantomMonitor.exe to exist - run build\build.ps1 first.
;
; Installs per-user into LocalAppData on purpose: no admin prompt, no UAC, and
; nothing written outside the user's own profile. A tray utility has no business
; asking for administrator rights at install time.

#define AppName "Phantom Monitor"
#define AppVersion "1.1.3"
#define AppPublisher "Raymond Pierce"
#define AppURL "https://github.com/leaderdog-code/phantommonitor"
#define AppExe "PhantomMonitor.exe"

[Setup]
AppId={{8E2C4B91-7A3D-4F16-9C58-2D6E1B0A4F73}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\PhantomMonitor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=PhantomMonitor-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=..\LICENSE

; The app runs from the tray with no ordinary window, so Inno's own detection
; cannot shut it down: it found nothing to close, installed over a running copy
; and failed on the locked exe. The [Code] section closes it instead.
;
; Deliberately no AppMutex. It is checked before any of that code runs, so it
; only produced a "please close all instances" prompt for an app the installer
; is perfectly capable of closing itself - and aborted outright when silent.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start Phantom Monitor when I sign in"; \
    GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\{#AppExe}";  DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.example.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";            Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}";  Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}";      Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}";      Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Created at runtime beside the exe, so Inno does not know about them.
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\icon_active.ico"
Type: files; Name: "{app}\icon_paused.ico"

[Code]
{ Close any running copy before installing over it.

  There can be more than one process: the tray app, plus the settings window,
  which runs as a second PhantomMonitor.exe. Closing only the one Setup happens
  to notice leaves the other holding the executable, and the install fails at
  the very end - after the user has already been asked to retry twice.

  Ask politely first so it can release the cursor clip and save its state, then
  insist. }
const
  WM_CLOSE_MSG = 16;

function CloseRunningCopies(): Boolean;
var
  Window: HWND;
  ResultCode, Waited: Integer;
begin
  Window := FindWindowByClassName('PhantomMonitorWnd');
  if Window <> 0 then
    PostMessage(Window, WM_CLOSE_MSG, 0, 0);

  Waited := 0;
  while (Waited < 5000) and (FindWindowByClassName('PhantomMonitorWnd') <> 0) do
  begin
    Sleep(200);
    Waited := Waited + 200;
  end;

  { Whatever is left - a settings window, or a copy that ignored the request. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM PhantomMonitor.exe /T',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(600);
  Result := True;
end;

{ Both hooks on purpose. InitializeSetup runs before anything is checked or
  copied, which is early enough that nothing ever sees a locked file.
  PrepareToInstall catches a copy the user started while the wizard was open. }
{ A portable copy sets itself to start with Windows by dropping
  PhantomMonitor.vbs into the Startup folder. Installing afterwards leaves both
  that and Setup's own shortcut, so two copies race at every logon - each with
  its own settings, and whichever wins decides which rules apply. The symptom
  is "my settings stopped working" with a tray icon sitting there perfectly
  happily, which is nearly impossible to diagnose.

  Setup is plainly taking over, so offer to clear the other one out. }
procedure RemovePortableAutostart();
var
  StrayVbs: String;
begin
  StrayVbs := ExpandConstant('{userstartup}\PhantomMonitor.vbs');
  if FileExists(StrayVbs) then
  begin
    if MsgBox('Another copy of Phantom Monitor is set to start with Windows:'
              + #13#10#13#10 + StrayVbs + #13#10#13#10
              + 'That is a portable copy, with its own separate settings. If '
              + 'both start, whichever wins decides which settings apply.'
              + #13#10#13#10 + 'Remove that start-up entry?',
              mbConfirmation, MB_YESNO) = IDYES then
      DeleteFile(StrayVbs);
  end;
end;

function InitializeSetup(): Boolean;
begin
  CloseRunningCopies();
  RemovePortableAutostart();
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningCopies();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  CloseRunningCopies();
  Result := True;
end;

{ Settings and the saved desktop icon layout are the user's data, not ours.
  Deleting them silently loses a layout they may have spent time on; keeping
  them silently leaves files behind after an "uninstall". So ask, and default
  to a clean removal. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox('Also remove your settings and saved desktop icon layout?'#13#10#13#10
              + 'Choose No to keep them, so a future reinstall picks up where '
              + 'you left off.', mbConfirmation, MB_YESNO) = IDYES then
    begin
      DeleteFile(ExpandConstant('{app}\config.json'));
      DeleteFile(ExpandConstant('{app}\icon_layouts.json'));
      RemoveDir(ExpandConstant('{app}'));
    end;
  end;
end;
