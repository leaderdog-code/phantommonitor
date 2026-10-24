# SYSTEM.md — phantommonitor constitution

> Single source of truth for how the system fits together. Read before touching code.
> If a window has had this open a while, re-read — it may have changed.

Mapped 2026-10-24 at commit `aa2db0f`. Subsystem detail lives in [MEMORY.md](MEMORY.md) → `systems/`.

---

## INVARIANTS (rules that must never break)

1. **Never block the primary display.** The tray icon lives there; blocking it would evacuate the
   only UI the user has. Enforced in `Guard.blocked()` (`phantommonitor.py:1430-1444`), which drops
   the primary from the blocked list and warns once.
2. **Never stomp on a cursor clip the app does not own.** `ClipCursor` is a single system-wide clip.
   Check `GetClipCursor` and `clip_is_owned()` first (`:655`), and release what you set. A stale clip
   traps the user's pointer with no visible cause.
3. **Only put back windows the display change actually displaced.** The restore compares against a
   snapshot frozen the moment the change began (`_snapshot_windows`, `:2178`). A window the change
   did not touch is left strictly alone — moving it is indistinguishable from the bug being fixed.
4. **Identify a display by its EDID hardware id, never by its number.** Display numbers are
   reassigned by hot-plugging, so a number silently starts meaning a different physical screen.
   Rules, pins, hotkey targets and arrangement slots all key on the hwid.
5. **A user's explicit overrule beats any guess.** `not_av_devices` and a manually ticked Block rule
   must survive the display being unplugged, re-detected, or guessed differently by the vendor list.
6. **Do not force a window frame onto an app that manages its own.** Ask it to leave full-screen
   (`leave_fullscreen`, `FULLSCREEN_TOGGLES`, `:1226`); forcing styles makes mstsc and the guard
   fight each other. Relocating is the guard's job; window mode is the app's.
7. **One definition per concept, and the settings window is a separate process.** Anything the UI
   needs from the main module is duplicated deliberately (`settings_window.py:42-44`), because
   importing it would re-execute the app. Change one copy, change both.
8. **Keep `APP_VERSION` and `AppVersion` in step** (`phantommonitor.py:75`, `build/installer.iss:17`).
   The installer refuses to upgrade sensibly otherwise.

---

## WHAT THIS PROGRAM IS (one paragraph)

A Windows tray utility that stops Windows treating an AV receiver as a monitor. An amp or switch on
HDMI advertises a valid EDID, so Windows creates a real display nobody can see — windows open onto
it, the mouse slides into it, and a display change scatters the desktop. Phantom Monitor identifies
such displays from their EDID, evacuates any window that lands on one, optionally fences the pointer
out of them, and puts windows and desktop icons back where they were after the topology changes. It
also keeps named window layouts ("base", "Stream") that can be applied from the tray, Settings or a
Stream Deck. Working correctly means: nothing ever appears on the phantom, the pointer never gets
stuck, and a monitor-off/on cycle leaves the desktop exactly as it was.

---

## THE MAP (how the pieces connect)

```
Windows display/window events
   SetWinEventHook + WM_DISPLAYCHANGE  ->  TrayApp._on_event / _wnd_proc  (tray-and-hotkeys)
          |
          +-- enum_monitors + EDID          ->  display-identity
          +-- which of these is a phantom?  ->  block-rules
          |
          +-- window on a blocked display   ->  window-guard        -> move it off
          +-- pointer heading for one       ->  cursor-fence        -> ClipCursor
          +-- topology changed              ->  window-layout-memory -> put windows back
          |                                     desktop-icons        -> put icons back
          +-- user asked for a layout       ->  arrangements         -> place/launch apps
          +-- app has a home display        ->  pinned-apps          -> send it there
```

- **display-identity** — `phantommonitor.py` — what a display *is*. Must not decide policy.
- **block-rules** — `phantommonitor.py`, `settings_window.py` — which displays are phantoms.
- **window-guard** — `phantommonitor.py` — the evacuation. The only place that moves a window for
  policy reasons.
- **cursor-fence** — `phantommonitor.py` — the pointer. Must always be able to release.
- **window-layout-memory** — `phantommonitor.py` — snapshot/restore across display changes.
- **desktop-icons** — `desktop_icons.py` — Explorer ListView positions, cross-process.
- **arrangements** — `phantommonitor.py`, `settings_window.py` — named layouts on disk.
- **pinned-apps** — `phantommonitor.py` — an app's home display.
- **tray-and-hotkeys** — `phantommonitor.py` — the hidden window that is the whole UI and input path.
- **settings-window** — `settings_window.py` — the Tk UI, in a child process.
- **config-and-startup** — `phantommonitor.py` — keys, data location, single instance, autostart.
- **diagnostics** — `phantommonitor.py`, `watch_amp.py` — `--diag`, update check, amp probes.
- **packaging** — `build/` — exe, installer, version pin, test harness.

