---
subsystem: cursor-fence
owns: phantommonitor.py, test_phantommonitor.py
anchors: cursor_clip_rect, cursor_lock_rect, clip_is_owned, rect_within, spans_displays, fenceable, _apply_cursor_clip, _release_cursor_clip, _toggle_cursor_block, cursor_clipped, cursor_locked_app, clip_warned, ClipCursor, GetClipCursor, block_cursor, cursor_lock_apps, cursor_lock_fullscreen, cursor_never_lock
test: `python test_phantommonitor.py` — STOP the tray app first (the suite exits 2 on finding `PhantomMonitorWnd`, test_phantommonitor.py:11-14) and attach ≥2 displays (33-38). The fence checks are pure logic over real monitor geometry — `cursor_clip_rect` 331-353, `cursor_lock_rect` 489-584, `fenceable` 502-543, `clip_is_owned` 727-737, `rect_within` 745-753 — so no `ClipCursor` is ever called. `_apply_cursor_clip` itself is only covered by the start-up subprocess check (779-807). Prints "N/M checks passed", exits 1 on any FAIL.
---
# Cursor fence — keep the mouse pointer out of blocked displays

## What it does
With `block_cursor` on, `TrayApp._apply_cursor_clip` (phantommonitor.py:2474) confines the pointer
to one rectangle that covers every allowed display and no blocked one, so a phantom display cannot
be reached at all. The same method is where a full-screen app instead has the pointer *held inside*
its own display. It is re-run constantly because Windows drops the clip on every foreground change:
startup (1975), `EVENT_SYSTEM_FOREGROUND` (2159), each `TIMER_SWEEP` tick (3650), rescue (2679),
block toggle (3061), pin change (3227), `_toggle_cursor_block` (3522), `_toggle_enabled` (3529).

## Where it lives
- **Which blocked displays to fence** — `fenceable` 630: drops any display with an app pinned to it
  (`app_displays`), because a reserved chat/dashboard screen must stay clickable.
- **The rectangle** — `cursor_clip_rect` 681: the bounding box of the allowed displays, returned
  only if no blocked display overlaps it (694-699); otherwise `None`.
- **Holding the pointer inside an app** — `cursor_lock_rect` 1162. Never for a blocked host (1188). A name in `cursor_lock_apps` wins outright, whatever the shape (1197-1203). Otherwise it must be
  borderless (`is_user_movable` false) *and* cover 99.5% of its host (1203), must not
  `spans_displays` > 1 (1210), must not be in `cursor_never_lock` (1212-1213), and
  `cursor_lock_fullscreen` must be on (1214).
- **Not stomping someone else's clip** — `_apply_cursor_clip` 2546-2570 reads `GetClipCursor` first.
  Identical to ours → nothing to do (2549-2551). Inside our allowed box → it already satisfies the
  fence, so leave it (2552), **but only** if `clip_is_owned` (655) says the foreground window could
  have set it (`rect_within` 675, shell and `cursor_never_lock` apps excluded). Otherwise it is
  stale and gets taken back (2568-2569).
- **Releasing** — `_release_cursor_clip` 2469 (`ClipCursor(None)`), called when `block_cursor` or
  `enabled` is off, nothing is blocked, nothing is fenceable, or the layout has no single rectangle
  (2488, 2495, 2499), and on shutdown (3769).
- **State** — `cursor_clipped`, `cursor_locked_app`, `clip_warned` initialised 1939-1941; the
  warning about an unfenceable layout fires once per change (2500-2503, reset 3060, 3520).
- **Escape hatch** — the rescue action calls `ClipCursor(None)` directly before re-fencing
  (2672-2679): the tray icon may be outside a bad clip, so rescue must not depend on it.
- **UI/config** — tray item "Keep pointer off blocked displays" 2914-2916; settings checkbox
  settings_window.py:532; defaults `block_cursor` DEFAULT_CONFIG:99, `cursor_lock_apps` 150,
  `cursor_lock_fullscreen` 151, `cursor_never_lock` 152-153 (mstsc.exe, explorer.exe,
  screenclippinghost.exe, snippingtool.exe).

## CASCADE — a change here must reach ALL of these
- Every `_apply_cursor_clip` / `_release_cursor_clip` call site: phantommonitor.py:1975, 2159, 2679,
  3061, 3227, 3522, 3529, 3650, 3769 — plus the raw `ClipCursor(None)` at 2673.
- `fenceable` depends on `app_displays`, so pinning/unpinning must re-fence *before* moving the
  window (3225-3229, comment 3224-3226).
- `cursor_lock_rect` borrows `monitor_of_rect` (1131), `covers_monitor` (1156), `is_user_movable`
  (1357) and `spans_displays` (645) — changing any of those changes the fence as well as the
  window guard.
- `Guard.blocked()` (1426) feeds both `fenceable` and `cursor_clip_rect`; its never-block-primary
  rule (1434-1446) is what stops the fence sealing the user away from the tray icon.
- Config keys: DEFAULT_CONFIG:99/150-153 **and** config.example.json:20 **and**
  settings_window.py:532 **and** docs/configuration.md:40-44 and 52-68 **and** README.md:330,
  357-371.
