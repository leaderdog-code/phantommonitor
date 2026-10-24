# MEMORY.md — Phantom Monitor

Index of the subsystem docs. Open the one whose hook matches what you are about to change, and read
it **before** editing. Each doc carries its own `owns:` globs and `anchors:` — the anchors are what
decide which doc an edit needs, because `phantommonitor.py` holds most subsystems and is owned by
many docs.

Mapped 2026-10-24 at commit `aa2db0f`.

## Subsystems

- [Display identity](systems/display-identity.md) — what a display *is*: EDID, hardware ids, friendly names, topology signatures. Never trust the display number.
- [Block rules](systems/block-rules.md) — deciding which displays are phantoms: rule forms (`@800x600`, `@<1280x720`, `@interlaced`), amp detection, and why the primary is never blocked.
- [Window guard](systems/window-guard.md) — the core job: notice a window on a blocked display and move it off, including the full-screen and minimized cases.
- [Cursor fence](systems/cursor-fence.md) — keeping the pointer out of blocked displays. One system-wide clip, so never stomp on another app's.
- [Window layout memory](systems/window-layout-memory.md) — snapshot before a display change, put back only what the change displaced, and un-hide windows that vanished.
- [Desktop icons](systems/desktop-icons.md) — reading and writing icon positions in Explorer's ListView across process boundaries.
- [Arrangements](systems/arrangements.md) — named saved layouts (`base`, `Stream`), applied from the tray, Settings or `--arrange`, with a persisted undo.
- [Pinned apps](systems/pinned-apps.md) — send a named app to a chosen display and keep it there, even when that display is blocked.
- [Tray and hotkeys](systems/tray-and-hotkeys.md) — the hidden message-pump window, the tray menus (left/right/middle click), and the global hotkeys that get swallowed.
- [Settings window](systems/settings-window.md) — the Tk UI, in a child process with its own log, so a modal dialog cannot stall the guard.
- [Config and startup](systems/config-and-startup.md) — every config key, where data lives when the app folder is read-only, which copy wins the logon race, and the CLI flags.
- [Diagnostics](systems/diagnostics.md) — the `--diag` report users paste into issues, the update check, and the standalone amp probes.
- [Packaging](systems/packaging.md) — PyInstaller build, Inno installer, the version pin between `APP_VERSION` and `AppVersion`, and the test harness.

## Also here

- [KNOWN_BUGS.md](KNOWN_BUGS.md) — 27 open findings from the mapping pass, each marked CONFIRMED or REPORTED. None are fixed.
- [how-it-works.md](how-it-works.md) · [configuration.md](configuration.md) · [development.md](development.md) — the user-facing docs. Changing behaviour usually means changing one of these too; the CASCADE section of each subsystem doc says which.

## Standing facts worth knowing before you touch anything

- **The test suite refuses to run while the tray app is live** (`test_phantommonitor.py:11-14`) and needs two real displays. Stop the app first: `python test_phantommonitor.py`.
- **On this machine the app runs from source**, not from the exe — `pythonw.exe phantommonitor.py`. The built artifacts in `build/dist/` are only for GitHub and the second PC.
- **`APP_VERSION` in `phantommonitor.py` and `AppVersion` in `build/installer.iss` must move together.**
- **Adding a config key touches four places**: `DEFAULT_CONFIG`, `config.example.json`, the Settings UI, and `docs/configuration.md`. That chain is currently drifted — see KNOWN_BUGS 20.
- **`looks_like_av_device` and `unsafe_hotkey` exist twice**, in `phantommonitor.py` and `settings_window.py`, deliberately — the settings UI is a separate process and importing the main module would re-execute it. Identical as of this mapping. Change one, change both.
