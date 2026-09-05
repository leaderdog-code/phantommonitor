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
#define AppVersion "1.1.2"
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
