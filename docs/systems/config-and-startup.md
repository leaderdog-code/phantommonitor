---
subsystem: config-and-startup
owns: phantommonitor.py, PhantomMonitor.vbs, install_elevated_autostart.ps1
anchors: DEFAULT_CONFIG, DATA_DIR, CONFIG_PATH, STARTUP_VBS, _writable, single_instance, running_copy_path, _toggle_autostart, _reload_config, setup_logging, set_dpi_awareness, open_text_file, warn_box
test: `python phantommonitor.py --list`, then `--diag` (real displays, not the running app); `python test_phantommonitor.py` for the full suite — QUIT the tray app first, it refuses to run while `PhantomMonitorWnd` exists (test_phantommonitor.py:11-15) and needs 2+ displays (:33-35). Its last check runs the program as a subprocess and waits for the startup marker — the only proof startup works.
---
# Config and startup — where settings live, which copy wins, and how it gets launched

## What it does
Decides the folder all state is written to, loads and saves `config.json`, sets
DPI awareness before any geometry is measured, opens one rotating log, enforces
a single running copy, dispatches the CLI flags, installs or removes autostart.

## Where it lives
- `phantommonitor.py:35` `APP_NAME`; `:40-43` `APP_DIR` — the exe's folder when frozen (PyInstaller's `__file__` points into a temp dir deleted on exit); `:46-60` `_writable()` writes and removes `.write_probe`.
- `:64-65` `DATA_DIR` = `APP_DIR` **if writable**, else `%LOCALAPPDATA%\PhantomMonitor` (then `~`). A portable exe dropped in Program Files keeps running instead of dying with no window, tray icon or log.
- `:66-71` `CONFIG_PATH`, `LOG_DIR`, `LOG_PATH`, `ICON_LAYOUT_PATH`, `ARRANGEMENT_PATH`, `ARRANGEMENT_UNDO_PATH` — all under `DATA_DIR`. `:76-80` `STARTUP_VBS` = `%APPDATA%\...\Startup\PhantomMonitor.vbs`.
- `:268-293` `setup_logging` — `RotatingFileHandler`, 512 KB x 3, in try/except so logging can never be what stops startup; `path=` gives a child its own file. `:296-312` `set_dpi_awareness` — per-monitor-v2, per-monitor, system, none.
- `:385-394` `load_config` — defaults, then `update()` from disk; unreadable JSON logs a warning and falls back to defaults (a typo silently loses every setting rather than crashing). `:396-401` `save_config`.
- `:349-382` `open_text_file` — notepad.exe default, `editor` override, env vars expanded; deliberately not `os.startfile`, as `.json`/`.log` often have no association.
- `:3962-3984` `running_copy_path`; `:3986-3997` `confirm_box`; `:3999-4012` `warn_box`; `:4014-4016` `single_instance` (mutex `Global\PhantomMonitor_SingleInstance`).
- `:3534-3559` `_toggle_autostart` — writes a VBS launching the exe directly when frozen, else `pythonw.exe` + script; `:3160-3170` `_reload_config`; `:4019-4121` `main()`.
- `PhantomMonitor.vbs:28-44` — finds `pythonw.exe` in four fixed Python 3.10-3.12 paths then PATH; `:17-19` falls back to `%LOCALAPPDATA%\PhantomMonitor\phantommonitor.py` when copied into Startup.
- `install_elevated_autostart.ps1:3-6` — a `RunLevel Highest` logon task, so UIPI cannot stop the guard moving an elevated app's window; `:54-58` deletes `STARTUP_VBS` so only one copy starts.

### Every DEFAULT_CONFIG key (phantommonitor.py:82-162)
`blocked_hwids` rules · `enabled` master switch · `sweep_interval_ms` 2000
(floored at 500, `:1969`) · `locationchange_cooldown_ms` 400 ·
`fix_minimized_restore_position` rewrites a minimized window's restore rect ·
`unfullscreen_borderless` **false** — forcing a frame on an app that manages
its own fights it · `leave_fullscreen` ask a known app to exit full-screen
first · `intercept_hotkeys` low-level key hook for apps that swallow keys ·
`block_cursor` pointer fence · `restore_windows` + `window_save_debounce_ms`
2000 · `restore_icons` + `icon_save_debounce_ms` 4000 (from the last icon move)
· `windowed_fraction` 0.6 when un-maximizing onto a target · `hotkeys` rescue +
monitor_1..3, matched combos SWALLOWED · `hotkey_targets` pins a slot to a
hwid, since display numbers are reassigned by hot-plugging ·
`blocked_rules_parked` unticked rules, verbatim · `ignore_process_names`,
`ignore_window_classes` · `editor` "" = notepad.exe · `av_devices` /
`not_av_devices` amp declarations and overrules — labels only, nothing is
blocked on this basis · `cursor_lock_apps`, `cursor_lock_fullscreen` true,
`cursor_never_lock` (mstsc, explorer, screenclippinghost, snippingtool) ·
`settings_zoom` 1.0 · `app_displays` exe→hwid pin, `app_positions` remembered
rects · `support_url` "" hides the tray item · `check_updates_on_start`
**false** · `log_level` "INFO".

### main() flags (phantommonitor.py:4019-4121)
`--ask-name` (`:4027`) one-field Tk prompt, run as a child so a modal dialog
cannot stall the tray's message pump · `--arrange [n] [--open]` (`:4031-4050`)
**posts `WM_APP_ARRANGE` to the already-running copy**, exiting 1 with "Phantom
Monitor is not running" if there is none — it never starts a second copy ·
`--diag` (`:4052`) · `--settings` (`:4056`) child process, logging to
`logs/settings.log` not the shared log (`:4022-4024`) · `--list` (`:4068`) ·
`--rescue` (`:4080`, *before* the mutex check, so it works while the tray app
runs) · `--no-tray` (`:4113`, *after* it, so it exits 1 if a copy is live).

