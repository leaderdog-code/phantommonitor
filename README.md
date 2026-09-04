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

Not Windows-specific either — Linux and macOS see the same phantom. And it is not
only receivers: **anything that accepts HDMI audio has to present as a display**,
so soundbars with an HDMI input, HDMI audio extractors and capture cards all do
it for the same reason.

The same thing happens with anything else that sits between the graphics card
and a panel and holds the link up on its own: a **DisplayPort-to-HDMI
converter**, an HDMI switch or splitter, a KVM, a capture card. Those keep
reporting a display when the screen behind them is switched off, so Windows
keeps putting windows on it. A TV you only sometimes use, or a projector left
plugged in, behaves the same way.

## Reserving a monitor you actually use

Blocking is not only for phantom displays. It works on any display, so it also
answers "keep windows off that screen, it has a job":

- A **streamer's chat monitor** — a dialog box landing on it mid-stream is
  exactly the sort of thing you cannot undo live
- A **dashboard or monitoring screen** that should show one thing and stay
  showing it
- A **TV playing something**, where a notification window is an interruption
- A **vertical monitor** given over to chat, docs or a terminal
- A **capture or preview display** feeding something else

The mouse fence matters here too: a cursor wandering onto a screen being
captured is visible to everyone watching.

Use a plain hardware id for this — `GSM7814`, not `GSM7814@<1280x720`. The
resolution qualifier exists so a phantom display stands aside when a real screen
appears behind it; a monitor you are deliberately reserving should stay blocked
until you untick it.

### Sending an app to a display and keeping it there

Blocking on its own would evacuate *everything*, including the app you wanted on
that screen. So name it:

```json
"app_displays": { "discord.exe": "GSM7814", "obs64.exe": "GSM7814" }
```

Those apps open on that display and are never evacuated from it, even when it is
blocked. Everything else is still kept off. That is the chat-monitor or
dashboard setup: one screen that only ever shows the thing it is for.

Placement happens **when a window opens**, not continuously. Drag it somewhere
else afterwards and it stays where you put it — the rule decides where things
start, it does not police them. If the assigned display is not attached, the
window opens wherever Windows puts it and goes to its display next time.

One caveat: the pointer fence is all-or-nothing rather than per-display, so if
you need to click on that screen, leave `block_cursor` off.

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
> build it yourself. Each release lists SHA-256 hashes so you can
> confirm the file you downloaded is the one that was published.

## Running from source

### Requirements

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

Right-click the tray icon and choose **Settings**. Use `PhantomMonitor.vbs` to
start it without a console window, and the tray menu's **Start with Windows** to
have it launch at logon.

## The settings window

Everything is set here rather than buried in a menu.

**Displays** lists each one by name, size, hardware id and position, with a tick
to block it and a dropdown to give it a hotkey slot. Displays are listed in slot
order, so the list reads in the order of the keys you press.

**Behaviour** holds the toggles: the master switch, the pointer fence, window and
icon restore, whether to ask full-screen apps to leave full-screen, and whether
to catch hotkeys from apps that swallow them.

**Hotkeys** are typed rather than recorded by pressing — deliberately. Capturing
keystrokes means intercepting everything typed into the box, and ctrl+alt is
AltGr, so it produces a symbol rather than the key you pressed. Dead keys,
non-Latin layouts and IMEs lie behind that. A text field with an example is
duller and works.

The tray menu keeps only what is reached for in passing: rescue, the two guard
toggles, moving the current window, and the file actions.

## Block rules

| Rule | Meaning |
|------|---------|
| `DON0015` | Always block this display |
| `DON0015@800x600` | Block only at exactly that size |
| `DON0015@<1280x720` | Block only while it has **fewer pixels** than that |

The `<` form exists for "I might plug a real screen in later" — **but only some
receivers make it possible**, so check yours before relying on it.

A receiver that drops to a small fallback EDID when nothing is awake behind it
gives you that resolution change as a signal: the rule matches while it is a
phantom and stops matching when a real screen appears, so blocking turns itself
on and off with no clicking. A Denon tested here behaves that way, falling back
to 800×600 a few minutes after a screen goes dark.

A setup that reports the same EDID regardless gives you no signal at all. A
Yamaha tested here behaved that way — though it was reached through a
DisplayPort-to-HDMI adapter, and an active adapter terminates the link and
regenerates it, so the constant EDID may be the adapter's doing rather than the
amp's. Either way the result is the same from the desktop: nothing changes when
the screen goes off. **Use a plain hardware id and tick it when you want that
display**, exactly as you would for a monitor you are reserving.

Run `PhantomMonitor.exe --diag` with a screen awake behind the amp and again
with it off. If the reported resolution changes, the qualified rule will work.
If it does not, nothing in software can detect it and a tick is the honest
answer.

Expect the tick more often than not. Graphics cards commonly ship with a single
HDMI port and three DisplayPorts, so anyone running an amp *and* a TV is on an
adapter for at least one of them — and an active adapter terminates the link and
regenerates it, so the card only ever sees the adapter. Automatic detection
tends to work for whatever occupies the one native HDMI port, and not for the
rest.

Use `<1280x720` rather than an exact size: fallback EDIDs come in 640×480,
800×600 and 1024×768. The comparison is by **area**, which is the only version
that gets both ends right — comparing both dimensions would miss 1024×768, which
is taller than 720, and comparing either would falsely catch a monitor turned on
its side, where a 1080×1920 portrait panel is narrower than 1280. Every fallback
EDID is under that many pixels; every real display, rotated or not, is over it.

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
This is worth doing, because the numbers move.

