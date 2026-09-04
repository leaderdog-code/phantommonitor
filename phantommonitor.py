"""
PhantomMonitor - keeps windows off a phantom display.

Windows has no API to reserve a monitor, so this does the next best thing: it
watches for windows landing on a blocked display and evacuates them within a few
milliseconds. Blocked displays are identified by their EDID hardware ID (e.g.
DON0015), so the rule survives reboots, cable swaps and port changes.

Runs as a tray icon. --list / --rescue / --no-tray for one-shot use.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg
from logging.handlers import RotatingFileHandler

import win32api
import win32con
import win32gui
import win32process

import desktop_icons

APP_NAME = "PhantomMonitor"

# Frozen by PyInstaller, __file__ points inside a temporary extraction folder
# that is deleted on exit - settings and logs written there would vanish. Use
# the folder the executable actually lives in.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_DIR = os.path.join(APP_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "phantommonitor.log")
ICON_LAYOUT_PATH = os.path.join(APP_DIR, "icon_layouts.json")
PROJECT_URL = "https://github.com/leaderdog-code/phantommonitor"
RELEASES_URL = PROJECT_URL + "/releases"
LATEST_API = "https://api.github.com/repos/leaderdog-code/phantommonitor/releases/latest"
APP_VERSION = "1.0.0"   # keep in step with AppVersion in build/installer.iss
STARTUP_VBS = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    "PhantomMonitor.vbs",
)

DEFAULT_CONFIG = {
    "blocked_hwids": [],
    "enabled": True,
    "sweep_interval_ms": 2000,
    "locationchange_cooldown_ms": 400,
    "fix_minimized_restore_position": True,
    # Off by default: forcing a frame onto an app that manages its own window
    # fights it. mstsc accepts the styles, then goes full-screen again and the
    # two sets of rules collide. Relocating is the guard's job; window mode is
    # the app's. Leave full-screen from inside the app (mstsc: Ctrl+Alt+Break).
    "unfullscreen_borderless": False,
    # Ask a known app to leave its own full-screen mode before moving it, so it
    # rebuilds a real frame with real buttons. This is the good mechanism.
    "leave_fullscreen": True,
    "intercept_hotkeys": True,
    # Fence the mouse pointer out of blocked displays too. Only possible when
    # the blocked display sits outside the bounding box of the others.
    "block_cursor": False,
    # Put desktop icons back after a display change scrambles them.
    # Put windows back where they were after a display change moves them.
    # Restores only the windows a display change actually displaced, by
    # comparing against a snapshot frozen the moment the change began. Windows
    # the change did not touch are left strictly alone.
    "restore_windows": True,
    "window_save_debounce_ms": 2000,
    "restore_icons": True,
    # Saves are triggered by actually moving an icon, not by a timer. This is
    # how long to wait after the last move before writing, so that dragging
    # several icons results in one save rather than one per icon.
    "icon_save_debounce_ms": 4000,
    "windowed_fraction": 0.6,
    # Any combo of ctrl/alt/shift/win plus a letter, digit, F-key or named key
    # (left, right, up, down, home, end, space, insert, delete, pause, esc).
    # Empty string disables one. Matched combos are SWALLOWED so they never
    # reach the focused app, so avoid combos a game or app already uses -
    # plain ctrl+alt+<digit> is a popular game binding, hence the extra shift.
    "hotkeys": {
        "rescue": "ctrl+alt+shift+0",
        "monitor_1": "ctrl+alt+shift+1",
        "monitor_2": "ctrl+alt+shift+2",
        "monitor_3": "ctrl+alt+shift+3",
    },
    # Pin each hotkey to a monitor's EDID hardware id. Display numbers are
    # reassigned by hot-plugging, so a number alone can silently start meaning a
    # different physical screen; the hardware id never changes. The number is
    # used only when the pinned monitor is not attached.
    "hotkey_targets": {},
    # Rules removed by unticking a display in the tray, kept verbatim so
    # re-ticking restores the exact rule instead of a bare hardware id.
    "blocked_rules_parked": [],
    "ignore_process_names": [],
    "ignore_window_classes": [],
    # Editor for the tray's "Edit settings" / "View log" items. Empty means
    # notepad.exe, which every Windows install has. A name on PATH or a full
    # path both work, and environment variables are expanded.
    "editor": "",
    # Where "Support this project" points. Empty hides the menu item entirely -
    # nobody should have a donation link nagging them from a tray menu they did
    # not ask for one in.
    # Send named apps to a display and remember where they sat there, e.g.
    # {"discord.exe": "GSM7814"}. Those apps open on that display and are never
    # evacuated from it, even when it is blocked - which is what makes a
    # dedicated chat or dashboard screen possible.
    "app_displays": {},
    "app_positions": {},
    "support_url": "",
    # Off by default. Checking asks GitHub for the latest release, which means
    # contacting a server, and a tray utility should not do that unasked.
    "check_updates_on_start": False,
    "log_level": "INFO",
}

# --- win32 constants not exposed by win32con ---------------------------------
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MOVESIZESTART = 0x000A
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0

DWMWA_CLOAKED = 14
MONITORINFOF_PRIMARY = 0x1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WM_TRAYICON = win32con.WM_USER + 20
TIMER_SWEEP = 1
TIMER_SETTLE = 2  # re-sweeps while a display topology change settles
TIMER_ICONS = 3   # debounced save after desktop icons stop moving
TIMER_WINDOWS = 4  # debounced save after windows stop being dragged
TIMER_VERIFY = 5   # later passes, for windows nudged after the settle expired

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000
HOTKEY_ID_BASE = 100  # +0 = rescue, +n = move active window to monitor n

# Shell furniture, never user windows.
SKIP_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow", "TaskListThumbnailWnd", "tooltips_class32",
    "#32768", "DV2ControlHost", "ForegroundStaging", "MultitaskingViewFrame",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
    "ApplicationFrameInputSinkWindow", "EdgeUiInputTopWndClass",
    "Shell_InputSwitchTopLevelWindow", "SysShadow", "PhantomMonitorWnd",
}

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
kernel32 = ctypes.windll.kernel32

WinEventProcType = ctypes.WINFUNCTYPE(
    None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_APP_HOTKEY = win32con.WM_APP + 1
WM_APP_ICONS = win32con.WM_APP + 2
WM_APP_SETTINGS_CLOSED = win32con.WM_APP + 3

# Explorer records desktop icon positions here, and rewrites it shortly after
# they move. Watching this is how icon changes are noticed: Explorer does not
# raise an accessibility event for a move, so there is nothing else to listen
# to, and polling 160 icons across a process boundary on a timer is wasteful.
ICON_BAG_KEY = r"Software\Microsoft\Windows\Shell\Bags\1\Desktop"
REG_NOTIFY_CHANGE_LAST_SET = 0x0004
WAIT_OBJECT_0 = 0x0000

LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]

# Declare prototypes explicitly: ctypes defaults to a c_int return, which
# truncates 64-bit handles and would quietly break unhooking at shutdown.
user32.SetWinEventHook.restype = wt.HANDLE
user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, WinEventProcType,
                                   wt.DWORD, wt.DWORD, wt.DWORD]
user32.UnhookWinEvent.argtypes = [wt.HANDLE]
user32.SetWindowsHookExW.restype = wt.HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelKeyboardProc,
                                     wt.HINSTANCE, wt.DWORD]
user32.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.ClipCursor.argtypes = [ctypes.c_void_p]
advapi32 = ctypes.windll.advapi32
advapi32.RegNotifyChangeKeyValue.argtypes = [wt.HKEY, wt.BOOL, wt.DWORD, wt.HANDLE, wt.BOOL]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.SetEvent.argtypes = [wt.HANDLE]
user32.SetTimer.restype = ctypes.c_void_p
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_void_p, wt.UINT, ctypes.c_void_p]
user32.KillTimer.argtypes = [wt.HWND, ctypes.c_void_p]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]

log = logging.getLogger(APP_NAME)


# --- setup -------------------------------------------------------------------

def setup_logging(level):
    os.makedirs(LOG_DIR, exist_ok=True)
    log.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512000, backupCount=3, encoding="utf-8")
    handler.setFormatter(fmt)
    log.addHandler(handler)
    if sys.stdout is not None:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        log.addHandler(stream)


def set_dpi_awareness():
    """Must run before any geometry call, or coordinates come back virtualized."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except OSError:
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except OSError:
        return "none"


def version_tuple(text):
    """'v1.2.3' -> (1, 2, 3), ignoring anything that is not a number."""
    parts = []
    for chunk in str(text).lstrip("vV").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def latest_release(timeout=6):
    """The newest published tag, or None if it cannot be reached."""
    import json as _json
    import urllib.request
    request = urllib.request.Request(
        LATEST_API, headers={"Accept": "application/vnd.github+json",
                             "User-Agent": "PhantomMonitor/" + APP_VERSION})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _json.loads(response.read().decode("utf-8")).get("tag_name")
    except Exception as exc:
        log.info("update check failed: %s", exc)
        return None


def open_url(url):
    """Open a link in whatever the user's browser is."""
    try:
        os.startfile(url)
        return True
    except OSError as exc:
        log.error("could not open %s: %s", url, exc)
        return False


def open_text_file(path, editor=""):
    """Open a text file for editing.

    Deliberately not os.startfile: .json and .log frequently have no file
    association, and the user gets a "How do you want to open this file?"
    prompt instead of their settings.

    Deliberately not a list of guessed install paths either - those are true on
    one machine and wrong everywhere else. notepad.exe ships with every Windows
    install, so that is the default; anyone wanting something else sets
    "editor" in config.json, which is where machine-specific choices belong.
    """
    if not os.path.exists(path):
        log.info("nothing to open at %s yet", path)
        return False

    if editor:
        wanted = os.path.expandvars(editor)
        exe = wanted if os.path.exists(wanted) else shutil.which(wanted)
        if exe:
            try:
                subprocess.Popen([exe, path])
                return True
            except OSError as exc:
                log.warning("editor %r would not start (%s); using Notepad", editor, exc)
        else:
            log.warning("editor %r not found; using Notepad", editor)

    try:
        subprocess.Popen(["notepad.exe", path])
        return True
    except OSError as exc:
        log.error("could not open %s: %s", path, exc)
        return False


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                cfg.update(json.load(handle))
        except (OSError, ValueError) as exc:
            log.warning("config unreadable (%s), using defaults", exc)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2)
    except OSError as exc:
        log.error("could not save config: %s", exc)


# --- monitors ----------------------------------------------------------------

QDC_ONLY_ACTIVE_PATHS = 2
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wt.UINT), ("Denominator", wt.UINT)]


class _PATH_SOURCE(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", wt.UINT), ("modeInfoIdx", wt.UINT),
                ("statusFlags", wt.UINT)]


class _PATH_TARGET(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", wt.UINT), ("modeInfoIdx", wt.UINT),
                ("outputTechnology", wt.UINT), ("rotation", wt.UINT),
                ("scaling", wt.UINT), ("refreshRate", _RATIONAL),
                ("scanLineOrdering", wt.UINT), ("targetAvailable", wt.BOOL),
                ("statusFlags", wt.UINT)]


class _PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", _PATH_SOURCE), ("targetInfo", _PATH_TARGET),
                ("flags", wt.UINT)]


class _MODE_INFO(ctypes.Structure):
    _fields_ = [("raw", ctypes.c_byte * 64)]


class _DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wt.UINT), ("size", wt.UINT), ("adapterId", _LUID),
                ("id", wt.UINT)]


class _SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER), ("viewGdiDeviceName", wt.WCHAR * 32)]


