# Building and testing

## Running from source

Requirements:

- Windows 10 or 11
- Python 3.9+
- `pip install pywin32` (required), `pillow` (optional, nicer tray icon)

Any graphics card. EDID is a VESA standard, not a vendor feature, and displays
are read through standard Windows APIs — `EnumDisplayDevices`,
`QueryDisplayConfig`, and the cached EDID in the registry. No vendor SDK.

```
pip install -r requirements.txt
python phantommonitor.py --list
python phantommonitor.py
```

`PhantomMonitor.vbs` starts it without a console window.

## Tests

```
python test_phantommonitor.py
```

138 checks. **Stop the tray app first** — it rescues the test fixtures
mid-assertion, and the suite refuses to run while it is live.

The tests drive real windows on your real displays, and pick the smallest
non-primary display as the stand-in for a phantom, so they run on any
multi-monitor machine. They also start the program as a subprocess and require
it to reach the end of its startup sequence: everything else is pure logic, and
a startup crash once passed the entire suite while the program could not run at
all.

## Building the binaries

```
powershell -ExecutionPolicy Bypass -File build\build.ps1
powershell -ExecutionPolicy Bypass -File build\build_installer.ps1
```

The first produces `build\dist\PhantomMonitor.exe` via PyInstaller and smoke
tests it. The second needs [Inno Setup](https://jrsoftware.org/isdl.php) and
produces `PhantomMonitor-Setup.exe`.

Keep `APP_VERSION` in `phantommonitor.py` and `AppVersion` in
`build\installer.iss` in step.

## Older Windows

Built and tested on Windows 10. Everything it uses — `SetWinEventHook`,
`ClipCursor`, `EnumDisplayDevices`, registry EDID — predates Windows 7, so it
should run there. Two things degrade rather than fail:

- Per-monitor DPI awareness needs 8.1+. On 7 it falls back to system DPI.
- `QueryDisplayConfig` needs 7+. Without it, display names come from
  `EnumDisplayDevices` instead.

If you try it on 7 or 8.1, please open an issue either way.

## How this was built

Written by Raymond Pierce, co-authored with Claude Opus 5, driven by real
hardware: a Denon AVR, a Yamaha HTR-4063, a Toshiba TV and three displays. Most
of what is in these docs was measured rather than assumed, and several
confident claims had to be withdrawn when testing contradicted them.

---

[Back to the README](../README.md)
