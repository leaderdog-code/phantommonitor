---
subsystem: display-identity
owns: phantommonitor.py, test_phantommonitor.py
anchors: enum_monitors, hwid_for_device, friendly_name, _edid_name, edid_preferred, display_settings_numbers, device_digit, known_displays, topology_signature, virtual_origin, origin_from_signature, normalize_layouts, _preferred_cache, _name_cache, refresh_monitors, target_for, by_hwid, _SOURCE_DEVICE_NAME
test: python test_phantommonitor.py  (170 checks; needs REAL hardware — two or more attached displays — and refuses to run while the tray app is up, test_phantommonitor.py:11-14. Read-only cross-checks: python phantommonitor.py --list, --diag.)
---
# Display identity — what a display *is*, independent of what Windows calls it today

## What it does
Turns the monitors Windows is showing into `Monitor` objects matched by
hardware rather than by position in a list. Block rules, hotkey targets, app
pins and saved icon/window layouts all key off what this produces, so its
definition of "the same display" is the program's.

Four identifiers exist and are NOT interchangeable: the `\\.\DISPLAYn` device
(reassigned on hot-plug), the EDID hardware id `DON0015` (stable, but per MODEL
not per panel), the EDID friendly name `DENON-AVAMP` (stable), and
`mon.number` (this app's hotkey slot, from display-config path order — not
Windows' number).

## Where it lives
All of it is in `phantommonitor.py`.

- `class Monitor` — `phantommonitor.py:490`; fields set at `:491-499`
  (`device`, `rect`, `work`, `primary`, `number`, `hwid`, `name`). `label()`
  (`:501`) is the one display string the whole UI prints — name, size, hwid,
  position, primary star — and deliberately carries **no** number.
- `enum_monitors()` — `:1010`. The only enumerator. Builds every `Monitor`,
  numbers them from `display_settings_numbers()` (`:1011`), sorts by number.
- `hwid_for_device()` — `:523`. `\\.\DISPLAY3` → `DON0015`, by splitting
  `EnumDisplayDevices(device, 0).DeviceID` on `\` and taking element 1.
- `friendly_name()` — `:536`. Walks
  `HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\<hwid>\<instance>\Device Parameters`
  → `EDID` value (`:544-556`), memoised in `_name_cache` (`:533`, `:540`).
- `_edid_name()` — `:563`. Scans the four 18-byte descriptors at offsets
  **54, 72, 90, 108** (`:567`) for the one headed `00 00 00` with tag byte
  `0xFC` (`:569-570`) — the monitor-name descriptor.
- `edid_preferred()` — `:743`. Reads the **first** detailed timing descriptor,
  `edid[54:72]` (`:775`), and decodes width `d[2] | ((d[4] & 0xF0) << 4)`,
  lines `d[5] | ((d[7] & 0xF0) << 4)`, interlace `bool(d[17] & 0x80)`
  (`:776-778`). An interlaced descriptor counts lines **per field**, so the
  frame height is doubled: `lines * (2 if interlaced else 1)` (`:781`).
  Returns `(width, height, interlaced)` or `None`. Cached in `_preferred_cache`
  (`:740`).
- `display_settings_numbers()` — `:449`, with ctypes structs `_LUID` (`:410`),
  `_PATH_INFO` (`:431`), `_MODE_INFO` (`:436`, an opaque 64-byte blob — the
  call needs the array, not the contents) and `_SOURCE_DEVICE_NAME` (`:445`):
  `GetDisplayConfigBufferSizes` → `QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS)` →
  `DisplayConfigGetDeviceInfo` per path (`:459-478`). `{}` on any failure.
- `device_digit()` — `:484`. The `\\.\DISPLAYn` digit, only used when the
  display config is unreadable (`:497`).
- `known_displays()` — `:988`. Every hwid Windows has *ever* cached an EDID
  for, attached or not — how a rule gets set for a pass-through amp whose id
  is invisible while a screen is awake behind it.
- `virtual_origin()` `:573`, `origin_from_signature()` `:580`,
  `normalize_layouts()` `:594`, `topology_signature()` `:623` — arrangement
  identity. The signature is `WxH@X,Y` per monitor, `|`-joined, in `number`
  order (`:625-627`).
- `Guard.refresh_monitors()` — `:1411`. Clears `_preferred_cache` (`:1412`),
  re-enumerates, logs every label, warns when two panels share one hwid
  (`:1417-1424`).
- `Guard.by_number()` `:1464`, `by_hwid()` `:1470`, `target_for()` `:1476` —
  hotkey resolution; `hotkey_targets` pins a slot to an hwid, the number is
  only the fallback (`:1484-1491`).

## CASCADE — a change here must reach ALL of these
- **Every consumer of `enum_monitors()`**: `Guard.refresh_monitors`
  (`phantommonitor.py:1414`), `--diag` (`:3890`), `--settings` (`:4056`, which
  also builds the 7-tuple rows and the `absent` list from `known_displays()`
  at `:4060`), `--list` (`:4066`).
- **`Monitor.label()` callers**: `:1415`, `:1627`, `:2656`, `:2930`, `:2963`,
  `:2978`, `:2984`, `:3221`, `:4073`, `test_phantommonitor.py:39,41,42`.
- **The settings window takes a flattened tuple, not a `Monitor`**:
  `phantommonitor.py:4057-4058` → `settings_window.run()` (`:345`), unpacked at
  `settings_window.py:469` as `name, hwid, w, h, x, y, primary`. Add a field
  and BOTH ends change.
- **Numbering**: `device_digit` → `Monitor.number` → `by_number` (`:1464`),
  `target_for` (`:1476`), `hotkey_targets` (`DEFAULT_CONFIG:128`), the
  `monitor_%d` hotkey names (`:120-124`, menu `:2981`), and the slot combobox
  in `settings_window.py:519-524` / save at `:614-618`.
- **`hwid`**: `blocked_hwids` / `blocked_rules_parked` (see
  `docs/systems/block-rules.md`), `hotkey_targets` (`:128`), `app_displays` /
  `app_positions` (`:145-146`, `Guard.assigned_display` `:1702`), arrangement
  slots (`:3439`), `av_devices` / `not_av_devices` (`:148-149`).
- **`topology_signature`**: icon layouts (`:2410`, `:2435`, `:3674`), window
  layouts (`:1977`, `:2202`), the store normalised by `normalize_layouts`
  (`:2391`). Change its format and every saved layout orphans —
  `origin_from_signature` (`:580`) parses it back and must change with it.
- **`edid_preferred`**: `monitor_matches_block` `@interlaced` (`:972`),
  `--diag` (`:3901`), `suggest_rule` (`:3906`). Any new cache must be cleared
  in `refresh_monitors` (`:1412`).
- **Tests**: `test_phantommonitor.py:358-391`, `:716-718`.
- **Docs**: `README.md:57`, `README.md:197-215`, `docs/how-it-works.md:5-27`
  and `:55-115`, `docs/development.md:11-14` (standard APIs only, no vendor
  SDK), `docs/configuration.md:34-50`.

## Rules (invariants)
1. **A hardware id names a MODEL, not a panel.** Two identical monitors share
   one; `refresh_monitors` warns (`:1417-1424`) and nothing may assume it is
   unique.
2. **Never print a number claiming to be Windows'.** `Monitor.label()`
   (`:501-514`) omits it on purpose: neither the `\\.\DISPLAYn` digit nor
   display-config path order reproduces Display Settings. `number` is this
   app's hotkey *slot*, worded that way at `:3895`, `:4073` and
   `settings_window.py:499-501`.
3. **A hwid beats a number** wherever both exist — `target_for` (`:1476`).
4. **EDID is read, never written**, and only from the registry cache.
5. **`edid_preferred` is what the display ASKS FOR, not what Windows is set
   to** (`:745-750`). `EnumDisplaySettings` answers the other question.
6. **Clear `_preferred_cache` on every topology change** (`:1412`), or an amp
   stays blocked after a screen wakes behind it.
7. Enumeration must never raise: `display_settings_numbers` → `{}` on failure
   (`:479-481`), `hwid_for_device` → `None` (`:525-527`).

## Reuse, don't rebuild
- The monitors → **`enum_monitors()`** (`:1010`) or `guard.monitors`. Never
  call `EnumDisplayMonitors` yourself.
- Print a display → **`Monitor.label()`** (`:501`).
- Find one by id or slot → **`Guard.by_hwid` / `by_number` / `target_for`**
  (`:1470`, `:1464`, `:1476`).
- Name for an id → **`friendly_name()`** (`:536`), cached. What it asks for →
  **`edid_preferred()`** (`:743`), cached.
- Displays not plugged in → **`known_displays()`** (`:988`).
- Key something per layout → **`topology_signature()`** (`:623`) with
  **`virtual_origin()`** (`:573`); convert old stores with
  **`normalize_layouts()`** (`:594`).
- "Which display is this rect on?" → **`monitor_of_rect()`** (`:1131`).

## Traps
- **Display-config path order is not Windows' numbering.** It agreed on one
  layout and disagreed after an adapter swap (Settings 4/3/2/1 against this
  app's 2/1/3/4). A number that claims to be Windows' and is not invites acting
  on it — hence the missing number and the "slot" wording (`2dd78a4`).
- **Mode ≠ preferred timing.** Watching the mode Windows picked produced years
  of wrong conclusions about AV amps, because Windows restores the mode last
  chosen for an arrangement. Reading EDID in both states settled it
  (`16fad0e`, `3a13549`, `5c6792e`).
- **Interlaced descriptors halve the height.** Without the `*2` at `:781`, a
  1080i amp reads as 1920x540 and every size comparison is wrong.
- **Icon coordinates were once stored relative to the virtual origin.** That
  origin moves whenever a display on the left changes size, so the same
  physical spot had a different number in every arrangement. Version 2 stores
  screen coordinates; `normalize_layouts` (`:594`) converts v1 exactly by
  recovering the origin from the signature.
- **`\\.\DISPLAYn` is a hot-plug casualty** (`:453-458`). Only `device_digit`
  may use it, and only as a fallback.
- **A pass-through amp's id vanishes while a screen is awake behind it** — you
  cannot enumerate your way to it, which is why `known_displays()` reads the
  registry rather than the live set.
- **The tests drive real windows on real displays**, picking the smallest
  non-primary one as the phantom stand-in (`test_phantommonitor.py:33-38`).

## Related subsystems
- `docs/systems/block-rules.md` — consumes `hwid`, `rect`, `edid_preferred`.
- Arrangements / icon layouts — keyed by `topology_signature`.
- Hotkeys and app pinning — resolve through `by_hwid` / `target_for`.
- `docs/how-it-works.md` — the hardware reasoning behind EDID and hot plug.

LAST VERIFIED: 2026-10-24 · aa2db0f · all file:line re-grepped live; confirmed the registry EDID path, the 54/72/90/108 descriptor offsets, the `0xFC` name tag, `edid[54:72]` preferred timing, `d[17] & 0x80` interlace, the `*2` frame height.
