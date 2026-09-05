# How it works

## Why an amp becomes a display

A PC recognises a display if, and only if, it can read a valid EDID over the
DDC lines. Not if a screen is on. Not if a screen exists.

The handshake uses two pins. The source puts +5 V on pin 18; the sink returns
it on pin 19, Hot Plug Detect. Once HPD is asserted the source reads the sink's
capabilities. No voltage on HPD means disconnected.

An AV receiver is an HDMI **repeater**. Its job is to read the EDID of whatever
is downstream and write that into its own upstream registers, so the PC
transmits to suit the real display. That is why Windows shows the TV's hardware
id rather than the amp's whenever a TV is awake behind it. The amp is
impersonating the TV, and that is correct behaviour.

When the screen goes away the amp substitutes an EDID of its own, then drops
HPD and re-asserts it to force the PC to re-read. The documented sequence is:
pull HPD low, wait ~100 ms, load the new EDID, take it high. To Windows that is
indistinguishable from unplugging the cable and plugging it back in, so it
re-enumerates every output. That is why all your monitors blank and relay for a
second.

Source: Analog Devices, *EDID: Extended Display Identification Data* (HDMI FAQ,
EngineerZone).

## The timing is asymmetric

| Direction | Delay | Why |
|---|---|---|
| Screen **on** | seconds | HPD fires, the real EDID is read at once |
| Screen **off** | a while — varies | Nothing fires; the amp ages out its cache |

The off direction varies by hardware and is worth measuring rather than
guessing. On the **Denon AVR-790**, timed with a stopwatch from pressing the
monitor's power button to the pointer being fenced again: **1 minute 5
seconds**, cross-checked against the guard log. The panel itself took about a
second to actually power down after the button, so the interval the amp saw was
a second shorter. The Yamaha appeared to take longer but was never timed from
the button, so no figure is claimed for it.

Every other duration this project quoted was the gap between two log lines,
which is not the same as the time since somebody switched a screen off. Three
such figures were published and withdrawn before anyone thought to hold a
stopwatch.

Switching a screen on releases blocking almost immediately. Switching it off
waits out the amp's cache, so there is a window where the phantom is not yet
blocked. It corrects itself.

How long is firmware's decision. It is not specified anywhere, and receivers
differ. Measure your own with `--diag` rather than trusting a number.

## What the amp does when a screen wakes up

Two behaviours, both measured on native HDMI ports with a screen plugged in and
unplugged:

| Amp | Nothing behind it | Screen awake behind it | Signal |
|---|---|---|---|
| Yamaha HTR-4063 | `YMH3148` | `TSB0210` — the TV's own id | the **id** |
| Denon AVR-790 | `DON0015`, EDID prefers 1920x1080i | `DON0015`, EDID prefers 1920x1080 | **none** |

The Yamaha passes the downstream EDID upstream, so Windows sees the TV rather
than the amp. Its resolution is identical in both states, so no size rule would
ever have caught it. The id is the whole signal.

The Denon does not pass it through, and offers nothing else either. Plugging a
monitor in left the id and the name untouched — still `DON0015`, still
"DENON-AVAMP". Reading its EDID directly in both states shows it advertising
1920x1080 whether or not anything is attached; with nothing behind it the
preferred timing is 1920x1080 **interlaced** at 74.25 MHz, which is a broadcast
TV timing, because a TV is what it expects downstream.

So passing EDID upstream is what a repeater is *specified* to do, and not what
they all do. Where an amp does not, there may be no automatic signal at all, and
a manual tick is the honest answer.

**Treat both results as per model, not per brand.** These are two units, one of
each kind, and the one giving no signal is the older of the two. A modern amp
has to agree 4K, HDR and eARC settings with the real television, which gives it
more reason to hand the television's details straight through. Nothing here
predicts what a different Denon, or a different Yamaha, will do.

### A warning about resolution as a signal

This project spent a long time believing the Denon "dropped to 800x600 when
nothing was attached", and built a whole rule form around it. It does not. The
test machine simply had that display *set* to 800x600 by hand years earlier, and
Windows was restoring the chosen mode.

The mistake survived because Windows' reported resolution was taken as the
amp's behaviour. What it is *set* to and what it *asks for* are different
things, and only the second tells you anything about the hardware.

You do not need to read EDID bytes to see the difference. In Windows display
settings, the resolution marked **(Recommended)** is the preferred timing from
the EDID — what the display is asking for. On the AVR-790 with nothing attached
that reads "1920 x 1080 (Recommended)" while the display is set to 800x600. The
gap between those two numbers is the whole story.

**Neither advertises a small "fallback" resolution.** Both offer modes up to
1920x1080 with nothing attached. Earlier notes claimed the Denon dropped to
800x600 on its own; it does not. That machine simply had the amp's display *set*
to 800x600 by hand, and Windows was restoring the chosen mode.

So size is a poor signal and identity is a good one. A size rule works only when
the phantom happens to sit at a resolution smaller than a real screen would use
— usually because you set it that way. Useful, but it is your configuration
doing the work, not the amp announcing itself.

`--diag` tells you which you have. Compare it with a screen awake behind the
amp, and again a few minutes after switching that screen off.

## What the cable path decides

| Path | When the screen is switched off |
|---|---|
| Direct DisplayPort | Vanishes. No phantom, but Windows scrambles the windows that were on it. |
| Direct HDMI | Varies. Many TVs hold the link in standby. |
| Through an AV receiver | Cached EDID for some minutes, then its own. A rule tracks this once the timeout passes. |
| Through an adapter chain | Nothing changes at all. |
| TV with quick-start or CEC | The input stays powered, so the link never drops. |

Two separate things make a dark screen look present, and they are easy to
confuse.

**Something holds the link up.** Usually the display's own standby. To tell,
pull the screen's mains plug: if it then disappears, it was the screen's
quick-start setting. If it persists, something in the middle is holding it.

**EDID can be read with no power at all.** EDID is often held in a non-volatile
EEPROM wired to the DDC lines, whose pull-up is supplied over the link. Where a
display is wired that way, nothing you do to its power will make it disappear,
and no software can detect it. A manual tick is the only answer.

## Why DDC/CI does not save us

DDC/CI could be asked "are you switched on" directly. It rarely answers:

- Most TVs never implement it. It is a monitor feature.
- Receivers, converters, switches and KVMs do not pass the I2C channel through.

`--diag` reports the DDC/CI power state per display. If a receiver ever answers
with something other than "not supported", please open an issue — it would mean
a screen switched off behind it can be detected without waiting out the
timeout.

## Why evacuation, not prevention

Windows has no API to reserve a display or veto a window's placement. Nothing
can say "never put anything here". The only approach available is to notice a
window arriving and move it, which is done from an out-of-context
`SetWinEventHook` and completes in under 10 ms.

Since a valid EDID *is* a monitor as far as Windows is concerned, there is
nothing for Windows to distinguish. It is not misbehaving.

---

[Back to the README](../README.md)
