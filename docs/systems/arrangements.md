---
subsystem: arrangements
owns: phantommonitor.py, settings_window.py, test_phantommonitor.py
anchors: _apply_arrangement, _undo_arrangement, _gather_slots, _store_mode, _load_modes, _save_modes, _show_arrange_menu, _ask_for_fullscreen, fill_slots, missing_launches, offset_onto, exe_path, ask_for_name, ARRANGEMENT_PATH, ARRANGEMENT_UNDO_PATH, WM_APP_ARRANGE, build_arrangements_tab
test: `python test_phantommonitor.py` from the project root. STOP the tray app first — it refuses to run while `PhantomMonitorWnd` exists (test_phantommonitor.py:11-15), and needs two real displays (33-35). Covers only the pure helpers: `fill_slots` 628-649, `missing_launches` 588-608, `offset_in`/`offset_onto` 653-679. Nothing covers `_gather_slots`, `_apply_arrangement`, `_store_mode` or the undo — verify by hand: save a layout, scatter the windows, re-apply.
---
# Arrangements — named window layouts on disk, applied on demand

## What it does
Saves the current window layout under a name ("Stream", "Accounting", "base") and puts it back later
— after a reboot, or when switching jobs. Distinct from the in-memory window snapshot, which answers
"what did that display change just move?" and dies with the session (phantommonitor.py:3233-3237).

A layout is a list of **slots**, one per window, each holding the exe name, the **display's hardware
id**, and the rect **relative to that display** — never absolute coordinates (`_gather_slots`
3337-3369, `offset_in` 821). Slots match live windows **by app, not by handle**: "two brave.exe
windows go in these two rectangles" (`fill_slots` 848-872). Handles die at reboot, titles change.

## Where it lives
- **On disk** — `ARRANGEMENT_PATH` = `arrangement.json` in `DATA_DIR` (phantommonitor.py:70),
  `ARRANGEMENT_UNDO_PATH` = `arrangement_undo.json` (71). Real shape, from the live files:
  ```
  {"modes": {"<name>": {"slots": [
      {"app": "brave.exe", "hwid": "ABC1234", "rel": [x, y, w, h], "exe": "C:\\...\\brave.exe"},
      {"app": "mstsc.exe", "hwid": "ABC1234", "rel": [0,0,2560,1440], "exe": "...", "full": true}]}}}
  ```
  `exe` and `full` are optional. The undo file is the **legacy** shape — bare `{"slots": [...]}`,
  no `exe`, no `full` (`_write_arrangement` 3264-3265).
- **Legacy migration** — `_load_modes` 3238-3249: a file with a top-level `slots` key and no `modes`
  is read as one mode named `"Saved layout"` (3247) and rewritten in the `modes` shape next time
  anything is saved. The same migration exists again in settings_window.py:210-220 — two copies.
- **Save** — `_save_arrangement` 3276-3296 → `_name_then_save` 3298-3313 → `_store_mode` 3315-3335.
  Slots are gathered *before* the name is asked (3283), so what is saved is the screen as it was when
  the menu was clicked. The prompt runs in a **child process** (`ask_for_name` 3837, `--ask-name` in
  `main` 4027-4030): a modal Tk dialog inside the tray app would stall the message pump that runs
  evacuation, the fence and the hotkeys.
- **Apply** — `_apply_arrangement` 3371-3482. `_undo_arrangement` 3484-3486 is apply with
  `path=ARRANGEMENT_UNDO_PATH`.
- **Tray** — `_show_menu` 2922-2959 (save per display, apply, "…opening what is missing", undo).
  Middle-clicking the icon opens `_show_arrange_menu` 2811-2862, wired at `_wnd_proc` 3582-3586.
- **Settings** — `build_arrangements_tab` settings_window.py:182-342, added at 659. Lists, renames and
  deletes directly in the JSON, but **never moves a window itself**: Apply posts `0x8000+5`, Undo
  posts `0x8000+6` to `PhantomMonitorWnd` (settings_window.py:308-312, 322-326).
- **CLI** — `--arrange [n] [--open]` in `main` 4032-4046 posts `WM_APP_ARRANGE` to the running copy
  rather than starting a second one. wparam is a 1-based display index (an EDID will not fit in a
  window message); lparam 1 means launch. Handled at `_wnd_proc` 3612-3618.
- **Messages** — `WM_APP_ARRANGE` 210, `WM_APP_ARRANGE_NAMED` 211, `WM_APP_UNDO_ARRANGE` 212;
  branches at 3612, 3621, 3636. A *name* cannot ride in a window message, so it goes into
  `arrangement.json.apply`, which the handler reads and deletes (3625-3630).

## CASCADE — a change here must reach ALL of these
- On-disk shape: `_load_modes` 3238, `_save_modes` 3251, `_load_arrangement` 3256,
  `_write_arrangement` 3264 **and** the duplicate `read`/`write` in settings_window.py:210-227.
- Slot fields: `_gather_slots` 3337 writes them; `_apply_arrangement` 3438-3476, `fill_slots` 848,
  `missing_launches` 895 and `describe` (settings_window.py:243-258) read them.
- Message numbers: 210-212 here **and** the hardcoded `0x8000 + 5` / `0x8000 + 6` at
  settings_window.py:312 and 325 — magic numbers there, not imports.