def display_settings_numbers():
    """{'\\\\.\\DISPLAY1': 1, ...} matching the numbers Windows Display Settings shows.

    Deliberately not the digit in \\.\DISPLAYn. Those get reassigned on a
    hot-plug and then no longer agree with the numbers on the "Rearrange your
    displays" page - plug a screen in and \\.\DISPLAY2 can become the third
    monitor. Settings numbers the active display-config paths in order, so that
    is what a hotkey called "move to monitor 2" has to mean.
    """
    try:
        n_path, n_mode = wt.UINT(), wt.UINT()
        if user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS,
                                              ctypes.byref(n_path),
                                              ctypes.byref(n_mode)) != 0:
            return {}
        paths = (_PATH_INFO * n_path.value)()
        modes = (_MODE_INFO * n_mode.value)()
        if user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_path), paths,
                                     ctypes.byref(n_mode), modes, None) != 0:
            return {}
        numbers = {}
        for index in range(n_path.value):
            name = _SOURCE_DEVICE_NAME()
            name.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME
            name.header.size = ctypes.sizeof(_SOURCE_DEVICE_NAME)
            name.header.adapterId = paths[index].sourceInfo.adapterId
            name.header.id = paths[index].sourceInfo.id
            if user32.DisplayConfigGetDeviceInfo(ctypes.byref(name)) == 0:
                numbers.setdefault(name.viewGdiDeviceName, len(numbers) + 1)
        return numbers
    except Exception as exc:
        log.debug("display-config numbering unavailable: %s", exc)
        return {}


def device_digit(device):
    """Fallback numbering from \\.\DISPLAYn, if the display config is unreadable."""
    tail = str(device).rsplit("DISPLAY", 1)[-1]
    return int(tail) if tail.isdigit() else 0


class Monitor:
    def __init__(self, handle, device, rect, work, primary, number=0):
        self.handle = handle
        self.device = device          # \\.\DISPLAY3
        self.rect = rect              # (l, t, r, b) full bounds
        self.work = work              # (l, t, r, b) minus taskbar
        self.primary = primary
        self.number = number or device_digit(device)
        self.hwid = hwid_for_device(device) or ""
        self.name = friendly_name(self.hwid) or self.hwid or device

    def label(self):
        """Identify a display by things that cannot mislead.

        Deliberately no leading number. Windows Display Settings prints its own
        numbers, and there is no documented way to obtain them - they are not
        the \\.\DISPLAYn digit, nor the display-config path order, and both of
        those have matched Settings on one layout and disagreed on the next.
        Showing a number that claims to be Windows' and is not is worse than
        showing none, because it invites acting on it. The name, size, hardware
        id and position always agree with what is in front of you.
        """
        width = self.rect[2] - self.rect[0]
        height = self.rect[3] - self.rect[1]
        star = "  *primary" if self.primary else ""
        return "%s  %dx%d [%s] at %d,%d%s" % (
            self.name, width, height, self.hwid or "?",
            self.rect[0], self.rect[1], star)

    def __repr__(self):
        return "<Monitor %s %s %s>" % (self.device, self.hwid, self.rect)


def hwid_for_device(device):
    """\\.\DISPLAY3 -> 'DON0015' (the EDID vendor + product code)."""
    try:
        mon = win32api.EnumDisplayDevices(device, 0)
    except Exception:
        return None
    parts = (mon.DeviceID or "").split("\\")
    return parts[1] if len(parts) > 1 else None


_name_cache = {}


def friendly_name(hwid):
    """Pull the monitor's own name out of its cached EDID block."""
    if not hwid:
        return None
    if hwid in _name_cache:
        return _name_cache[hwid]
    name = None
    try:
        base = "SYSTEM\\CurrentControlSet\\Enum\\DISPLAY\\" + hwid
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                inst = winreg.EnumKey(key, index)
                try:
                    with winreg.OpenKey(key, inst + "\\Device Parameters") as dev_key:
                        edid, _ = winreg.QueryValueEx(dev_key, "EDID")
                except OSError:
                    continue
                name = _edid_name(edid)
                if name:
                    break
    except OSError:
        pass
    if name:
        _name_cache[hwid] = name
    return name


def _edid_name(blob):
    if not blob or len(blob) < 128:
        return None
    for off in (54, 72, 90, 108):
        desc = blob[off:off + 18]
        if desc[0:3] == b"\x00\x00\x00" and desc[3] == 0xFC:
            return desc[5:18].split(b"\n")[0].decode("ascii", "ignore").strip() or None
    return None


def virtual_origin(monitors):
    """Top-left of the virtual desktop. ListView icon coordinates are relative to it."""
    if not monitors:
        return (0, 0)
    return (min(m.rect[0] for m in monitors), min(m.rect[1] for m in monitors))


def origin_from_signature(sig):
    """Recover a stored arrangement's virtual origin, to convert old layouts."""
    xs, ys = [], []
    for part in str(sig).split("|"):
        try:
            _size, pos = part.split("@")
            x, y = pos.split(",")
            xs.append(int(x))
            ys.append(int(y))
        except ValueError:
            continue
    return (min(xs), min(ys)) if xs else (0, 0)


def normalize_layouts(data):
    """Return a version-2 icon layout store, converting a version-1 one.

    Version 1 held ListView coordinates, which are relative to the virtual
    desktop's top-left corner. That corner moves whenever a display on the left
    changes size, so the same physical spot had a different number in every
    arrangement and layouts could never carry across. Version 2 holds screen
    coordinates. An arrangement's own origin is recoverable from its signature,
    so old files convert exactly.
    """
    empty = {"version": 2, "last": "", "layouts": {}}
    if not isinstance(data, dict):
        return empty
    if data.get("version") == 2:
        data.setdefault("layouts", {})
        data.setdefault("last", "")
        return data

    layouts = {}
    for sig, icons in data.items():
        if not isinstance(icons, dict):
            continue
        ox, oy = origin_from_signature(sig)
        layouts[sig] = dict(
            (name, [pos[0] + ox, pos[1] + oy]) for name, pos in icons.items()
            if isinstance(pos, (list, tuple)) and len(pos) == 2)
    return {"version": 2, "last": "", "layouts": layouts}


def topology_signature(monitors):
    """A stable name for one display arrangement, used to key saved icon layouts."""
    return "|".join("%dx%d@%d,%d" % (m.rect[2] - m.rect[0], m.rect[3] - m.rect[1],
                                     m.rect[0], m.rect[1])
                    for m in sorted(monitors, key=lambda m: m.number))


def cursor_clip_rect(monitors, blocked):
    """One rectangle covering every allowed display and no blocked one.

    Windows can only fence the cursor into a single rectangle, so this works
    only when the blocked display sits outside the bounding box of the others -
    which is exactly the case when it has been parked off in a corner. Returns
    None when the layout cannot be expressed that way, rather than fencing the
    user out of a display they actually use.
    """
    blocked_devices = set(m.device for m in blocked)
    allowed = [m for m in monitors if m.device not in blocked_devices]
    if not allowed or not blocked:
        return None
    box = (min(m.rect[0] for m in allowed), min(m.rect[1] for m in allowed),
           max(m.rect[2] for m in allowed), max(m.rect[3] for m in allowed))
    for mon in blocked:
        if overlap_area(box, mon) > 0:
            return None
    return box


# EDID vendor codes belonging to AV equipment rather than display makers. A
# receiver names itself in its EDID - "DENON-AVAMP", "HTR-4063" - which is a far
# better signal than resolution: a Yamaha advertises a full 1920x1080 with
# nothing attached, so no size rule can spot it, but it still says what it is.
AV_VENDORS = {
    "DON": "Denon", "YMH": "Yamaha", "ONK": "Onkyo", "PIO": "Pioneer",
    "MAR": "Marantz", "HAR": "Harman", "INT": "Integra", "NAD": "NAD",
    "ARC": "Arcam", "SON": "Sony AV", "TEA": "TEAC",
}
AV_NAME_HINTS = ("avamp", "av amp", "receiver", "avr", "amplifier", "soundbar",
                 "htr-", "rx-v", "vsx-", "sr-", "extractor", "splitter")


def looks_like_av_device(hwid, name):
    """A guess at whether a display is really an amp, switch or extractor.

    Only ever used to suggest - never to block anything on its own. Being wrong
    here should cost a line of text, nothing more.
    """
    vendor = AV_VENDORS.get((hwid or "")[:3].upper())
    if vendor:
        return vendor
    lowered = (name or "").lower()
    if any(hint in lowered for hint in AV_NAME_HINTS):
        return "AV device"
    return None


def parse_block_spec(spec):
    """Parse a block rule into (hwid, size, smaller_than).

        'DON0015'             always block
        'DON0015@800x600'     block only at exactly that size
        'DON0015@<1280x720'   block only while smaller than that
    """
    hwid, _, mode = str(spec).partition("@")
    size, smaller = None, False
    mode = mode.strip().lower()
    if mode:
        if mode.startswith("<"):
            smaller, mode = True, mode[1:]
        try:
            width, height = mode.split("x")
            size = (int(width), int(height))
        except ValueError:
            log.warning("block rule %r has a bad size; matching on id alone", spec)
            smaller = False
    return hwid.strip(), size, smaller


def monitor_matches_block(mon, spec):
    """Does this monitor match a block rule?

    The size qualifier exists because an AV receiver with nothing attached
    advertises its own small fallback EDID - that tiny resolution IS the
    "no display here" signal. Once a real screen is plugged in the receiver
    reports a real resolution (or passes the screen's own EDID through), the
    rule stops matching, and the guard steps aside without being asked.

    Prefer the '<' form: fallback EDIDs vary between 640x480, 800x600 and
    1024x768, and an exact match would also break if the mode were ever changed
    by hand.
    """
    hwid, size, smaller = parse_block_spec(spec)
    if mon.hwid != hwid:
        return False
    if size is None:
        return True
    width = mon.rect[2] - mon.rect[0]
    height = mon.rect[3] - mon.rect[1]
    if smaller:
        # Compare AREA, not dimensions. Requiring both to be smaller would miss
        # a 1024x768 fallback, which is taller than 720; requiring either would
        # falsely catch a monitor turned on its side, where a 1080x1920 portrait
        # panel is narrower than 1280. Area is right for both: every fallback
        # EDID is under 1280x720 worth of pixels and every real display, rotated
        # or not, is over it.
        return width * height < size[0] * size[1]
    return (width, height) == size


def enum_monitors():
    numbers = display_settings_numbers()
    mons = []
    for hmon, _hdc, _rect in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(hmon)
        device = info["Device"]
        mons.append(Monitor(
            handle=hmon,
            device=device,
            rect=tuple(info["Monitor"]),
            work=tuple(info["Work"]),
            primary=bool(info["Flags"] & MONITORINFOF_PRIMARY),
            number=numbers.get(device, 0),
        ))
    mons.sort(key=lambda m: m.number)
    return mons


# --- window inspection -------------------------------------------------------

_proc_cache = {}


def process_name(hwnd):
    _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    if pid in _proc_cache:
        return _proc_cache[pid]
    name = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        try:
            size = wt.DWORD(512)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    if len(_proc_cache) > 400:
        _proc_cache.clear()
    _proc_cache[pid] = name
    return name


