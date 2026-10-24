---
subsystem: settings-window
owns: settings_window.py, phantommonitor.py
anchors: settings_window, apply_zoom, zoom_factor, font_baseline, windows_scale, ZOOM_CHOICES, ZOOM_FONTS, build_arrangements_tab, _open_settings, WM_APP_SETTINGS_CLOSED, settings_child, settings_zoom, looks_like_av_device, unsafe_hotkey, AV_VENDORS, AV_NAME_HINTS, ask_for_name, assigned_slot, absent_vars, auto_av, not_av_devices
test: No automated UI test — Tk is never driven by the suite. Launch it: `python phantommonitor.py --settings` (needs real displays; `main()` 4055-4064 enumerates them). `python test_phantommonitor.py` covers the duplicated logic — `unsafe_hotkey` 417-430, `looks_like_av_device` 468-490, 755-771 — but against the phantommonitor.py copies, NOT these; stop the tray app first (test:11-14). By hand: open it from the tray, change the zoom twice, Save, then check `logs/settings.log` and that `config.json` kept the keys the UI never shows.
---
# Settings window — the Tk UI, in a child process so it cannot stall the guard

## What it does
A separate process (`phantommonitor.py --settings`, main() 4055-4064) that reads
`config.json` with `load` (17), shows two tabs — Displays and Arrangements — and writes
it back with `save` (25). It moves nothing: the tray app reloads when the child exits.

## Where it lives
- **Launch** — `TrayApp._open_settings` (phantommonitor.py:3125) spawns
  `[sys.executable, (<script>,) "--settings"]`, keeps the handle in `settings_child`,
  suspends the hotkeys (3147-3150) and threads a wait that posts
  `WM_APP_SETTINGS_CLOSED` (209) at exit; the `_wnd_proc` branch (3640-3645) clears the
  handle, calls `_reload_config` (3160) and re-installs the hook.
- **Entry** — `main()` 4055-4064 builds `(name, hwid, w, h, x, y, primary)` tuples from
  `enum_monitors()` plus the not-attached list from `known_displays()`, then calls `run`
  (345). `ask_for_name` (phantommonitor.py:3837, `--ask-name` 4027) is a second such
  child dialog.
- **Its own log** — `setup_logging` (phantommonitor.py:268) takes a `path`; `main()`
  (4192-4210) passes `logs/settings.log` for `--settings` and `logs/ask_name.log`
  for `--ask-name`. Separate files, not merely non-default ones: `--ask-name` can be
  spawned while this window is open.
- **Zoom** — `ZOOM_CHOICES` 96, `ZOOM_FONTS` 97, `font_baseline` 102 (captured at 356),
  `apply_zoom` 113, `windows_scale` 148 (`GetDpiForWindow`), `zoom_factor` 163. Combobox
  423-429, live on `<<ComboboxSelected>>`, saved at 636.
- **Scrolling body** — canvas + frame 373-377; `refit` (380) sizes the window to its
  content up to 85% of screen height, scrollbar only when needed; `on_wheel` 396.
- **Displays tab** — rows at 470-504 in slot order via `assigned_slot` (454);
  `rule_for` (448) finds a block rule by hardware id. Not-attached displays get their
  own frame and `absent_vars` (506-525). Toggles 527-543, hotkey boxes 553-566,
  write-back in `apply_and_close` (580-640).
- **Arrangements tab** — `build_arrangements_tab` (182), added at 659; it posts to the
  guard rather than moving windows. See arrangements.md.

## CASCADE — a change here must reach ALL of these
- **A config key the UI should expose**, verified chain: `DEFAULT_CONFIG`
  (phantommonitor.py:82-164) → `config.example.json` → the widget here (`toggles`
  530-537) → `apply_and_close` (580-640) must write it → docs/configuration.md:38-50.
  The chain is **already broken**: `av_devices`, `not_av_devices`, the three `cursor_*` keys, `settings_zoom` and
  `check_updates_on_start` are in `DEFAULT_CONFIG` but not in `config.example.json`,
  which docs/configuration.md:3 calls complete.
- **`unsafe_hotkey` / `RESERVED` / `looks_like_av_device` / `AV_VENDORS` /
  `AV_NAME_HINTS`**: both copies — settings_window.py:35-93 and
  phantommonitor.py:706-738, 1870-1890 — plus test:417-430, 755-771, and the key
  list at 568-576.
- **The monitor tuple shape** passed to `run`: phantommonitor.py:4057-4059 and the
  unpacks here (470, 590, 615, 652).
- **The block-rule syntax**: `rule_for` (448) and `apply_and_close` (589-612) split on
  `"@"` by hand, while `parse_block_spec` (phantommonitor.py:923) is the real parser
  that `_make_block_toggle` (3041) uses. Keep them in step.
- **How the child is launched**: `_open_settings` (3125), `WM_APP_SETTINGS_CLOSED`
  (209, 3640), `main()` 4019-4064, docs/configuration.md:130.
- **Zoom or DPI**: `apply_zoom` (113), `zoom_factor` (163), `settings_zoom`
  (`DEFAULT_CONFIG` 159), docs/configuration.md:24-28.

## Rules (invariants)
1. **It runs as a CHILD PROCESS, never a thread.** Tk wants its own event loop and the
   tray app already runs a Win32 pump on which evacuation, the fence and the hotkeys
   depend; a modal Tk dialog inside it would stall all of them, and a UI crash must not
   take the guard down (1-7, phantommonitor.py:3126-3130).
2. **It has its OWN log file.** Two processes of one script holding one
   `RotatingFileHandler` collide at the rollover: the rename fails with a sharing
   violation and both spew tracebacks on every write, stopping startup
   (phantommonitor.py:270-277, 4021-4023).