- Tests: test_phantommonitor.py:331-353, 489-584, 727-737, 745-753.

## Rules (invariants)
1. **There is exactly ONE cursor clip on the whole system**, and the last caller wins — hence
   `GetClipCursor` is read before every write (2546-2570, comment 2536-2545). Never write
   unconditionally.
2. **Never defer to a clip no live window owns.** A clip outlives its process; `clip_is_owned` (655)
   requires the clip to lie inside the foreground window and never trusts the shell (665-672).
3. **`ClipCursor`, not a `WH_MOUSE_LL` hook** (docstring 2475-2481, docs/configuration.md:54-55): it
   costs nothing per mouse movement, so it cannot add input latency in a game.
4. **A blocked display can only be fenced when it lies outside the bounding box of the others**
   (`cursor_clip_rect` 684-688, 696-698). When it does not, return `None` and release — never fence the user
   out of a display they actually use.
5. **A pinned display stays reachable** (`fenceable` 630-642). Blocking keeps other windows off; a
   screen nobody can click is lost, not reserved.
6. The primary is never blocked (1434-1446), so the tray icon that switches the fence off is always
   reachable.
7. Turning the guard off must release the fence too (2483-2490) — "off" has to mean off.
8. Full-screen detection for the pointer is stricter than elsewhere: 99.5% coverage **and**
   borderless (1203). `covers_monitor`'s default 95% would catch any maximized window.

## Reuse, don't rebuild
- Computing the fence: `cursor_clip_rect` (681). Deciding which blocked displays it applies to:
  `fenceable` (630). Applying or dropping it: `_apply_cursor_clip` (2474) /
  `_release_cursor_clip` (2469) — never call `ClipCursor` elsewhere (the one exception is the
  rescue escape hatch, 2673).
- "Is rect A inside rect B": `rect_within` (675). "Does this window straddle screens":
  `spans_displays` (645). "Is this window really full-screen": `is_user_movable` (1357) +
  `covers_monitor` (1156), or `cursor_lock_rect` (1162) which already combines them.
- "Whose clip is this": `clip_is_owned` (655). Foreground process name: `process_name` (1033).

## Traps
- **Overwriting a game's own clip every two seconds** widened it back out and let the cursor escape
  mid mouse-look (5e5f7ef). Read the clip before writing it.
- **Then deferring too readily sealed the user in.** Don't Starve exited leaving its clip behind;
  the fence politely declined to touch it, forever, and the tray icon was outside the rectangle — it
  took another process calling `ClipCursor(None)` to break out (67ec03f). That is why
  `clip_is_owned` exists and why rescue frees the pointer first.
- **Standing down for full-screen apps left the phantom unguarded.** 6a439f0 released the fence for
  any window blanketing a display; 5a12e25 removed that — the loose 95%-no-frame-check test meant a
  maximized window or the desktop dropped the fence during ordinary desktop use.
- **explorer.exe trapped the pointer on the primary**: the desktop is a screen-sized Explorer
  window, so clicking the wallpaper locked the mouse there (7be109e). Fixed by the 99.5% +
  no-caption test and by shipping explorer.exe in `cursor_never_lock`.
- **The Win+Shift+S snip layer is borderless and spans every screen**, so it matched the full-screen
  test and dragged the pointer off whatever display the user was aiming at (cf70974) — hence
  `spans_displays` and the snip hosts in the default never-lock list.
- **Fencing every blocked display without distinction** made a deliberately reserved chat monitor
  unclickable (57e29e6) — hence `fenceable`.
- **Full-screen RDP must never be locked in**: it would strand the pointer away from every other
  screen (57e29e6; mstsc.exe is the first entry in `cursor_never_lock`, DEFAULT_CONFIG:152).
- **An uninitialised attribute here kills the whole app silently.** `cursor_locked_app` was read in
  `_apply_cursor_clip` before it was ever set, so startup died with no console, no tray icon and
  nothing in the log (d04c508). The suite's start-up subprocess check (test_phantommonitor.py:
  779-807) exists only because of this.

## Related subsystems
- **window-guard** (docs/systems/window-guard.md) — `Guard.blocked()`, `monitor_of_rect`,
  `covers_monitor`, `is_user_movable` are shared; the rescue action sweeps windows and frees the
  pointer in one go (2668-2679).
- Pinned apps / `app_displays` — `assigned_display` 1701, `_make_pin_action` 3198.
- Display identification — `enum_monitors` 1010, `monitor_matches_block` 948.

LAST VERIFIED: 2026-10-24 · aa2db0f · Read `fenceable`, `spans_displays`, `clip_is_owned`,
`rect_within`, `cursor_clip_rect`, `cursor_lock_rect`, `_apply_cursor_clip`,
`_release_cursor_clip`, `_toggle_cursor_block`, every call site by grep, DEFAULT_CONFIG,
settings_window.py:532, config.example.json, docs/configuration.md, README.md, the cursor checks in
test_phantommonitor.py, and commits 5e5f7ef, 67ec03f, 6a439f0, 5a12e25, 7be109e, cf70974, 57e29e6,
9e55065, d04c508.