def is_cloaked(hwnd):
    """UWP windows parked on another virtual desktop read as visible but cloaked."""
    val = ctypes.c_int(0)
    ok = dwmapi.DwmGetWindowAttribute(
        wt.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return val.value != 0 if ok == 0 else False


def is_manageable(hwnd, cfg):
    """True for real top-level user windows, False for shell furniture and popups."""
    try:
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return False
        if win32gui.GetAncestor(hwnd, win32con.GA_ROOT) != hwnd:
            return False
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if style & win32con.WS_CHILD:
            return False
        if exstyle & win32con.WS_EX_TOOLWINDOW:
            return False
        cls = win32gui.GetClassName(hwnd)
        if cls in SKIP_CLASSES or cls in cfg.get("ignore_window_classes", []):
            return False
        if is_cloaked(hwnd):
            return False
        # A minimized window's live rect is the iconic one (~160x24 off-screen),
        # so measure the size it will restore to instead.
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMINIMIZED:
            normal = placement[4]
            width, height = normal[2] - normal[0], normal[3] - normal[1]
        else:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
        if width < 80 or height < 60:
            return False
        ignored = [p.lower() for p in cfg.get("ignore_process_names", [])]
        if ignored and process_name(hwnd) in ignored:
            return False
        return True
    except Exception:
        return False


def shorten(text, limit):
    """Trim for a menu, without leaving a dangling separator mid-title.

    Window titles are full of " - " separators, so a blind cut often ends on
    one and reads as though the label itself is unfinished.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip(" -–—:|/\\") + "…"


def title_of(hwnd):
    try:
        return win32gui.GetWindowText(hwnd) or "<" + win32gui.GetClassName(hwnd) + ">"
    except Exception:
        return "<gone>"


# --- geometry ----------------------------------------------------------------

def rect_center(rect):
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def overlap_area(rect, mon):
    left = max(rect[0], mon.rect[0])
    top = max(rect[1], mon.rect[1])
    right = min(rect[2], mon.rect[2])
    bottom = min(rect[3], mon.rect[3])
    return max(0, right - left) * max(0, bottom - top)


def monitor_of_rect(rect, monitors, require_overlap=False):
    """Largest overlap wins.

    With require_overlap the answer is None when the rect touches no monitor at
    all. That matters: apps routinely park hidden helper windows far off-screen
    (the classic spot is -32000,-32000), and the nearest-monitor fallback would
    otherwise declare them to be on whichever display sits furthest top-left -
    here, the Denon - and drag them into view. Only use the fallback when
    picking a *source* monitor for proportional placement, never to decide
    whether a window is sitting on a blocked display.
    """
    best, best_area = None, 0
    for mon in monitors:
        area = overlap_area(rect, mon)
        if area > best_area:
            best, best_area = mon, area
    if best is not None:
        return best
    if require_overlap or not monitors:
        return None
    cx, cy = rect_center(rect)
    return min(monitors, key=lambda m: (cx - rect_center(m.rect)[0]) ** 2
               + (cy - rect_center(m.rect)[1]) ** 2)


def covers_monitor(rect, mon, fraction=0.95):
    """True if the rect blankets essentially all of the monitor, i.e. full-screen."""
    mon_area = (mon.rect[2] - mon.rect[0]) * (mon.rect[3] - mon.rect[1])
    return mon_area > 0 and overlap_area(rect, mon) >= fraction * mon_area


KEYEVENTF_KEYUP = 0x0002
VK_CANCEL = 0x03  # Break

# Apps with a full-screen mode of their own that we can ask them to leave.
# Far better than forcing window styles on them: the app rebuilds its real
# frame, buttons and all, and its own idea of its state stays consistent.
# class name -> (description, modifiers, virtual-key)
FULLSCREEN_TOGGLES = {
    "TscShellContainerClass": ("Remote Desktop, Ctrl+Alt+Break",
                               MOD_CONTROL | MOD_ALT, VK_CANCEL),
}


def send_key_combo(hwnd, mods, vk):
    """Focus a window and inject a key combo, so the app acts on it itself.

    Waits for the foreground to actually change before typing. Injecting
    straight after SetWindowForeground sends the keys to whatever had focus
    before - they vanish, and the toggle silently does nothing.
    """
    for _ in range(4):
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.35)
        if win32gui.GetForegroundWindow() == hwnd:
            break
    else:
        log.warning("could not focus %r to send its full-screen toggle",
                    title_of(hwnd)[:60])
        return False

    downs = []
    if mods & MOD_CONTROL:
        downs.append(win32con.VK_CONTROL)
    if mods & MOD_ALT:
        downs.append(win32con.VK_MENU)
    if mods & MOD_SHIFT:
        downs.append(win32con.VK_SHIFT)

    # Pace every event. Pressing the modifiers back-to-back and firing the key
    # straight after is too fast for the receiving app's own keyboard hook to
    # have registered the modifier state, and the combo is simply ignored.
    sequence = ([(key, 0) for key in downs] + [(vk, 0), (vk, KEYEVENTF_KEYUP)]
                + [(key, KEYEVENTF_KEYUP) for key in reversed(downs)])
    for key, flag in sequence:
        user32.keybd_event(key, 0, flag, 0)
        time.sleep(0.04)
    return True


FRAME_STYLES = (win32con.WS_CAPTION | win32con.WS_THICKFRAME | win32con.WS_SYSMENU
                | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX)


def restore_frame(hwnd):
    """Give a borderless full-screen window its title bar and resize edges back.

    Returns True if the app accepted the change. Some apps reassert their own
    styles, so the caller must check rather than assume.
    """
    try:
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style | FRAME_STYLES)
        return bool(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_CAPTION)
    except Exception as exc:
        log.debug("frame restore failed for %s: %s", hwnd, exc)
        return False


def windowed_rect(dst, fraction):
    """A comfortable windowed size, centred in the destination's work area."""
    dl, dt, dr, db = dst.work
    width = max(320, int((dr - dl) * fraction))
    height = max(240, int((db - dt) * fraction))
    return dl + ((dr - dl) - width) // 2, dt + ((db - dt) - height) // 2, width, height


def window_displaced(hwnd, placement, cfg, monitors=None):
    """Has this window moved since `placement` was recorded?

    The whole basis of window restore: if a window still sits exactly where the
    snapshot says, the display change did not touch it and neither should we.

    Pass `monitors` to catch maximized windows. A maximized window keeps the
    same rcNormalPosition when Windows shunts it to another display, so
    comparing placements alone reports it as untouched when it has in fact
    moved screens - which is exactly the case a user notices, because their
    full-screen window is suddenly on the wrong monitor.
    """
    try:
        if not win32gui.IsWindow(hwnd) or not is_manageable(hwnd, cfg):
            return False
        current = win32gui.GetWindowPlacement(hwnd)
    except Exception:
        return False

    if not (current[1] == placement[1] and current[4] == placement[4]):
        return True

    if monitors and current[1] == win32con.SW_SHOWMAXIMIZED:
        origin = (0, 0)
        for mon in monitors:
            if mon.primary:
                origin = (mon.work[0], mon.work[1])
                break
        normal = placement[4]
        expected = (normal[0] + origin[0], normal[1] + origin[1],
                    normal[2] + origin[0], normal[3] + origin[1])
        try:
            actual = win32gui.GetWindowRect(hwnd)
        except Exception:
            return False
        want = monitor_of_rect(expected, monitors)
        got = monitor_of_rect(actual, monitors)
        if want is not None and got is not None and want.device != got.device:
            return True
    return False


def pick_menu_target(last_focused, cfg, current=None):
    """The window the tray menu should act on.

    Deliberately not just GetForegroundWindow(): clicking the tray icon gives
    the foreground to the taskbar, so by the time the menu opens the user's
    window is already gone. It has to have been remembered as it gained focus.
    """
    if current is None:
        current = win32gui.GetForegroundWindow()
    if current and is_manageable(current, cfg):
        return current
    if (last_focused and win32gui.IsWindow(last_focused)
            and is_manageable(last_focused, cfg)):
        return last_focused
    return 0


def is_user_movable(hwnd):
    """A window with no caption and no resize frame cannot be dragged or resized."""
    try:
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    except Exception:
        return True
    return bool(style & (win32con.WS_CAPTION | win32con.WS_THICKFRAME))


def centred_rect(dst, rect):
    """Same size, centred in the destination's work area (so the taskbar stays clear)."""
    dl, dt, dr, db = dst.work
    width = min(rect[2] - rect[0], dr - dl)
    height = min(rect[3] - rect[1], db - dt)
    return dl + ((dr - dl) - width) // 2, dt + ((db - dt) - height) // 2, width, height


def placed_rect(src, dst, rect, size=None):
    """Keep the window's proportional position on the new monitor, clamped to fit.

    `size` is the size to aim for, which may be larger than the window currently
    is: a trip through a small display clips a window, and without this it would
    stay clipped forever once brought back to a big one.
    """
    want_w, want_h = size if size else (rect[2] - rect[0], rect[3] - rect[1])
    width = min(want_w, dst.work[2] - dst.work[0])
    height = min(want_h, dst.work[3] - dst.work[1])
    dl, dt, dr, db = dst.work

    if src is not None:
        sl, st, sr, sb = src.work
        span_x = max(1, (sr - sl) - (rect[2] - rect[0]))
        span_y = max(1, (sb - st) - (rect[3] - rect[1]))
        fx = min(1.0, max(0.0, (rect[0] - sl) / float(span_x)))
        fy = min(1.0, max(0.0, (rect[1] - st) / float(span_y)))
    else:
        fx = fy = 0.5

    x = max(dl, min(dl + int(((dr - dl) - width) * fx), dr - width))
    y = max(dt, min(dt + int(((db - dt) - height) * fy), db - height))
    return x, y, width, height


# --- the guard ---------------------------------------------------------------

class Guard:
    def __init__(self, cfg):
        self.cfg = cfg
        self.monitors = []
        self.dragging = False
        self.last_touch = {}
        self.clipped = {}  # hwnd -> the size it had before a small display clipped it
        self.refresh_monitors()

    def refresh_monitors(self):
        self.monitors = enum_monitors()
        log.info("displays: %s", " | ".join(m.label() for m in self.monitors))
        # An EDID id identifies the MODEL, not the individual panel, so two
        # identical monitors share one. Blocking it would block both.
        seen = {}
        for mon in self.monitors:
            seen.setdefault(mon.hwid, []).append(mon.number)
        dupes = dict((h, n) for h, n in seen.items() if len(n) > 1)
        if dupes:
            log.warning("identical models share a hardware id %s - blocking one "
                        "blocks them all", dupes)

    def blocked(self):
        specs = self.cfg.get("blocked_hwids", [])
        blocked = [m for m in self.monitors
                   if any(monitor_matches_block(m, s) for s in specs)]
        # Refuse to block everything - there would be nowhere to evacuate to.
        if blocked and len(blocked) >= len(self.monitors):
            log.warning("every display is blocked; standing down")
            return []
        return blocked

    def allowed(self):
        blocked = set(m.device for m in self.blocked())
        return [m for m in self.monitors if m.device not in blocked]

    def rescue_target(self):
        allowed = self.allowed()
        if not allowed:
            return None
        for mon in allowed:
            if mon.primary:
                return mon
        return allowed[0]

    def by_number(self, number):
        for mon in self.monitors:
            if mon.number == number:
                return mon
        return None

    def by_hwid(self, hwid):
        for mon in self.monitors:
            if hwid and mon.hwid == hwid:
                return mon
        return None

    def target_for(self, number):
        """Resolve a hotkey's monitor, preferring a pinned hardware id.

        Display numbers are not stable: hot-plugging reassigns them, and Windows
        numbers by display-config path order, so "monitor 2" can silently become
        a different physical screen. hotkey_targets pins each hotkey to an EDID
        hardware id, which never changes. The number is only the fallback.
        """
        pinned = (self.cfg.get("hotkey_targets") or {}).get(str(number))
        if pinned:
            mon = self.by_hwid(pinned)
            if mon is not None:
                return mon
            log.info("hotkey %d is pinned to %s, which is not attached; "
                     "falling back to display number %d", number, pinned, number)
        return self.by_number(number)

    def workspace_offset(self):
        """WINDOWPLACEMENT uses workspace coords: screen coords minus primary work origin."""
        for mon in self.monitors:
            if mon.primary:
                return (mon.work[0], mon.work[1])
        return (0, 0)

    # -- moving
    def move_window(self, hwnd, dst, reason, allow_toggle=True):
        try:
            flags, show_cmd, pt_min, pt_max, normal = win32gui.GetWindowPlacement(hwnd)
        except Exception:
            return False

        if show_cmd == win32con.SW_SHOWMINIMIZED:
            return self._move_minimized(hwnd, dst, flags, show_cmd, pt_min, pt_max,
                                        normal, reason)

        was_max = show_cmd == win32con.SW_SHOWMAXIMIZED
        if was_max:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return False
        src = monitor_of_rect(rect, self.monitors)

        # Never expand a window to fill the destination. Blowing a borderless
        # window up to a 4K monitor buries the taskbar and tray and still leaves
        # no edge to grab - worse than the problem being solved. Keep the size,
        # stay inside the work area, and centre borderless ones so they land
        # somewhere obvious instead of tucked against a corner.
        borderless = not is_user_movable(hwnd)
        full_screen = borderless and src is not None and covers_monitor(rect, src)
        unfullscreened = False

        # Best option by far: ask the app to leave full-screen itself, then move
        # it once it has a real frame again. Moving a full-screen window without
        # this just relocates something the user still cannot resize or drag.
        if full_screen and allow_toggle and self.cfg.get("leave_fullscreen", True):
            toggle = FULLSCREEN_TOGGLES.get(win32gui.GetClassName(hwnd))
            if toggle:
                log.info("%r is full-screen; asking it to leave (%s), then moving",
                         title_of(hwnd)[:60], toggle[0])
                threading.Thread(target=self._leave_fullscreen_then_move,
                                 args=(hwnd, dst, reason, toggle),
                                 daemon=True).start()
                return True

        if full_screen and self.cfg.get("unfullscreen_borderless", False):
            # Relocating a full-screen window just moves the problem: it is still
            # borderless, so there is no title bar to drag and no edge to resize.
            # Hand its frame back and give it a normal windowed size instead.
            if restore_frame(hwnd):
                x, y, width, height = windowed_rect(
                    dst, float(self.cfg.get("windowed_fraction", 0.6)))
                unfullscreened = True
            else:
                x, y, width, height = centred_rect(dst, rect)
                log.warning("%r is full-screen and refused a window frame; centred "
                            "it instead. Leave full-screen from inside the app "
                            "(mstsc: Ctrl+Alt+Break).", title_of(hwnd)[:60])
        elif borderless:
            x, y, width, height = centred_rect(dst, rect)
        else:
            # Aim for the size it had before any small display clipped it, so a
            # trip via the 800x600 Denon is not a one-way shrink.
            want = self.clipped.get(hwnd) or (rect[2] - rect[0], rect[3] - rect[1])
            x, y, width, height = placed_rect(src, dst, rect, want)
            if width < want[0] or height < want[1]:
                self.clipped[hwnd] = want        # still owed its real size
                if len(self.clipped) > 200:
                    self.clipped.clear()
            else:
                self.clipped.pop(hwnd, None)     # fully restored

        flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
        if unfullscreened:
            flags |= win32con.SWP_FRAMECHANGED  # or the new frame is not drawn
        try:
            win32gui.SetWindowPos(hwnd, 0, x, y, width, height, flags)
        except Exception as exc:
            log.warning("move failed for %r: %s", title_of(hwnd)[:60], exc)
            return False

        if was_max:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)

        landed = monitor_of_rect(win32gui.GetWindowRect(hwnd), self.monitors)
        if landed is not None and landed.device == dst.device:
            log.info("moved [%s] %r  %s -> %s%s", reason, title_of(hwnd)[:60],
                     rect, win32gui.GetWindowRect(hwnd),
                     "  (left full-screen, frame restored)" if unfullscreened
                     else "  (borderless, centred)" if borderless else "")
            return True
        log.warning("move rejected for %r - elevated window? "
                    "run PhantomMonitor elevated to manage it", title_of(hwnd)[:60])
        return False

    def _leave_fullscreen_then_move(self, hwnd, dst, reason, toggle):
        """Ask the app to leave full-screen, wait for its frame, then move it.

        Runs on its own thread on purpose: this has to wait, and blocking the
        message loop would stall the keyboard hook, which Windows then evicts.
        """
        _label, mods, vk = toggle
        send_key_combo(hwnd, mods, vk)
        for _ in range(25):  # up to ~2.5s for the app to rebuild its frame
            time.sleep(0.1)
            if not win32gui.IsWindow(hwnd):
                return
            if is_user_movable(hwnd):
                break
        if not is_user_movable(hwnd):
            log.warning("%r did not leave full-screen; moving it as it is",
                        title_of(hwnd)[:60])
        # allow_toggle=False so this can never loop back into itself.
        self.move_window(hwnd, dst, reason + "+unfullscreen", allow_toggle=False)

    def _move_minimized(self, hwnd, dst, flags, show_cmd, pt_min, pt_max, normal, reason):
        """Rewrite where a minimized window will reappear, without un-minimizing it."""
        if not self.cfg.get("fix_minimized_restore_position", True):
            return False
        ox, oy = self.workspace_offset()
        screen = (normal[0] + ox, normal[1] + oy, normal[2] + ox, normal[3] + oy)
        src = monitor_of_rect(screen, self.monitors)
        x, y, width, height = placed_rect(src, dst, screen)
        new_normal = (x - ox, y - oy, x + width - ox, y + height - oy)
        try:
            win32gui.SetWindowPlacement(hwnd, (flags, show_cmd, pt_min, pt_max, new_normal))
        except Exception as exc:
            log.debug("placement rewrite failed for %r: %s", title_of(hwnd)[:60], exc)
            return False
        log.info("restore-point [%s] %r -> %s", reason, title_of(hwnd)[:60], dst.label())
        return True

    # -- detection
    def window_rect_for_check(self, hwnd):
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMINIMIZED:
            ox, oy = self.workspace_offset()
            n = placement[4]
            return (n[0] + ox, n[1] + oy, n[2] + ox, n[3] + oy)
        return win32gui.GetWindowRect(hwnd)

    def check_window(self, hwnd, reason, include_offscreen=False):
        if not self.cfg.get("enabled", True):
            return False
        blocked = self.blocked()
        if not is_manageable(hwnd, self.cfg):
            return False
        if not blocked and not include_offscreen:
            return False
        try:
            rect = self.window_rect_for_check(hwnd)
        except Exception:
            return False

        # require_overlap: a window touching no monitor at all is normally
        # parked off-screen on purpose - apps hide helper windows out there and
        # dragging them into view breaks things.
        assigned = self.assigned_display(hwnd)
        if assigned is not None:
            where = monitor_of_rect(rect, self.monitors, require_overlap=True)
            if where is not None and where.device == assigned.device:
                # Pinned there on purpose. Blocking keeps everything else off,
                # which is the whole point of a dedicated screen.
                return False

        here = monitor_of_rect(rect, self.monitors, require_overlap=True)

        if here is None:
            # Nowhere at all. Automatic sweeps leave it; an explicit rescue is
            # the user asking for exactly this - a window a display change threw
            # into dead space, which is otherwise unreachable by any means.
            if not include_offscreen:
                return False
            log.info("recovering %r from off-screen %s",
                     title_of(hwnd)[:60], rect)
        elif here.device not in set(m.device for m in blocked):
            return False

        target = self.rescue_target()
        if target is None:
            return False
        return self.move_window(hwnd, target, reason)

    def sweep(self, reason="sweep", include_offscreen=False):
        """Evacuate blocked displays. With include_offscreen, also recover
        windows sitting on no display at all - what an explicit rescue means."""
        if not self.cfg.get("enabled", True):
            return 0
        if not self.blocked() and not include_offscreen:
            return 0
        moved = 0
        hwnds = []
        win32gui.EnumWindows(lambda h, acc: acc.append(h), hwnds)
        for hwnd in hwnds:
            try:
                if self.check_window(hwnd, reason, include_offscreen):
                    moved += 1
            except Exception as exc:
                log.debug("sweep skipped %s: %s", hwnd, exc)
        if moved:
            log.info("%s rescued %d window(s)", reason, moved)
        return moved

    def assigned_display(self, hwnd):
        """The display this window's app is pinned to, if it is attached."""
        wanted = (self.cfg.get("app_displays") or {}).get(process_name(hwnd))
        return self.by_hwid(wanted) if wanted else None

    def remember_app_position(self, hwnd):
        """Note where a pinned app was left, so it opens there next time.

        Stored in screen coordinates, like icon layouts, so the position stays
        meaningful when the desktop origin moves.
        """
        mon = self.assigned_display(hwnd)
        if mon is None:
            return False
        try:
            if not is_manageable(hwnd, self.cfg):
                return False
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return False
        here = monitor_of_rect(rect, self.monitors, require_overlap=True)
        if here is None or here.device != mon.device:
            return False  # only remember a position on its own display
        positions = dict(self.cfg.get("app_positions") or {})
        positions[process_name(hwnd)] = list(rect)
        self.cfg["app_positions"] = positions
        save_config(self.cfg)
        log.info("remembered where %s sits on %s", process_name(hwnd), mon.name)
        return True

    def place_assigned(self, hwnd):
        """Put a newly opened window on its assigned display.

        Only on opening. After that the user is in charge - a rule decides where
        something starts, it does not follow it around.
        """
        mon = self.assigned_display(hwnd)
        if mon is None or not is_manageable(hwnd, self.cfg):
            return False
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return False
        here = monitor_of_rect(rect, self.monitors, require_overlap=True)
        if here is not None and here.device == mon.device:
            return False  # already where it belongs

        saved = (self.cfg.get("app_positions") or {}).get(process_name(hwnd))
        if saved and len(saved) == 4:
            wanted = tuple(saved)
            if monitor_of_rect(wanted, self.monitors, require_overlap=True) is mon:
                x, y = wanted[0], wanted[1]
                width, height = wanted[2] - wanted[0], wanted[3] - wanted[1]
            else:
                saved = None
        if not saved:
            x, y, width, height = centred_rect(mon, rect)

        try:
            win32gui.SetWindowPos(hwnd, 0, x, y, width, height,
                                  win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        except Exception as exc:
            log.debug("could not place %s: %s", process_name(hwnd), exc)
            return False
        log.info("opened %r on %s (its assigned display)",
                 title_of(hwnd)[:50], mon.name)
        return True

    def move_active_to(self, number, hwnd=None, reason="hotkey"):
        """Move a window to a monitor by number.

        hwnd is passed explicitly by the tray menu, which must capture the user's
        window before the menu opens: showing a popup menu requires taking the
        foreground, after which GetForegroundWindow() returns this app's own
        hidden window rather than whatever the user was looking at.
        """
        mon = self.target_for(number)
        if hwnd is None:
            hwnd = win32gui.GetForegroundWindow()
        if mon is None or not hwnd or not win32gui.IsWindow(hwnd):
            return False
        if mon.hwid in set(m.hwid for m in self.blocked()):
            # Allowing this would just start a fight with the guard, which would
            # yank the window straight back out again.
            log.info("monitor %d (%s) is blocked; refusing to move there",
                     number, mon.name)
            return False
        if not is_manageable(hwnd, self.cfg):
            log.info("%r is not a movable window", title_of(hwnd)[:60])
            return False
        return self.move_window(hwnd, mon, reason)


# --- icons -------------------------------------------------------------------

def make_icon_file(path, body, slash):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((5, 9, 59, 43), radius=5, fill=body,
                           outline=(245, 245, 245, 255), width=3)
    draw.rectangle((27, 43, 37, 51), fill=body)
    draw.rectangle((17, 51, 47, 57), fill=body)
    if slash:
        draw.line((9, 53, 55, 5), fill=(214, 48, 49, 255), width=9)
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
    return True


def load_icon(path):
    if os.path.exists(path):
        try:
            return win32gui.LoadImage(0, path, win32con.IMAGE_ICON, 0, 0,
                                      win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        except Exception:
            pass
    return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)


# --- hotkeys -----------------------------------------------------------------

NAMED_KEYS = {
    "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
    "up": win32con.VK_UP, "down": win32con.VK_DOWN,
    "home": win32con.VK_HOME, "end": win32con.VK_END,
    "pageup": win32con.VK_PRIOR, "pagedown": win32con.VK_NEXT,
    "space": win32con.VK_SPACE, "insert": win32con.VK_INSERT,
    "delete": win32con.VK_DELETE, "esc": win32con.VK_ESCAPE,
    "escape": win32con.VK_ESCAPE, "tab": win32con.VK_TAB,
    "pause": win32con.VK_PAUSE, "break": VK_CANCEL,
}


def parse_hotkey(spec):
    """'ctrl+alt+shift+1' -> (modifiers, virtual-key), or None if unparseable."""
    mods, vk = 0, None
    for part in str(spec).lower().split("+"):
        part = part.strip()
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "windows", "super"):
            mods |= MOD_WIN
        elif part in NAMED_KEYS:
            vk = NAMED_KEYS[part]
        elif len(part) == 1:
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = win32con.VK_F1 + int(part[1:]) - 1
    if vk is None or mods == 0:
        return None  # a bare key with no modifier would hijack normal typing
    return mods | MOD_NOREPEAT, vk


