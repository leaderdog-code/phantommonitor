---
subsystem: diagnostics
owns: phantommonitor.py, watch_amp.py, time_it.py
anchors: print_diagnostics, diagnostics_text, _show_diagnostics, ddc_power_state, latest_release, version_tuple, _check_updates, suggest_rule, LATEST_API, RELEASES_URL, APP_VERSION, check_updates_on_start, support_url
test: `python phantommonitor.py --diag` and `--list` (real displays needed; the running app is not). On a running copy: tray → "Settings and more → Diagnose my displays...". `python test_phantommonitor.py` covers `version_tuple` and `looks_like_av_device` but NOT the report text — quit the tray app first (test_phantommonitor.py:11-15), 2+ displays (:33-35).
---
# Diagnostics — the report a user pastes into an issue, plus the amp probes

## What it does
Produces one text report of everything worth knowing about the machine's
displays, offers it three ways (stdout, clipboard, file), optionally asks GitHub
whether a newer release exists, and carries two standalone scripts for the one
thing the report cannot show: how an AV receiver behaves *over time*.

## Where it lives
- `phantommonitor.py:3879-3960` `print_diagnostics(cfg)` — the whole report. Header (`:3881-3887`): Windows and Python versions, DPI awareness (it calls `set_dpi_awareness()` again, `:3884`), frozen-build flag, block rules. Per monitor (`:3890-3938`): name + hotkey slot, hardware id, AV hint, EDID preferred timing, suggested rule, GDI device, bounds, work area, blocked-now, DDC/CI power state, and count/smallest/largest of modes via `EnumDisplaySettings`. Then the pointer fence rect (`:3940-3945`), the "add it to av_devices and report it" nudge (`:3946-3951`), the **five-minute EDID warning** (`:3952-3958`), and the closing ask for DDC reports (`:3959`).
- `:3823-3835` `diagnostics_text(cfg)` — captures `print_diagnostics` with `contextlib.redirect_stdout` rather than duplicating it, so terminal and tray cannot drift.
- `:3488-3516` `_show_diagnostics` — builds the text, writes `LOG_DIR/diagnostics.txt`, copies it to the clipboard (`win32clipboard`, CF_UNICODETEXT), opens it via `open_text_file(path, cfg["editor"])`. The clipboard copy is the point: paste into an issue without a terminal.
- `:3780-3821` `ddc_power_state(mon)` — `dxva2` VCP feature `0xD6`; returns `on`/`standby`/`suspend`/`off (soft)`/`off (hard)`, or `not supported`. Receivers, DP-to-HDMI converters, switches and KVMs do not pass I2C through, which is why a screen switched off behind one cannot be detected.
- `:788-818` `suggest_rule(hwid, name, is_av, preferred, w, h)` — a ladder, best first: `@interlaced`, then `@<1280x720` when under that size, then a plain id plus "switch the screen off, wait a few minutes, look again". Only the last rung always works. Returns `(None, None)` when not flagged AV, so the suggestion block is skipped. Fed by `edid_preferred(hwid)` (`:743`).
- `:315-322` `version_tuple("v1.2.3") -> (1,2,3)`; `:324-337` `latest_release(timeout=6)` — GET `LATEST_API`, UA `PhantomMonitor/<APP_VERSION>`, returns `tag_name` or `None` (logs at INFO, never raises); `:339-347` `open_url`; `:72-75` `PROJECT_URL`, `RELEASES_URL`, `LATEST_API` (GitHub `releases/latest`), `APP_VERSION = "1.1.5"`.
- `:3098-3123` `_check_updates(quiet=False)` — runs `latest_release()` on a daemon thread, because a network call must never freeze the tray. Newer tag → offer to open `RELEASES_URL`. `quiet=True` suppresses both the "could not reach GitHub" and "you have the latest version" boxes.
- `:1982-1983` the only automatic call: `if cfg.get("check_updates_on_start", False): self._check_updates(quiet=True)` in `TrayApp.__init__`.
- `:3005` tray "Diagnose my displays..."; `:3021-3025` "Check for updates", "Project page", and "Support this project" **only if `support_url` is non-empty**.
- `main()`: `--diag` `:4052-4054` (prints, exits 0); `--list` `:4068-4078` (per monitor: hotkey slot, label, `[BLOCKED]`, AV hint).
The two probe scripts below are **not in the repository** — `.gitignore` excludes them as
"throwaway diagnostics with a specific machine's hardware ids baked in", so a clone will not have
them. They are described here because the receiver measurements this project relies on came out of
them, and because anyone reporting a receiver may want to rebuild the equivalent.

- `watch_amp.py` — prints what is on the HDMI chain each time it changes, for 1500 s. Hardcodes the local monitors' hardware ids as "the real monitors" (:7) and reports the first display that is not one of them, `GONE` when there is none. Run `python watch_amp.py`, then plug/unplug while it watches.
- `time_it.py` — measures how long an amp holds the last good EDID, reading the preferred timing straight from `HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\<hwid>\...\Device Parameters\EDID` (:14-37), independently of phantommonitor. `python time_it.py` (defaults to `DON0015`) or `python time_it.py YMH3148`; press Enter as you switch the screen off and it reports the elapsed time when the EDID changes. Gives up after 30 min.

Config keys read here (`DEFAULT_CONFIG`): `check_updates_on_start` (`:160`,
**False**), `support_url` (`:157`, **""**), `log_level` (`:161`, `"INFO"`, used
by `setup_logging` at `:4022`), `editor` (`:137`, `""` = notepad.exe).

