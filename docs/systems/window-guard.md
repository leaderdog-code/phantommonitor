---
subsystem: window-guard
owns: phantommonitor.py, test_phantommonitor.py
anchors: Guard, move_window, check_window, rescue_target, _leave_fullscreen_then_move, _move_minimized, window_rect_for_check, workspace_offset, move_active_to, window_displaced, is_manageable, monitor_of_rect, covers_monitor, is_user_movable, placed_rect, centred_rect, windowed_rect, restore_frame, FULLSCREEN_TOGGLES, send_key_combo, SKIP_CLASSES, pick_menu_target, unfullscreen_borderless, leave_fullscreen, fix_minimized_restore_position, windowed_fraction, locationchange_cooldown_ms
test: `python test_phantommonitor.py` — STOP the tray app first (it rescues the fixtures mid-assertion; the suite exits 2 on finding `PhantomMonitorWnd`, test_phantommonitor.py:11-14). Needs ≥2 real displays (test_phantommonitor.py:33-38): it parks real Tk windows on the smallest non-primary display and asserts they are evacuated, and launches `phantommonitor.py` as a subprocess to require the startup marker (779-807). Prints "N/M checks passed", exits 1 on any FAIL.
---
# Window guard — notice a window on a blocked display, move it off

## What it does
`Guard` (phantommonitor.py:1402) watches every top-level window and evacuates any that lands on a
blocked display. Two drivers: WinEvent hooks (`_on_event`, 2115) call `check_window` on drag-end
(2132), on `EVENT_OBJECT_LOCATIONCHANGE` (2146, behind the per-hwnd cooldown at 2139) and on
show/foreground (2160); a `TIMER_SWEEP` tick (set 1968, handled 3649) runs a full `sweep` (1681).
`check_window` (1639) decides, `move_window` (1501) moves.

## Where it lives
- **Decide** — `check_window` 1639: bails on `enabled` false (1640) or `is_manageable` false (1642);
  skips a window on its own pinned display (`assigned_display`, 1659-1665); uses
  `monitor_of_rect(..., require_overlap=True)` (1667), so an off-screen window is claimed only with
  `include_offscreen` (1671-1676); target is `rescue_target()` (1678, 1455) = primary if allowed.
- **Measure** — `window_rect_for_check` phantommonitor.py:1631 returns a *minimized* window's
  restore rect in screen coords via `workspace_offset` (1493); a minimized window's live rect is the
  iconic one (same reason in `is_manageable`, 1077-1083).
- **Move** — `move_window` 1501. Minimized → `_move_minimized` (1613), which only rewrites
  `rcNormalPosition` and never un-minimizes. Maximized → `SW_RESTORE`, move, `SW_MAXIMIZE`
  (1511-1513, 1578-1579). Borderless + `covers_monitor` → the full-screen path. Otherwise
  `placed_rect` (1374) keeps the proportional position, aiming at `self.clipped[hwnd]` (1403,
  1566-1574) so a trip through a small display is not a one-way shrink.
- **Full-screen** — `FULLSCREEN_TOGGLES` 1226 maps a window class to the app's own shortcut
  (`TscShellContainerClass` → Ctrl+Alt+Break, `VK_CANCEL` 1220). `move_window` 1533-1541 spawns
  `_leave_fullscreen_then_move` (1593) on a thread; it calls `send_key_combo` (1232), waits ~2.5s
  for `is_user_movable` (1601-1604), then re-enters `move_window` with `allow_toggle=False` so it
  cannot recurse (1611).
- **Opt-in restyle** — `unfullscreen_borderless` (DEFAULT_CONFIG:92, used 1543) calls `restore_frame`
  (1275, `FRAME_STYLES` 1271) and `windowed_rect` (1290, `windowed_fraction` 112), adding
  `SWP_FRAMECHANGED` (1577). If the app refuses the styles it falls back to `centred_rect` (1366)
  and logs the Ctrl+Alt+Break advice (1554-1557).
- **Filter** — `is_manageable` phantommonitor.py:1061 (`SKIP_CLASSES` 189, `is_cloaked` 1053,
  `process_name` 1033, `ignore_window_classes` 1075, `ignore_process_names` 1090, min 80x60 at 1086).
- **Which displays** — `Guard.blocked()` 1426 never blocks the primary (1434-1446) and stands down
  if everything would be blocked (1446-1449); `allowed()` 1451.
- **By hand** — `move_active_to` 1774 (tray menu 3096, hotkeys 2681) refuses a blocked destination
  (1786-1792); `pick_menu_target` (1340) supplies the hwnd, because opening the menu steals focus.

## CASCADE — a change here must reach ALL of these
- Every `sweep`/`check_window` caller: phantommonitor.py:1974 (startup), 2132/2146/2160 (events),
  2678 (rescue hotkey), 2920 (tray "Rescue windows now"), 3063 (block toggle), 3170 (reload), 3532
  (re-enable), 3588 (double-click), 3649 (periodic), 3670 (display change), 4078/4115 (CLI).
- Shared helpers reach other subsystems: `placed_rect`/`centred_rect` → `place_assigned` (1732),
  `_apply_arrangement` (3371); `covers_monitor`/`is_user_movable` → `cursor_lock_rect` (1162),
  `_restore_windows` (2292, 2328); `FULLSCREEN_TOGGLES` → `_ask_for_fullscreen` (2233), called from
  `_restore_windows` (2361) and `_apply_arrangement` (3478).
- Config keys: `DEFAULT_CONFIG` phantommonitor.py:82-164 **and** config.example.json:23-33 **and**
  the settings-window toggle list settings_window.py:530-537 (only `enabled` and `leave_fullscreen`
  have a checkbox there) **and** docs/configuration.md:36-50.