# Never bind these. A matched hotkey is swallowed before it reaches the focused
# application, so binding ctrl+c would stop copy working everywhere on the
# machine with no visible cause. Duplicated in settings_window.py, which runs as
# its own process where importing this module would re-execute it.
RESERVED_HOTKEYS = {
    "ctrl+shift+esc", "ctrl+alt+delete",
    "alt+tab", "alt+esc", "alt+space", "alt+f4",
    "win+l", "win+d", "win+e", "win+r", "win+tab",
}


def unsafe_hotkey(spec):
    """Why a combination must not be bound, or None if it is fine."""
    spec = (spec or "").strip().lower()
    if not spec:
        return None
    parts = [p for p in spec.split("+") if p]
    mods = [p for p in parts if p in ("ctrl", "alt", "shift", "win")]
    if len(parts) < 2 or not mods:
        return "needs at least one modifier"
    if len(mods) < 2:
        return "needs two modifiers, or it would swallow a shortcut everything uses"
    if spec in RESERVED_HOTKEYS:
        return "Windows or every application already uses it"
    return None


def resolve_hotkeys(cfg):
    """Config -> {action: (mods, vk)}, where action 0 = rescue, N = monitor N."""
    table = cfg.get("hotkeys")
    if not isinstance(table, dict):  # fall back to the older prefix scheme
        table = {}
        if cfg.get("hotkey_rescue"):
            table["rescue"] = cfg["hotkey_rescue"]
        prefix = cfg.get("hotkey_move_prefix")
        if prefix:
            for number in range(1, 10):
                table["monitor_%d" % number] = "%s+%d" % (prefix, number)

    out = {}
    for name, spec in table.items():
        if not spec:
            continue
        problem = unsafe_hotkey(spec)
        if problem:
            log.warning("refusing hotkey %r for %r: %s", spec, name, problem)
            continue
        combo = parse_hotkey(spec)
        if not combo:
            log.warning("hotkey %r for %r is not understood; ignoring", spec, name)
            continue
        if name == "rescue":
            out[0] = combo
        elif name.startswith("monitor_") and name.split("_")[-1].isdigit():
            out[int(name.split("_")[-1])] = combo
        else:
            log.warning("unknown hotkey entry %r; ignoring", name)
    return out