3. **One settings window at a time.** `_open_settings` returns early while
   `settings_child.poll()` is None (3132-3134); two meant the last Save silently
   overwrote the other (da32509). Hotkeys are suspended for the child's lifetime
   (3147-3150) and restored at 3640.
4. **The zoom is live and computed from a stored baseline.** `font_baseline` (102) is
   captured once before any scaling and `apply_zoom` (113) always multiplies *that*, so
   repeated changes cannot compound (135cfb1). Named-font sizes only, never
   `tk scaling` — points times points. Font sign is kept (138-141): negative is pixels.
5. **It honours Windows' own DPI scaling** (948a62f). The app declares per-monitor-v2
   awareness so Windows stops scaling it; `zoom_factor` (163) opens at `windows_scale`
   unless a saved `settings_zoom` says otherwise — a saved choice wins.
6. **The body scrolls** (368-394); at 3x the content outgrows a 4K panel.
7. **Hotkeys are typed, not recorded**, validated on **Save** not as you type
   (553-576, 581-587); the window stays open with the reason shown.
8. **Unticking a block parks the rule verbatim** (`new_parked` 589-612); rules for
   displays not shown pass through untouched (612), as `_make_block_toggle` does.
9. **The amp tick is a guess you can overrule both ways** (94a27c9). `auto_av` (469)
   holds what the built-in list recognised; that is never written back, so a later
   version can revise its guesses, while an overrule goes to `not_av_devices` (630).

## Reuse, don't rebuild
- Reading/writing the config → `load` (17) / `save` (25). Do not reopen the file.
- Asking the guard to act → post to `PhantomMonitorWnd` (308-312, 322-326). **Never
  move a window, register a hotkey or touch a display from here.**
- Scaling the UI → `apply_zoom` (113) with the baseline from `font_baseline` (102).
  Never scale from current sizes, never add `tk scaling`. Current zoom → `zoom_factor`
  (163); Windows' own → `windows_scale` (148). Resizing after adding widgets →
  `refit` (380), already bound to the body's `<Configure>`.
- A new tab → `build_arrangements_tab` (182): take `tabs` and `config_path`, do your
  own IO, add yourself at the end of `run`. A one-field prompt → `ask_for_name`
  (phantommonitor.py:3837) via `--ask-name`.

## Traps
- **`looks_like_av_device` and `unsafe_hotkey` are DUPLICATED here** (46-93) from
  phantommonitor.py:706-738 and 1870-1890, deliberately: importing the main module here
  would re-execute it. **Diffed at aa2db0f by AST, docstrings stripped: `AV_VENDORS`,
  `AV_NAME_HINTS`, `looks_like_av_device` and the reserved set are identical;
  `unsafe_hotkey` differs only in the constant name. NOT diverged — keep it so, and
  re-diff before trusting this.**
- **Recording hotkeys by keypress was tried twice and abandoned** (7117678, e8da684,
  reverted by 5143462). Ctrl+Alt is AltGr, so on many layouts it gives a symbol rather
  than the key; then come dead keys, non-Latin layouts and IMEs, and a stray ctrl+c in
  a box got bound. Typed boxes plus validation on Save are settled; do not "improve".
- **Sharing the main rotating log was a real startup-blocking bug**, caught only by the
  startup smoke test (5c6792e). Any NEW child process needs its own log path, and its own
  file rather than another child's: pointing `--ask-name` at `settings.log` would just move
  the collision, since both can be open at once.
- **`load` does not merge `DEFAULT_CONFIG`** and cannot: importing the main module from
  this child process would re-execute it. So every toggle now carries its own default in
  the `toggles` table, and those defaults must be kept in step with `DEFAULT_CONFIG` by
  hand. Defaulting them all to `True` was wrong for `block_cursor` (**False**,
  phantommonitor.py:99): a config missing the key, or an unreadable one — `load` returns
  `{}` — showed every box ticked, and Save turned the pointer fence on for a user who had
  never asked for it. **Adding a toggle means adding its real default here.**
- **The slot combobox does not enforce one display per slot** (614-618,
  `new_targets[slot] = hwid`, last writer wins) where the tray's `_make_assign_action`
  (phantommonitor.py:3066) does: two displays on slot 2 silently lose one.
- **`slot_count = max(4, len(monitors))` (410)** bounds the hotkey rows, so a
  hand-edited `monitor_5` on four displays is dropped on the first Save.
- Displays sort by slot **on open, not live** (462) — live sorting made rows jump
  mid-edit (d84103e). `build_arrangements_tab` hard-codes `0x8000 + 5` / `0x8000 + 6`
  (310, 325) instead of the named constants, and docs/configuration.md:7-32 never
  mentions that tab.

## Related subsystems
tray-and-hotkeys (launches this; shares `unsafe_hotkey`) · arrangements ·
config-and-startup · display-identity (`enum_monitors`, `known_displays`).

LAST VERIFIED: 2026-10-24 · aa2db0f · Read all 660 lines of settings_window.py and the phantommonitor.py sites (_open_settings 3125, _reload_config 3160, WM_APP_SETTINGS_CLOSED 3640, main() 4019-4064, setup_logging 268, ask_for_name 3837). Diffed both duplicated helpers by AST, docstrings stripped — identical bar the constant name. Compared DEFAULT_CONFIG against config.example.json and docs/configuration.md programmatically (7 keys missing). Commits 7117678, e8da684, 5143462, da32509, d84103e, 3badade, 135cfb1, 948a62f, 94a27c9, 5c6792e read. UI NOT launched.
