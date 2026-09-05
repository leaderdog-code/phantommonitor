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

A rule is a hardware id, optionally qualified by size. `--list` prints the ids
of your displays.

| Rule | Blocks that display |
|---|---|
| `ABC1234` | whenever Windows reports this id |
| `ABC1234@1920x1080` | only while it is exactly this size |
| `ABC1234@<1280x720` | only while it has fewer pixels than this |

Which form you want depends on how your amp behaves when a screen wakes up
behind it. There are two kinds, and both were measured here:

| The amp | When a screen wakes up behind it | Rule | Tested on |
|---|---|---|---|
| Passes the screen's EDID upstream | its **id** disappears — Windows sees the screen | plain id, e.g. `YMH3148` | Yamaha HTR-4063 |
| Keeps its own EDID | **nothing changes** — same id, same size | tick it by hand | Denon AVR-790 |

With the first kind the rule stands itself down when a screen is awake and comes
back when the screen goes. Nothing to switch.

With the second kind there is no signal to work with, so blocking is a tick you
turn off when you want to use the screen. Do not expect an amp to announce
itself by dropping to a small resolution — the AVR-790 tested here advertises
1920x1080 whether or not anything is plugged in behind it.

**This is per model, not per brand.** Two units were tested, one of each kind,
and the one that gives no signal is an older receiver. Newer ones are more
likely to pass EDID through, since 4K, HDR and eARC all need the amp to
negotiate with the real display. Do not assume yours behaves like a Denon or a
Yamaha because of the badge — run `--diag` and look.

**Quick way to tell what your amp is doing.** With nothing plugged in behind it,
open Windows display settings, select that display and look at the resolution
list. The mode marked **(Recommended)** is the one the amp is asking for — it is
the preferred timing out of its EDID. If that says 1920x1080 with no screen
attached, the amp is not signalling anything and size alone will not help you.

### Which rule to use

Run `--diag`. For anything it recognises as an amp it prints what that display
is *asking for* and suggests a rule. The ladder it works down:

| If the amp | Rule | Automatic? |
|---|---|---|
| shows the screen's id when one wakes up | plain id | yes |
| asks for an **interlaced** mode with nothing behind it | `@interlaced` | yes |
| sits at a small resolution you set yourself | `@<1280x720` | yes |
| does none of these | plain id + untick when you use the screen | no |

Only the last rung always works. The others depend on what your hardware is
willing to tell us, and that varies **by model, not by brand** — two receivers
tested here behave completely differently, and a newer one may well do
something neither of them does.

### Receivers people have tested

| Receiver | With nothing behind it | With a screen awake | Rule |
|---|---|---|---|
| Yamaha HTR-4063 | id `YMH3148` | id becomes the screen's | plain id |
| Denon AVR-790 | asks for 1920x1080**i** | asks for 1920x1080**p** | `@interlaced` |

Two units, one household. Nobody is going to buy a dozen receivers across four
eras to fill this table in, so it grows by report or not at all.

**Please add yours.** Open an issue with `--diag` output twice: once with a
screen awake behind the amp, once several minutes after switching that screen
off. The "it asks for" line is the one that matters. Every model added is one
more that works out of the box for whoever turns up next with the same
problem.

### The fallback that always works

#### Making an amp automatic when it gives no signal

You can create the signal yourself. With nothing plugged in behind the amp, set
that display to something small — 800x600 — and use `@<1280x720`. Windows
remembers modes per display arrangement, so:

- Nothing attached → Windows restores 800x600 → the rule matches → blocked
- Monitor attached → Windows uses the monitor's proper resolution → not blocked

**This costs you nothing.** While nothing is plugged in, that display is
invisible; its resolution is a number on a screen nobody can see. Plug a real
monitor in and it runs at full resolution as normal — the small setting applies
only to the arrangement where there is no screen.

The one thing to avoid is setting that display *large* while nothing is
attached, because Windows will remember that instead and the rule will quietly
stop matching.

`<` compares total pixels rather than width and height, so it is not fooled by a
monitor turned on its side. Use an exact `@1920x1080` only for a display you are
reserving that must stay blocked whatever is attached.

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