# --- tray application --------------------------------------------------------

class TrayApp:
    def __init__(self, cfg):
        self.cfg = cfg
        self.guard = Guard(cfg)
        self.actions = {}
        self.menu_target = 0
        self.last_focused = 0  # updated on every foreground change; the tray menu needs it
        self.hooks = []
        self.event_proc = None
        self.key_hook = None
        self.key_proc = None
        self.cursor_clipped = False
        self.clip_warned = False
        self.icons_warned = False
        self.icons_quiet_until = 0.0  # do not snapshot while a change is settling
        self.icon_listview = 0
        self.icon_watch_stop = None
        self.settings_child = None   # one settings window at a time
        self.window_snapshot = {}   # {hwnd: placement} - the last known good state
        self.windows_frozen = False  # stop snapshotting while a change is underway
        self.last_signature = ""     # only restore when the layout actually changed
        self.verify_left = 0         # remaining late-nudge checks after a restore
        self.hotkeys = resolve_hotkeys(cfg)  # {0: rescue, N: monitor N}
        self.settle_left = 0
        self.icon_active = 0
        self.icon_paused = 0
        self.hwnd = self._create_window()
        self._make_icons()
        self._add_tray_icon()
        self._install_hooks()
        self._register_hotkeys()
        self._install_key_hook()
        user32.SetTimer(self.hwnd, TIMER_SWEEP,
                          max(500, int(cfg.get("sweep_interval_ms", 2000))), None)
        # Prime it, so the menu works before the first focus change arrives.
        startup_fg = win32gui.GetForegroundWindow()
        if startup_fg and is_manageable(startup_fg, cfg):
            self.last_focused = startup_fg
        self.guard.sweep("startup")
        self._apply_cursor_clip()
        self._find_icon_listview()
        self.last_signature = topology_signature(self.guard.monitors)
        self._snapshot_icons("startup")
        self._snapshot_windows("startup")
        self._start_icon_watch()
        if self.cfg.get("check_updates_on_start", False):
            self._check_updates(quiet=True)

    # -- window plumbing
    def _create_window(self):
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "PhantomMonitorWnd"
        wc.lpfnWndProc = self._wnd_proc
        wc.hInstance = win32api.GetModuleHandle(None)
        try:
            atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            atom = "PhantomMonitorWnd"
        hwnd = win32gui.CreateWindow(atom, APP_NAME, 0, 0, 0, 0, 0, 0, 0,
                                     wc.hInstance, None)
        win32gui.UpdateWindow(hwnd)
        return hwnd

    def _make_icons(self):
        active = os.path.join(APP_DIR, "icon_active.ico")
        paused = os.path.join(APP_DIR, "icon_paused.ico")
        if not os.path.exists(active):
            make_icon_file(active, (10, 90, 170, 255), True)
        if not os.path.exists(paused):
            make_icon_file(paused, (120, 120, 120, 255), False)
        self.icon_active = load_icon(active)
        self.icon_paused = load_icon(paused)

    def _tooltip(self):
        blocked = self.guard.blocked()
        if not self.cfg.get("enabled", True):
            return APP_NAME + " - paused"
        if not blocked:
            return APP_NAME + " - no display blocked"
        return APP_NAME + " - blocking " + ", ".join(m.name for m in blocked)

    def _add_tray_icon(self):
        icon = self.icon_active if self.cfg.get("enabled", True) else self.icon_paused
        nid = (self.hwnd, 0,
               win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
               WM_TRAYICON, icon, self._tooltip())
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    def _update_tray_icon(self):
        icon = self.icon_active if self.cfg.get("enabled", True) else self.icon_paused
        nid = (self.hwnd, 0,
               win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
               WM_TRAYICON, icon, self._tooltip())
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
        except Exception:
            pass

    def _remove_tray_icon(self):
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except Exception:
            pass

    # -- event hooks
    def _install_hooks(self):
        self.event_proc = WinEventProcType(self._on_event)
        ranges = [
            (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
            (EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND),
            (EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW),
            (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),
        ]
        for low, high in ranges:
            handle = user32.SetWinEventHook(
                low, high, 0, self.event_proc, 0, 0,
                WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
            if handle:
                self.hooks.append(handle)
        log.info("installed %d window event hooks", len(self.hooks))

    def _start_icon_watch(self):
        """Wake whenever Explorer rewrites the desktop icon layout.

        Blocks in the kernel until the registry key actually changes, so it
        costs nothing while nothing is happening - unlike re-reading every icon
        on a timer.
        """
        if not self.cfg.get("restore_icons", True):
            return
        self.icon_watch_stop = kernel32.CreateEventW(None, True, False, None)
        thread = threading.Thread(target=self._icon_watch_loop, daemon=True)
        thread.start()

    def _icon_watch_loop(self):
        while True:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ICON_BAG_KEY)
            except OSError:
                if kernel32.WaitForSingleObject(self.icon_watch_stop, 10000) == WAIT_OBJECT_0:
                    return
                continue
            try:
                changed = kernel32.CreateEventW(None, True, False, None)
                rc = advapi32.RegNotifyChangeKeyValue(
                    wt.HKEY(int(key)), False, REG_NOTIFY_CHANGE_LAST_SET,
                    wt.HANDLE(changed), True)
                if rc != 0:
                    log.debug("registry watch unavailable (%s); icon saves are manual", rc)
                    return
                while True:
                    result = kernel32.WaitForSingleObject(changed, 1000)
                    if kernel32.WaitForSingleObject(self.icon_watch_stop, 0) == WAIT_OBJECT_0:
                        return
                    if result == WAIT_OBJECT_0:
                        win32gui.PostMessage(self.hwnd, WM_APP_ICONS, 0, 0)
                        break
            except Exception as exc:
                log.debug("icon watch error: %s", exc)
                return
            finally:
                try:
                    key.Close()
                except Exception:
                    pass

    def _find_icon_listview(self):
        self.icon_listview = desktop_icons.desktop_listview()
        return self.icon_listview

    def _icons_moved(self):
        """An icon was dragged. Debounce, so one drag session means one save."""
        if not self.cfg.get("restore_icons", True):
            return
        user32.KillTimer(self.hwnd, TIMER_ICONS)
        delay = max(500, int(self.cfg.get("icon_save_debounce_ms", 4000)))
        user32.SetTimer(self.hwnd, TIMER_ICONS, delay, None)

    def _on_event(self, _hook, event, hwnd, id_object, id_child, _thread, _ts):
        if not hwnd:
            return
        # Icon moves arrive against the desktop ListView with a child id, so
        # they have to be picked up before the top-level-window filter below.
        if (event == EVENT_OBJECT_LOCATIONCHANGE and self.icon_listview
                and hwnd == self.icon_listview):
            self._icons_moved()
            return
        if id_object != OBJID_WINDOW or id_child != 0:
            return
        try:
            if event == EVENT_SYSTEM_MOVESIZESTART:
                self.guard.dragging = True
                return
            if event == EVENT_SYSTEM_MOVESIZEEND:
                self.guard.dragging = False
                self.guard.check_window(hwnd, "drag-end")
                self.guard.remember_app_position(hwnd)
                self._windows_moved()
                return
            if event == EVENT_OBJECT_LOCATIONCHANGE:
                if self.guard.dragging:
                    return
                cooldown = self.cfg.get("locationchange_cooldown_ms", 400) / 1000.0
                now = time.monotonic()
                if now - self.guard.last_touch.get(hwnd, 0.0) < cooldown:
                    return
                self.guard.last_touch[hwnd] = now
                if len(self.guard.last_touch) > 500:
                    self.guard.last_touch.clear()
                self.guard.check_window(hwnd, "moved")
                # Also covers maximize, snap and app-driven moves, none of which
                # produce a MOVESIZEEND the way a mouse drag does.
                self._windows_moved()
                return
            if event == EVENT_OBJECT_SHOW:
                # A rule decides where a window starts, not where it stays.
                self.guard.place_assigned(hwnd)

            if event == EVENT_SYSTEM_FOREGROUND:
                if is_manageable(hwnd, self.cfg):
                    # Remember it while we still can - the tray menu needs this.
                    self.last_focused = hwnd
                self._apply_cursor_clip()  # Windows drops the clip on focus changes
            self.guard.check_window(hwnd, "shown" if event == EVENT_OBJECT_SHOW else "focus")
        except Exception as exc:
            log.debug("event %s handling failed: %s", event, exc)

    # -- window layouts
    #
    # A display change makes Windows shuffle every window, and it never puts
    # them back. Layouts are keyed by display arrangement and held per session
    # by window handle, which is exact: these are the same windows that were
    # open a moment ago, so there is no guessing which window is which.
    def _windows_moved(self):
        """A window moved. Debounce, so one drag session means one snapshot."""
        if not self.cfg.get("restore_windows", True) or self.windows_frozen:
            return
        user32.KillTimer(self.hwnd, TIMER_WINDOWS)
        delay = max(500, int(self.cfg.get("window_save_debounce_ms", 2000)))
        user32.SetTimer(self.hwnd, TIMER_WINDOWS, delay, None)

    def _snapshot_windows(self, reason="moved"):
        """Record where every window is *now* - the last known good state.

        Deliberately one live snapshot rather than one per display arrangement.
        The question worth answering after a display change is "which windows
        did that just move?", not "where were these last time this arrangement
        existed" - the latter reapplies a historical layout over whatever the
        user has done since, and drags windows the change never touched.
        """
        if not self.cfg.get("restore_windows", True) or self.windows_frozen:
            return 0
        snapshot = {}
        hwnds = []
        win32gui.EnumWindows(lambda h, acc: acc.append(h), hwnds)
        for hwnd in hwnds:
            if not is_manageable(hwnd, self.cfg):
                continue
            try:
                snapshot[hwnd] = win32gui.GetWindowPlacement(hwnd)
            except Exception:
                continue
        if not snapshot:
            return 0
        self.window_snapshot = snapshot
        log.debug("window snapshot [%s]: %d windows", reason, len(snapshot))
        return len(snapshot)

    def _restore_windows(self, reason="display change"):
        """Put back only the windows the display change actually displaced.

        The snapshot was frozen the moment the change began, so any window whose
        placement now differs was moved by the change and not by the user. A
        window still sitting where the snapshot says is left strictly alone.
        """
        if not self.cfg.get("restore_windows", True) or not self.window_snapshot:
            return 0
        restored = skipped = 0
        for hwnd, placement in self.window_snapshot.items():
            try:
                if not window_displaced(hwnd, placement, self.cfg):
                    continue  # untouched by the change - do not interfere
                current = win32gui.GetWindowPlacement(hwnd)
                # Only put it back somewhere that still exists.
                ox, oy = self.guard.workspace_offset()
                n = placement[4]
                target = (n[0] + ox, n[1] + oy, n[2] + ox, n[3] + oy)
                if monitor_of_rect(target, self.guard.monitors,
                                   require_overlap=True) is None:
                    skipped += 1
                    continue
                wanted = placement
                if current[1] == win32con.SW_SHOWMINIMIZED:
                    # Leave it minimized; just correct where it will reappear.
                    wanted = (placement[0], current[1], placement[2], placement[3],
                              placement[4])
                win32gui.SetWindowPlacement(hwnd, wanted)
                restored += 1
            except Exception:
                continue
        if restored or skipped:
            log.info("put back %d displaced window(s) [%s]%s", restored, reason,
                     "; %d skipped, their old spot is gone" % skipped if skipped else "")
        return restored

    # -- desktop icon layouts
    #
    # Windows recalculates the desktop icon grid on a display change and never
    # puts the icons back - one unplug can dump 160 icons onto the wrong screen,
    # stacked on top of each other. Layouts are stored per display arrangement,
    # so unplugging and replugging returns to the layout that arrangement had.
    def _load_layouts(self):
        """{"version": 2, "last": sig, "layouts": {sig: {name: [screen_x, screen_y]}}}

        Positions are stored in SCREEN coordinates. ListView coordinates are
        relative to the virtual desktop's top-left corner, which shifts whenever
        a display on the left changes size - so the same physical spot has a
        different number in every arrangement, and layouts could never carry
        from one to another.
        """
        empty = {"version": 2, "last": "", "layouts": {}}
        try:
            with open(ICON_LAYOUT_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return empty
        was_v1 = isinstance(data, dict) and data.get("version") != 2
        result = normalize_layouts(data)
        if was_v1 and result["layouts"]:
            log.info("converted %d icon layout(s) to screen coordinates",
                     len(result["layouts"]))
        return result

    def _write_layouts(self, data):
        try:
            with open(ICON_LAYOUT_PATH, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=1)
        except OSError as exc:
            log.error("could not write icon layouts: %s", exc)

    def _snapshot_icons(self, reason="periodic"):
        if not self.cfg.get("restore_icons", True):
            return 0
        icons = desktop_icons.get_icons()
        if not icons:
            return 0
        sig = topology_signature(self.guard.monitors)
        ox, oy = virtual_origin(self.guard.monitors)
        data = self._load_layouts()
        # Merge rather than replace. A virtual-desktop manager such as Dexpot
        # can swap in a different set of icons, and replacing would throw away
        # the positions of every icon that is not currently on screen.
        merged = data["layouts"].get(sig) or {}
        merged.update(dict((name, [pos[0] + ox, pos[1] + oy])
                           for name, pos in icons.items()))
        data["layouts"][sig] = merged
        data["last"] = sig
        self._write_layouts(data)
        log.info("icon layout saved [%s]: %d icons", reason, len(icons))
        return len(icons)

    def _restore_icons(self, reason="display change"):
        if not self.cfg.get("restore_icons", True):
            return 0
        listview = desktop_icons.desktop_listview()
        if listview and desktop_icons.auto_arrange_on(listview):
            if not self.icons_warned:
                log.warning("desktop auto-arrange is on, so icon positions will "
                            "not stick; turn it off in the desktop right-click menu")
                self.icons_warned = True
            return 0
        sig = topology_signature(self.guard.monitors)
        data = self._load_layouts()
        layout = data["layouts"].get(sig)
        source = "this arrangement"
        if not layout:
            # Never seen this arrangement. Inherit from the last one instead of
            # letting Explorer's scramble stand: positions are in screen
            # coordinates, so anything on a display that did not move is still
            # exactly right.
            layout = data["layouts"].get(data.get("last") or "")
            source = "inherited from the previous arrangement"
        if not layout:
            log.info("no icon layout saved yet; keeping the current one as a baseline")
            return self._snapshot_icons("first run")

        ox, oy = virtual_origin(self.guard.monitors)
        wanted, offscreen = {}, 0
        for name, pos in layout.items():
            sx, sy = pos[0], pos[1]
            # Skip anything that would land where there is no longer a display.
            if monitor_of_rect((sx, sy, sx + 32, sy + 32), self.guard.monitors,
                               require_overlap=True) is None:
                offscreen += 1
                continue
            wanted[name] = (sx - ox, sy - oy)

        moved = desktop_icons.set_icons(wanted, listview)
        if moved or offscreen:
            log.info("restored %d desktop icon positions [%s, %s]%s",
                     moved, reason, source,
                     "; %d skipped as off-screen" % offscreen if offscreen else "")
        return moved

    # -- cursor fence
    def _release_cursor_clip(self):
        if self.cursor_clipped:
            user32.ClipCursor(None)
            self.cursor_clipped = False

    def _apply_cursor_clip(self):
        """Fence the pointer out of blocked displays.

        Uses ClipCursor rather than a low-level mouse hook: it costs nothing per
        mouse movement, so it cannot add input latency in a game. Windows drops
        the clip whenever the foreground window changes, so this is re-applied
        on focus changes, display changes and the periodic sweep.
        """
        if (not self.cfg.get("block_cursor", False)
                or not self.cfg.get("enabled", True)
                or not self.guard.blocked()):
            # Nothing blocked means nothing to fence - not a failure to fence.
            # And switching the guard off has to release the fence too, or
            # "off" still leaves the pointer unable to reach a display.
            self._release_cursor_clip()
            return
        box = cursor_clip_rect(self.guard.monitors, self.guard.blocked())
        if box is None:
            self._release_cursor_clip()
            if not self.clip_warned:
                log.warning("cannot fence the pointer out of the blocked display: "
                            "this layout needs more than one rectangle")
                self.clip_warned = True
            return
        rect = wt.RECT(box[0], box[1], box[2], box[3])
        if user32.ClipCursor(ctypes.byref(rect)):
            if not self.cursor_clipped:
                log.info("pointer fenced to %s", box)
            self.cursor_clipped = True

    # -- low-level keyboard hook
    #
    # RegisterHotKey alone is not enough. mstsc installs its own WH_KEYBOARD_LL
    # hook and swallows key combos before Windows dispatches registered hotkeys,
    # so nothing reaches us while an RDP session has focus - worst of all in
    # full screen, where the tray icon may be covered too. Low-level hooks are
    # called most-recently-installed first, so install one of our own and
    # re-install it whenever the foreground changes, staying ahead of mstsc's.
    def _install_key_hook(self):
        if not self.cfg.get("intercept_hotkeys", True):
            return
        self.key_proc = LowLevelKeyboardProc(self._on_key)   # keep a reference
        self.key_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self.key_proc, wt.HINSTANCE(0), 0)
        if not self.key_hook:
            log.warning("keyboard hook failed; hotkeys will not reach us over "
                        "full-screen RDP")

    def _remove_key_hook(self):
        if self.key_hook:
            user32.UnhookWindowsHookEx(self.key_hook)
            self.key_hook = None

    def _refresh_key_hook(self):
        """Re-install so we sit in front of hooks other apps just added."""
        if self.key_hook:
            self._remove_key_hook()
            self._install_key_hook()

    def _mods_held(self):
        held = 0
        if user32.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000:
            held |= MOD_CONTROL
        if user32.GetAsyncKeyState(win32con.VK_MENU) & 0x8000:
            held |= MOD_ALT
        if user32.GetAsyncKeyState(win32con.VK_SHIFT) & 0x8000:
            held |= MOD_SHIFT
        if (user32.GetAsyncKeyState(win32con.VK_LWIN) & 0x8000
                or user32.GetAsyncKeyState(win32con.VK_RWIN) & 0x8000):
            held |= MOD_WIN
        return held

    def _key_action(self, vk):
        """0 for rescue, 1-9 for that monitor, None if the combo is not ours."""
        held = self._mods_held()
        for action, (mods, key) in self.hotkeys.items():
            if key == vk and (mods & ~MOD_NOREPEAT) == held:
                return action
        return None

    def _on_key(self, ncode, wparam, lparam):
        if ncode == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            action = None
            try:
                vk = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
                action = self._key_action(vk)
            except Exception:
                pass
            if action is not None:
                # Hand off to the message loop rather than working inside the
                # hook: Windows silently evicts a hook that takes too long.
                # Returning 1 swallows the key so it never reaches the session.
                win32gui.PostMessage(self.hwnd, WM_APP_HOTKEY, action, 0)
                return 1
        return user32.CallNextHookEx(None, ncode, wparam, lparam)

    # -- hotkeys
    def _action_name(self, action):
        return "rescue" if action == 0 else "move to monitor %d" % action

    def _register_hotkeys(self):
        specs = self.cfg.get("hotkeys") or {}
        for action, (mods, vk) in sorted(self.hotkeys.items()):
            spec = specs.get("rescue" if action == 0 else "monitor_%d" % action, "?")
            # Spell out exactly which physical screen each hotkey resolves to,
            # so a renumbering is visible in the log rather than a surprise.
            if action == 0:
                what = "rescue all"
            else:
                target = self.guard.target_for(action)
                what = ("move to %s" % target.label() if target
                        else "move to monitor %d (not attached)" % action)
            if user32.RegisterHotKey(self.hwnd, HOTKEY_ID_BASE + action, mods, vk):
                log.info("hotkey %s -> %s", spec, what)
            else:
                log.warning("hotkey %s (%s) is already claimed by another app",
                            spec, what)

    def _unregister_hotkeys(self):
        for action in range(0, 10):
            user32.UnregisterHotKey(self.hwnd, HOTKEY_ID_BASE + action)

    def _run_action(self, action):
        if action == 0:
            self.guard.sweep("hotkey", include_offscreen=True)
        elif 1 <= action <= 9:
            self.guard.move_active_to(
                action, pick_menu_target(self.last_focused, self.cfg), "hotkey")

    def _rebuild_hotkeys(self):
        self._unregister_hotkeys()
        self._register_hotkeys()

    # -- menu
    def _show_menu(self):
        self.menu_target = pick_menu_target(self.last_focused, self.cfg)
        self.guard.refresh_monitors()
        self.actions = {}
        menu = win32gui.CreatePopupMenu()
        next_id = [1000]

        def add(text, handler, checked=False, enabled=True):
            flags = win32con.MF_STRING
            if checked:
                flags |= win32con.MF_CHECKED
            if not enabled:
                flags |= win32con.MF_GRAYED
            item_id = next_id[0]
            next_id[0] += 1
            win32gui.AppendMenu(menu, flags, item_id, text)
            self.actions[item_id] = handler

        def add_sub(text, submenu):
            win32gui.AppendMenu(menu, win32con.MF_STRING | win32con.MF_POPUP,
                                submenu, text)

        def sep():
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")

        enabled = self.cfg.get("enabled", True)
        # Ask the guard what is actually blocked right now: a rule can be
        # qualified by resolution, so a rule existing is not the same as it
        # currently applying.
        blocked_now = set(m.device for m in self.guard.blocked())

        add("Rescue windows now",
            lambda: self.guard.sweep("manual", include_offscreen=True))
        add("Save window + icon layout now", self._snapshot_all)
        add("Restore window + icon layout", self._restore_all)
        add("Auto-restore window positions", self._toggle_restore_windows,
            checked=self.cfg.get("restore_windows", True))
        add("Auto-restore desktop icons", self._toggle_restore_icons,
            checked=self.cfg.get("restore_icons", True))
        add("Keep windows off blocked displays", self._toggle_enabled,
            checked=enabled)
        add("Keep pointer off blocked displays", self._toggle_cursor_block,
            checked=self.cfg.get("block_cursor", False) and enabled,
            enabled=enabled)
        sep()

        move_menu = win32gui.CreatePopupMenu()
        specs = self.cfg.get("hotkeys") or {}
        for mon in self.guard.monitors:
            if mon.device in blocked_now:
                win32gui.AppendMenu(move_menu, win32con.MF_STRING | win32con.MF_GRAYED,
                                    0, mon.label() + "   (blocked)")
                continue
            item_id = next_id[0]
            next_id[0] += 1
            spec = specs.get("monitor_%d" % mon.number, "")
            win32gui.AppendMenu(move_menu, win32con.MF_STRING, item_id,
                                mon.label() + (("\t" + spec) if spec else ""))
            self.actions[item_id] = self._make_move_action(mon.number)

        # Name the window it will act on, so there is no guessing about which
        # one the menu captured.
        if self.menu_target and is_manageable(self.menu_target, self.cfg):
            add_sub('Move "%s" to' % shorten(title_of(self.menu_target), 34), move_menu)
        else:
            win32gui.AppendMenu(menu, win32con.MF_STRING | win32con.MF_GRAYED, 0,
                                "Move active window to  (none focused)")
            win32gui.DestroyMenu(move_menu)
        sep()

        add("Check for updates", self._check_updates)
        add("Project page", lambda: open_url(PROJECT_URL))
        support = (self.cfg.get("support_url") or "").strip()
        if support:
            add("Support this project", lambda: open_url(support))
        sep()

        add("Start with Windows", self._toggle_autostart,
            checked=os.path.exists(STARTUP_VBS))
        editor = self.cfg.get("editor", "")
        add("Settings...", self._open_settings)
        add("Edit config file", lambda: open_text_file(CONFIG_PATH, editor))
        add("Reload settings", self._reload_config)
        add("Open config folder", lambda: os.startfile(APP_DIR))
        add("View log", lambda: open_text_file(LOG_PATH, editor))
        sep()
        add("Quit " + APP_NAME, self._quit)

        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
                                pos[0], pos[1], 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)

    def _make_block_toggle(self, hwid):
        def toggle():
            blocked = list(self.cfg.get("blocked_hwids", []))
            parked = list(self.cfg.get("blocked_rules_parked", []))
            existing = [s for s in blocked if parse_block_spec(s)[0] == hwid]
            if existing:
                # Park the exact rule rather than discarding it: re-ticking used
                # to come back as a bare hardware id, silently losing a
                # resolution qualifier like DON0015@<1280x720.
                blocked = [s for s in blocked if s not in existing]
                parked = [s for s in parked if parse_block_spec(s)[0] != hwid] + existing
            elif hwid:
                restored = [s for s in parked if parse_block_spec(s)[0] == hwid]
                blocked.extend(restored or [hwid])
                parked = [s for s in parked if parse_block_spec(s)[0] != hwid]
            self.cfg["blocked_hwids"] = blocked
            self.cfg["blocked_rules_parked"] = parked
            save_config(self.cfg)
            log.info("blocked displays now: %s", blocked or "(none)")
            self.clip_warned = False
            self._apply_cursor_clip()
            self._update_tray_icon()
            self.guard.sweep("config-change")
        return toggle

    def _make_assign_action(self, hwid, slot):
        """Point a hotkey slot at a display, by hardware id.

        This is what makes the numbers mean something: the user decides which
        screen is 1, 2, 3, and it stays that way through renumbering, adapter
        swaps and anything else Windows does to its own ordering.
        """
        def assign():
            targets = dict(self.cfg.get("hotkey_targets") or {})
            # One display per slot, one slot per display.
            targets = dict((k, v) for k, v in targets.items() if v != hwid)
            targets[str(slot)] = hwid
            self.cfg["hotkey_targets"] = targets
            save_config(self.cfg)
            self._rebuild_hotkeys()
            log.info("hotkey slot %d now means %s", slot, hwid)
        return assign

    def _make_unassign_action(self, hwid):
        def clear():
            targets = dict(self.cfg.get("hotkey_targets") or {})
            self.cfg["hotkey_targets"] = dict(
                (k, v) for k, v in targets.items() if v != hwid)
            save_config(self.cfg)
            self._rebuild_hotkeys()
            log.info("%s is no longer pinned to a hotkey slot", hwid)
        return clear

    def _make_move_action(self, number):
        target = self.menu_target  # bind now, not when the item is clicked
        return lambda: self.guard.move_active_to(number, target, "tray menu")

    def _check_updates(self, quiet=False):
        """Ask GitHub for the newest release. Off the message loop - a network
        call must never be able to freeze the tray."""
        def look():
            tag = latest_release()
            if tag is None:
                if not quiet:
                    win32api.MessageBox(
                        0, "Could not reach GitHub to check for updates.",
                        APP_NAME, win32con.MB_OK | win32con.MB_ICONINFORMATION)
                return
            if version_tuple(tag) > version_tuple(APP_VERSION):
                log.info("update available: %s (running %s)", tag, APP_VERSION)
                message = ("%s is available - you have %s.\n\n"
                           "Open the downloads page?" % (tag, APP_VERSION))
                answer = win32api.MessageBox(
                    0, message, APP_NAME,
                    win32con.MB_YESNO | win32con.MB_ICONINFORMATION)
                if answer == win32con.IDYES:
                    open_url(RELEASES_URL)
            elif not quiet:
                win32api.MessageBox(
                    0, "You have the latest version (%s)." % APP_VERSION,
                    APP_NAME, win32con.MB_OK | win32con.MB_ICONINFORMATION)

        threading.Thread(target=look, daemon=True).start()

    def _open_settings(self):
        """Run the settings window as a child process.

        Not a thread: Tk owns an event loop and this process already runs a
        Windows message pump, and a crash in the settings UI should not take
        the guard down with it.
        """
        if self.settings_child and self.settings_child.poll() is None:
            log.info("settings window is already open")
            return
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--settings"]
        else:
            command = [sys.executable, os.path.join(APP_DIR, "phantommonitor.py"),
                       "--settings"]
        try:
            child = subprocess.Popen(command)
        except OSError as exc:
            log.error("could not open settings: %s", exc)
            return
        self.settings_child = child

        # Stand the hotkeys down while settings are open. Otherwise pressing a
        # combination to record it is swallowed by the very hook that is
        # listening for it, and moves a window instead of filling the box.
        self._unregister_hotkeys()
        self._remove_key_hook()
        log.info("hotkeys suspended while the settings window is open")

        def wait_then_reload():
            child.wait()
            win32gui.PostMessage(self.hwnd, WM_APP_SETTINGS_CLOSED, 0, 0)

        threading.Thread(target=wait_then_reload, daemon=True).start()

    def _reload_config(self):
        """Re-read config.json and re-arm hotkeys without restarting."""
        fresh = load_config()
        self.cfg.clear()          # Guard holds the same dict, so mutate in place
        self.cfg.update(fresh)
        self.hotkeys = resolve_hotkeys(self.cfg)
        self._rebuild_hotkeys()
        self._refresh_key_hook()
        self._update_tray_icon()
        log.info("settings reloaded")
        self.guard.sweep("reload")

    def _snapshot_all(self):
        windows = self._snapshot_windows("manual")
        icons = self._snapshot_icons("manual")
        log.info("saved %d window position(s) and %d icon(s)", windows, icons)

    def _restore_all(self):
        self._restore_windows("manual")
        self._restore_icons("manual")

    def _toggle_restore_windows(self):
        self.cfg["restore_windows"] = not self.cfg.get("restore_windows", True)
        save_config(self.cfg)
        log.info("window position restore %s",
                 "on" if self.cfg["restore_windows"] else "off")
        if self.cfg["restore_windows"]:
            self._snapshot_windows("just enabled")

    def _toggle_restore_icons(self):
        self.cfg["restore_icons"] = not self.cfg.get("restore_icons", True)
        self.icons_warned = False
        save_config(self.cfg)
        log.info("desktop icon restore %s",
                 "on" if self.cfg["restore_icons"] else "off")
        if self.cfg["restore_icons"]:
            self._snapshot_icons("just enabled")

    def _toggle_cursor_block(self):
        self.cfg["block_cursor"] = not self.cfg.get("block_cursor", False)
        self.clip_warned = False
        save_config(self.cfg)
        self._apply_cursor_clip()
        log.info("pointer fence %s", "on" if self.cfg["block_cursor"] else "off")

    def _toggle_enabled(self):
        self.cfg["enabled"] = not self.cfg.get("enabled", True)
        save_config(self.cfg)
        log.info("guard %s", "enabled" if self.cfg["enabled"] else "paused")
        self._apply_cursor_clip()
        self._update_tray_icon()
        if self.cfg["enabled"]:
            self.guard.sweep("re-enabled")

    def _toggle_autostart(self):
        if os.path.exists(STARTUP_VBS):
            try:
                os.remove(STARTUP_VBS)
                log.info("autostart removed")
            except OSError as exc:
                log.error("could not remove autostart: %s", exc)
            return
        if getattr(sys, "frozen", False):
            # A packaged build launches itself; there is no interpreter or
            # script path involved.
            body = ('Set sh = CreateObject("WScript.Shell")\r\n'
                    'sh.Run """%s""", 0, False\r\n' % os.path.abspath(sys.executable))
        else:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            script = os.path.join(APP_DIR, "phantommonitor.py")
            body = ('Set sh = CreateObject("WScript.Shell")\r\n'
                    'sh.Run """%s"" ""%s""", 0, False\r\n' % (pythonw, script))
        try:
            with open(STARTUP_VBS, "w", encoding="utf-8") as handle:
                handle.write(body)
            log.info("autostart installed: %s", STARTUP_VBS)
        except OSError as exc:
            log.error("could not install autostart: %s", exc)

    def _quit(self):
        win32gui.DestroyWindow(self.hwnd)

    # -- message pump
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            if lparam in (win32con.WM_RBUTTONUP, win32con.WM_LBUTTONUP):
                try:
                    self._show_menu()
                except Exception:
                    # A window procedure swallows exceptions, so a broken menu
                    # builder shows up as clicks doing nothing at all. Say so.
                    log.exception("could not build the tray menu")
            elif lparam == win32con.WM_LBUTTONDBLCLK:
                self.guard.sweep("double-click")
            return 0

        if msg == win32con.WM_COMMAND:
            handler = self.actions.get(win32api.LOWORD(wparam))
            if handler:
                try:
                    handler()
                except Exception as exc:
                    log.error("menu action failed: %s", exc)
            return 0

        if msg == win32con.WM_HOTKEY:
            self._run_action(wparam - HOTKEY_ID_BASE)
            return 0

        if msg == WM_APP_HOTKEY:  # posted from the low-level keyboard hook
            self._run_action(wparam)
            return 0

        if msg == WM_APP_ICONS:  # Explorer rewrote the desktop icon layout
            self._icons_moved()
            return 0

        if msg == WM_APP_SETTINGS_CLOSED:
            self.settings_child = None
            self._reload_config()      # re-registers hotkeys from the new config
            self._install_key_hook()
            log.info("hotkeys resumed")
            return 0

        if msg == win32con.WM_TIMER:
            if wparam == TIMER_SWEEP:
                self.guard.sweep("periodic")
                self._apply_cursor_clip()
                if not win32gui.IsWindow(self.icon_listview):
                    self._find_icon_listview()  # Explorer may have restarted
            elif wparam == TIMER_ICONS:
                user32.KillTimer(self.hwnd, TIMER_ICONS)
                if time.monotonic() >= self.icons_quiet_until:
                    self._snapshot_icons("icons moved")
            elif wparam == TIMER_VERIFY:
                if self._restore_windows("late nudge"):
                    self._snapshot_windows("after late fix")
                self.verify_left -= 1
                if self.verify_left <= 0:
                    user32.KillTimer(self.hwnd, TIMER_VERIFY)
            elif wparam == TIMER_WINDOWS:
                user32.KillTimer(self.hwnd, TIMER_WINDOWS)
                if time.monotonic() >= self.icons_quiet_until:
                    self._snapshot_windows("windows moved")
            elif wparam == TIMER_SETTLE:
                self.guard.refresh_monitors()
                self.guard.sweep("display-change")
                self.settle_left -= 1
                if self.settle_left <= 0:
                    user32.KillTimer(self.hwnd, TIMER_SETTLE)
                    sig = topology_signature(self.guard.monitors)
                    if sig == self.last_signature:
                        # A display event that left the layout identical - a
                        # link renegotiating, a monitor waking. Nothing was
                        # scrambled, so restoring here would not put anything
                        # back; it would only undo what the user has done since
                        # the last snapshot, yanking windows while they work.
                        log.info("display event with no layout change; "
                                 "nothing to restore")
                        self.windows_frozen = False
                    else:
                        log.info("layout changed; restoring")
                        self.last_signature = sig
                        # Windows and Explorer have finished shuffling by now.
                        self._restore_windows()
                        self._find_icon_listview()  # it may have been recreated
                        self._restore_icons()
                        # Our own restore moves icons, which fires the same
                        # events; stay quiet briefly so it does not re-save
                        # what it just did.
                        self.icons_quiet_until = time.monotonic() + 10
                        self.windows_frozen = False
                        self._snapshot_windows("after restore")
                        # Windows and apps keep nudging windows for a while
                        # after the settle expires - a maximized window
                        # re-laying out, an app reacting to the resolution
                        # change. Check again shortly. These passes only touch
                        # windows that differ from the snapshot, and the
                        # snapshot now tracks the user again, so anything they
                        # move in the meantime is left alone.
                        self.verify_left = 3
                        user32.SetTimer(self.hwnd, TIMER_VERIFY, 2500, None)
            return 0

        if msg in (win32con.WM_DISPLAYCHANGE, win32con.WM_DEVICECHANGE):
            # Topology takes a moment to settle, and Windows shuffles windows
            # as it does - re-sweep a few times over the next few seconds.
            log.info("display configuration changed")
            self.guard.refresh_monitors()
            self._rebuild_hotkeys()
            self.settle_left = 6
            # Freeze both snapshots for the duration. Whatever they hold right
            # now is "before the change", which is exactly what a restore needs
            # to compare against.
            self.windows_frozen = True
            self.icons_quiet_until = time.monotonic() + 30
            user32.SetTimer(self.hwnd, TIMER_SETTLE, 700, None)
            return 0

        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0

        if msg == win32con.WM_DESTROY:
            log.info("shutting down")
            for handle in self.hooks:
                user32.UnhookWinEvent(handle)
            self._unregister_hotkeys()
            self._remove_key_hook()
            self._release_cursor_clip()
            if self.icon_watch_stop:
                kernel32.SetEvent(self.icon_watch_stop)
            user32.KillTimer(self.hwnd, TIMER_SWEEP)
            self._remove_tray_icon()
            win32gui.PostQuitMessage(0)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def ddc_power_state(mon):
    """Ask the monitor its power state over DDC/CI.

    Anything sitting between the graphics card and the panel tends to break
    this - AV receivers, DisplayPort-to-HDMI converters, HDMI switches, KVMs -
    because they do not pass the I2C channel through. That is why a screen
    switched off behind one cannot be detected. Worth reporting if yours does
    answer: it would change what is possible.
    """
    try:
        dxva2 = ctypes.windll.dxva2
    except Exception:
        return "unavailable"

    class PHYSICAL_MONITOR(ctypes.Structure):
        _fields_ = [("hPhysicalMonitor", wt.HANDLE),
                    ("szPhysicalMonitorDescription", wt.WCHAR * 128)]

    names = {1: "on", 2: "standby", 3: "suspend", 4: "off (soft)", 5: "off (hard)"}
    try:
        handle = wt.HANDLE(int(mon.handle))
        count = wt.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(handle,
                                                             ctypes.byref(count)):
            return "no physical monitor"
        arr = (PHYSICAL_MONITOR * count.value)()
        if not dxva2.GetPhysicalMonitorsFromHMONITOR(handle, count.value, arr):
            return "cannot open"
        try:
            cur, mx = wt.DWORD(), wt.DWORD()
            ok = dxva2.GetVCPFeatureAndVCPFeatureReply(
                arr[0].hPhysicalMonitor, 0xD6, None,
                ctypes.byref(cur), ctypes.byref(mx))
            if not ok:
                return ("not supported (most TVs never implement it; receivers "
                    "and converters can also swallow it)")
            return names.get(cur.value, str(cur.value))
        finally:
            dxva2.DestroyPhysicalMonitors(count.value, arr)
    except Exception as exc:
        return "error: %s" % exc


