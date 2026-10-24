---
subsystem: tray-and-hotkeys
owns: phantommonitor.py, test_phantommonitor.py
anchors: TrayApp, _wnd_proc, _create_window, WM_TRAYICON, WM_APP_HOTKEY, HOTKEY_ID_BASE, PhantomMonitorWnd, _show_menu, _make_block_toggle, _make_move_action, _run_action, _register_hotkeys, _unregister_hotkeys, _rebuild_hotkeys, _install_key_hook, _key_action, _mods_held, _on_key, resolve_hotkeys, parse_hotkey, unsafe_hotkey, RESERVED_HOTKEYS, NAMED_KEYS, KBDLLHOOKSTRUCT, WH_KEYBOARD_LL, pick_menu_target, hotkey_targets, intercept_hotkeys, _add_tray_icon
test: `python test_phantommonitor.py` (docs/development.md:24-29: 170 checks). REFUSES to run while the tray app is live (test:11-14, looks for `PhantomMonitorWnd`); needs 2+ real displays (test:33-35). Covers tray-menu capture 203-227, hotkey pinning 377-391, hotkey safety 417-430. The startup smoke test 775-806 runs the app as a subprocess and waits for the log marker "tracking" — the only proof `TrayApp.__init__` completes.
---
# Tray and hotkeys — the hidden window that is the whole UI and input path

## What it does
`TrayApp` (phantommonitor.py:1928) is the program's only live object: a hidden
message-only window, the tray icon and its three menus, the window event hooks, a
low-level keyboard hook, the `RegisterHotKey` registrations and five timers. Everything
else is driven from `_wnd_proc` (3565). There is no other UI thread.

## Where it lives
- **Hidden window** — `_create_window` (1986) registers class `PhantomMonitorWnd`, a 0×0
  window. That name is the public handle: `--arrange` finds it (4043), settings posts to
  it (settings_window.py:309, 323), the suite detects a running copy by it (test:11),
  and it is in `SKIP_CLASSES` (194) so the guard never manages it.
- **Icon** — `_make_icons` (2000) → `make_icon_file` (1801, Pillow only) if the .ico is
  missing, else `load_icon` (1818); add/update/remove 2018-2040, `_tooltip` 2010.
- **Clicks**, in the `WM_TRAYICON` branch (3566): **left** → `_open_settings` (3567),
  **right** → `_show_menu` (3575), **middle** → `_show_arrange_menu` (3582),
  double-click → a sweep (3587).
- **Right-click menu** — `_show_menu` (2866) with local `add` / `add_sub` / `sep` helpers
  (2871-2890) taking an `into=` submenu. Top level: a Block tick per display
  (2905-2907), the two master switches (2912-2917), Rescue (2919), Arrangements, pinning,
  "Move <title> to", "Settings and more" (3003-3029), Quit. Handlers go into
  `self.actions`, run from `WM_COMMAND` (3591-3598).
- **Hotkey parsing** — `NAMED_KEYS` (1830), `parse_hotkey` (1842) → `(mods|MOD_NOREPEAT,
  vk)` or None, `RESERVED_HOTKEYS` (1870), `unsafe_hotkey` (1877), `resolve_hotkeys`
  (1893) → `{0: rescue, N: monitor N}`, dropping unsafe/unparseable entries with a log
  line and still reading the legacy `hotkey_rescue` / `hotkey_move_prefix` keys.
- **Hotkey delivery, two independent paths** — `RegisterHotKey` at
  `HOTKEY_ID_BASE + action` (`_register_hotkeys` 2646, `_unregister_hotkeys` 2664)
  arriving as `WM_HOTKEY` (3600); and a `WH_KEYBOARD_LL` hook (`_install_key_hook` 2584,
  gated on `intercept_hotkeys`) whose `_on_key` (2626) reads `KBDLLHOOKSTRUCT.vkCode`
  (226), matches via `_key_action` (2618) + `_mods_held` (2605), posts `WM_APP_HOTKEY`
  (3604), returns 1 to swallow the key.
- **Actions** — `_run_action` (2668): 0 unclips the cursor, `_unhide_vanished`, sweeps
  with `include_offscreen=True`, re-fences; 1-9 call `Guard.move_active_to` (1774) with
  `pick_menu_target` (1340). `_action_name` 2643.
- **Timers** (179-183) are serviced at 3647-3741; the six `WM_APP_*` ids (207-212) at
  3604, 3608, 3640, 3612, 3621, 3636. `_toggle_enabled` (3525) flips `enabled`, saves,
  re-fences, repaints, sweeps. `_quit` (3561) → `WM_DESTROY` (3763) unhooks everything
  and removes the icon.

## CASCADE — a change here must reach ALL of these
- **A new hotkey action (beyond rescue + monitor_1..9)**: `resolve_hotkeys` (1893-1926,
  the `monitor_` branch), `_run_action` (2668-2683, handles only 0 and 1-9),
  `_action_name` (2643), `_unregister_hotkeys` (2664-2666, hard-coded `range(0, 10)`),
  `DEFAULT_CONFIG["hotkeys"]` (119-124), `config.example.json`, the move submenu
  (2973-2985), the `rows` list at settings_window.py:559-562,
  docs/configuration.md:30-32, README.md:373-382, test_phantommonitor.py:417-430.
- **Changing `unsafe_hotkey` / `RESERVED_HOTKEYS`**: the verbatim copies at
  settings_window.py:35-43, :76-93, the key list there at 568-576, and test:417-430.
- **A new `WM_APP_*` message**: declare at 207-212, add a `_wnd_proc` branch, AND update
  every poster — settings_window.py:310 and :325 hard-code `0x8000 + 5` / `0x8000 + 6`,
  and `main()` posts `WM_APP_ARRANGE` at 4051.
