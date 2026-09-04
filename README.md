# Phantom Monitor

**Stops Windows putting your windows on a phantom display — the invisible
"monitor" an AV receiver, soundbar or HDMI switch creates just by being plugged
in.**

If you send HDMI to a Denon, Yamaha, Onkyo, Marantz, Pioneer or any other AVR
for sound, it advertises itself to Windows as a display whenever there is no
live screen behind it. Windows believes it, and starts putting windows on a
monitor that does not exist.

There are two ways to end up here, and the second catches people who assume
modern kit is immune:

- **Audio only.** Your monitors go straight to the graphics card and the
  receiver gets HDMI purely for sound, so nothing is ever attached behind it.
  Common when you want VRR/G-Sync, DSC or high refresh rates that the amp will
  not carry, or simply do not want your monitors to depend on the amp being on.
- **Video passthrough, screen switched off.** A current 4K receiver feeding a
  TV is a real display while the TV is awake. Turn the TV off and leave the amp
  on for music, and the receiver falls back to advertising its own EDID - so it
  becomes a phantom display on a setup that was fine a minute ago.

The receiver reports a fallback EDID — often 800×600 — so Windows treats it as
real desktop space and cheerfully puts windows there. They are invisible and
unreachable. The classic escape is powering the amp off, which dumps them back
the moment you power it on again.

**This is an HDMI-introduced problem, not a Windows bug.** HDMI carries audio
and video on the same link, so anything you send audio to has to look like a
display. Windows is behaving correctly; the desktop space is simply real to
everything except you.

The usual advice is to buy an EDID dummy plug, or to disconnect the display in
Windows — but HDMI audio requires the display output to stay active, so
disconnecting it kills the sound you plugged the amp in for.

PhantomMonitor fixes it in software: it watches for windows arriving on a blocked
display and evacuates them, typically in under 10ms, so you never see it happen.

It is also useful for a TV you only sometimes use, a projector left plugged in,
an HDMI switch or splitter, a capture card, or a KVM that presents itself as a
monitor.

**Keywords, so people with this problem can find it:** AV receiver phantom
monitor, AVR ghost display, HDMI audio creates fake monitor, windows opening on
invisible monitor, Denon / Yamaha / Onkyo / Marantz second display, soundbar
shows as monitor, windows disappear off screen, mouse lost on invisible
display.

## What it does

- **Evacuates windows** from displays you mark as blocked
- **Fences the mouse pointer** out of them too, so the cursor can't get lost
- **Takes full-screen apps out of full-screen** before moving them, using the
  app's own shortcut, so you get a real window with real buttons
- **Restores window positions** after a display change scrambles them
- **Restores desktop icon positions**, which Windows also scrambles and never
  puts back
- **Fixes minimized windows** whose stored restore position is on a blocked
  display — the nastiest variant, since the window looks fine until you restore
  it and it vanishes

Displays are matched by **EDID hardware id**, not by index, so your rules survive
reboots, cable swaps and port changes.

## Install

**Most people want the installer.** Download `PhantomMonitor-Setup.exe` from
[Releases](../../releases) and run it. It installs per-user, so there is no
administrator prompt, and offers a **"Start Phantom Monitor when I sign in"**
tick box during setup. It appears in Add/Remove Programs like any other app.

**Or take the portable executable.** `PhantomMonitor.exe` from the same place
needs no installation at all — put it anywhere and double-click. Settings and
logs are written beside it. Use the tray menu's **Start with Windows** if you
want it at sign-in.

Neither needs Python. Both have it bundled inside.

> **Windows will warn you the first time.** SmartScreen shows "Windows protected
> your PC" for any program from a publisher it has not seen before, which is
> every small free tool without a paid code-signing certificate. Click **More
> info → Run anyway**. The source is right here if you would rather read it or
> build it yourself, and each release links a VirusTotal scan.

## Running from source

## Requirements

- Windows 10 or 11 (see below for older)
- Any graphics card — NVIDIA, AMD or Intel. EDID is a VESA standard, not a
  vendor feature, and every display is read through standard Windows APIs
  (`EnumDisplayDevices`, `QueryDisplayConfig`, the cached EDID in the registry).
  Nothing here touches a vendor SDK.
