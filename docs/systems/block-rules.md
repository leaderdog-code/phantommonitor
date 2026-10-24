---
subsystem: block-rules
owns: phantommonitor.py, settings_window.py, test_phantommonitor.py
anchors: parse_block_spec, monitor_matches_block, looks_like_av_device, AV_VENDORS, AV_NAME_HINTS, suggest_rule, blocked_hwids, blocked_rules_parked, av_devices, not_av_devices, _make_block_toggle, primary_warned
test: python test_phantommonitor.py  (170 checks; checks 6 and 16 plus :700-718 cover this. Needs REAL hardware — two or more attached displays — and refuses to run while the tray app is up, test_phantommonitor.py:11-14. Read-only cross-check: python phantommonitor.py --diag.)
---
# Block rules — deciding which displays are phantoms, without locking the user out

## What it does
Answers one question per attached display: *should windows and the pointer be
kept off it?* A rule is a hardware id with an optional qualifier. Rules live in
`blocked_hwids`; `Guard.blocked()` turns them into the live blocked list that
evacuation, the fence, the tray ticks and the move hotkeys all consult.

Separately — and it must stay separate — the AV-device *guess*
(`looks_like_av_device`) only labels a display in the UI. **Nothing is ever
blocked because something looks like an amp.**

## Where it lives
- **Rule forms** — `parse_block_spec()` `phantommonitor.py:923`, returning
  `(hwid, size, smaller_than, interlaced_only)`:

  | Spec | Meaning |
  |---|---|
  | `DON0015` | always block that id (`size=None`, `:945`) |
  | `DON0015@800x600` | only at exactly that size (`:939-941`) |
  | `DON0015@<1280x720` | only while it has fewer PIXELS (`:937-941`) |
  | `DON0015@interlaced` | only while its EDID asks interlaced (`:934`) |

  A bad size warns and falls back to the id alone (`:942-944`).
- **Matching** — `monitor_matches_block()` `:948`: id must match (`:965-966`);
  `@interlaced` consults `edid_preferred(hwid)[2]` (`:967-973`); `<` compares
  **area**, `width * height < size[0] * size[1]` (`:978-984`); else exact
  `(width, height)` (`:985`).
- **The live decision** — `Guard.blocked()` `:1426`: apply every spec
  (`:1427-1429`), **drop the primary** with a once-per-topology warning
  (`:1430-1444`, flag reset at `:1413`), refuse to block everything
  (`:1445-1448`). `Guard.allowed()` `:1451` is the complement, by `device`.
- **AV guess** — `AV_VENDORS` `:706`, `AV_NAME_HINTS` `:711`,
  `looks_like_av_device()` `:715`: `denied` overrules first (`:727`), then
  `declared` (`:729`), then the three-letter EDID vendor prefix (`:731-733`),
  then a name-substring hint (`:734-736`).
- **Rule advice** — `suggest_rule()` `:788`, a ladder, best first: interlaced
  preferred timing → `@interlaced` (`:797-800`); small now → `@<1280x720`
  (`:801-804`); progressive now → plain id **plus** a "wrong moment to look"
  warning (`:805-815`); else plain id (`:816-818`).
- **Config keys** — `DEFAULT_CONFIG`: `blocked_hwids` `:83`,
  `blocked_rules_parked` `:131`, `av_devices`/`not_av_devices` `:148-149`.
- **Tray toggle** — `TrayApp._make_block_toggle()` `:3041`. Unticking parks
  the verbatim rule in `blocked_rules_parked` (`:3046-3051`); reticking
  restores it rather than writing a bare id (`:3052-3055`); then saves,
  re-clips, re-icons and sweeps (`:3058-3063`). Menu `:2894-2908`, ticks from
  `Guard.blocked()`, not the rule list.
- **Settings window** — `settings_window.py` runs as its **own process**, so
  `AV_VENDORS` `:46`, `AV_NAME_HINTS` `:51` and `looks_like_av_device()` `:55`
  are deliberately duplicated (reason at `:42-44`), and id extraction is
  re-implemented as `spec.split("@")[0].strip()` instead of importing
  `parse_block_spec` (`:448-452`, `:596-612`). Rows `:469-493`, absent kit
  `:505-519`, save `:620-621`, `:630-633`.

## CASCADE — a change here must reach ALL of these
- **Add or change a rule form** → `parse_block_spec` (`:923`),
  `monitor_matches_block` (`:948`), `suggest_rule` (`:788`),
  `settings_window.py:448-452`/`:596-612` (its own `split("@")`),
  `test_phantommonitor.py:295-327`, `:700-718`, `README.md:201-206`,
  `_blocked_help` in `config.example.json:3`.
- **Change what counts as blocked** → `Guard.blocked()` `:1426` is the only
  authority: `allowed()` `:1451`, `check_window` `:1642`, `sweep` `:1686`,
  `move_active_to` `:1787`, `_tooltip` `:2011`, `_apply_cursor_clip` `:2484`,
  `:2492`, tray menu `:2897`, `:2976`. `--diag` (`:3893`, `:3933`) and
  `--list` (`:4069`) call `monitor_matches_block` *directly*, bypassing the
  primary exemption — keep them in step or they disagree with the guard.
- **Touch the AV lists or the guess** → `phantommonitor.py:706-737` **and**
  `settings_window.py:46-72` (duplicate, identical today); consumers `--diag`
  `:3897`, `:3940`, `:3946`, `--list` `:4070`, `settings_window.py:471`,
  `:511`; test table `:471-482`.
