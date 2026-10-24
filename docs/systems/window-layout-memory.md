---
subsystem: window-layout-memory
owns: phantommonitor.py, test_phantommonitor.py
anchors: _snapshot_windows, _restore_windows, _unhide_vanished, window_displaced, window_snapshot, layouts_by_sig, windows_frozen, _windows_moved, _ask_for_fullscreen, topology_signature, TIMER_SETTLE, TIMER_VERIFY, window_save_debounce_ms
test: `python test_phantommonitor.py` (docs/development.md:33) — needs two real displays (test:33-35) and the tray app STOPPED or it exits 2 (test:11-14). Sections 18 and 20 (test:355-415) cover this. The restore path is not unit-tested: with the tray running, switch a display off and on, then check `logs/phantommonitor.log` for `put back N displaced window(s)`.
---
# Window layout memory — put back only the windows a display change displaced

## What it does
Keeps a live record of where every manageable window sits, freezes it the instant a
display change starts, and once the dust settles moves back only those windows whose
placement now differs. Windows the change did not touch are left alone
(`_restore_windows`, phantommonitor.py:2253-2259).

## Where it lives
- `TrayApp.window_snapshot` — `{hwnd: GetWindowPlacement}`, the last known good state
  (phantommonitor.py:1947). Per session, in memory, keyed by handle — these are literally
  the same windows, so no matching heuristics (:2164-2169). Lost on reboot (README.md:389).
- `TrayApp.layouts_by_sig` — one remembered snapshot per display arrangement (:1954),
  written alongside the live one in `_snapshot_windows` (:2202), keyed by
  `topology_signature(monitors)` (:623-627, `"WxH@x,y|..."` by monitor number).
- `_snapshot_windows` (:2178-2208) — `EnumWindows` → `is_manageable` (:1061) →
  `GetWindowPlacement`. INFO only for `startup`/`manual`, DEBUG otherwise.
- `_windows_moved` (:2170-2176) — debounce on every move/drag-end/location-change event
  (:2134, :2149); `window_save_debounce_ms` (default 2000, floor 500) re-arms
  `TIMER_WINDOWS`, fired at :3664-3667.
- `window_displaced` (:1298-1337) — same show-state and same `rcNormalPosition` means
  untouched. `_restore_windows` (:2253-2367) — the restore; `_ask_for_fullscreen`
  (:2210-2251) runs on a thread after it. `_unhide_vanished` (:2765-2804).
- `_snapshot_all` / `_restore_all` / `_toggle_restore_windows` (:3172-3187), from the tray
  menu (:3007-3011). Config `restore_windows`, `window_save_debounce_ms` (:105-106);
  settings checkbox settings_window.py:533.
- **No disk file.** `ICON_LAYOUT_PATH` / `icon_layouts.json` (:69) is desktop icons only —
  see `desktop-icons.md`. Window layouts are RAM-only.

### The sequence
1. `WM_DISPLAYCHANGE` / `WM_DEVICECHANGE` (:3744-3757): refresh monitors, rebuild hotkeys,
   `settle_left = 6`, `windows_frozen = True`, `icons_quiet_until = now + 30`,
   `TIMER_SETTLE` at 700 ms. Freezing is what makes the snapshot mean "before".
2. `TIMER_SETTLE` (:3668-3673) re-sweeps 6 times while topology settles.
3. On the last tick (:3674-3741): if the signature is unchanged, log and restore anyway
   (:3675-3690); otherwise, if `layouts_by_sig` already holds this arrangement, swap that
   in as the snapshot (:3692-3700). Then `_unhide_vanished` → `_restore_windows` →
   `_unhide_vanished` (:3721-3723), `_rehome_pinned`, `_restore_icons`,
   `windows_frozen = False`, `_snapshot_windows("after restore")`, `verify_left = 3`,
   `TIMER_VERIFY` at 2500 ms.
4. `TIMER_VERIFY` ×3 (:3657-3663): `_unhide_vanished`, then `_restore_windows("late nudge")`,
   re-snapshotting if anything moved — for nudges that arrive after the settle expired.

