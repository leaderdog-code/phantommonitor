---
subsystem: desktop-icons
owns: desktop_icons.py, phantommonitor.py, watch_icon_events.py, test_phantommonitor.py
anchors: desktop_listview, LVM_GETITEMPOSITION, LVM_SETITEMPOSITION, auto_arrange_on, _Remote, get_icons, set_icons, _snapshot_icons, _restore_icons, _icon_watch_loop, _icons_moved, ICON_BAG_KEY, ICON_LAYOUT_PATH, normalize_layouts, virtual_origin, origin_from_signature, icon_save_debounce_ms
test: `python desktop_icons.py` prints the ListView handle, auto-arrange flag and first 12 icons (desktop_icons.py:239-245); needs a live Explorer desktop. `python test_phantommonitor.py` (docs/development.md:33) covers the coordinate maths, section 18 (test:355-375); needs two displays and the tray app STOPPED (test:11-14, :33-35). End to end: change the display layout with the tray running, then look for `restored N desktop icon positions` in the log.
---
# Desktop icons — keep icon positions across a display change

## What it does
Windows recalculates the desktop icon grid on every display change and never puts the
icons back; one unplug can dump 160 icons onto the wrong screen, stacked
(desktop_icons.py:1-11, phantommonitor.py:2369-2374). This reads every icon's position
before and writes it back after, per display arrangement.

## Where it lives
### `desktop_icons.py` — the cross-process plumbing
- `desktop_listview()` (:82-104) — `Progman > SHELLDLL_DefView > SysListView32`, falling
  back to an `EnumWindows` hunt under `WorkerW` because a wallpaper slideshow re-parents
  the desktop (:91-103); 0 if not found. `auto_arrange_on(lv)` (:107-112) tests
  `LVS_AUTOARRANGE` (:33) on its style.
- `_Remote` (:115-152) — the heart of it. `OpenProcess` on Explorer with
  `PROCESS_VM_OPERATION|VM_READ|VM_WRITE|QUERY_INFORMATION` (:36-39), `VirtualAllocEx` a
  scratch buffer **inside Explorer** (:126), `write`/`read` via `Write/ReadProcessMemory`,
  free on exit — the ListView messages want a pointer valid in the *receiving* process.
  `send()` (:74-79) is `SendMessageTimeoutW`, `SMTO_ABORTIFHUNG`, 3 s, so a busy Explorer
  cannot wedge the tray app; `None` on failure.
- `get_icons(lv)` (:155-194) — `LVM_GETITEMCOUNT`, then per index `LVM_GETITEMPOSITION`
  (read back as a `POINT`) and `LVM_GETITEMTEXTW` through an `LVITEMW` (:64-71) at offset
  520, after a 260-wchar text buffer. Returns `{name: (x, y)}` in **ListView
  coordinates**, `{}` on failure.
- `set_icons(layout, lv)` (:197-236) — rebuilds the name-to-index map by walking the list
  in the same order, then packs x/y into `lParam` for `LVM_SETITEMPOSITION` (:233-235); no
  buffer needed. Skips icons already in place; returns how many moved.

### `phantommonitor.py` — policy, storage, triggers
- `ICON_LAYOUT_PATH` = `<DATA_DIR>/icon_layouts.json` (:69). Version-2 store:
  `{"version": 2, "last": <sig>, "layouts": {<sig>: {<icon name>: [screen_x, screen_y]}}}`
  (`_load_layouts` :2375-2395); the live file holds 8 arrangements, 160-194 icons each.
  **Icons only** — window layouts are RAM-only and share nothing but the key.
- `normalize_layouts` (:594-620) — converts a version-1 store by adding the origin from
  `origin_from_signature` (:580-591); v2 passes through. `virtual_origin` (:573-577) is
  the live origin; `topology_signature` (:623-627) is the per-arrangement key.
- `_snapshot_icons(reason)` (:2404-2423) — reads, converts to screen coordinates and
  **merges** into the arrangement's entry (:2413-2419), so a virtual-desktop manager
  swapping icon sets cannot wipe the rest. Sets `last`.
- `_restore_icons(reason)` (:2425-2466) — one-time warning and refusal if auto-arrange is
  on (:2429-2434); inherits the `last` arrangement's layout when this one has never been
  seen (:2439-2445); seeds a baseline on first run (:2446-2448); drops icons that would
  land where no display exists (:2454-2458); converts back and calls `set_icons`.
- Triggers: `_start_icon_watch` (:2058-2069) starts a daemon thread; `_icon_watch_loop`
  (:2071-2101) blocks in `RegNotifyChangeKeyValue` on `ICON_BAG_KEY` =
  `HKCU\...\Shell\Bags\1\Desktop` (:218) with `REG_NOTIFY_CHANGE_LAST_SET` (:219) and
  posts `WM_APP_ICONS` (:208) when Explorer rewrites it. `_wnd_proc` turns that into
  `_icons_moved` (:3608), which debounces `TIMER_ICONS` by `icon_save_debounce_ms`
  (default 4000, floor 500) (:2107-2113); the timer saves at :3653-3656. A LOCATIONCHANGE
  on the ListView also calls it (:2118-2123).
- `_find_icon_listview` (:2103) — re-found on the sweep if the handle died (:3651) and
  after a display change (:3725). On demand: `_toggle_restore_icons` (:3189),
  `_snapshot_all`/`_restore_all` (:3172-3179).

### Loose files
- `watch_icon_events.py` — a 40-line diagnostic: hooks `SetWinEventHook` over
  0x8000-0x8010 and prints every accessibility event on the desktop ListView for 60 s
  (:28-40). It answers "is there an event we can save on?"; phantommonitor.py:214-217
  records the answer as no — hence the registry watch.