- **Touch `blocked_hwids`/`blocked_rules_parked`** → `DEFAULT_CONFIG:83,131`,
  `Guard.blocked` `:1427`, `_make_block_toggle` `:3043-3057`, startup warning
  `:4111`, `--diag` `:3887`, `settings_window.py:442-443`, `:620-621`,
  `config.example.json:3-6`, `docs/configuration.md:39`.
- **Touch `av_devices`/`not_av_devices`** → `DEFAULT_CONFIG:148-149`, `--diag`
  `:3891`, `--list` `:4071`, `settings_window.py:445-446`, its two-list save
  `:628-633`, `docs/configuration.md:46`.
- **Docs stating hardware behaviour**: `README.md:197-312` (rule table, which
  rule to use, tested receivers `:262-270`, the fallback),
  `docs/how-it-works.md:55-115` (incl. `:86`, "A warning about resolution as a
  signal"), `docs/configuration.md:7-33`.

## Rules (invariants)
1. **The primary display is never blocked** (`:1430-1444`). The taskbar and
   tray icon live there; blocking it evacuates its windows and fences the
   pointer away from the only menu that could undo it. A rule reaches the
   primary by accident easily — two identical panels share one hwid, and
   Windows reassigns primary after a topology change. Asserted at
   `test_phantommonitor.py:117-123`.
2. **Never block every display.** `:1445-1448` — nowhere to evacuate to.
3. **The AV guess never blocks anything** — `:716-723`, `:148-149`,
   `settings_window.py:495-498`. Being wrong must cost one line of text.
4. **A user overrule beats the vendor list, both ways.** `not_av_devices` is
   checked before `av_devices` (`:727-730`) — `SON` catches a Sony amp and
   would catch a Sony display just the same.
5. **Unticking parks the rule verbatim** (`:3046-3051`), so re-ticking cannot
   silently downgrade `DON0015@interlaced` to a bare `DON0015`.
6. **`<` compares area, never dimensions** (`:978-984`).
7. **Only `Guard.blocked()` answers "is this blocked?"** — a rule existing is
   not the same as it applying now (`:2894-2897`).

## Reuse, don't rebuild
- Read the hwid out of a rule → **`parse_block_spec()`** (`:923`). Never
  `spec.split("@")` in new `phantommonitor.py` code.
- Monitor vs rule → **`monitor_matches_block()`** (`:948`).
- "What is blocked right now?" → **`Guard.blocked()`** / **`allowed()`**
  (`:1426`, `:1451`), never a fresh loop over `blocked_hwids`.
- "Is this an amp?" → **`looks_like_av_device()`** (`:715`), passing BOTH
  `av_devices` and `not_av_devices`.
- "What rule should they use?" → **`suggest_rule()`** (`:788`).
- Tick or untick from code → **`TrayApp._make_block_toggle()`** (`:3041`); it
  parks, saves, re-fences and sweeps.
- Which blocked displays deserve a fence → **`fenceable()`** (`:630`), then
  **`cursor_clip_rect()`** (`:681`).

## Traps
- **The Denon does NOT fall back to 800x600.** Withdrawn in `16fad0e`,
  restated in `3a13549`: that 800x600 was a resolution the user had set by hand
  years earlier and Windows was restoring it. The AVR-790 advertises every mode
  up to 1920x1080 with nothing attached. `@<WxH` is still useful — but only
  when *you* pin the amp's display small, which is the configuration doing the
  work, not the hardware announcing itself. Say so wherever written.
- **The AVR-790 signals a phantom only through interlace.** Same id, same
  reported resolution either way; the EDID preferred timing is 1920x1080**i**
  alone and 1920x1080**p** with a screen awake (`5c6792e`, `0d7625b`). Hence
  `@interlaced`, and hence reading the EDID, not the current mode.
- **A Yamaha HTR-4063 advertises a full 1920x1080 with nothing attached**
  (`a48e15f`) — no size rule at any threshold spots it. It does pass the
  downstream EDID upstream, so its *id* disappears when a screen wakes behind
  it and a plain rule is already automatic (`863559d`).
- **Behaviour varies by model, not brand** (`838903a`, `a540e9b`); never
  generalise from one unit. `README.md:262-270` is the reported table.
- **Dimension comparison blocked a rotated monitor.** `@<1280x720` on a
  1080x1920 portrait panel matched because 1080 < 1280. Area fixed that and
  1024x768, taller than 720 (`72b3d2f`; pinned at `:312-327`).
- **Advice sampled at the wrong moment sends people to a manual tick.** With
  a screen awake behind the amp there is nothing to detect, so `suggest_rule`
  says so rather than recommend a plain rule (`0872aaa`, `:805-815`).
- **The settings window is a second process of the same script.** Do not
  "fix" the duplication by importing `phantommonitor` from it — that
  re-executes the module. (Being a second process of it also once made both
  share one rotating log and spew sharing-violation tracebacks on every write,
  `5c6792e`.) Keep the copies in step by hand.

## Related subsystems
- `docs/systems/display-identity.md` — supplies `hwid`, `rect`,
  `edid_preferred`, which every rule form depends on.
- Pointer fence — `fenceable()` `:630`, `cursor_clip_rect()` `:681`; a screen
  with an app pinned via `app_displays` stays clickable.
- Evacuation — `Guard.check_window` `:1639`, `sweep` `:1680`.
- `docs/how-it-works.md` — why an amp becomes a display at all.

LAST VERIFIED: 2026-10-24 · aa2db0f · all file:line re-grepped live; `looks_like_av_device`, `AV_VENDORS`, `AV_NAME_HINTS` AST-diffed across both files, identical today; primary exemption confirmed in `Guard.blocked` and the tests.