## CASCADE — a change here must reach ALL of these
- `_snapshot_windows`:2178 and `_restore_windows`:2253 — one shared placement format.
- `window_displaced`:1298 — the comparison; also asserted at test_phantommonitor.py:400-415.
- `layouts_by_sig`:1954, :2202, :3692 and `topology_signature`:623 — the key must match in
  all three, and `topology_signature` is also the icon-layout key (:2410, :2435).
- The `_wnd_proc` timer branches :3647-3742 and the display-change branch :3744-3757 —
  freeze, settle, restore, verify are one state machine; `windows_frozen` and
  `icons_quiet_until` gate `_snapshot_windows` (:2187) and `_windows_moved` (:2172).
- `_windows_moved` callers: :2134 (MOVESIZEEND), :2149 (LOCATIONCHANGE).
- Tray menu :3007-3013, `_snapshot_all`/`_restore_all` :3172-3179, toggle :3181-3187.
- Config: `DEFAULT_CONFIG`:105-106, config.example.json:21,31,
  docs/configuration.md:45, settings_window.py:533.
- Docs: README.md:100-112 and :389.
- Tests: test_phantommonitor.py sections 18 (:355-375) and 20 (:393-415).

## Rules (invariants)
1. **Restore only what moved.** Compare against the frozen snapshot; a window still where
   the snapshot says is left strictly alone (:2253-2267). Reapplying a whole saved layout
   drags windows the change never touched (:2181-2186).
2. **Never `SetWindowPlacement` a window that is full-screen right now** — it drops it out
   of full screen. Use `SetWindowPos` to the target display's full bounds (:2292-2315).
3. **Judge full-screen by what it WAS, not only what it is.** A sleeping display makes
   Windows shrink the window off it first, so the live test passes (:2276-2282, :2316-2331).
4. **Never restore a window to a spot that no longer exists** — check `monitor_of_rect`
   first and count it skipped (:2334-2342).
5. **A minimized window stays minimized**; only its reappear position is corrected
   (:2344-2347).
6. **`_unhide_vanished` must never run on the periodic sweep.** The snapshot is live then,
   so something just tucked away would keep being dragged back (:2782-2785). It runs on the
   display-change restore (:3721, :3723), `TIMER_VERIFY` (:3658) and the rescue hotkey
   (:2677) — nowhere else.
7. **Freeze before restoring.** `windows_frozen = True` on the display event (:3754), back
   to False only after the restore (:3731).

## Reuse, don't rebuild
- Need "has this window moved?" → `window_displaced(hwnd, placement, cfg, monitors=None)`
  (:1298). Do not re-implement a rect compare.
- Need an arrangement key → `topology_signature(monitors)` (:623). Its inverse is
  `origin_from_signature` (:580); `virtual_origin(monitors)` (:573) is the live one.
- Need "is this a real user window?" → `is_manageable(hwnd, cfg)` (:1061).
- Need "is it app-managed / full-screen?" → `is_user_movable(hwnd)` (:1357) +
  `covers_monitor(rect, mon, fraction)` (:1156).
- Need "which display is this rect on?" → `monitor_of_rect(rect, monitors, require_overlap)`
  (:1131). Placement rects are workspace-relative: add `self.guard.workspace_offset()`
  (:1493) first, as :2283-2288 does.
- Need an app back in full-screen → `_ask_for_fullscreen(hwnd, want_hwid)` on a thread
  (:2210); shortcut table `FULLSCREEN_TOGGLES` (:1226). Everything at once →
  `_snapshot_all` / `_restore_all` (:3172-3179).

## Traps
- **Judging by topology signature alone misses the worst case** (9e55065). A game taking
  exclusive full screen flips the display config and flips it back; the signature matches
  again by the time the settle expires, having just watched Windows sweep every window onto
  the primary. The branch at :3675-3690 only decides the log line — the restore runs anyway.
- **Skipping app-managed windows is not a fix** (eb65fed, after 85e7dca). Skipping stopped
  RDP being un-fullscreened, and also left a full-screen session Windows had parked on the
  wrong display sitting there forever. The breaking call was `SetWindowPlacement`, not the
  move: hence :2304-2308.
- **A single live snapshot cannot survive a monitor being switched off** (f01d080). It kept
  learning while the screen was away, so the collapsed layout became the remembered one.
  Hence `layouts_by_sig`.