## CASCADE — a change here must reach ALL of these
Adding or renaming a config key (chain verified, all four steps real):
1. `phantommonitor.py:82-162` `DEFAULT_CONFIG` — the default.
2. `config.example.json` — shipped documentation; installed by `build/installer.iss:65`.
3. `settings_window.py:530-537` `toggles` (booleans) or the write in `apply_and_close` (`:580-640`). Anything absent there is config-file-only.
4. `docs/configuration.md:36-50` "Settings worth knowing", and `:125-132` for a CLI flag.

A key read on a hot path also needs `_reload_config` (`:3160-3170`), which
refreshes only hotkeys, the key hook, the tray icon and a sweep.
A path constant (`:64-71`) cascades to `_show_menu`'s "Edit config file" /
"Open config folder" / "View log" (`:3017-3020`), `_show_diagnostics` (`:3500`),
`logs/phantommonitor.log` hard-coded at test_phantommonitor.py:782, and
`build/installer.iss:77-81,178-180`. The window class `PhantomMonitorWnd`
(`:1988`) cascades to `running_copy_path` (`:3965`), `--arrange` (`:4039`),
`SKIP_CLASSES` (`:195`), `build/installer.iss:101,106`, test_phantommonitor.py:11.

## Rules (invariants)
- `set_dpi_awareness()` runs before anything reads a rect (`:297`, called at `:4025`), or coordinates come back virtualized and every geometry decision is wrong.
- Nothing may raise before logging exists. `setup_logging` swallows `OSError` (`:288-289`) and `DATA_DIR` falls back — under `pythonw` there is no console, so a startup exception is silent.
- A second copy must say **which** copy holds the mutex, in the log *and* a `warn_box` (`:4087-4108`). Each has its own `config.json`, so whichever wins the logon race decides the rules.
- `--arrange` talks to the running copy; it must never build its own `Guard`.
- `support_url` empty means no menu item (`:3023-3025`).

## Reuse, don't rebuild
- Settings: `load_config()` / `save_config(cfg)` (`:385`, `:396`). Never `json.load(CONFIG_PATH)` by hand — you lose the defaults merge.
- A file for the user: `open_text_file(path, cfg.get("editor", ""))` (`:349`). A link: `open_url(url)` (`:339`).
- A yes/no or a warning: `confirm_box` / `warn_box` (`:3986`, `:3999`) — not a fresh `MessageBoxW`.
- The live instance: `win32gui.FindWindow("PhantomMonitorWnd", None)`, plus `running_copy_path()` (`:3962`) for its exe path. Driving it from outside: post a `WM_APP_*` (`:207-212`), handled in `_wnd_proc` (`:3604-3645`).
- Applying changed settings live: `_reload_config()` (`:3160`), which mutates `self.cfg` **in place** because `Guard` holds the same dict (`:3162-3164`).
- Asking for a string: spawn `--ask-name` (`:3301-3304`), never a modal Tk dialog in the pump.

## Traps
- **ad14283** — `setup_logging` tried to create `LOG_DIR` under a read-only program folder, threw before logging existed, and the app died with no window, no tray icon and no log. Hence `_writable`/`DATA_DIR` and the swallowed `OSError`. Any new "write beside the exe" must go through `DATA_DIR`.
- **d02f13a** — two autostarted copies (portable + installed), each with its own config; the loser exited with a log line nobody reads. Symptom: "my settings stopped working" with a tray icon sitting there happily. An hour to diagnose. Hence the named `warn_box` and the installer's `RemovePortableAutostart`.
- **5c6792e** — the settings child opened the *same* rotating log; at rollover the rename hit a sharing violation and both processes spewed tracebacks on every write, stopping startup completing. Hence `setup_logging(path=...)` and `settings.log`.
- **d04c508** — `cursor_locked_app` was read but never initialised; the `AttributeError` killed startup silently under `pythonw` while the whole suite passed. Hence the startup smoke test. Every `self.*` a handler reads must be set in `TrayApp.__init__` (`:1929-1983`).
- `config.example.json` has drifted: it omits `check_updates_on_start`, `av_devices`, `not_av_devices`, the three `cursor_lock*` keys and `settings_zoom`, and sets `restore_windows: false` where `DEFAULT_CONFIG:105` has `true`. Step 2 is the one that gets skipped.
- Comments inside `DEFAULT_CONFIG` have slipped off their keys: the `restore_icons` comment sits above `restore_windows` (`:101-105`), the `support_url` one above `app_displays` (`:138-146`). Read the key, not the comment above it.

## Related subsystems
`diagnostics.md` (`--diag`, `--list`, update check, `log_level`/`editor`/`support_url`), `packaging.md` (`APP_VERSION`, the installer's single-copy handling and autostart cleanup).

LAST VERIFIED: 2026-10-24 · aa2db0f · Read phantommonitor.py:35-162, 268-401, 1929-1983, 2995-3030, 3160-3170, 3488-3560, 3962-4121; PhantomMonitor.vbs; install_elevated_autostart.ps1; config.example.json; live config.json; settings_window.py:1-40, 505-640; docs/configuration.md. Confirmed the four-step config-key chain and its drift; read ad14283, d02f13a, 5c6792e, d04c508.