- `icon_layouts.json` — the live store, **private**: it holds the name of every desktop
  item, so it is gitignored (.gitignore:13) and never goes in a bug report
  (docs/configuration.md:118-121).
- `icons_backup_scrambled.json` — a one-off manual rescue copy, referenced by no code
  (grep: no hits): flat `{name: [x, y]}`, 160-odd icons, no version wrapper — a bare
  version-1 layout in ListView coordinates. Also gitignored (.gitignore:14).

## CASCADE — a change here must reach ALL of these
- Coordinate space: `_snapshot_icons`:2417 (+origin), `_restore_icons`:2459 (-origin),
  `normalize_layouts`:594, `origin_from_signature`:580, `virtual_origin`:573 — change one
  and every saved layout shifts. Asserted at test_phantommonitor.py:355-375.
- Config/UI/docs: `DEFAULT_CONFIG`:107-111, config.example.json:22,30,
  docs/configuration.md:45 and :118-121, README.md:77 and :109-112,
  settings_window.py:534, tray menu :3007-3013.
- Store shape: `_load_layouts`:2375, `_write_layouts`:2397, `normalize_layouts`:594 (bump
  the version there, nowhere else), the on-disk file. The key `topology_signature`:623 is
  shared with window layout memory (:2202, :3674).
- Triggers: `_start_icon_watch`:2058, `_icon_watch_loop`:2071, `_icons_moved`:2107,
  `WM_APP_ICONS`:3608, `TIMER_ICONS`:3653, LOCATIONCHANGE :2118-2123, and
  `icons_quiet_until` (:1943, :3730, :3755), which suppresses saves after a change.
- Display-change path: `_restore_icons`:3726, after `_find_icon_listview`:3725.
- Cross-process layer: `_Remote`, `send`, `LVITEMW` and the buffer offsets are shared
  by `get_icons` and `set_icons`.

## Rules (invariants)
1. **Stored in SCREEN coordinates, read and written in ListView coordinates.** Add
   `virtual_origin` on save (:2417), subtract on restore (:2459). ListView coordinates are
   relative to the virtual desktop's top-left, which moves whenever a display on the left
   changes size, so a raw layout can never carry between arrangements (:2376-2382,
   :596-602).
2. **Never replace an arrangement's entry; merge** (:2413-2419).
3. **Do nothing when auto-arrange is on** — Windows overrides every position, so a restore
   would be a lie. Warn once (:2429-2434, desktop_icons.py:107-108).
4. **Never place an icon where no display exists** — count it off-screen, skip (:2454-2458).
5. **Saves are move-driven, never timed.** The registry Bag key is the trigger; polling
   160 icons across a process boundary is wasteful (:214-217, :2059-2063). Debounce them:
   one drag session, one save (:2107-2108).
6. **Stay quiet after our own restore.** It moves icons, firing the same trigger;
   `icons_quiet_until = now + 10` after a settle (:3730) and `+ 30` on the display-change
   event (:3755) stop it re-saving the scramble.
7. **Every ListView send must be timeout-guarded** (`send`, desktop_icons.py:74-79): a
   plain `SendMessage` to a hung Explorer hangs the tray app's message loop.

## Reuse, don't rebuild
- The desktop ListView → `desktop_icons.desktop_listview()`; in `TrayApp`, the cached
  `self.icon_listview` / `_find_icon_listview()` (:2103).
- Read or write positions → `get_icons(lv)` / `set_icons(layout, lv)`; any other
  cross-process ListView message → `_Remote` (:115) + `send` (:74).
- The saved store → `_load_layouts` / `_write_layouts` (:2375, :2397); never
  `json.load(ICON_LAYOUT_PATH)` directly, or a version-1 file is read as version 2.
- Key and origin → `topology_signature` (:623), `virtual_origin` (:573),
  `origin_from_signature` (:580). All of it at once → `_snapshot_all`/`_restore_all`:3172.

## Traps
- **A layout saved in ListView coordinates is worthless across arrangements.** Version 2
  fixed that; new coordinate handling must go through `normalize_layouts`.
- **Auto-arrange silently defeats everything.** `set_icons` reports icons "moved" and
  Windows snaps them back, so check `auto_arrange_on` first — `_restore_icons` does
  (:2429), a bare `set_icons` call does not.
- **Our own restore looks exactly like a user drag.** Without `icons_quiet_until` the
  scramble would be saved over the good layout within four seconds.
- **The handle goes stale.** Explorer restarting or a wallpaper change re-creates the
  ListView; the sweep re-finds it (:3651), as does the settle (:3725).
- **Names are an icon's only identity.** `set_icons` re-walks the list to map name to
  index (:206-224); renaming a desktop item orphans its saved position, and an icon not in
  the store stays where Explorer put it.

## Related subsystems
- `window-layout-memory.md` — same `WM_DISPLAYCHANGE` to `TIMER_SETTLE` path, same
  `topology_signature` key, restored one line apart (:3722-3726).

LAST VERIFIED: 2026-10-24 · aa2db0f · Read all of desktop_icons.py and watch_icon_events.py, plus `_start_icon_watch`, `_icon_watch_loop`, `_icons_moved`, `_snapshot_icons`, `_restore_icons`, `_load_layouts` and the `WM_APP_ICONS`/`TIMER_ICONS`/`TIMER_SETTLE` branches; parsed the live `icon_layouts.json` (v2, 8 arrangements) and `icons_backup_scrambled.json` (flat, v1-shaped, unreferenced).