## CASCADE — a change here must reach ALL of these
- The report's **format or fields**: `print_diagnostics` (`:3879`) is the single producer — `diagnostics_text` and `_show_diagnostics` follow automatically. But the output is a user-facing contract: `.github/ISSUE_TEMPLATE/bug.yml:11-17` requires "Output of PhantomMonitor.exe --diag", and `receiver-report.yml:35-48` requires it **twice** (screen awake, and several minutes after switching it off). Removing a field invalidates reports already filed against those forms; `docs/configuration.md:128` calls `--diag` "everything worth pasting into a bug report".
- `suggest_rule`'s ladder (`:788`) decides the rules users are told to write — cascade to `parse_block_spec` (`:923`) / `monitor_matches_block` (`:948`), `config.example.json:3` `_blocked_help`, and the receiver form's "Which rule ended up working?" (`receiver-report.yml:49-53`).
- `APP_VERSION` (`:75`) — see `packaging.md`; also `build/installer.iss:17`.
- A new tray item means `_show_menu` (`:3005`, `:3021-3025`) and, if config-driven, the whole chain in `config-and-startup.md`.
- `LATEST_API`/`RELEASES_URL` (`:73-74`) cascade to `_check_updates` (`:3117`) and `latest_release`'s User-Agent (`:329-330`).

## Rules (invariants)
- The update check is **off by default** (`:158-160`): a tray utility should not contact a server unasked. `check_updates_on_start` is the only opt-in; everything else is a menu click.
- The network call runs on a daemon thread (`:3122`). Nothing here may block the message pump.
- `latest_release` never raises — it catches everything and returns `None` (`:335-337`).
- `support_url` empty ⇒ no menu item (`:3023-3025`). The default is empty, and **no Ko-fi, sponsor or donation link is configured anywhere in this repo**.
- `diagnostics_text` must stay a capture of `print_diagnostics`, never a second implementation (`:3826-3828`).
- `--diag` must work without the tray app, and must write nothing.

## Reuse, don't rebuild
- The report anywhere: `diagnostics_text(cfg)` (`:3823`). Never re-collect display facts by hand.
- Display facts programmatically: `enum_monitors()` (`:1010`), `Monitor.label()` (`:501`), `known_displays()` (`:988`).
- Is this an amp? `looks_like_av_device(hwid, name, declared, denied)` (`:715`) — honours `av_devices`/`not_av_devices` in both directions.
- What is it asking for? `edid_preferred(hwid)` (`:743`), cached and cleared on topology change. Which rule to advise? `suggest_rule` (`:788`).
- Version comparison: `version_tuple` (`:315`), which tolerates `v` prefixes and non-numeric chunks. A link: `open_url` (`:339`). A file: `open_text_file` (`:349`).
- A one-off probe script: copy `watch_amp.py`'s three-line bootstrap (`sys.path.insert` + `import phantommonitor as mg` + `mg.set_dpi_awareness()`) rather than re-deriving monitor enumeration.

## Traps
- **0d7625b** — the suggestion exists because behaviour varies by **model, not brand**, and nobody is going to buy a dozen receivers to establish what each does. Confirmed on a Denon AVR-790 with Resolution on Auto: 1080p with a monitor awake behind it, 1080i once the screen is off. `suggest_rule` suggests; it must not decide.
- **The five-minute rule.** Both amps tested held the last good EDID for roughly four minutes after the screen went dark (`:3952-3958`). Comparing `--diag` straight after hitting the TV remote shows no change, and people conclude wrongly that nothing is detectable. `time_it.py` is how that number was measured; re-measure for new hardware rather than quoting it.
- **4a53933** — freeform issues did not reliably contain the two readings that matter, hence the forms. `bug.yml:22-24` warns people **not** to attach `icon_layouts.json`: it lists the name of every item on their desktop. Keep that warning on any new form; `docs/configuration.md:118-121` says the same.
- **852e5a2** — the support link and the update check were added together precisely so neither nags. Touch either, keep the opt-in shape.
- DDC/CI almost always answers `not supported` through an amp — expected, not a bug. A receiver that *does* answer is the interesting case, and the report asks for it (`:3959-3961`).
- `watch_amp.py` is machine-specific: an absolute path to this checkout (:3) and two LG hardware ids (:7). A probe, not a shipped tool; edit it for the machine you are on.
- Releases carry **no VirusTotal links** — nothing in the repo references one. Do not claim otherwise in release notes or the README.

## Related subsystems
`config-and-startup.md` (`log_level`, `editor`, `support_url`, `check_updates_on_start`, `DATA_DIR`/`LOG_DIR`, the `--diag`/`--list` dispatch), `packaging.md` (`APP_VERSION`, what the update check compares against).

LAST VERIFIED: 2026-10-24 · aa2db0f · Read phantommonitor.py:72-75, 82-162, 315-347, 788-818, 1982-1983, 3005-3025, 3098-3123, 3488-3516, 3780-3960, 4019-4078; watch_amp.py and time_it.py in full; all of .github/ISSUE_TEMPLATE/; docs/configuration.md; live config.json (`support_url` empty, `check_updates_on_start` false). Grepped for VirusTotal / Ko-fi / sponsor / donate — only the comments about the empty `support_url`. Read 0d7625b, 4a53933, 852e5a2.