- Python 3.9+
- `pip install pywin32` (required), `pip install pillow` (optional, for a nicer
  tray icon — it falls back to a stock icon without it)

## Getting started

```
pip install -r requirements.txt
python phantommonitor.py --list
```

That prints every attached display with its hardware id:

```
1: LG ULTRAGEAR+  (3840x2160) [GSM5BBF] * hwid=GSM5BBF    \\.\DISPLAY1
2: LG ULTRAGEAR   (2560x1440) [GSM7814]   hwid=GSM7814    \\.\DISPLAY3
3: DENON-AVAMP    (800x600)   [DON0015]   hwid=DON0015    \\.\DISPLAY2
```

Then just run it:

```
python phantommonitor.py
```

Right-click the tray icon and tick the display you want blocked. Use
`PhantomMonitor.vbs` to start it without a console window, and the tray menu's
**Start with Windows** to have it launch at logon.

## Block rules

| Rule | Meaning |
|------|---------|
| `DON0015` | Always block this display |
| `DON0015@800x600` | Block only at exactly that size |
| `DON0015@<1280x720` | Block only while **either** dimension is smaller |

The `<` form exists for "I might plug a real screen in later". An AV receiver
with nothing attached advertises a small fallback EDID — that tiny resolution
*is* the "no display here" signal. Plug a real screen in and the receiver either
passes its EDID through or reports a real resolution; either way the rule stops
matching and PhantomMonitor stands aside on its own. Unplug it and blocking
resumes. No clicking.

Use `<1280x720` rather than an exact size: fallback EDIDs come in 640×480,
800×600 and 1024×768, and that last one is *taller* than 720 — hence "either
dimension". Every real TV clears the threshold.

PhantomMonitor refuses to block every display at once, since there would be
nowhere to evacuate to.

## Hotkeys

Configurable in `config.json`. Defaults:

| Hotkey | Action |
|--------|--------|
| `Ctrl`+`Alt`+`Shift`+`0` | Rescue every window off blocked displays |
| `Ctrl`+`Alt`+`Shift`+`1/2/3` | Move the focused window to that display |

Any mix of ctrl/alt/shift/win plus a letter, digit, F-key or named key. **Matched
combos are swallowed and never reach the focused app**, so avoid combos your
games use — that's why the default includes shift, since plain `Ctrl`+`Alt`+digit
is a common game binding.

Hotkeys are registered twice: as normal Windows hotkeys, and through a low-level
keyboard hook that is re-installed on every foreground change. The second one
exists because some apps — Remote Desktop especially — install their own
keyboard hook and swallow key combos before Windows dispatches them.

**Pin hotkeys to a display** with `hotkey_targets`, e.g. `{"2": "GSM7814"}`.
Display numbers get reassigned when you plug things in, so a number alone can
quietly start meaning a different physical screen. A hardware id never changes.

## Full-screen apps

Moving a full-screen window without taking it out of full-screen is useless: it
arrives on the new display still borderless, with no title bar to drag and no
edge to resize. So PhantomMonitor asks the app to leave full-screen using the
app's *own* shortcut, waits for it to rebuild its frame, and only then moves it.

Known apps live in `FULLSCREEN_TOGGLES`, keyed by window class:

| App | Class | Toggle |
|-----|-------|--------|
| Remote Desktop | `TscShellContainerClass` | `Ctrl`+`Alt`+`Break` |

Pull requests adding more are welcome. If an app ignores its toggle,
PhantomMonitor gives up after ~2.5s and moves it anyway, centred and inside the
work area so the taskbar stays reachable. It is never expanded to fill the
destination — that buries the taskbar and still leaves nothing to grab.

## Apps that save their own position

If a window keeps returning to a blocked display even with PhantomMonitor running,
the app is probably storing the position itself. Remote Desktop does exactly
this, in `%USERPROFILE%\Documents\Default.rdp`:

```
screen mode id:i:2                      <- 2 = full screen, 1 = windowed
desktopwidth:i:800  desktopheight:i:600 <- session size, fixed at connect time
winposstr:s:0,1,-3360,-600,-2544,39     <- last window position
```

mstsc rewrites that file when it exits, so edit it with mstsc closed. Note that
the session resolution is baked in when the connection is made — an existing
session keeps its old size until you reconnect.

