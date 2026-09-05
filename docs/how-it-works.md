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
| Screen **off** | minutes | Nothing fires; the amp ages out its cache |

Switching a screen on releases blocking almost immediately. Switching it off
waits out the amp's cache, so there is a window where the phantom is not yet
blocked. It corrects itself.

How long is firmware's decision. It is not specified anywhere, and receivers
differ. Measure your own with `--diag` rather than trusting a number.

## What the amp does when a screen wakes up

Passing the downstream EDID upstream is what a repeater is *specified* to do,
so expect it to be the normal case rather than a quirk of one brand.

Measured on a **Yamaha HTR-4063**, native HDMI: with a Toshiba awake behind it
Windows reports `TSB0210`; `YMH3148` appears only once the amp lets go. The
resolution was identical in both states. So the id is the signal, and a plain
rule on the amp's own id stands itself down when a screen wakes up.

A **Denon** was also tested, but only ever with nothing behind it, where it
reports `DON0015`. Earlier notes here said it "keeps its own identity whatever
is behind it" — that was never actually tested with a screen attached, and given
what a repeater is for, it is more likely to pass the screen's EDID through in
the same way. Treat the Yamaha result as the expected behaviour and check your
own.

**Neither advertises a small "fallback" resolution.** Both offer modes up to
1920x1080 with nothing attached. Earlier notes claimed the Denon dropped to
800x600 on its own; it does not. That machine simply had the amp's display *set*
to 800x600 by hand, and Windows was restoring the chosen mode.

So size is a poor signal and identity is a good one. A size rule works only when
the phantom happens to sit at a resolution smaller than a real screen would use
— usually because you set it that way. Useful, but it is your configuration
doing the work, not the amp announcing itself.

`--diag` tells you which you have. Compare it with a screen awake behind the
amp, and again several minutes after switching that screen off.

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
