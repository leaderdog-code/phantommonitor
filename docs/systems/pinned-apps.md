---
subsystem: pinned-apps
owns: phantommonitor.py, test_phantommonitor.py
anchors: assigned_display, place_assigned, remember_app_position, _rehome_pinned, _rehome_all_pinned, _make_pin_action, fenceable, app_displays, app_positions, centred_rect, offset_in, offset_onto
test: `python test_phantommonitor.py` from the project root. STOP the tray app first — it refuses to run while `PhantomMonitorWnd` exists (test_phantommonitor.py:11-15), drives real windows, and needs two real displays, treating the smallest non-primary one as the phantom (33-35). Pin coverage: 444-466 (a pinned app is not evacuated; pinned elsewhere it still is; `assigned_display`) and 499-510 / 532-543 (`fenceable`). Nothing covers `place_assigned`, `remember_app_position` or either rehome pass — verify by hand.
---
# Pinned apps — send a named app to a chosen display and keep it there

## What it does
Dedicates a screen to one or more apps. A pinned app **opens on its display**, **reopens where it
was last left on it**, and is **never evacuated from it even when that display is blocked** — the
only thing that makes a dedicated chat/dashboard/stream screen possible, since blocking otherwise
empties the screen including the app you wanted there (e973988).

The pin is chosen **by destination**, not by where the window happens to be sitting. The screen you
want to dedicate is usually one you have already blocked, and that is exactly the screen you cannot
drag a window onto — "pin it where it is" would have meant unblock, drag, pin, re-block
(`_make_pin_action` 3198-3205, e939e7f).

Not to be confused with **hotkey-slot pinning** — `hotkey_targets`, `_make_assign_action` 3066-3082,
`_make_unassign_action` 3084-3093 — which binds the numbers 1-4 to hardware ids and only shares the
word "pin".

## Where it lives
- **Config** — two keys in `DEFAULT_CONFIG` (phantommonitor.py:155-156), also in
  config.example.json:7-9 and docs/configuration.md:41:
  - `app_displays`: `{exe name (lower-case): display hardware id}`. Live config.json:
    `{"discord.exe": "ABC1234", "signal.exe": "ABC1234"}`.
  - `app_positions`: `{exe name: {"rel": [dx, dy, w, h]}}`, e.g.
    `"discord.exe": {"rel": [2381, 1334, 1411, 777]}` — an offset within the pinned display.
- **Lookup** — `Guard.assigned_display` 1701-1704: the exe's pinned display, **or None if it is not
  attached today**.
- **The exemption** — `Guard.check_window` 1653-1660: if the window is on its assigned display,
  return False before the blocked-display test. The whole feature.
- **Placement** — `Guard.place_assigned` 1732-1771, on `EVENT_OBJECT_SHOW` (2152-2153), from both
  rehome passes (2712, 2757) and right after pinning (3229). Returns False if the window is already
  on its display (1744-1746).
- **Remembering** — `Guard.remember_app_position` 1706-1730, on `EVENT_SYSTEM_MOVESIZEEND` (2133).
  Records only a position held **on its own pinned display** (1721-1723).
- **Rehome** — on return: `_rehome_pinned` 2721-2763, from the display-change settle path at 3724
  with the hwids that just reappeared. On demand: `_rehome_all_pinned` 2684-2719, tray item "Send
  pinned apps to their displays" (2970-2971).
- **Tray UI** — `_show_menu` 2957-2971: "Always open \<app\> on" → a submenu of every display,
  ticked where pinned, plus "Do not pin it anywhere". `_make_pin_action` 3198-3229.
- **Pointer fence** — `fenceable` 630-642, used by `_apply_cursor_clip` 2491-2493. A display with
  something pinned to it is *reserved, not banished*: blocking keeps other windows off, but walling
  the pointer out too would leave a screen nobody can click on.

## CASCADE — a change here must reach ALL of these
- Both config keys: `DEFAULT_CONFIG` 155-156, config.example.json:7-9, docs/configuration.md:41,
  README.md 314-352 ("Dedicating a screen to one app").
- Every reader of `app_displays`: `fenceable` 641, `assigned_display` 1703, `_apply_cursor_clip`
  2493, `_rehome_all_pinned` 2692, `_rehome_pinned` 2736, `_show_menu` 2960/2970,
  `_make_pin_action` 3214-3222.
- Every reader of `app_positions`: `remember_app_position` 1725-1727, `place_assigned` 1749-1760 —
  which still converts the **legacy absolute-list** form (pre-2cd3bcf). Do not drop that branch
  without a migration.
- The evacuation exemption at `check_window` 1653-1660 — change the pin shape and this must follow,
  or blocking starts emptying the dedicated screen again.
- The fence: `fenceable` 630 and `_apply_cursor_clip` 2491. `_make_pin_action` re-applies the clip
  **before** moving the window (3225-3228), or it lands where the pointer cannot follow.
- Tests: test_phantommonitor.py 444-466 and 499-510 / 532-543.