## Privacy note

`icon_layouts.json` stores the **name of every item on your desktop**. That is
often private — tax documents, medical files, client names. It is in
`.gitignore`; don't commit it, and don't attach it to a bug report.

## Configuration

`config.json` sits next to the script. See `config.example.json` for every
setting with explanations. Edit it from the tray (**Edit hotkeys / settings**)
and apply without restarting (**Reload settings**).

A few worth knowing:

- `block_cursor` — fence the pointer out of blocked displays. Uses `ClipCursor`,
  so it costs nothing per mouse movement and cannot add input latency in a game.
  Windows can only fence into a *single rectangle*, so this works when the
  blocked display sits outside the bounding box of the others; if it doesn't,
  PhantomMonitor refuses rather than locking you out of a display you use.
- `restore_windows` / `restore_icons` — put things back after a display change.
- `editor` — what opens the settings and log. Empty means Notepad.
- `ignore_process_names` / `ignore_window_classes` — exclusions.

## What it deliberately leaves alone

Shell furniture (desktop, taskbar, menus, tooltips), child and tool windows,
cloaked windows (UWP apps on another virtual desktop), and anything smaller than
80×60. Windows parked far off-screen on purpose — the classic spot is
-32000,-32000 — are left where they are; apps hide helper windows there
routinely.

While you are dragging a window, it holds off and only acts once you let go, so
it never fights your mouse.

## Elevated windows

Windows' UIPI stops a normal process from moving a window owned by an elevated
app, so an admin Command Prompt stranded on a blocked display can't be rescued
unless PhantomMonitor is elevated too. If you hit that, run once from an admin
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File install_elevated_autostart.ps1
```

That registers a scheduled task which starts it elevated at logon with no UAC
prompt. The log says `move rejected ... elevated window?` when this is the cause.

## Command line

```
python phantommonitor.py            # tray app
python phantommonitor.py --list     # show displays and hardware ids
python phantommonitor.py --rescue   # one-shot sweep, then exit
```

Logs rotate in `logs/phantommonitor.log`.

## Tests

```
python test_guard.py
```

Stop the tray app first — it will rescue the test fixtures mid-assertion, and
the suite refuses to run while it's live. The tests drive real windows on your
real displays.

## Older Windows

| Version | Status |
|---------|--------|
| Windows 10 / 11 | Tested |
| Windows 8.1 | Should work — untested, reports welcome |
| Windows 7 | Will not run as built |

The Windows APIs used here all predate Windows 7, and the two newer ones
degrade on purpose: per-monitor DPI v2 (Windows 10) falls back to the 8.1 API
and then the Vista one, and the cloaked-window check (`DWMWA_CLOAKED`,
Windows 8) simply reports false where it does not exist — which is correct on
an OS without virtual desktops.

Python is the actual barrier. Python 3.9 dropped Windows 7, so the packaged
executable will not launch there; it would need building against Python 3.8,
which is itself end-of-life. If you are on 8.1 the executable ought to run —
if you try it, please open an issue either way.

## Known limits

- Window position restore is **per session**, held in memory. It fixes the
  unplug/replug scramble; it does not survive a reboot.
- Nothing can detect that a monitor has been physically switched off if the
  receiver keeps serving its cached EDID. DDC/CI would answer, but receivers
  generally don't pass the I2C channel through. A receiver typically drops the
  cached EDID a few minutes after the screen goes dark, and blocking resumes
  then.
- An EDID hardware id identifies a monitor *model*, not an individual panel, so
  two identical monitors share one id. PhantomMonitor warns when it sees that.

## Licence

MIT. See `LICENSE`.

## Building it yourself

```powershell
powershell -ExecutionPolicy Bypass -File builduild.ps1
```

Produces `build/dist/PhantomMonitor.exe` with Python bundled in — roughly 27 MB,
no dependencies.

For the installer as well:

```powershell
winget install --id JRSoftware.InnoSetup.7 -e
powershell -ExecutionPolicy Bypass -File builduild_installer.ps1
```

That produces `build/dist/PhantomMonitor-Setup.exe`. Inno Setup is free for
non-commercial use; see [their site](https://jrsoftware.org/isdl.php) if you are
shipping something you sell.