- Both menus list the modes: `_show_menu` 2922-2959 and `_show_arrange_menu` 2811-2862.
- Tests test_phantommonitor.py 588-608, 628-649, 653-679; docs README.md 114-196 and
  docs/configuration.md:131 (the `--arrange` row).
- `_ask_for_fullscreen` 2210-2260 is shared with the display-change restore (2368-2370) — do not
  change its signature for arrangements alone. Both callers now collect `(hwnd, hwid)` tuples and
  pass `want_hwid`; keep them symmetrical. It returns nothing, but logs a warning when the app
  could not be focused, so a window left windowed is visible in the log rather than silent.

## Rules (invariants)
1. **A slot is relative + a hardware id, never absolute.** `offset_in` 821 on save, `offset_onto` 827
   on apply. The same monitor moves — dragged to the other side in Display Settings, re-plugged,
   made primary — and every absolute desktop coordinate shifts (827-838).
2. **`offset_onto` clamps to the work area** (839-846), so a resolution change cannot strand a window
   off the edge. `full` slots deliberately bypass that clamp (3444-3449).
3. **A `full` slot means full-screen, not "a rect the size of the display."** Recorded when a window
   is app-placed and covers ≥98% of its monitor (3361-3367), applied at the display's **full bounds**
   including the taskbar (3444-3449), then `_ask_for_fullscreen` on a thread (3477-3479).
4. **`_ask_for_fullscreen` must wait for the window to land first.** It polls up to 6 s for it to be
   on `want_hwid` before sending the app's shortcut (2225-2241), then gives up with a log line
   rather than guessing. See Traps.
5. **A display that is not attached is skipped**, never faked (`by_hwid` → None → `continue`,
   3439-3441). Extra windows are left alone; missing apps leave empty slots (`fill_slots` 859-872).
6. **A minimized window is arranged without being opened** — `SetWindowPlacement` on
   `rcNormalPosition` (workspace coords, hence `workspace_offset`), not `SetWindowPos` (3456-3465).
7. **The undo is on disk**, written only when something moved (3481). A restart must not throw it
   away, and a handle means nothing after a reboot.
8. **Launching is never a side effect of arranging.** `launch=True` only from "…opening what is
   missing" (2942-2946, 2850-2854) or `--open` (4044).

## Reuse, don't rebuild
- Read/write layouts: `_load_modes` 3238 / `_save_modes` 3251. Do not re-parse the JSON.
- Pair slots to live windows: `fill_slots` 848. Coordinates: `offset_in` 821 / `offset_onto` 827.
- Start what is missing: `missing_launches` 895 + `exe_path` 874; the cap of 3 per app (913) stops a
  bad layout spawning a hundred windows.
- Ask for a string: `ask_for_name` 3837 via `--ask-name`. Yes/no: `confirm_box` 3986.
- Ask an app to go full-screen: `_ask_for_fullscreen` 2210 — never type a key combo yourself.
- Trigger an arrange from outside the guard: post `WM_APP_ARRANGE*` (210-212), as Settings and the
  CLI do. Only the guard thread touches windows.

## Traps
- **"Arrangements must not touch full-screen windows" was the wrong fix** (8b69b69, reverted by
  3bde901). Skipping app-placed windows did stop the two-taskbars bug, but it also removed what the
  layout was being used for — forcing an RDP session back onto the display it belongs on. The real
  bug was only the work-area clamp. Lesson: when a helper misbehaves for one case, fix it for that
  case; do not amputate the capability.
- **Sending the full-screen key combo too early put RDP full-screen on the wrong display** (adbefe0 →
  reverted 1965914 → redone properly 2ce34ca). The first attempt slept 1.5 s and typed regardless;
  the session went full-screen on the 4K panel and resized itself to 3840x2160 — unreadable. Hence
  the poll at 2225-2241. Never reduce it to a fixed sleep.
- **Arranging gathers windows in from other screens** — the point, and why undo exists (802c03a). A
  brave.exe window you wanted left elsewhere gets collected: slots know "a brave.exe window", not
  which one you cared about.
- **Undo is a toggle, not a one-shot.** It runs through `_apply_arrangement`, which writes a fresh
  undo at 3481 — pressing Undo twice redoes. The file is never deleted, so the menu item (2857,
  2952) and the Settings button (`undo_state`, settings_window.py:329-333) stay enabled forever.
- **Saving one display into an existing name merges, it does not replace** (3326-3330) — deliberate,
  so a mode can be built a screen at a time, but the confirm dialog at 3317-3325 says "Replace it?"
  and quotes the whole mode's count.
- **A pin and a saved arrangement can disagree** about where an app belongs, with no warning. See
  docs/systems/pinned-apps.md.

## Related subsystems
- **pinned-apps** (docs/systems/pinned-apps.md) — shares `offset_in`/`offset_onto`, can contradict an
  arrangement. A slot aimed at a blocked display is fought by `Guard.check_window` 1639 unless the
  app is pinned there.
- **Window restore after a display change** — in-memory `window_snapshot` / `_restore_windows` 2252+
  and `_ask_for_fullscreen` 2210: another mechanism, another question.


LAST VERIFIED: 2026-10-24 · aa2db0f · every file:line above read in the working tree; both JSON files read on disk and their real shape (`full`, `exe`) transcribed; the legacy-to-`modes` migration found at phantommonitor.py:3238-3249 and again at settings_window.py:210-220; commits 8b69b69, 3bde901, adbefe0, 1965914, 2ce34ca read in full. Tests NOT run: the tray app is live and the suite refuses to run beside it.
