---
subsystem: packaging
owns: build/build.ps1, build/build_installer.ps1, build/installer.iss, build/PhantomMonitor.spec, test_phantommonitor.py
anchors: AppVersion, CloseRunningCopies, RemovePortableAutostart, PhantomMonitor-Setup, ISCC, PyInstaller, park_on_denon, FakeMon, BLOCK_HWID
test: `python test_phantommonitor.py` — 170 checks; QUIT the tray app first (it rescues the fixtures mid-assertion and the suite exits 2, test_phantommonitor.py:11-15), 2+ real displays needed (:33-35). Build: `powershell -ExecutionPolicy Bypass -File build\build.ps1` (smoke-tests itself with `& $exe --list`, :39), then the same for `build_installer.ps1` (needs Inno Setup). Installer proof per 9a10849: with a copy running, `PhantomMonitor-Setup.exe /VERYSILENT` returns 0 and leaves no PhantomMonitor.exe process.
---
# Packaging — the exe, the installer, the version pin, and the test harness

## What it does
Turns the source tree into one self-contained `PhantomMonitor.exe` and a
per-user `PhantomMonitor-Setup.exe`, and holds the suite that must pass before
either ships.

## Where it lives
- `build/build.ps1` — `pip install --upgrade pyinstaller pywin32 pillow` (:13), then PyInstaller `--onefile --noconsole --name PhantomMonitor --icon build\app.ico`, `--distpath build\dist`, `--workpath build\work`, `--specpath build` (:20-29). **All paths absolute**: `--specpath` makes PyInstaller resolve relative paths against the spec folder, so `build\app.ico` would be sought at `build\build\app.ico` (:17-18). Prints the size, runs `& $exe --list` as a smoke test (:33-39).
- `build/PhantomMonitor.spec` — what that run *generates*. `.gitignore`'s `build/*.spec` keeps it untracked, and it is rewritten every build, so hand edits are lost. Records `upx=True`, `console=False`, `runtime_tmpdir=None`, absolute paths into this checkout (:5, :38). Change build options in `build.ps1`.
- `build/build_installer.ps1` — requires `build\dist\PhantomMonitor.exe` (:12-15), finds `ISCC.exe` in four candidate locations then PATH (:18-28), compiles `build\installer.iss`, reports `build\dist\PhantomMonitor-Setup.exe` (:30-37).
- `build/installer.iss` — `#define AppVersion "1.1.6"` (:17), `AppId` GUID (:23), `DefaultDirName={localappdata}\PhantomMonitor` with `PrivilegesRequired=lowest` (:30-33) — per-user on purpose; a tray utility has no business asking for admin at install time (:12-14). `CloseApplications=force`, `RestartApplications=no`, **no `AppMutex`** (:42-50). `[Files]` the exe, README, LICENSE, `config.example.json` (:62-65); `[Icons]` an optional `{userstartup}` shortcut (:71); `[Run]` postinstall `skipifsilent` (:74-75); `[UninstallDelete]` the runtime `logs\` and two generated `.ico`s (:77-81).
- `installer.iss` `[Code]`: `CloseRunningCopies` (:96-117) posts `WM_CLOSE` (16) to `PhantomMonitorWnd`, polls up to 5 s, then `taskkill /F /IM PhantomMonitor.exe /T` and sleeps 600 ms; called from `InitializeSetup` (:147), `PrepareToInstall` (:154), `InitializeUninstall` (:160). `RemovePortableAutostart` (:130-145) offers to delete `{userstartup}\PhantomMonitor.vbs`. `CurUninstallStepChanged` (:170-183) asks before deleting `config.json` and `icon_layouts.json`.
- `phantommonitor.py:75` `APP_VERSION = "1.1.6"   # keep in step with AppVersion in build/installer.iss`. `requirements.txt`: `pywin32>=306` required, `pillow>=10.0` optional (a stock icon without it). `docs/development.md`: run/test/build commands, the check count (:29), the version-pin rule (:50-51).
- `test_phantommonitor.py` — a plain script, not pytest. `sys.path.insert` to this checkout (:3), refuses to run if `PhantomMonitorWnd` exists (:11-15), forces `hotkey_targets = {}` so pins cannot shadow number-based checks (:20-21), `FakeMon(w, h, hwid)` (:25-29) stands in for a display so size rules need no live mode change, and it picks the **smallest non-primary display** as the stand-in phantom (`BLOCK_HWID`, :31-38) so it runs on any multi-monitor machine. One Tk window is the fixture (:44-47); `where(h)` (:55), `park_on_denon(...)` (:59-63) parks it on the blocked display, `check(name, condition, detail)` (:65-67) appends to `results` and prints PASS/FAIL. 24 scenarios; exit 1 if any failed (:814).
- The startup smoke test (:773-812): spawns `python phantommonitor.py`, tails `logs/phantommonitor.log` from a pre-recorded byte offset for `MARKER = "tracking"` (:781) — the last line startup logs, from `_snapshot_windows` at `phantommonitor.py:2207`. 15 s, capturing stderr if it died.