### Why displays are not numbered here

Windows Display Settings prints a number on each display, and **there is no
documented way to obtain it**. It is not the `\.\DISPLAYn` digit, and it is
not the display-config path order — both have matched Settings on one layout and
disagreed on the next, on the same machine, minutes apart. Plugging in a
different adapter is enough to reshuffle them.

So displays are identified by name, size, hardware id and position, all of which
agree with what is physically in front of you. The number beside a hotkey is a
**slot in this app**, nothing more, and pinning it to a hardware id is what makes
it mean a fixed screen.

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
setting with explanations. Most of it is reachable from **Settings** in the tray
menu; **Edit config file** opens the raw file, and **Reload settings** applies
changes without restarting.

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
PhantomMonitor.exe            # tray app
PhantomMonitor.exe --list     # show displays and hardware ids
PhantomMonitor.exe --diag     # full diagnostics, for bug reports
PhantomMonitor.exe --rescue   # one-shot sweep, then exit
```

`--diag` prints your Windows version, every display with its hardware id,
bounds, offered resolutions and whether it answers DDC/CI, plus which rules
currently match. **Please paste it into any issue you open** — nearly every
question about behaviour comes down to what a particular receiver reports.

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

## What sits between the card and the screen

Whether a switched-off screen disappears from Windows, or lingers as a phantom,
depends entirely on what is in the cable path. This decides which rule you want.

| Path | When the screen is switched off |
|------|--------------------------------|
| Direct DisplayPort | Display **vanishes**. No phantom — but Windows scrambles the windows that were on it, and never puts them back. |
| Direct HDMI | Varies by panel. Many TVs and some monitors hold the link in standby. |
| Through an **AV receiver** | Receiver keeps serving a cached EDID, then falls back to its own after a few minutes. A resolution-qualified rule tracks this by itself. |
| Through an **adapter chain** (DisplayPort→DVI→HDMI and similar) | Nothing changes at all. |
| **TV with quick-start / CEC enabled** | The TV's input stays powered, so the link never drops, whatever it is plugged into. |

Two separate things can make a dark screen look present, and they are easy to
confuse:

**Something holds the link up.** Usually the display's own standby — most TVs
keep their input powered for quick-start and CEC — but an active converter can
do it too. To tell which, pull the screen's mains plug: if the display then
disappears from Windows, it was the screen's standby, and its **quick-start or
eco setting** controls it. If it persists, whatever is in the middle is holding
it and no setting on the screen will change that.

**The display will not answer DDC/CI.** That is the channel that could be asked
"are you switched on", and there are two reasons it goes quiet:

- **Most TVs simply do not implement it.** It is a monitor feature — brightness
  and input control from the PC — and TVs expect their own remote and CEC
  instead. Computer monitors usually answer; televisions usually do not.
- **Something in the path does not pass it on.** Receivers and some active
  converters terminate the link and regenerate it rather than relaying I2C
  commands.

Note that EDID passing through proves nothing either way: EDID is a passive read
over the same wires, and it works in plenty of setups where DDC/CI does not.

`--diag` reports this per display. Where it says *not supported*, power state is
undetectable and no software can work around it.

So:

- **Detectable** (resolution changes when the screen goes away) → use a
  qualified rule like `ABC1234@<1280x720` and it manages itself
- **Undetectable** (nothing changes) → use a plain `ABC1234` and tick it in the
  tray when you want that screen

Worth knowing that quick-start is a genuine trade rather than an obvious win to
disable: turning it off makes the display vanish properly when switched off, so
no rule is needed — but then every power cycle is a display change, which blanks
every monitor and reshuffles your windows and desktop icons.

## Known limits

- Window position restore is **per session**, held in memory. It fixes the
  unplug/replug scramble; it does not survive a reboot.
- Nothing can detect that a screen has been switched off while something keeps
  serving its EDID. DDC/CI would answer, but most televisions never implement
  it and receivers and converters often do not pass it on. A receiver typically
  drops a cached EDID a few minutes after the screen goes dark and blocking
  resumes then; an adapter or a TV's own quick-start standby may hold it
  indefinitely, and then blocking is a tick in the settings.
- **Setups differ, and not by a little.** A Denon tested here, on a native HDMI
  port, drops to a 800×600 fallback EDID a few minutes after the screen behind
  it goes dark — which is what makes automatic blocking possible. A Yamaha
  tested here, reached through a DisplayPort-to-HDMI adapter, reported the same
  EDID whether or not anything was awake. Whether that was the amp or the
  adapter is not separable without trying it on a native HDMI port, and it does
  not matter to the outcome: no change means no signal means a manual tick.
  Assume nothing about your own hardware until `--diag` tells you which kind
  you have.
- The timings quoted here — the four-minute hold in particular — come from one
  machine. `--diag` output from other hardware is the most useful thing anyone
  could send.
- An EDID hardware id identifies a monitor *model*, not an individual panel, so
  two identical monitors share one id. PhantomMonitor warns when it sees that.

## How this was built

Co-authored with Claude Opus 5 (Anthropic), driven and tested by me against a
real Denon receiver, a TV behind a DisplayPort adapter chain, and up to four
displays at once. Every behaviour described above was
verified on actual hardware rather than assumed — including the awkward parts,
like how long a receiver holds a cached EDID after the screen goes dark, and
which key form Remote Desktop actually accepts to leave full screen. The rough
edges I know about are listed under Known limits.

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
