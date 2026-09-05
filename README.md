# Phantom Monitor

Guards against phantom displays — the invisible monitors an AVR, soundbar or
other HDMI device creates in Windows. Nothing lands on them and the mouse is
fenced out. Wake a real screen behind the amp and it can stand aside by itself,
so that display works normally.

## The problem

Send HDMI to an amp for sound and it reports itself to Windows as a display.
Windows treats that as real desktop space and puts windows there. They are
invisible and unreachable.

It happens two ways:

- **Audio only.** Nothing is ever connected behind the amp.
- **Passthrough, TV off.** The amp is a real display while the TV is awake.
  Turn the TV off, leave the amp on for music, and it falls back to its own
  EDID.

This is HDMI, not a Windows bug. HDMI carries audio and video on one link, so
anything you send audio to has to identify itself as a display.

## Install

Download from [Releases](../../releases).

| File | What it is |
|---|---|
| `PhantomMonitor-Setup.exe` | Installer. Per-user, no admin prompt, optional start at sign-in. |
| `PhantomMonitor.exe` | Portable. Run it from anywhere. Config and logs sit beside it. |

Neither needs Python. SmartScreen will warn on first run — unsigned software
always does. **More info -> Run anyway**, or build it yourself from source.

## Setup

Run it, right-click the tray icon, choose **Settings**, tick the display you
want blocked. That is the whole setup.

Displays are matched by EDID hardware id, not by index, so rules survive
reboots, cable swaps and port changes.

Whether it stands aside on its own when a screen wakes up behind the amp
depends on which rule suits your hardware — see [Block rules](#block-rules).

## What it does

- Moves windows off blocked displays, in under 10 ms
- Fences the mouse pointer out of them
- Holds the pointer inside full-screen games, and gives it back when you
  alt-tab out
- Puts windows back after a display change scrambles them
- Puts desktop icons back, which Windows never does
- Fixes minimized windows whose restore position is on a blocked display
- Pins named apps to a display, so one screen can be dedicated to them

## Block rules

A rule names a hardware id, and blocks a display **whenever that id is the one
Windows is reporting**.

| Rule | Blocks that display |
|---|---|
| `DON0015` | whenever this id is present |
| `DON0015@800x600` | only while it is exactly this size |
| `DON0015@<1280x720` | only while it has fewer pixels than this |

"Whenever the id is present" is the useful part, and it is why the plain form is
usually enough. An AV receiver is an HDMI repeater: it reads the EDID of
whatever is downstream and presents that upstream, so with a screen awake behind
it Windows sees *the screen's* id, not the amp's. The rule stops matching on its
own, and that display becomes an ordinary monitor.

Switch the screen off and the amp eventually goes back to advertising itself.
Its id returns, the rule matches again, and blocking resumes without you
touching anything.

Use `@` forms only when the id alone will not do — a display you are reserving
that must stay blocked with a screen attached, or an amp that reports the same
id either way. The `<` form compares total pixels, so it is not fooled by a
monitor turned on its side.

Check yours rather than assuming. Run `--diag` with a screen awake behind the
amp, switch that screen off, **wait several minutes**, and run it again. Amps
hold the last EDID for some minutes, so an immediate second look shows no change
and tells you nothing.

## Dedicating a screen to one app

Blocking alone empties a display. To reserve one — a chat monitor, a dashboard
— block it *and* pin what belongs there:

```json
"app_displays": { "discord.exe": "GSM7814" }
```

Pinned apps open on that display and are never evacuated from it. Everything
else is kept off. The pointer fence skips displays with something pinned to
them, so you can still click and type there.

## Games

A full-screen app holds the pointer inside its own display, reapplied whenever
you alt-tab back in. Without that, stepping out to check a map loses the screen
edge for the rest of the session.

Naming games individually does not scale, so it is opt-out:

```json
"cursor_never_lock": [
  "mstsc.exe", "explorer.exe", "screenclippinghost.exe", "snippingtool.exe"
]
```

Full-screen RDP is excluded by default — being locked into a remote session
would strand the pointer. Overlays that span several displays, like the
`Win+Shift+S` snip layer, never claim the pointer either.

## Hotkeys

| Key | Action |
|---|---|
| `Ctrl+Alt+Shift+0` | Rescue: free the pointer and pull back stranded windows |
| `Ctrl+Alt+Shift+1-9` | Move the active window to that slot |

Slots are assigned by you in Settings and pinned to a hardware id. They are not
the numbers Windows shows in Display Settings — those cannot be read by any
documented API.

Rescue is the way out of anything odd. It frees the cursor first, so it works
even when something has the pointer trapped.

## Limits

- Window restore is per session, held in memory. It does not survive a reboot.
- A screen switched off behind an amp is detected late, not never. There is a
  gap of some minutes before blocking resumes.
- Some kit is never detectable: active DisplayPort-to-HDMI adapters, a TV's
  quick-start standby, EDID served from a DDC-powered EEPROM. Tick those
  manually.
- Moving windows belonging to elevated apps needs Phantom Monitor elevated too.
- Two identical monitors share one hardware id. It warns when it sees that.

## More

- [How it works](docs/how-it-works.md) — EDID, hot plug detect, what different
  amps do, and what the cable path decides
- [Configuration](docs/configuration.md) — every setting, the settings window,
  full-screen handling, apps that store their own position
- [Building and testing](docs/development.md) — running from source, the test
  suite, producing the binaries

## Licence

MIT. Written by Raymond Pierce, co-authored with Claude Opus 5.

Bug reports are welcome, especially `--diag` output from receivers other than
the Denon and Yamaha tested here.