## CASCADE — a change here must reach ALL of these
- **A release version** goes in BOTH `phantommonitor.py:75` (`APP_VERSION`) and `build/installer.iss:17` (`AppVersion`); nothing enforces it. `APP_VERSION` also drives the update comparison (`phantommonitor.py:3109-3120`) and the GitHub User-Agent (`:330`), so a stale value makes every update check wrong. Tag the commit too; `latest_release` compares tag names.
- Renaming the executable: `build.ps1:24,33`, `build_installer.ps1:12,34`, `installer.iss:20,62,113` (the `taskkill /IM` argument), `docs/development.md:46-48`.
- Changing the class `PhantomMonitorWnd`: `installer.iss:101,106`, `test_phantommonitor.py:11`, plus the list in `config-and-startup.md`.
- Changing what the app writes at runtime: `installer.iss:77-81` (`[UninstallDelete]`) and `:178-180` (the opt-in wipe) — otherwise an uninstall leaves files behind or deletes something it should have asked about.
- A startup log line after `_snapshot_windows` makes `MARKER` stale: update `test_phantommonitor.py:781`, or the smoke test starts passing on a partial startup.
- A new key in `config.example.json` is installed by `installer.iss:65` — chain in `config-and-startup.md`.

## Rules (invariants)
- `APP_VERSION` (`phantommonitor.py:75`) and `AppVersion` (`build/installer.iss:17`) are one fact in two files. Both are `1.1.6` today.
- **No `AppMutex` in installer.iss.** Inno checks it *before* `[Code]` runs, so it produced "Please close all instances" for an app the installer can close itself, and aborted outright under `/VERYSILENT` (:46-48, 9a10849).
- The installer closes the running copy itself: `WM_CLOSE` first, so the app can release the cursor clip and save state, then `taskkill` (:91-92, :103-114).
- A PyInstaller one-file build is **always two processes** — bootloader parent plus the child running the app (9a10849). Anything that counts processes, kills by image name, or reasons about the single-instance mutex must expect both; `taskkill /T` is there for this.
- Install is per-user into `{localappdata}`, `PrivilegesRequired=lowest`. Never raise it.
- Uninstall asks before removing `config.json` and `icon_layouts.json` (:166-183).
- Run the suite with the tray app stopped; a green run with it live proves nothing.

## Reuse, don't rebuild
- Building: `build/build.ps1` then `build/build_installer.ps1`. Never invoke PyInstaller or ISCC by hand, and never hand-edit `build/PhantomMonitor.spec` — regenerated and gitignored.
- A new test: add `check("name", <condition>, "<detail>")` (`test_phantommonitor.py:65`) to the numbered sequence. Park the window with `park_on_denon()` (:59), read it back with `where(hwnd)` (:55). Use `BLOCK_HWID`, never a literal `DON0015`. For a size rule without changing the real display mode, `FakeMon(w, h, hwid)` (:25).
- Closing a running copy from a script: copy `CloseRunningCopies` (`installer.iss:96-117`) — `FindWindowByClassName('PhantomMonitorWnd')`, `WM_CLOSE`, poll, `taskkill`.

## Traps
- **9a10849** — updating over a running copy failed at the very end on the locked exe, *after* the user had been asked to retry twice. Two causes: the app has no ordinary window, so Inno's detection found nothing to close, and the one-file build's second process kept holding the file. Fixed by `CloseRunningCopies` from `InitializeSetup` and `PrepareToInstall`.
- **d02f13a** — installing after trying the portable build left `PhantomMonitor.vbs` in Startup *and* Setup's shortcut, so two copies with two configs raced at every logon. Hence `RemovePortableAutostart` (:122-145). Keep it on any installer change.
- **d04c508** — every logic test passed while the program could not start at all, because none of them build the tray app. The startup smoke test is the only thing that catches that; never weaken it to go green.
- `build/dist/` is gitignored, so what is on disk is not evidence of what is released. Check the
  git tags and the GitHub releases page instead. Releasing: bump `APP_VERSION` and `AppVersion`
  together, run the suite, run `build.ps1` then `build_installer.ps1`, tag, and upload both
  artifacts to the release. **v1.1.6 was tagged 2026-10-24** with 24 bug fixes from the
  subsystem-mapping pass.
- `build.ps1:13` upgrades PyInstaller, pywin32 and pillow on every run, so a build can pick up a new toolchain without anyone choosing to. If one suddenly misbehaves, check what that pulled in before blaming the code.
- `watch_amp.py` and `time_it.py` are gitignored, so a clean clone lacks them. Developer probes (see `diagnostics.md`), not shipped tools.

## Related subsystems
`config-and-startup.md` (`DATA_DIR`, the single-instance check the installer works around, the `STARTUP_VBS` it cleans up), `diagnostics.md` (`APP_VERSION` and the update check against the published tag).

LAST VERIFIED: 2026-10-24 · aa2db0f · Read build/build.ps1, build_installer.ps1, installer.iss (all 183 lines incl. `[Code]`), PhantomMonitor.spec, requirements.txt, .gitignore, docs/development.md, test_phantommonitor.py (head, scenario list, smoke test :773-814). Confirmed `APP_VERSION = "1.1.6"` (phantommonitor.py:75) against `AppVersion "1.1.6"` (installer.iss:17), the `MARKER`/`phantommonitor.py:2207` pairing, and the release procedure. Read 9a10849, d02f13a, d04c508.
