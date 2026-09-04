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


# Tk key names -> the names the config uses.
KEYSYM_NAMES = {
    "Left": "left", "Right": "right", "Up": "up", "Down": "down",
    "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown",
    "space": "space", "Insert": "insert", "Delete": "delete",
    "Escape": "esc", "Tab": "tab", "Pause": "pause", "Cancel": "break",
}
# Windows virtual key codes. Tk reports these in event.keycode, and unlike the
# character they do not change with the keyboard layout or with AltGr.
VK_NAMES = {}
VK_NAMES.update(dict((0x30 + n, str(n)) for n in range(10)))            # 0-9
VK_NAMES.update(dict((0x41 + n, chr(ord("a") + n)) for n in range(26)))  # a-z
VK_NAMES.update(dict((0x70 + n, "f%d" % (n + 1)) for n in range(24)))    # F1-F24
VK_NAMES.update({
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x24: "home", 0x23: "end", 0x21: "pageup", 0x22: "pagedown",
    0x20: "space", 0x2D: "insert", 0x2E: "delete",
    0x1B: "esc", 0x09: "tab", 0x13: "pause", 0x03: "break",
})

MODIFIER_KEYSYMS = ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L",
                    "Shift_R", "Win_L", "Win_R", "Super_L", "Super_R")


def held_modifiers():
    """Read the modifier keys directly rather than from the Tk event.

    Tk's event.state bits for Alt and the Windows key are inconsistent across
    versions and layouts; asking Windows is unambiguous.
    """
    import win32api
    import win32con
    down = lambda vk: win32api.GetAsyncKeyState(vk) & 0x8000
    mods = []
    if down(win32con.VK_CONTROL):
        mods.append("ctrl")
    if down(win32con.VK_MENU):
        mods.append("alt")
    if down(win32con.VK_SHIFT):
        mods.append("shift")
    if down(win32con.VK_LWIN) or down(win32con.VK_RWIN):
        mods.append("win")
    return mods


def capture_hotkey(event, var):
    """Turn a real keystroke into a config string.

    Only captures when a modifier is held, so ordinary typing and pasting still
    work in the same box - press the combination to record it, or type it out if
    you would rather.
    """
    if event.keysym in MODIFIER_KEYSYMS:
        return "break"
    if event.keysym in ("Delete", "BackSpace") and not held_modifiers():
        var.set("")
        return "break"

    mods = held_modifiers()
    if not mods or mods == ["shift"]:
        return None  # plain typing - leave the entry alone

    # Prefer the virtual key code over the character. Ctrl+Alt is AltGr, so on
    # many layouts that combination produces a symbol rather than the key's own
    # character - ctrl+alt+shift+4 arrives as something that is not "4" at all.
    # The physical key is a 4 whatever the layout decides it should type.
    name = VK_NAMES.get(getattr(event, "keycode", None))
    if name is None:
        name = KEYSYM_NAMES.get(event.keysym)
    if name is None:
        if len(event.keysym) == 1 and event.keysym.isalnum():
            name = event.keysym.lower()
        elif event.keysym.startswith("F") and event.keysym[1:].isdigit():
            name = event.keysym.lower()
    if not name:
        return "break"

    var.set("+".join(mods + [name]))
    return "break"


def run(config_path, monitors):
    """monitors: list of (name, hwid, width, height, x, y, primary)."""
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

    rules = list(cfg.get("blocked_hwids") or [])
    parked = list(cfg.get("blocked_rules_parked") or [])
    targets = dict(cfg.get("hotkey_targets") or {})

    def rule_for(hwid):
        for spec in rules:
            if spec.split("@")[0].strip() == hwid:
                return spec
        return None

    block_vars, slot_vars = {}, {}
    for row, (name, hwid, w, h, x, y, primary) in enumerate(monitors, start=1):
        text = "%s\n%d×%d  [%s]  at %d,%d%s" % (
            name, w, h, hwid, x, y, "   ★ primary" if primary else "")
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

    ttk.Label(box, text="A hotkey slot is yours to assign - Windows' own display\n"
                        "numbers cannot be read back, so they are not used here.",
              foreground="#555").grid(row=len(monitors) + 1, column=0, columnspan=3,
                                      sticky="w", padx=10, pady=(2, 8))

    # --- behaviour ----------------------------------------------------------
    opts = ttk.LabelFrame(root, text="Behaviour")
    opts.grid(row=1, column=0, sticky="ew", **pad)

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
    keys = ttk.LabelFrame(root, text="Hotkeys")
    keys.grid(row=2, column=0, sticky="ew", **pad)
    hotkeys = dict(cfg.get("hotkeys") or {})
    entries = {}
    rows = [("rescue", "Rescue windows (also recovers off-screen ones)")]
    rows += [("monitor_%d" % i, "Move window to slot %d" % i)
             for i in range(1, slot_count + 1)]
    for row, (key, text) in enumerate(rows):
        ttk.Label(keys, text=text).grid(row=row, column=0, sticky="w", padx=10, pady=2)
        var = tk.StringVar(value=hotkeys.get(key, ""))
        entries[key] = var
        field = ttk.Entry(keys, textvariable=var, width=22)
        field.grid(row=row, column=1, padx=10)
        field.bind("<KeyPress>", lambda e, v=var: capture_hotkey(e, v))
    ttk.Label(keys, text="Click a box and press the combination you want — no need to\n"
                         "type it. Delete clears one. Typing still works if you prefer.\n"
                         "Matched combos are swallowed, so avoid ones your games use.",
              foreground="#555").grid(row=len(rows), column=0, columnspan=2,
                                      sticky="w", padx=10, pady=(2, 8))

    status = tk.StringVar(value="")
    ttk.Label(root, textvariable=status, foreground="#0a7").grid(
        row=3, column=0, sticky="w", padx=12)

    def apply_and_close():
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
        # Rules for displays that are not attached right now must survive.
        attached = set(m[1] for m in monitors)
        new_rules += [s for s in rules if s.split("@")[0].strip() not in attached]

        new_targets = {}
        for _n, hwid, _w, _h, _x, _y, _p in monitors:
            slot = slot_vars[hwid].get()
            if slot and slot != "-":
                new_targets[slot] = hwid

        cfg["blocked_hwids"] = new_rules
        cfg["blocked_rules_parked"] = new_parked
        cfg["hotkey_targets"] = new_targets
        for key, var in toggle_vars.items():
            cfg[key] = bool(var.get())
        cfg["hotkeys"] = dict((k, v.get().strip()) for k, v in entries.items())
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
