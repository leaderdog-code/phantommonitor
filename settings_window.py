"""The settings window.

Runs as its own process, launched with --settings, for two reasons: Tk wants to
own an event loop and the tray app already runs a Windows message pump, and a
crash in the settings UI should not take the guard down with it. It reads and
writes config.json; the tray app reloads when this exits.
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import ttk


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save(path, cfg):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2)


# Combinations that must never be bound. A matched hotkey is SWALLOWED before it
# reaches the focused application, so binding ctrl+c would stop copy working
# everywhere on the machine with no visible cause. Anything with a single
# modifier is refused outright - that is where the universal shortcuts live -
# along with a few two-modifier ones the system already owns.
RESERVED = {
    "ctrl+shift+esc",   # Task Manager
    "ctrl+alt+delete",  # cannot be hooked anyway
    "alt+tab", "alt+esc", "alt+space", "alt+f4",
    "win+l", "win+d", "win+e", "win+r", "win+tab",
}


# Kept in step with phantommonitor.py. Duplicated for the same reason
# unsafe_hotkey is: this runs as its own process, where importing the main
# module would re-execute it.
AV_VENDORS = {
    "DON": "Denon", "YMH": "Yamaha", "ONK": "Onkyo", "PIO": "Pioneer",
    "MAR": "Marantz", "HAR": "Harman", "INT": "Integra", "NAD": "NAD",
    "ARC": "Arcam", "SON": "Sony AV", "TEA": "TEAC",
}
AV_NAME_HINTS = ("avamp", "av amp", "receiver", "avr", "amplifier", "soundbar",
                 "htr-", "rx-v", "vsx-", "sr-", "extractor", "splitter")


def looks_like_av_device(hwid, name, declared=(), denied=()):
    """A guess at whether a display is really an amp, switch or extractor.

    Only ever used to suggest, never to block anything on its own. An amp names
    itself in its EDID - "DENON-AVAMP", "HTR-4063" - which is a far better
    signal than resolution, since a Yamaha advertises a full 1920x1080 with
    nothing attached to it at all.
    """
    if hwid and hwid in (denied or ()):
        return None
    if hwid and hwid in (declared or ()):
        return "marked by you as an amp or adapter"
    vendor = AV_VENDORS.get((hwid or "")[:3].upper())
    if vendor:
        return vendor
    lowered = (name or "").lower()
    if any(hint in lowered for hint in AV_NAME_HINTS):
        return "AV device"
    return None


def unsafe_hotkey(spec):
    """Return why a combination must not be bound, or None if it is fine.

    Duplicated in phantommonitor.py rather than shared: this runs as its own
    process, where importing the main module would re-execute it.
    """
    spec = (spec or "").strip().lower()
    if not spec:
        return None
    parts = [p for p in spec.split("+") if p]
    mods = [p for p in parts if p in ("ctrl", "alt", "shift", "win")]
    if len(parts) < 2 or not mods:
        return "needs at least one modifier"
    if len(mods) < 2:
        return "needs two modifiers, or it would swallow a shortcut everything uses"
    if spec in RESERVED:
        return "Windows or every application already uses it"
    return None


def run(config_path, monitors, absent=()):
    """monitors: list of (name, hwid, width, height, x, y, primary).

    absent: [(hwid, name)] for displays Windows has seen before but that are
    not plugged in now. A pass-through amp hides its own id whenever a screen is
    awake behind it, and that id is exactly the one worth blocking - so it has
    to be settable while it is nowhere to be seen.
    """
    cfg = load(config_path)
    root = tk.Tk()
    root.title("Phantom Monitor settings")
    root.resizable(False, False)
    try:
        root.iconbitmap(os.path.join(os.path.dirname(config_path), "app.ico"))
    except Exception:
        pass

    pad = {"padx": 10, "pady": 4}
    slot_count = max(4, len(monitors))

    # --- displays -----------------------------------------------------------
    box = ttk.LabelFrame(root, text="Displays")
    box.grid(row=0, column=0, sticky="ew", **pad)

    ttk.Label(box, text="Block windows\nand pointer", justify="center").grid(
        row=0, column=1, padx=8, pady=(6, 2))
    ttk.Label(box, text="Hotkey slot").grid(row=0, column=2, padx=8, pady=(6, 2))
    ttk.Label(box, text="Amp, not\na display", justify="center").grid(
        row=0, column=3, padx=8, pady=(6, 2))

    rules = list(cfg.get("blocked_hwids") or [])
    parked = list(cfg.get("blocked_rules_parked") or [])
    targets = dict(cfg.get("hotkey_targets") or {})
    declared = list(cfg.get("av_devices") or [])
    denied = list(cfg.get("not_av_devices") or [])

    def rule_for(hwid):
        for spec in rules:
            if spec.split("@")[0].strip() == hwid:
                return spec
        return None

    def assigned_slot(hwid):
        for key, value in targets.items():
            if value == hwid and key.isdigit():
                return int(key)
        return 99  # unassigned displays sink to the bottom

    # Read in slot order, so the list matches the keys you actually press.
    # Sorted on open rather than live, or rows would jump about mid-edit.
    ordered = sorted(monitors, key=lambda m: (assigned_slot(m[1]), m[0]))

    block_vars, slot_vars, av_vars = {}, {}, {}
    # Which ids the built-in vendor list recognised on its own. The tick is
    # shown for these too - leaving it clear would contradict the "an amp or
    # adapter" line beside it - but it stays clickable, because a three-letter
    # prefix is a guess and the person looking at the hardware overrules it.
    auto_av = set()
    for row, (name, hwid, w, h, x, y, primary) in enumerate(ordered, start=1):
        hint = looks_like_av_device(hwid, name, declared, denied)
        text = "%s\n%d×%d  [%s]  at %d,%d%s%s" % (
            name, w, h, hwid, x, y, "   ★ primary" if primary else "",
            ("\n%s — an amp or adapter, not a display" % hint) if hint else "")
        ttk.Label(box, text=text, justify="left").grid(
            row=row, column=0, sticky="w", padx=(10, 4), pady=3)

        bvar = tk.BooleanVar(value=rule_for(hwid) is not None)
        block_vars[hwid] = bvar
        ttk.Checkbutton(box, variable=bvar).grid(row=row, column=1)

        current = next((k for k, v in targets.items() if v == hwid), "")
        svar = tk.StringVar(value=current or "-")
        slot_vars[hwid] = svar
        ttk.Combobox(box, textvariable=svar, width=4, state="readonly",
                     values=["-"] + [str(i) for i in range(1, slot_count + 1)]).grid(
            row=row, column=2, padx=8)

        if hint and hwid not in declared:
            auto_av.add(hwid)
        avar = tk.BooleanVar(value=bool(hint))
        av_vars[hwid] = avar
        ttk.Checkbutton(box, variable=avar).grid(row=row, column=3)

    ttk.Label(box, text="Marking something as an amp only labels it - nothing is\n"
                        "blocked on that basis. Tick it for an amp we do not know\n"
                        "about; untick a wrong guess. Please report unlisted kit so\n"
                        "others benefit.\n"
                        "Listed by hotkey slot. A slot is yours to assign - Windows'\n"
                        "own display numbers cannot be read back, so they are not\n"
                        "used here.",
              foreground="#555").grid(row=len(monitors) + 1, column=0, columnspan=4,
                                      sticky="w", padx=10, pady=(2, 8))

    # --- displays seen before but not attached now --------------------------
    absent_vars = {}
    if absent:
        seen = ttk.LabelFrame(root, text="Seen before, not connected now")
        seen.grid(row=1, column=0, sticky="ew", **pad)
        for row, (hwid, name) in enumerate(absent):
            hint = looks_like_av_device(hwid, name, declared)
            label = "%s   [%s]%s" % (name, hwid,
                                     ("   %s" % hint) if hint else "")
            ttk.Label(seen, text=label).grid(row=row, column=0, sticky="w",
                                             padx=10, pady=2)
            var = tk.BooleanVar(value=rule_for(hwid) is not None)
            absent_vars[hwid] = var
            ttk.Checkbutton(seen, text="Block", variable=var).grid(
                row=row, column=1, padx=10)
        ttk.Label(seen, text="An amp that passes a screen's EDID through shows the\n"
                             "screen's id while it is awake and its own when it is\n"
                             "not - so its own id is set here, in advance.",
                  foreground="#555").grid(row=len(absent), column=0, columnspan=2,
                                          sticky="w", padx=10, pady=(2, 8))

    # --- behaviour ----------------------------------------------------------
    opts = ttk.LabelFrame(root, text="Behaviour")
    opts.grid(row=2, column=0, sticky="ew", **pad)

    toggles = [
        ("enabled", "Keep windows off blocked displays  (master switch)"),
        ("block_cursor", "Keep the mouse pointer off blocked displays"),
        ("restore_windows", "Put windows back after a display change"),
        ("restore_icons", "Put desktop icons back after a display change"),
        ("leave_fullscreen", "Ask full-screen apps to leave full-screen before moving them"),
        ("intercept_hotkeys", "Catch hotkeys from apps that swallow them (Remote Desktop)"),
    ]
    toggle_vars = {}
    for row, (key, text) in enumerate(toggles):
        var = tk.BooleanVar(value=bool(cfg.get(key, True)))
        toggle_vars[key] = var
        ttk.Checkbutton(opts, text=text, variable=var).grid(
            row=row, column=0, sticky="w", padx=10, pady=2)

    # --- hotkeys ------------------------------------------------------------
    #
    # Typed, not recorded by pressing. Capturing keystrokes means intercepting
    # everything typed into the box, and the edge cases never end: ctrl+alt is
    # AltGr so it produces a symbol rather than the key, plus dead keys,
    # non-Latin layouts and IMEs. A text field with an example is duller and
    # works. It is also validated on Save rather than as you type, so a stray
    # keystroke cannot quietly bind something.
    keys = ttk.LabelFrame(root, text="Hotkeys")
    keys.grid(row=3, column=0, sticky="ew", **pad)
    warning = tk.StringVar(value="")

    hotkeys = dict(cfg.get("hotkeys") or {})
    entries = {}
    rows = [("rescue", "Rescue windows (also recovers off-screen ones)")]
    rows += [("monitor_%d" % i, "Move window to slot %d" % i)
             for i in range(1, slot_count + 1)]
    for row, (key, text) in enumerate(rows):
        ttk.Label(keys, text=text).grid(row=row, column=0, sticky="w", padx=10, pady=2)
        var = tk.StringVar(value=hotkeys.get(key, ""))
        entries[key] = var
        ttk.Entry(keys, textvariable=var, width=22).grid(row=row, column=1, padx=10)

    example = (
        "Type these out. For example:   ctrl+alt+shift+1\n"
        "Modifiers: ctrl, alt, shift, win  -  at least two are required.\n"
        "Keys: a-z, 0-9, f1-f24, left, right, up, down, home, end, pageup,\n"
        "pagedown, space, insert, delete, esc, tab.  Leave blank to disable.\n"
        "A matched combination is swallowed, so avoid ones your games use."
    )
    ttk.Label(keys, text=example, foreground="#555", justify="left").grid(
        row=len(rows), column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2))
    ttk.Label(keys, textvariable=warning, foreground="#b00").grid(
        row=len(rows) + 1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))

    def apply_and_close():
        new_hotkeys = dict((k, v.get().strip()) for k, v in entries.items())
        for _key, spec in sorted(new_hotkeys.items()):
            problem = unsafe_hotkey(spec)
            if problem:
                # Leave the window open so it can be corrected.
                warning.set("%s  -  %s" % (spec, problem))
                return

        new_rules, new_parked = [], list(parked)
        for _n, hwid, _w, _h, _x, _y, _p in monitors:
            existing = rule_for(hwid)
            if block_vars[hwid].get():
                # Re-ticking restores a parked rule verbatim, qualifier and all.
                revived = next((s for s in new_parked
                                if s.split("@")[0].strip() == hwid), None)
                new_rules.append(existing or revived or hwid)
                new_parked = [s for s in new_parked
                              if s.split("@")[0].strip() != hwid]
            elif existing:
                new_parked = [s for s in new_parked
                              if s.split("@")[0].strip() != hwid] + [existing]
        # Displays that are not attached: keep whatever the user ticked here,
        # and leave rules for anything not shown at all untouched.
        shown = set(m[1] for m in monitors) | set(absent_vars)
        for hwid, var in absent_vars.items():
            existing = rule_for(hwid)
            if var.get():
                new_rules.append(existing or hwid)
            elif existing:
                new_parked = [s for s in new_parked
                              if s.split("@")[0].strip() != hwid] + [existing]
        new_rules += [s for s in rules if s.split("@")[0].strip() not in shown]

        new_targets = {}
        for _n, hwid, _w, _h, _x, _y, _p in monitors:
            slot = slot_vars[hwid].get()
            if slot and slot != "-":
                new_targets[slot] = hwid

        cfg["blocked_hwids"] = new_rules
        cfg["blocked_rules_parked"] = new_parked
        cfg["hotkey_targets"] = new_targets
        # Two lists, because a tick can mean two different things. Ticking
        # something the built-in list already knows adds nothing, so only a
        # genuinely new declaration is stored. Unticking something it DID
        # recognise is a real decision and has to be remembered, or the guess
        # would simply come back next time the window opened.
        shown = set(av_vars)
        ticked = set(h for h, v in av_vars.items() if v.get())
        cfg["av_devices"] = sorted(h for h in ticked if h not in auto_av)
        cfg["not_av_devices"] = sorted(
            (set(denied) - shown)                       # not attached now
            | ((shown - ticked) & (auto_av | set(denied))))
        for key, var in toggle_vars.items():
            cfg[key] = bool(var.get())
        cfg["hotkeys"] = new_hotkeys
        save(config_path, cfg)
        root.destroy()

    buttons = ttk.Frame(root)
    buttons.grid(row=4, column=0, sticky="e", padx=10, pady=(4, 12))
    ttk.Button(buttons, text="Cancel", command=root.destroy).grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text="Save", command=apply_and_close).grid(row=0, column=1)

    root.bind("<Escape>", lambda _e: root.destroy())
    root.update_idletasks()
    # Centre on the primary display.
    primary = next((m for m in monitors if m[6]), monitors[0] if monitors else None)
    if primary:
        _n, _i, pw, ph, px, py, _p = primary
        root.geometry("+%d+%d" % (px + (pw - root.winfo_width()) // 2,
                                  py + (ph - root.winfo_height()) // 3))
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()
