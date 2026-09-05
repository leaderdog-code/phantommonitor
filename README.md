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
| `PhantomMonitor.exe` | Portable. Run it from anywhere. Config and logs sit beside it, or in your profile if that folder is read-only. |

Neither needs Python. SmartScreen will warn on first run — unsigned software
always does. **More info -> Run anyway**, or build it yourself from source.

## Setup

Run it and **left-click the tray icon** to open Settings. Find your amp in the
Displays list and tick **Block windows and pointer** next to it. That is the
whole setup.

> **Cannot see the icon?** Windows hides new tray icons by default. Click the
> **^** arrow at the left of the clock and it will be in there. Drag it out onto
> the taskbar to keep it visible — it is how you reach everything.

**If nothing is ever plugged in behind your amp, you are done.** Everything
below about rules and detection is only for people who *sometimes* put a real
screen behind the amp and want to automate blocking.

Right-click the icon for everything else. That menu has a **Block <display>**
tick for each screen, which is the quick way to let yourself use the screen
behind the amp for a while without opening Settings.

Displays are matched by EDID hardware id, not by index, so rules survive
reboots, cable swaps and port changes.

**You can stop parking the phantom in a far corner.** The usual workaround is to
drag the fake display diagonally away in Display Settings so the mouse cannot
easily wander onto it — which then puts a real screen somewhere useless if you
ever plug one in behind the amp. With the pointer fenced you can leave that
display wherever it physically belongs, and a screen plugged in behind the amp
lands somewhere sensible.

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

## Windows that get scattered

You do not need a phantom display to want this part.

**The commonest cause is sleep.** The PC sleeps, the displays drop away, and on
waking Windows has crammed everything onto the primary — the classic being the
terminals you had arranged across three screens, back on monitor 1 every single
morning. Unplugging a monitor, switching one off, and RDP sessions
connecting or disconnecting all do the same thing.

Sometimes a full-screen game does it too: it sweeps everything off your other
monitors onto the primary and leaves it there when you quit. A game taking exclusive full screen
changes the display configuration and changes it straight back, and Windows can
rearrange your desktop in between.

That one is less predictable — the same game on the same machine scattered
windows once here and left them alone an hour later. Sleep is the dependable
case.

One limit worth knowing: the snapshot lives in memory, so it survives sleep,
display changes and unplugging, but not a reboot. After restarting, arrange
things once and it learns from there.

Phantom Monitor takes a snapshot of where every window is, notices the display
event, waits for Windows and the game to finish shuffling, and puts back
anything that moved. Windows you moved yourself are left alone — it only
restores what the change displaced.

Desktop icons get the same treatment, since Windows recalculates the icon grid
on a display change and never restores it either.

Both are on by default. **Settings and more ▸ Layouts** has the switches, plus
manual save and restore.

## Saving an arrangement

Set a screen up the way you like — chat down one side, a couple of browser
windows filling the rest — then **Settings and more ▸ Save this arrangement
as...**, pick which display, and give it a name. *Streaming*, *Accounting*,
whatever fits.

Later, **Arrange windows like...** and pick the name. Or **Set up like...,
opening what is missing**, which starts anything that is not running first.

**Save per display.** A primary screen is usually a free-for-all nobody wants
recorded, while a side screen is deliberately laid out. Saving one display into
an existing name replaces only that display's part of it, so a mode can be built
up one screen at a time. *Every display* is there if you want the lot.

It saves *shapes per app*, not particular windows. "Two Brave windows go in
these two rectangles" rather than "this exact window goes here". Which browser
lands in which slot is chance, and does not matter: you navigate to what you
want in each one anyway. It means the arrangement survives a reboot, where
window handles do not, and it does not break when a page title changes.

- **Windows are gathered, not just tidied.** Arranging a display pulls that
  app's windows in from wherever they are, so Discord comes home even if it was
  on another screen. Handy for "set my streaming screen up", but it means a
  browser window you wanted left elsewhere can be collected too — the slots
  only know "a brave.exe window", not which one you cared about.
- **Undo.** *Settings and more ▸ Undo that arrangement* puts the windows it
  moved back where they were. It records only the ones it is about to touch,
  just before touching them, so anything it collected from another screen goes
  home. Available until you arrange again or restart the app.
- Extra windows beyond the slots are left where they are
- Missing apps just leave their slot empty
- Positions are relative to each display, so moving a monitor does not spoil it
- If a display from the saved arrangement is not attached, its windows are
  skipped

### Opening what is missing

**Settings and more ▸ Set this screen up, opening what is missing** starts
anything the layout needs that is not already open, waits for it, then arranges
everything. The executable is recorded when you save the arrangement.

It also handles apps sitting in the notification area. One of those cannot be
shown from outside — forcing its window visible gives an empty black frame,
because the app has suspended drawing while hidden. Running the program again
works, because single-instance apps respond by showing their own window
properly.

Kept separate from plain arranging on purpose: starting programs should never
happen as a side effect of tidying a screen.