def print_diagnostics(cfg):
    """Everything worth pasting into a bug report."""
    print("Phantom Monitor diagnostics")
    print("=" * 72)
    print("windows      : %s" % (sys.getwindowsversion(),))
    print("python       : %s" % sys.version.split()[0])
    print("dpi awareness: %s" % set_dpi_awareness())
    print("frozen build : %s" % bool(getattr(sys, "frozen", False)))
    print("block rules  : %s" % (cfg.get("blocked_hwids") or "(none)"))
    print()

    monitors = enum_monitors()
    for mon in monitors:
        blocked = any(monitor_matches_block(mon, r)
                      for r in cfg.get("blocked_hwids", []))
        print("%s   (hotkey slot %d)" % (mon.name, mon.number))
        print("   hardware id : %s" % (mon.hwid or "?"))
        hint = looks_like_av_device(mon.hwid, mon.name)
        if hint:
            print("   looks like  : %s, not a display - a likely thing to block"
                  % hint)
        print("   gdi device  : %s%s" % (mon.device, "  (primary)" if mon.primary else ""))
        print("   bounds      : %s" % (mon.rect,))
        print("   work area   : %s" % (mon.work,))
        print("   blocked now : %s" % blocked)
        print("   ddc/ci power: %s" % ddc_power_state(mon))
        modes, index = set(), 0
        while True:
            try:
                setting = win32api.EnumDisplaySettings(mon.device, index)
            except Exception:
                break
            modes.add((setting.PelsWidth, setting.PelsHeight))
            index += 1
        listed = sorted(modes)
        print("   modes       : %d offered%s" % (
            len(listed),
            ("  smallest %dx%d, largest %dx%d" % (listed[0] + listed[-1]))
            if listed else ""))
        print()

    blocked = [m for m in monitors
               if any(monitor_matches_block(m, r) for r in cfg.get("blocked_hwids", []))]
    box = cursor_clip_rect(monitors, blocked)
    # (box,) not box: a bare tuple is read as the format arguments.
    print("pointer fence: %s" % (box if box else
                                 "not possible with this layout / nothing blocked",))
    print()
    print("If a receiver reports a power state above rather than 'not supported',")
    print("please open an issue - it would mean a screen switched off behind it")
    print("can be detected, which is not currently thought possible.")