---

## SUBSYSTEM LEDGER (where a concept lives)

Run `python scripts/blast_radius.py <concept>` for the live answer; this table is the starting point.

| Concept | Where to look (files / grep terms) |
|---|---|
| a display's identity | `hwid_for_device`, `friendly_name`, `edid_preferred`, `Monitor.label` |
| "is this a phantom?" | `parse_block_spec`, `monitor_matches_block`, `Guard.blocked`, `blocked_hwids` |
| "is this an amp?" | `looks_like_av_device`, `AV_VENDORS`, `av_devices`, `not_av_devices` — **two copies** |
| moving a window | `Guard.move_window`, `placed_rect`, `is_manageable`, `window_displaced` |
| full-screen handling | `FULLSCREEN_TOGGLES`, `_leave_fullscreen_then_move`, `_ask_for_fullscreen`, `full` slot flag |
| the pointer | `cursor_clip_rect`, `cursor_lock_rect`, `clip_is_owned`, `block_cursor` |
| remembering positions | `_snapshot_windows`, `_restore_windows`, `topology_signature`, `icon_layouts.json` |
| desktop icons | `desktop_listview`, `get_icons`, `set_icons`, `LVM_SETITEMPOSITION`, `ICON_BAG_KEY` |
| named layouts | `_apply_arrangement`, `_gather_slots`, `fill_slots`, `arrangement.json`, `WM_APP_ARRANGE` |
| an app's home display | `app_displays`, `place_assigned`, `_rehome_pinned`, `assigned_display` |
| hotkeys | `parse_hotkey`, `resolve_hotkeys`, `RESERVED_HOTKEYS`, `hotkey_targets`, `_on_key` |
| a config key | `DEFAULT_CONFIG`, `config.example.json`, `settings_window.py`, `docs/configuration.md` |
| where data lives | `DATA_DIR`, `_writable`, `CONFIG_PATH`, `LOG_PATH` |
| versions | `APP_VERSION`, `build/installer.iss` `AppVersion` |

---

## KNOWN HAZARDS

The ones that have bitten repeatedly (the full working list is kept outside the repository):

- **Sending a full-screen keystroke before the window has landed.** It goes to whatever display the
  window is still on. `_ask_for_fullscreen` polls for the target first — always pass `want_hwid`.
  (The restore path at `:2361` currently does not. KNOWN_BUGS 1.)
- **"Fixing" the guard by skipping app-managed windows.** Done once (`2f47591`), it stranded Remote
  Desktop on the wrong display. Those windows must be *moved*, not skipped (`eb65fed`).
- **"Arrangements must not touch full-screen windows"** (`8b69b69`) removed a capability the owner
  relied on and was reverted. Check what a behaviour is *for* before guarding against it.
- **Two copies running from different folders**, each with its own config, racing at logon. The
  symptom is "my settings stopped working" with a tray icon sitting there happily. `single_instance`
  now names the other copy (`:4087-4108`).
- **A child process sharing the rotating log.** At the rotation boundary both processes threw and
  startup never completed. Every child dialog now has a log of its own, and not the same one as
  each other: `settings.log` for `--settings`, `ask_name.log` for `--ask-name`, which can be opened
  while the settings window is already up.
- **Claiming amp behaviour that was never measured.** Several confident statements about receivers
  reverting on a timer had to be withdrawn. Read the EDID; do not infer from the current resolution.
- **A silent startup crash under pythonw** — no window, no log, and the whole test suite still
  passed (`d04c508`). The suite now launches the app as a subprocess and requires the startup marker.

---

## HOW TO RUN / TEST / BUILD

- Run from source: `python phantommonitor.py` (or `PhantomMonitor.vbs` for no console)
- List displays: `python phantommonitor.py --list`
- Diagnostics: `python phantommonitor.py --diag`
- Test: `python test_phantommonitor.py` — **stop the tray app first**; needs two real displays
- Build: `powershell -ExecutionPolicy Bypass -File build\build.ps1`, then `build_installer.ps1`
- Entry point: `phantommonitor.py` `main()` (`:4019`)