### Triggering it from a Stream Deck or a script

```
PhantomMonitor.exe --arrange           all displays
PhantomMonitor.exe --arrange 2         just display 2
PhantomMonitor.exe --arrange 2 --open  open what is missing first
```

That asks the *running* copy to do it rather than starting a second one, so it
returns immediately and nothing flashes on screen. The number is the display's
slot as shown in Settings.

With `--open`, a Stream Deck button is a single action: `--arrange 2 --open`
starts whatever is missing, waits for it and lays the screen out. Windows
already open are simply moved, so pressing it twice is harmless.

This is separate from the automatic restore, which lives in memory and fixes
what a display change moved. The saved arrangement is on disk and is yours to
apply whenever.

## Block rules

A rule is a hardware id, optionally qualified by size. Settings shows the id
beside each display, and **Diagnose my displays...** lists them too.

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

**Do not guess from the badge.** Two amps of the same make can behave
completely differently, and only two units have been tested in total. Let the
app work it out instead: right-click the tray icon and choose **Which rule
should I use?**

**Quick way to tell what your amp is doing.** With nothing plugged in behind it,
open Windows display settings, select that display and look at the resolution
list. The mode marked **(Recommended)** is the one the amp is asking for — it is
the preferred timing out of its EDID. If that says 1920x1080 with no screen
attached, the amp is not signalling anything and size alone will not help you.

### Which rule to use

Only needed if you sometimes use a screen behind the amp. If nothing is ever
back there, tick **Block windows and pointer** for it and skip this section.

Right-click the tray icon and choose **Diagnose my displays...**. It opens a
report naming every display, what each one is *asking for*, and the rule to try
for anything that looks like an amp. It also copies the report to your
clipboard, ready to paste into an issue.

(The same thing from a terminal, if you prefer:
`PhantomMonitor.exe --diag`.)

The ladder it works down:

| If the amp | Rule | Automatic? |
|---|---|---|
| shows the screen's id when one wakes up | plain id | yes |
| asks for an **interlaced** mode with nothing behind it | `@interlaced` | yes |
| sits at a small resolution you set yourself | `@<1280x720` | yes |
| does none of these | plain id, and untick **Block** for it when you want the screen | no |

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

**Please add yours.** Use **Diagnose my displays...** twice — once with a
screen awake behind the amp, once a few minutes after switching that screen off
— and paste both into an issue. It copies itself to the clipboard each
time. The "it asks for" line is the one that matters. Every model added is one
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

Check yours rather than assuming. Use **Diagnose my displays...** with a screen
awake behind the amp, switch that screen off, **give it a few minutes**, and look
again.

The wait matters. An amp keeps serving the last screen's details for a while
after it goes dark, so looking straight away shows no change and tells you
nothing. On the Denon AVR-790 it took **about a minute** from pressing the
monitor's power button to blocking resuming. Other hardware may take longer, so
if the first re-check shows nothing, leave it five minutes and look once more
before concluding there is no signal.

## Dedicating a screen to one app

Say you want one screen to be *the Discord screen*, or the dashboard, or the
chat monitor for a stream. Two steps:

1. Put the app on that screen, then right-click the tray icon and choose
   **Always open discord.exe on this screen**. (The menu names whatever app you
   used last, so put it in front first.)
2. Tick **Block windows and pointer** for that display, in Settings or from the
   tray.

From then on that screen holds only what you pinned to it. Discord opens there
every time, nothing else lands there, and the app is never moved off it.

**You can still use that screen normally.** Click on it, type on it, drag things
around on it. Blocking stops *other* windows arriving; it does not lock you out.
The pointer fence skips any display with something pinned to it, precisely so a
chat monitor is one you can answer on.

Placement happens when a window **opens**. Drag it somewhere else afterwards and
it stays where you put it — the pin decides where things start, it does not
police them.

**Do you even need the pin?** Most apps already remember where you left them, so
if the screen is not blocked, pinning adds little. Its real job is the exemption:
without it, blocking a screen empties it completely, including the app you wanted
there. Pin things you are blocking around, and apps that forget their position
or get dumped on the primary by a display change.

<details>
<summary>Doing it by hand in config.json</summary>

```json
"app_displays": { "discord.exe": "GSM7814", "obs64.exe": "GSM7814" }
```

Several apps can share one screen. Hardware ids are shown in Settings beside
each display.

</details>

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

## If you are looking for this by symptom

Windows opening on an invisible monitor. Windows moving to the main monitor
when a game launches. Windows not going back after quitting a game. Desktop
icons rearranged after a resolution change. Mouse disappearing onto a screen
that is not there. AV receiver showing as a second display. Denon, Yamaha,
Onkyo, Marantz, Pioneer or soundbar appearing in Display Settings. HDMI audio
creating a fake monitor.

## Licence

MIT. Written by Raymond Pierce, co-authored with Claude Opus 5.

Bug reports are welcome, especially reports from receivers other than the two
tested here — the tray menu's **Diagnose my displays...** copies everything
needed.