- **Sending the full-screen shortcut too early resizes the session** (2ce34ca, reverting
  adbefe0/1965914). RDP was still on its old display, went full-screen on a 4K panel and
  resized the session to match. `_ask_for_fullscreen` waits up to 6 s for the window to
  land on `want_hwid` (:2225-2240). **Both** callers now pass it: the restore path carries
  `was_host.hwid` through `refullscreen` as `(hwnd, hwid)` (:2331, :2368-2370), the same
  shape `_apply_arrangement` uses (:3482-3487).
- **The restore used to fight itself for the foreground.** `_restore_windows` starts the
  `_ask_for_fullscreen` thread part-way through the settle sequence, which then goes on to
  `_unhide_vanished` (`ShowWindow`), `_rehome_pinned` (moves windows) and `_restore_icons`
  (pokes Explorer). Every one of those changes the foreground, so the thread's four
  `SetForegroundWindow` attempts over ~1.4 s never stuck and RDP was left windowed after
  every wake. It reads exactly like Windows' foreground lock and is not: before 6610c60
  added the second `_unhide_vanished` and 620bcb5 added `_rehome_pinned`, almost nothing
  ran after the restore and the same code worked. `_ask_for_fullscreen` now waits for
  `windows_frozen` to clear (:2237-2242) before touching focus, but **only when called with
  `after_settle=True`** — which `_restore_windows` passes (:2394-2397) and
  `_apply_arrangement` deliberately does not: an arrangement is a user action outside the
  sequence, and waiting there would let an unrelated `WM_DEVICECHANGE` (a USB plug, which
  also sets `windows_frozen`) hold a button press for seconds. **If you add anything else
  to the settle sequence, it must run before that flag is cleared** (:3769), or this
  returns.
- **A failed focus used to be logged as success.** `send_key_combo` returns `False` when
  `SetForegroundWindow` is refused, and sends nothing; `_ask_for_fullscreen` logged
  "asked ... to go back to full-screen" regardless, so a real failure read as a success in
  the log. It now warns instead (:2266-2274). That false line is what hid the conflict
  above for as long as it did.
- **Gecko hides its own window when moved during a display change** (6610c60). Firefox and
  Pale Moon have both done it, and the log showed the restore had just moved the window —
  so un-hiding has to run *after* the restore as well as before (:3717-3723). A hidden
  window cannot enter the snapshot (`is_manageable` requires visibility, :1064) and it
  still reports `SW_SHOWNORMAL`, so this is not a restored hidden state.
- `_unhide_vanished` **is** proven in real use, contrary to an earlier belief: `logs/` holds
  11 `brought back N window(s) that had gone invisible` lines (firefox ×3, mstsc ×3,
  palemoon ×3, discord, airdc), latest 2026-10-23. **Unproven:** what an Electron app does
  when un-hidden. discord.exe appears there once, and nothing in the code or logs confirms
  or rules out an empty black frame from `ShowWindow(SW_SHOW)` on a suspended renderer.
- The 200x100 floor in `_unhide_vanished` (:2793) is deliberate: smaller is not a lost window.

## Related subsystems
- `desktop-icons.md` — same display-change trigger, same `topology_signature` key, restored
  in the same settle branch (:3725-3726).
- Arrangements (`_apply_arrangement`, `ARRANGEMENT_PATH`:70) — on-disk, user-applied,
  deliberately separate from this in-memory restore (README.md:193-195).
- The guard/sweep and cursor fence share `_wnd_proc` and the monitor list, not the snapshot.

LAST VERIFIED: 2026-10-24 · aa2db0f · Read `_snapshot_windows`, `_restore_windows`, `_unhide_vanished`, `_ask_for_fullscreen`, `window_displaced`, `layouts_by_sig` and the whole `_wnd_proc` timer + WM_DISPLAYCHANGE path; confirmed window layouts are RAM-only (`icon_layouts.json` holds icons only); grepped `logs/` — 11 real `_unhide_vanished` firings; read 9e55065, eb65fed, 85e7dca, f01d080, 2ce34ca, 6610c60, 57f0b94, 77e274e.