## Rules (invariants)
1. **A pinned app is never evacuated from its own display** (`check_window` 1653-1660). Pinned
   *elsewhere*, it is evacuated like anything else (tested 459-462).
2. **A position is stored relative to its display**, via `offset_in` 821 — `{"rel": [dx, dy, w, h]}`
   (1726). Absolute coordinates shift when the monitor is dragged in Display Settings, re-plugged or
   made primary.
3. **Only remember a position held on the pinned display** (1721-1723), or you record where it sat
   on the wrong screen.
4. **Placement happens when a window opens, not continuously** (`place_assigned` 1734-1736). A rule
   decides where something starts; it does not follow it. Drag it off and it stays off.
5. **A pinned display is exempt from the pointer fence** (`fenceable` 630-642) — reserved, not
   banished.
6. **Rehoming on a display change is scoped to displays that have just come back** (`_rehome_pinned`
   2730-2733). On every display event it would drag a pinned window off a screen the user had
   deliberately moved it to.
7. **A pin to a display that is not attached is inert** — `assigned_display` 1704 and `by_hwid`
   1470-1474 both return None. It must never fall back to "some other screen".
8. **No usable saved position falls back to `centred_rect` 1366-1371**, which centres in the work
   area so the taskbar stays clear.

## Reuse, don't rebuild
- "Which display is this window's app pinned to?" → `Guard.assigned_display` 1701.
- "Put this window on its display" → `Guard.place_assigned` 1732. It already handles the
  already-there case, the saved position, the legacy absolute form and the centred fallback. Do not
  open-code `SetWindowPos`.
- "Send everything home now" → `_rehome_all_pinned` 2684; "…a display came back" → `_rehome_pinned`
  2721.
- Coordinates: `offset_in` 821 / `offset_onto` 827 (clamped, so a resolution change cannot strand a
  window off the edge). Centring: `centred_rect` 1366.
- Which blocked displays still get fenced → `fenceable` 630.
- Changing a pin from code → `_make_pin_action` 3198: saves the config, re-applies the fence, then
  places the window, in that order.

## Traps
- **A pin and a saved arrangement can disagree, and nothing warns you.** `app_displays` says
  discord.exe belongs on `XYZ5678` (config.json), while the "Stream" mode in arrangement.json has a
  discord.exe slot with `"hwid": "ABC1234"`. Applying "Stream" drags Discord to ABC1234
  (`_apply_arrangement` 3438-3470) and the guard will not pull it back — `place_assigned` only
  fires on open (2152) and on a display returning (3724) — but the next launch, the next time
  XYZ5678 comes back, or "Send pinned apps to their displays" yanks it home again. There is **no
  check, no warning and no precedence rule**. Say so when it bites; do not invent a silent winner.
- **Positions were stored absolute once** (fixed 2cd3bcf). The docstring claimed relative while the
  code stored absolute, so moving the monitor made the code discard a valid position and re-centre
  the window. Check the docstring against what is written.
- **`"pin it where it is"` is a dead end** (e939e7f): the screen worth dedicating is a blocked one,
  which is the one you cannot drag onto.
- **Re-apply the fence before moving the window** (3225-3228), or it lands on a screen the pointer
  is still walled out of.
- **Pinning is redundant for apps that already remember their position** on an *unblocked* screen
  (ba82a75, README.md 337-341). Its real job is the exemption from blocking.
- **A pin only reaches a window that exists.** An app in the notification area has no window;
  `place_assigned` fires on `EVENT_OBJECT_SHOW` when it is summoned (2152).
- **Identity vs device comparison is inconsistent.** `place_assigned` 1757 compares
  `monitor_of_rect(...) is mon` (object identity) while everywhere else compares `.device` (1722,
  1745, 2710, 2754). Both work only because `by_hwid` and `monitor_of_rect` return objects from the
  same `self.monitors` list; a refresh between the two calls would break it.
- README.md 318-322 still says step 1 is "put the app on that screen" and names the menu item
  "Always open discord.exe on this screen" — stale since e939e7f made it a destination submenu. It
  never mentions "Send pinned apps to their displays" (aa2db0f) or rehome-on-return (620bcb5).

## Related subsystems
- **arrangements** (docs/systems/arrangements.md) — the conflict above; shares `offset_in` /
  `offset_onto`.
- **Block rules / evacuation** — `Guard.check_window` 1639, `Guard.sweep` 1680; the pin is an
  exemption carved into that path.
- **Pointer fence** — `_apply_cursor_clip` 2474, `cursor_lock_rect` 1162.
- **Hotkey display slots** — `hotkey_targets`, `target_for` 1476, `_make_assign_action` 3066. A
  different "pin"; do not merge.

LAST VERIFIED: 2026-10-24 · aa2db0f · every file:line above read in the working tree; live config.json read for the real `app_displays` / `app_positions` examples; the discord.exe pin-vs-arrangement conflict confirmed by comparing config.json against arrangement.json on disk; commits e973988, 2cd3bcf, e939e7f, 52a22ad, ba82a75, 620bcb5, aa2db0f read in full. Tests NOT run: the tray app is live and the suite refuses to run beside it.
