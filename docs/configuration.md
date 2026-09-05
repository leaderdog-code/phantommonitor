# Configuration

`config.json` sits beside the program. `config.example.json` documents every
key. Most of it is reachable from **Settings** in the tray menu; **Reload
settings** applies changes without restarting.

## The settings window

**Displays** lists each one by name, size, hardware id and position, with a tick
to block it, a hotkey slot, and a tick marking it as an amp rather than a
display.

The amp tick is shown ticked for anything the built-in vendor list recognises,
and stays clickable. The list matches a three-letter EDID prefix, which is a
guess — `SON` catches a Sony display as readily as a Sony amp. Tick an amp
nobody has heard of; untick a wrong guess and that sticks. Marking something as
an amp only labels it. Nothing is blocked on that basis.

**Seen before, not connected now** lists displays Windows has ever seen, so you
can write a rule for one that is not plugged in. A pass-through amp hides its
own id whenever a screen is awake behind it, and that id is exactly the one
worth blocking.

**Window zoom** scales the window from 1x to 3x, applied live. The app declares
per-monitor-v2 DPI awareness, which it needs in order to measure displays, and
Windows responds by not scaling it — so on a dense panel at 100% every control
comes out half size. It opens at whatever Windows scaling is set to unless you
choose otherwise.

**Hotkeys** are typed, not recorded. Capturing keystrokes means intercepting
everything typed into the box, and `ctrl+alt` is AltGr, so it produces a symbol
rather than the key you pressed.

## Settings worth knowing

| Key | Effect |
|---|---|
| `enabled` | Master switch |
| `blocked_hwids` | The block rules |
| `block_cursor` | Fence the pointer out of blocked displays |
| `app_displays` | `{"discord.exe": "GSM7814"}` — pin apps to a display |
| `cursor_lock_fullscreen` | Hold the pointer inside full-screen apps (default on) |
| `cursor_never_lock` | Apps exempt from that, e.g. `mstsc.exe` |
| `cursor_lock_apps` | Force holding for an app, overriding the exemption |
| `restore_windows` / `restore_icons` | Put things back after a display change |
| `av_devices` / `not_av_devices` | Your amp declarations and overrules |
| `hotkey_targets` | Which hardware id each hotkey slot means |
| `settings_zoom` | Settings window scale |
| `ignore_process_names` / `ignore_window_classes` | Exclusions |
| `editor` | What opens config and log files. Empty means Notepad. |

## The pointer fence

Uses `ClipCursor`, not a mouse hook, so it costs nothing per mouse movement and
cannot add input latency in a game.

Windows allows only a **single** clip rectangle. So the fence works when the
blocked display sits outside the bounding box of the others. If it does not,
Phantom Monitor refuses rather than locking you out of a display you use.

Displays with an app pinned to them are excluded from the fence — a reserved
screen you cannot click on is not reserved, it is lost.

There is one clip on the whole system, so ownership matters. A clip already
inside the allowed region is left alone, but only if it lies inside the window
in front. A clip outlives the process that set it, and deferring to a stale one
seals the pointer into a rectangle with nothing in it.

## Full-screen apps

**Pointer.** A full-screen app holds the pointer inside its own display. Only
genuinely full-screen windows qualify: 99.5% coverage and no caption or resize
frame. A maximized window stops at the taskbar and does not count, nor does the
desktop, nor an overlay spanning several displays.

**Moving.** Moving a full-screen window without taking it out of full-screen is
useless — it arrives borderless with nothing to drag. So the app is asked to
leave full-screen using its own shortcut, then moved once its frame is back.

| App | Window class | Toggle |
|---|---|---|
| Remote Desktop | `TscShellContainerClass` | `Ctrl`+`Alt`+`Break` |

Pull requests adding more are welcome. If an app ignores its toggle, it is moved
anyway after ~2.5 s, centred and inside the work area. It is never expanded to
fill the destination — that buries the taskbar and still leaves nothing to grab.

## Apps that store their own position

If a window keeps returning to a blocked display, the app is probably storing
the position itself. Remote Desktop does, in
`%USERPROFILE%\Documents\Default.rdp`:

```
screen mode id:i:2                      2 = full screen, 1 = windowed
desktopwidth:i:800  desktopheight:i:600 session size, fixed at connect time
winposstr:s:0,1,-3360,-600,-2544,39     last window position
```

mstsc rewrites that file when it exits, so edit it with mstsc closed. Session
resolution is baked in at connect time; an existing session keeps its old size
until you reconnect.

## What it leaves alone

Shell furniture (desktop, taskbar, menus, tooltips), child and tool windows,
cloaked windows, anything smaller than 80x60, and windows parked far off-screen
on purpose — apps hide helper windows at -32000,-32000 routinely.

While you are dragging a window it holds off and acts only once you let go.

## Elevated windows

A normal process cannot move an elevated app's window. That is UIPI, and it is
deliberate. Run `install_elevated_autostart.ps1` to register a scheduled task
that starts Phantom Monitor elevated at logon if you need it.

## Privacy

`icon_layouts.json` stores the name of every item on your desktop. That is often
private. It is gitignored — do not attach it to a bug report.

## Command line

| Flag | Does |
|---|---|
| `--list` | Print attached displays and their hardware ids |
| `--diag` | Everything worth pasting into a bug report |
| `--rescue` | Pull stranded windows back, then exit |
| `--settings` | Open the settings window |
| `--arrange [n] [--open]` | Ask the running copy to apply a saved arrangement, all displays or just display n. With --open, start anything missing first |
| `--no-tray` | Run without a tray icon |

---

[Back to the README](../README.md)