- **A new tray menu item**: `_show_menu` (2866); a handler factory (`_make_block_toggle`
  3041, `_make_move_action` 3094, `_make_pin_action` 3198); README.md:50-56 if it changes
  what a click does. Layout items live in `_show_arrange_menu` (2811) too.
- **Renaming the window class**: 1988, `SKIP_CLASSES` 194, 4043,
  settings_window.py:309, :323, test_phantommonitor.py:11.
- **A new config key**: `DEFAULT_CONFIG` (82-164), `config.example.json`,
  docs/configuration.md:38-50.

## Rules (invariants)
- **A matched hotkey is swallowed.** `_on_key` returns 1 (2638), so the combination never
  reaches the focused app. Hence `unsafe_hotkey` (1877) demands **two** modifiers: plain
  `ctrl+alt+<digit>` is a popular game binding (116-119), and one modifier would kill a
  universal shortcut machine-wide with no visible cause. `resolve_hotkeys` refuses an
  unsafe entry even from a hand-edited config (1900-1904).
- **Hotkeys resolve through an EDID hardware id, not a display number.** `hotkey_targets`
  → `Guard.target_for` (1476); hot-plugging renumbers displays, so a number alone can
  silently start meaning a different physical screen (test 377-391). A hot-plug
  (`WM_DISPLAYCHANGE`, 3744) therefore re-registers the hotkeys.
- **The menu's target window must be captured before the menu opens.** Showing a popup
  takes the foreground, after which `GetForegroundWindow()` returns our own hidden
  window — hence `last_focused` (2158), `pick_menu_target` (1340), and
  `_make_move_action` (3094) binding `self.menu_target` at build time (test 203-227).
- **Nothing slow runs on the pump.** `_on_key` posts and returns (Windows evicts a slow
  hook, 2634-2636); `_check_updates` (3098) threads; modal dialogs are child processes.
  **The primary display is never blocked** — the taskbar and this icon live there.
- The win-event hooks are exactly `EVENT_SYSTEM_FOREGROUND`,
  `EVENT_SYSTEM_MOVESIZESTART..MOVESIZEEND`, `EVENT_OBJECT_SHOW` and
  `EVENT_OBJECT_LOCATIONCHANGE` (`_install_hooks` 2042-2058), all
  `WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS`. `_on_event` (2115) picks desktop
  icon moves off the ListView *before* the top-level filter at 2124 — child id, not 0.

## Reuse, don't rebuild
- Parsing a hotkey string → `parse_hotkey` (1842); validating → `unsafe_hotkey` (1877);
  the whole table → `resolve_hotkeys` (1893). Never re-parse by hand.
- "Which window should this act on?" → `pick_menu_target` (1340), never
  `GetForegroundWindow()` from a menu handler.
- Moving a window to a slot → `Guard.move_active_to` (1774): it refuses blocked targets,
  unmanageable windows and dead handles already.
- A menu item that flips a config key → copy `_make_block_toggle` (3041) or
  `_toggle_cursor_block` (3518): read, flip, `save_config`, re-apply, repaint.
- Re-arming after a config change → `_rebuild_hotkeys` (2806), or `_reload_config`
  (3160) for the lot. Never call `RegisterHotKey` directly.
- Building a menu → the `add` / `add_sub` / `sep` closures (2871-2890). A file or URL →
  `open_text_file` 349 / `open_url` 339. A modal prompt → a child process:
  `ask_for_name` 3837 via `--ask-name` (3298-3305), or `confirm_box` 3986 / `warn_box` 3999.

## Traps
- **A startup `AttributeError` is invisible.** `cursor_locked_app` was read in
  `_apply_cursor_clip` but never initialised, so startup died after the hotkeys logged
  and before the snapshots; under `pythonw` there is no console, so the icon never
  appeared and the whole suite still passed (d04c508). Hence every attribute is
  initialised up front in `__init__` (1929-1966) and the suite's subprocess start waits
  for the "tracking" line (test 775-806, logged 2207). **Add an attribute to `__init__`,
  not to the method that first uses it.**
- **A `WndProc` swallows exceptions**, so a broken menu builder reads as "clicking does
  nothing". Every click branch try/excepts and logs (3570, 3577, 3584, 3595).
- **Our own hook ate the combinations being typed into Settings.** `_open_settings`
  stands the hotkeys down for the child's lifetime (3147-3150); `WM_APP_SETTINGS_CLOSED`
  (3640) puts them back (e8da684).
- **`_make_block_toggle` parks a rule, it does not discard it.** Unticking and re-ticking
  used to downgrade `DON0015@interlaced` to a bare hardware id (3041-3064, ee10fe6).
- **Low-level hooks are called most-recently-installed first**, so mstsc's would
  otherwise eat everything, worst in full screen where the icon is covered too.
  `_refresh_key_hook` (2599) re-installs ours on every foreground change (2576-2583).
- **The menu grew to twenty flat items twice** (e775898, dc44aa7, 3e9c4e7, 18c6c49).
  Settled: master switches and daily actions at top level, the occasional behind
  "Settings and more", autostart last behind a separator.

## Related subsystems
settings-window · arrangements · window-layout-memory · window-guard.

LAST VERIFIED: 2026-10-24 · aa2db0f · Read TrayApp.__init__ through _wnd_proc and every hotkey helper. Confirmed left/right/middle dispatch 3567/3575/3582, the swallow 2638, the two-modifier rule 1884-1886, hotkey_targets via target_for 1476, the four EVENT_* ranges 2044-2049. Commits d04c508, e8da684, fab0f53, 5143462, ee10fe6, e775898, dc44aa7, 3e9c4e7, 18c6c49 read. Suite NOT run (needs the app stopped + real displays).