- Docs: docs/configuration.md:76-82 (the Moving / toggles table), README.md:143-146, 406.
- Tests: test_phantommonitor.py checks 1-16 (72-293) — sweep, pause, minimized restore point,
  off-screen, borderless full-screen, tray-menu hwnd, the ignored-toggle thread path (247-258),
  clip-and-restore sizing (260-285), refusing a blocked destination (287-291).

## Rules (invariants)
1. **`SetWindowPlacement` drops a borderless window out of full screen; `SetWindowPos` does not.**
   `_move_minimized` is the only `SetWindowPlacement` in the move path (1621), for
   `SW_SHOWMINIMIZED` only; `_restore_windows` moves a currently-full-screen window with
   `SetWindowPos` to the display bounds instead (2292-2314, comment 2295-2299).
2. **An app that manages its own frame is asked, never forced.** Nothing outside an app can put it
   into full screen (2212-2213), so the mechanism is `send_key_combo` with the app's own shortcut;
   `unfullscreen_borderless` stays off by default (DEFAULT_CONFIG:88-92).
3. Never expand a window to fill the destination (comment 1521-1524) — a blown-up borderless window
   buries the taskbar and still has no edge to grab. Keep the size, stay inside `work`.
4. Deciding whether a window is on a blocked display needs `monitor_of_rect(...,
   require_overlap=True)` (1131-1143): the nearest-monitor fallback drags `-32000,-32000` helper
   windows into view.
5. A window pinned to its own display is never evacuated from it (1659-1665), even when blocked.
6. `_leave_fullscreen_then_move` stays on its own thread (1595-1598): blocking the message loop
   stalls the keyboard hook and Windows evicts it.
7. Never move onto a blocked monitor on request (1786-1792) — it only starts a fight with the sweep.

## Reuse, don't rebuild
- Moving a window anywhere: `Guard.move_window` (1501). By monitor number: `move_active_to` (1774).
- "Which monitor is this rect on": `monitor_of_rect` (1131) + `overlap_area` (1123) / `rect_center`
  (1119).
- "Is this a real user window": `is_manageable` (1061). "Is it full-screen": `is_user_movable`
  (1357) + `covers_monitor` (1156). "Where should it land": `placed_rect` (1374), `centred_rect`
  (1366), `windowed_rect` (1290). Minimized coords: `workspace_offset` (1493).
- Leaving/re-entering full screen: `send_key_combo` (1232) with `FULLSCREEN_TOGGLES` (1226); for
  re-entry use `TrayApp._ask_for_fullscreen` (2210) — it waits for the right display.
- The window a menu item acts on: `pick_menu_target` (1340). Titles: `shorten` (1098) /
  `title_of` (1110).

## Traps
- **Skipping app-managed windows stranded RDP.** 2f47591 made the restore skip any window with no
  caption and no resize frame; eb65fed undid it — a full-screen session Windows parked on the wrong
  display then stayed there forever. The breaking call was `SetWindowPlacement`, not the move. Move
  such a window with `SetWindowPos`; never skip it.
- **"Is it full-screen now" is the wrong question at restore time.** A display going to sleep makes
  Windows shrink the window off it first, so the test passes on a window that was full-screen a
  second ago (2f47591, 3c10de0; live comment 2277-2283). Judge from the snapshot as well (2318-2331).
- **A keystroke sent too early lands in the wrong place.** Injecting straight after
  `SetForegroundWindow` sends the keys to whatever had focus before and the toggle silently does
  nothing (`send_key_combo` 1234-1237); asking for full screen before the window had settled on its
  destination made RDP go full-screen on a 4K panel (`_ask_for_fullscreen` 2216-2220). Modifier
  events also need pacing (1261-1264).
- **`send_key_combo` returns a bool, and the caller must honour it.** It gives up after four
  `SetForegroundWindow` attempts over ~1.4 s, warns, and returns `False` having sent *nothing*
  (1240-1250). A caller that ignores that and logs success turns a real failure into a silent one —
  which is exactly what happened on the restore path until 2026-10-24. Do not answer a `False` by
  forcing focus or retrying harder; Windows is refusing on purpose. Give the window time to settle
  first, then report honestly if it still will not take focus.
- **Blowing a borderless window up to the destination** buried the taskbar and tray — test
  "stays inside the work area" (test_phantommonitor.py:186-190) exists for that regression. A round
  trip via a small display used to shrink a window permanently — hence `self.clipped` (1403,
  1566-1574) and test 14 (test_phantommonitor.py:260-285).
- Two identical monitors share one EDID id, so one rule blocks both (warning 1422-1424), and Windows
  can hand primary to another screen after a topology change — hence the primary guard (1434-1446).

## Related subsystems
- **cursor-fence** (docs/systems/cursor-fence.md) — shares `covers_monitor`, `is_user_movable`,
  `monitor_of_rect`, `Guard.blocked()`.
- Window/icon restore after a display change — `_snapshot_windows` 2178, `_restore_windows` 2253,
  `window_displaced` 1298. Saved arrangements — `_apply_arrangement` 3371. Pinned apps —
  `assigned_display` 1701, `place_assigned` 1732, `_rehome_pinned` 2721. Display identification —
  `enum_monitors` 1010, `monitor_matches_block` 948.

LAST VERIFIED: 2026-10-24 · aa2db0f · Read Guard and every method above, all cited module helpers,
DEFAULT_CONFIG:82-164, every sweep/check_window/move_window call site (grep), settings_window.py:
530-537, config.example.json, docs/configuration.md, README.md, checks 1-16 of
test_phantommonitor.py, and commits 2f47591, 3c10de0, 44b6939, eb65fed, 85e7dca.