def single_instance():
    handle = kernel32.CreateMutexW(None, False, "Global\\PhantomMonitor_SingleInstance")
    return handle and kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    args = set(a.lower() for a in sys.argv[1:])
    cfg = load_config()
    setup_logging(cfg.get("log_level", "INFO"))
    awareness = set_dpi_awareness()

    if "--diag" in args:
        print_diagnostics(cfg)
        return 0

    if "--settings" in args:
        import settings_window
        monitors = [(m.name, m.hwid, m.rect[2] - m.rect[0], m.rect[3] - m.rect[1],
                     m.rect[0], m.rect[1], m.primary) for m in enum_monitors()]
        settings_window.run(CONFIG_PATH, monitors)
        return 0

    if "--list" in args:
        for mon in enum_monitors():
            rules = cfg.get("blocked_hwids", [])
            blocked = (" [BLOCKED]"
                       if any(monitor_matches_block(mon, r) for r in rules) else "")
            hint = looks_like_av_device(mon.hwid, mon.name)
            note = ("   <-- looks like a %s, not a monitor" % hint) if hint else ""
            print("  hotkey %d   %s%s%s" % (mon.number, mon.label(), blocked, note))
        return 0

    guard = Guard(cfg)
    if "--rescue" in args:
        print("rescued %d window(s)" % guard.sweep("cli", include_offscreen=True))
        return 0

    if not single_instance():
        log.warning("another PhantomMonitor is already running; exiting")
        return 1

    log.info("%s starting (dpi awareness: %s)", APP_NAME, awareness)
    if not cfg.get("blocked_hwids"):
        log.warning("no displays blocked yet - pick one from the tray menu")

    if "--no-tray" in args:
        guard.sweep("cli")
        return 0

    TrayApp(cfg)
    win32gui.PumpMessages()
    return 0


if __name__ == "__main__":
    sys.exit(main())
