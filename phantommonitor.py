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
from io import StringIO
from logging.handlers import RotatingFileHandler

import win32api
import win32clipboard
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


def _writable(folder):
    """Can we actually create files here? Portable copies land anywhere."""
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write_probe")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


# The portable build keeps its settings beside the executable, which is the
# point of it - copy the folder, keep your setup. But people drop a portable
# exe anywhere, and Program Files is read-only without elevation. Falling back
# to the profile keeps it running instead of dying at startup with no window,
# no tray icon and no log to say why.
DATA_DIR = APP_DIR if _writable(APP_DIR) else os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PhantomMonitor")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "phantommonitor.log")
ICON_LAYOUT_PATH = os.path.join(DATA_DIR, "icon_layouts.json")
ARRANGEMENT_PATH = os.path.join(DATA_DIR, "arrangement.json")
ARRANGEMENT_UNDO_PATH = os.path.join(DATA_DIR, "arrangement_undo.json")
PROJECT_URL = "https://github.com/leaderdog-code/phantommonitor"
RELEASES_URL = PROJECT_URL + "/releases"
LATEST_API = "https://api.github.com/repos/leaderdog-code/phantommonitor/releases/latest"
APP_VERSION = "1.1.2"   # keep in step with AppVersion in build/installer.iss
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
    # Hardware ids you have marked yourself as an amp, switch or extractor
    # rather than a display. Only affects what is shown - nothing is blocked on
    # this basis. Worth reporting so the built-in list catches up.
    "av_devices": [],
    "not_av_devices": [],
    "cursor_lock_apps": [],
    "cursor_lock_fullscreen": True,
    "cursor_never_lock": ["mstsc.exe", "explorer.exe",
                          "screenclippinghost.exe", "snippingtool.exe"],
    "settings_zoom": 1.0,
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
WM_APP_ARRANGE = win32con.WM_APP + 4   # asked for from outside, e.g. a Stream Deck
WM_APP_ARRANGE_NAMED = win32con.WM_APP + 5   # the settings window, via a file
WM_APP_UNDO_ARRANGE = win32con.WM_APP + 6   # undo, from the settings window

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
user32.GetClipCursor.restype = wt.BOOL
user32.GetClipCursor.argtypes = [ctypes.c_void_p]
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

def setup_logging(level, path=None):
    """Set up logging. PATH lets a child process use a file of its own.

    The settings window runs as a second process of this same script, so
    without that it would open the same rotating log. Two processes holding one
    rotating handler collide the moment it rolls over: the rename fails with a
    sharing violation and BOTH of them spew tracebacks on every write
    thereafter. It has to be a separate file.
    """
    log.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    # Never let logging be the thing that stops the program starting. Without
    # a console there is nothing to print a traceback to, so a failure here
    # would look exactly like the app silently not running.
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(path or LOG_PATH, maxBytes=512000,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(fmt)
        log.addHandler(handler)
    except OSError:
        pass
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


def fenceable(blocked, app_displays):
    """Of the blocked displays, the ones to also wall the pointer out of.

    A display with an app pinned to it is reserved, not banished - the
    point of a chat monitor is to read and answer on it. Blocking keeps
    other windows off; walling the pointer out as well would leave a
    screen nobody can click, which is not reserving it, it is losing it.

    A phantom has nothing pinned to it and stays fenced, which is the
    case the fence exists for.
    """
    pinned = set((app_displays or {}).values())
    return [m for m in blocked if m.hwid not in pinned]


def spans_displays(rect, monitors, fraction=0.10):
    """How many displays this window meaningfully covers."""
    n = 0
    for mon in monitors:
        area = (mon.rect[2] - mon.rect[0]) * (mon.rect[3] - mon.rect[1])
        if area > 0 and overlap_area(rect, mon) >= fraction * area:
            n += 1
    return n


def clip_is_owned(cur, fg_rect, fg_app, never_lock=()):
    """True if the window in front could plausibly have set this clip.

    A cursor clip outlives whatever set it. A game exits with the pointer
    still confined to where its window was, and deferring to that leaves
    the user sealed inside a rectangle containing nothing - unable even to
    reach the tray icon that would undo it.

    So a clip is only somebody else's if it lies inside the foreground
    window. The shell is excluded: the desktop is screen-sized, so it
    would vouch for almost any stale rectangle.
    """
    if not cur or not fg_rect:
        return False
    if (fg_app or "").strip().lower() in set(
            a.strip().lower() for a in (never_lock or ()) if a):
        return False
    return rect_within(cur, fg_rect)


def rect_within(inner, outer):
    """True if INNER sits entirely inside OUTER."""
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


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


def looks_like_av_device(hwid, name, declared=(), denied=()):
    """A guess at whether a display is really an amp, switch or extractor.

    Only ever used to suggest - never to block anything on its own. Being wrong
    here should cost a line of text, nothing more.

    `declared` holds hardware ids the user has marked themselves, for kit the
    built-in list has not caught up with.
    """
    # An overrule comes first. A three-letter vendor prefix is a guess, and a
    # wrong one is entirely possible - SON catches a Sony amp and would catch a
    # Sony display just the same. Whoever is looking at the hardware decides.
    if hwid and hwid in (denied or ()):
        return None
    if hwid and hwid in (declared or ()):
        return "marked by you as an amp or adapter"
    vendor = AV_VENDORS.get((hwid or "")[:3].upper())
    if vendor:
        return vendor
    lowered = (name or "").lower()
    if any(hint in lowered for hint in AV_NAME_HINTS):
        return "AV device"
    return None


_preferred_cache = {}


def edid_preferred(hwid):
    """(width, height, interlaced) from the display's preferred timing, or None.

    This is what the display *asks for*, which is not the same as what Windows
    is set to - Windows restores whatever mode was last chosen for a given
    arrangement, and mistaking one for the other sent this project down a long
    wrong path.

    Windows shows the same value as "(Recommended)" in the resolution list.

    Cached, because the sweep runs every couple of seconds and this reads the
    registry. Cleared whenever the display topology changes.
    """
    if hwid in _preferred_cache:
        return _preferred_cache[hwid]
    result = None
    try:
        base = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + hwid)
        index = 0
        while result is None:
            try:
                inst = winreg.EnumKey(key, index)
            except OSError:
                break
            try:
                params = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    "%s\%s\%s\Device Parameters" % (base, hwid, inst))
                edid = bytes(winreg.QueryValueEx(params, "EDID")[0])
                d = edid[54:72]                      # first detailed timing
                width = d[2] | ((d[4] & 0xF0) << 4)
                lines = d[5] | ((d[7] & 0xF0) << 4)
                interlaced = bool(d[17] & 0x80)
                # An interlaced descriptor counts lines per field, so the frame
                # is twice that.
                result = (width, lines * (2 if interlaced else 1), interlaced)
            except (OSError, IndexError):
                index += 1
    except OSError:
        result = None
    _preferred_cache[hwid] = result
    return result


def suggest_rule(hwid, name, is_av, preferred, width, height):
    """The rule most likely to work for this display, and why.

    A ladder, best first. Only the last rung always works, and the ones above
    it depend on what the hardware is willing to tell us - which varies by
    model, not by brand, so this suggests rather than decides.
    """
    if not is_av:
        return None, None
    if preferred and preferred[2]:
        return ("%s@interlaced" % hwid,
                "it is asking for an interlaced mode, which some amps use to "
                "mean nothing is awake behind them")
    if width * height < 1280 * 720:
        return ("%s@<1280x720" % hwid,
                "it is sitting at a small resolution, so a real screen behind "
                "it would stand the rule down")
    if preferred and not preferred[2]:
        # Progressive right now. That may be because a screen is awake behind
        # it, in which case the interesting state has not been sampled yet -
        # checking at the wrong moment is exactly how someone ends up with a
        # manual rule when an automatic one was available.
        return (hwid,
                "nothing distinguishes it from a real display RIGHT NOW. If a "
                "screen is awake behind it, switch that screen off, wait a few "
                "minutes and look again - if it then asks for an interlaced "
                "mode, use %s@interlaced instead. Otherwise block it outright "
                "and untick it when you want the screen" % hwid)
    return (hwid,
            "nothing here distinguishes it from a real display, so block it "
            "outright and untick it when you want to use a screen behind it")


def offset_in(rect, mon):
    """Where a window sits WITHIN a display: (dx, dy, width, height)."""
    return (rect[0] - mon.rect[0], rect[1] - mon.rect[1],
            rect[2] - rect[0], rect[3] - rect[1])


def offset_onto(rel, mon):
    """Put an offset back onto a display, clamped so it stays reachable.

    Relative rather than absolute because the same monitor moves around: drag
    it to the other side in Display Settings, plug it into a different port, or
    change which screen is primary, and every absolute coordinate on the
    desktop shifts. None of that should lose where you had a window on it.

    Clamped because a resolution change can leave the old offset hanging off
    the edge - the position is worth keeping, but not at the cost of a window
    you cannot reach.
    """
    dx, dy, width, height = rel
    left, top, right, bottom = mon.work
    width = max(80, min(width, right - left))
    height = max(60, min(height, bottom - top))
    x = min(max(left + dx, left), right - width)
    y = min(max(top + dy, top), bottom - height)
    return x, y, width, height


def fill_slots(slots, windows):
    """Pair saved slots with the windows that exist now, by app.

    Slots are per-application and interchangeable: a layout says "two Brave
    windows go in these two rectangles", not "this particular window goes
    here". Handles do not survive a reboot and titles change as you browse, so
    identifying a specific window is both hard and pointless - the arrangement
    is what is being restored, and whatever is in each window is the user's
    business.

    Extra windows are left alone. Empty slots stay empty.
    """
    by_app = {}
    for hwnd, app in windows:
        by_app.setdefault(app, []).append(hwnd)
    pairs, used = [], {}
    for slot in slots:
        app = (slot.get("app") or "").lower()
        n = used.get(app, 0)
        available = by_app.get(app) or []
        if n < len(available):
            pairs.append((available[n], slot))
            used[app] = n + 1
    return pairs


def exe_path(hwnd):
    """Full path of the program owning a window, or "" if it cannot be read."""
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                      False, pid)
        if not handle:
            return ""
        try:
            size = wt.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                   ctypes.byref(size)):
                return buf.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def missing_launches(slots, windows):
    """How many more windows each app needs, as {exe path: count}.

    An app minimized to the notification area cannot be shown from outside -
    forcing its window visible gives an empty frame, because it has suspended
    drawing. Running the program again works, because single-instance apps
    handle that themselves and show their own window properly. The same call
    starts an app that was not running at all.
    """
    have = {}
    for _hwnd, app in windows:
        have[app] = have.get(app, 0) + 1
    want, paths = {}, {}
    for slot in slots:
        app = (slot.get("app") or "").lower()
        if not app:
            continue
        want[app] = want.get(app, 0) + 1
        if slot.get("exe"):
            paths.setdefault(app, slot["exe"])
    out = {}
    for app, count in want.items():
        short = count - have.get(app, 0)
        if short > 0 and paths.get(app):
            out[paths[app]] = min(short, 3)   # never a runaway
    return out


def parse_block_spec(spec):
    """Parse a block rule into (hwid, size, smaller_than, interlaced_only).

        'DON0015'             always block
        'DON0015@800x600'     block only at exactly that size
        'DON0015@<1280x720'   block only while smaller than that
        'DON0015@interlaced'  block only while it asks for an interlaced mode
    """
    hwid, _, mode = str(spec).partition("@")
    size, smaller, interlaced = None, False, False
    mode = mode.strip().lower()
    if mode == "interlaced":
        return hwid.strip(), None, False, True
    if mode:
        if mode.startswith("<"):
            smaller, mode = True, mode[1:]
        try:
            width, height = mode.split("x")
            size = (int(width), int(height))
        except ValueError:
            log.warning("block rule %r has a bad size; matching on id alone", spec)
            smaller = False
    return hwid.strip(), size, smaller, interlaced


def monitor_matches_block(mon, spec):
    """Does this monitor match a block rule?

    The size qualifier is for when the phantom sits at a small resolution and a
    real screen behind it would be larger. The rule then stops matching once a
    screen appears, and the guard steps aside without being asked.

    Do not assume an amp announces itself by going small. Both receivers tested
    here advertise modes up to 1920x1080 with nothing attached; the small
    resolution on the test machine was one the user had chosen. Where the
    phantom does sit small, this works well - but it is usually the setup doing
    that, not the hardware.

    Prefer the '<' form over an exact size: it survives the mode being changed
    by hand, and small phantoms turn up at 640x480, 800x600 and 1024x768 alike.
    """
    hwid, size, smaller, interlaced_only = parse_block_spec(spec)
    if mon.hwid != hwid:
        return False
    if interlaced_only:
        # Some receivers advertise an interlaced preferred timing with nothing
        # behind them and a progressive one once a screen wakes up, while
        # Windows reports the same resolution for both - so the size forms are
        # blind to it and only the EDID shows the difference.
        pref = edid_preferred(hwid)
        return bool(pref and pref[2])
    if size is None:
        return True
    width = mon.rect[2] - mon.rect[0]
    height = mon.rect[3] - mon.rect[1]
    if smaller:
        # Compare AREA, not dimensions. Requiring both to be smaller would miss
        # 1024x768, which is taller than 720; requiring either would falsely
        # catch a monitor turned on its side, where a 1080x1920 portrait panel
        # is narrower than 1280. Area handles both: a small phantom is under
        # 1280x720 worth of pixels and a real display, rotated or not, is over.
        return width * height < size[0] * size[1]
    return (width, height) == size


def known_displays():
    """Every display Windows has cached an EDID for: [(hwid, name), ...].

    Lets rules be set for kit that is not plugged in at the moment - which
    matters for a pass-through amp, since while a screen is awake behind it the
    amp's own id is nowhere to be seen, and that id is exactly what wants
    blocking.
    """
    seen = {}
    try:
        base = "SYSTEM\CurrentControlSet\Enum\DISPLAY"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                hwid = winreg.EnumKey(root, index)
                name = friendly_name(hwid)
                if name and hwid not in seen:
                    seen[hwid] = name
    except OSError as exc:
        log.debug("could not enumerate known displays: %s", exc)
    return sorted(seen.items())


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


def cursor_lock_rect(rect, monitors, blocked, app, lock_apps,
                     never_lock=(), lock_fullscreen=True, borderless=False):
    """The display to confine the pointer to for a full-screen app, or None.

    Default is to hold it. A full-screen app is one the user is inside, and
    a game that loses its cursor at the screen edge is unplayable - alt-tab
    out once to check a map or answer a message and the edge is gone for the
    rest of the session, because nothing puts the confinement back.

    Naming games individually does not scale: a Steam or Epic library is
    hundreds of executables. So this is opt-OUT. cursor_never_lock carries
    the exceptions, and full-screen RDP is the one that matters - being
    locked into a remote session would strand the pointer away from every
    other screen.

    Two full-screen apps can want opposite things, so this cannot be one rule.
    A game wants the pointer held inside it and wants that back the instant you
    alt-tab in, having gone to check a map or answer a message. A full-screen
    RDP session on one monitor wants no such thing - being locked into it would
    strand the pointer away from every other screen.

    So confinement is opt-in per executable. Anything not listed is left alone.
    """
    if not rect or not monitors:
        return None
    host = monitor_of_rect(rect, monitors, require_overlap=True)
    if host is None or host in blocked:
        return None
    # Genuinely full-screen, not merely maximized. A maximized window
    # stops at the taskbar and still clears the 95% covers_monitor bar,
    # so that alone would hold the pointer inside any maximized browser.
    # The desktop itself is worse: Explorer owns a window the size of the
    # screen, so clicking the wallpaper would trap the mouse on that
    # monitor. Real full-screen covers the taskbar too and has no frame.
    name = (app or "").strip().lower()
    if name and name in set(a.strip().lower() for a in lock_apps or () if a):
        # Named explicitly, so hold it whatever shape it is. This has to come
        # before the tests below or it is not really "explicit": a racing sim
        # spanning three screens looks exactly like a screen-spanning overlay,
        # and naming it is the only way to tell us apart from one.
        return host.rect
    if not borderless or not covers_monitor(rect, host, fraction=0.995):
        return None
    # A window covering several displays at once is not a game, it is an
    # overlay - the Win+Shift+S snip layer is exactly this, borderless and
    # stretched across every screen. Holding the pointer to whichever
    # display it happens to overlap most drags the mouse off whatever the
    # user was actually pointing at.
    if spans_displays(rect, monitors) > 1:
        return None
    if name and name in set(a.strip().lower() for a in never_lock or () if a):
        return None               # named as never, e.g. full-screen RDP
    return host.rect if lock_fullscreen else None




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
        _preferred_cache.clear()   # EDIDs change with the topology
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

        Stored as an offset within the display rather than as a desktop
        coordinate, so moving that monitor in the arrangement, plugging it into
        another port or changing the primary does not lose the position.
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
        positions[process_name(hwnd)] = {"rel": list(offset_in(rect, mon))}
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
        if isinstance(saved, dict) and len(saved.get("rel") or ()) == 4:
            x, y, width, height = offset_onto(saved["rel"], mon)
        elif isinstance(saved, list) and len(saved) == 4:
            # Written before positions were stored relative. Convert it if it
            # still points at the right display, otherwise let it go.
            if monitor_of_rect(tuple(saved), self.monitors,
                               require_overlap=True) is mon:
                x, y, width, height = offset_onto(
                    offset_in(tuple(saved), mon), mon)
            else:
                x, y, width, height = centred_rect(mon, rect)
        else:
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
        self.cursor_locked_app = ""      # exe currently holding the pointer
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
        # Startup and an explicit save are worth stating: they are the two
        # moments someone checks whether this is actually tracking anything.
        # The rest happen constantly as windows move and would drown the log.
        log.log(logging.INFO if reason in ("startup", "manual") else logging.DEBUG,
                "tracking %d window position(s) [%s]", len(snapshot), reason)
        return len(snapshot)

    def _ask_for_fullscreen(self, hwnd):
        """Ask an app to go back into full-screen using its own shortcut.

        Only for windows that filled a display before a change and lost it -
        Remote Desktop drops out by itself when its monitor sleeps. Nothing
        outside an app can put it back into full-screen, but the app will do
        it if asked the way the user would.

        On a thread: this waits, and the message loop must keep running.
        """
        time.sleep(1.5)     # let the display settle and the app catch up
        try:
            if not win32gui.IsWindow(hwnd) or not is_user_movable(hwnd):
                return      # gone, or it sorted itself out already
            toggle = FULLSCREEN_TOGGLES.get(win32gui.GetClassName(hwnd))
            if not toggle:
                return
            label, mods, vk = toggle
            send_key_combo(hwnd, mods, vk)
            log.info("asked %r to go back to full-screen (%s)",
                     shorten(title_of(hwnd), 40), label)
        except Exception as exc:
            log.debug("could not ask for full-screen: %s", exc)

    def _restore_windows(self, reason="display change"):
        """Put back only the windows the display change actually displaced.

        The snapshot was frozen the moment the change began, so any window whose
        placement now differs was moved by the change and not by the user. A
        window still sitting where the snapshot says is left strictly alone.
        """
        if not self.cfg.get("restore_windows", True) or not self.window_snapshot:
            return 0
        restored = skipped = 0
        names, refullscreen = [], []
        for hwnd, placement in self.window_snapshot.items():
            try:
                if not window_displaced(hwnd, placement, self.cfg):
                    continue  # untouched by the change - do not interfere
                # Leave full-screen windows alone. SetWindowPlacement on one
                # drops it out of full screen, so "restoring" a full-screen RDP
                # session or game after a display change breaks the very thing
                # the user was looking at. They place themselves anyway.
                try:
                    # Leave app-managed windows alone entirely - no caption and
                    # no resize frame means the app is driving its own
                    # geometry: a full-screen game, an RDP session, a splash.
                    #
                    # Testing "is it full-screen right now" was not enough. A
                    # display going to sleep makes Windows shrink the window
                    # off it first, so by the time this runs it no longer
                    # covers anything, the test passed, and SetWindowPlacement
                    # then dropped a full-screen RDP session into a window it
                    # could not get out of by itself.
                    if not is_user_movable(hwnd):
                        log.debug("left %s alone: the app manages its own frame",
                                  shorten(title_of(hwnd), 30))
                        continue
                    # It may have been full-screen before and dropped out of
                    # it by itself when its display slept. Put it back on the
                    # right screen either way, and note that it wants asking
                    # to go full-screen again afterwards - moving it there is
                    # already better than Windows leaving it on the primary,
                    # and the app can restore the rest.
                    ox0, oy0 = self.guard.workspace_offset()
                    was = placement[4]
                    was_rect = (was[0] + ox0, was[1] + oy0,
                                was[2] + ox0, was[3] + oy0)
                    was_host = monitor_of_rect(was_rect, self.guard.monitors,
                                               require_overlap=True)
                    if (was_host is not None
                            and covers_monitor(was_rect, was_host, fraction=0.98)
                            and win32gui.GetClassName(hwnd) in FULLSCREEN_TOGGLES):
                        refullscreen.append(hwnd)
                except Exception:
                    pass
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
                # Name them. A bare count cannot answer "why did my full-screen
                # session get put back in a window", which is the question a
                # restore most often raises.
                try:
                    names.append("%s (%s)" % (shorten(title_of(hwnd), 30),
                                              process_name(hwnd)))
                except Exception:
                    pass
            except Exception:
                continue
        for hwnd in refullscreen:
            threading.Thread(target=self._ask_for_fullscreen,
                             args=(hwnd,), daemon=True).start()
        if restored or skipped:
            log.info("put back %d displaced window(s) [%s]%s%s", restored, reason,
                     "; %d skipped, their old spot is gone" % skipped if skipped else "",
                     ": " + ", ".join(names) if names else "")
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
        # Reserved screens - ones with an app pinned to them - stay
        # reachable. Only unclaimed blocked displays get walled off.
        fence_out = fenceable(self.guard.blocked(),
                              self.cfg.get("app_displays"))
        if not fence_out:
            self._release_cursor_clip()
            return
        box = cursor_clip_rect(self.guard.monitors, fence_out)
        if box is None:
            self._release_cursor_clip()
            if not self.clip_warned:
                log.warning("cannot fence the pointer out of the blocked display: "
                            "this layout needs more than one rectangle")
                self.clip_warned = True
            return
        # Stand aside for a full-screen app on a display we allow. It is
        # managing the pointer itself, and since the last ClipCursor call wins
        # and ours lands just after its focus-gain, imposing the fence here is
        # what knocks the cursor out of a game edge on alt-tab back in.
        try:
            fg = win32gui.GetForegroundWindow() or None
            fg_rect = win32gui.GetWindowRect(fg) if fg else None
            fg_app = process_name(fg) if fg else ""
        except Exception:
            fg, fg_rect, fg_app = None, None, ""
        # Listed apps get the pointer actively held inside them, re-applied on
        # every sweep - which is what makes it come back after an alt-tab out.
        lock = cursor_lock_rect(
            fg_rect, self.guard.monitors, self.guard.blocked(), fg_app,
            self.cfg.get("cursor_lock_apps"),
            self.cfg.get("cursor_never_lock"),
            self.cfg.get("cursor_lock_fullscreen", True),
            borderless=(fg is not None and not is_user_movable(fg)))
        if lock is not None:
            held = wt.RECT(lock[0], lock[1], lock[2], lock[3])
            if user32.ClipCursor(ctypes.byref(held)):
                if self.cursor_locked_app != fg_app:
                    log.info("holding the pointer inside %s on %s",
                             fg_app, lock)
                self.cursor_locked_app = fg_app
            self.cursor_clipped = False
            return
        if self.cursor_locked_app:
            log.info("released the pointer from %s", self.cursor_locked_app)
            self.cursor_locked_app = ""

        # There is only ONE cursor clip on the system, and this runs on every
        # sweep because Windows drops the clip whenever the foreground window
        # changes. So check who owns it first.
        #
        # A game confining the mouse to its window sets a clip of its own. If
        # that clip already sits inside the region we allow, it satisfies the
        # fence by itself - the pointer cannot reach a blocked display from
        # inside it. Overwriting it every two seconds would widen it back out
        # and let their cursor escape mid-game, which is a far worse bug than
        # the one being prevented.
        current = wt.RECT()
        if user32.GetClipCursor(ctypes.byref(current)):
            cur = (current.left, current.top, current.right, current.bottom)
            if cur == box:
                self.cursor_clipped = True   # still ours, nothing to do
                return
            if rect_within(cur, box):
                # Someone else owns it - but only believe that if the
                # window in front could plausibly have set it. A clip
                # outlives the process that made it: a game exits with
                # the pointer still confined to where its window was, and
                # deferring to that traps the user inside a rectangle with
                # nothing in it, with no way to reach the tray icon that
                # would fix it. Require the clip to lie inside the
                # foreground window, and never defer to the shell.
                if clip_is_owned(cur, fg_rect, fg_app,
                                 self.cfg.get("cursor_never_lock")):
                    if self.cursor_clipped:
                        log.info("%s is confining the pointer to %s; "
                                 "leaving its clip alone", fg_app, cur)
                    self.cursor_clipped = False
                    return
                log.info("stale clip %s belongs to no window in front; "
                         "taking the pointer back", cur)
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
            # Rescue means "get me out of whatever this is". A stale clip
            # can seal the pointer into a rectangle with nothing in it,
            # and the tray icon that would fix it may be outside. Free the
            # pointer first, then re-fence properly.
            user32.ClipCursor(None)
            self.cursor_clipped = False
            self.cursor_locked_app = ""
            self.guard.sweep("hotkey", include_offscreen=True)
            self._apply_cursor_clip()
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

        def add(text, handler, checked=False, enabled=True, into=None):
            flags = win32con.MF_STRING
            if checked:
                flags |= win32con.MF_CHECKED
            if not enabled:
                flags |= win32con.MF_GRAYED
            item_id = next_id[0]
            next_id[0] += 1
            win32gui.AppendMenu(into if into else menu, flags, item_id, text)
            self.actions[item_id] = handler

        def add_sub(text, submenu, into=None):
            win32gui.AppendMenu(into if into else menu,
                                win32con.MF_STRING | win32con.MF_POPUP,
                                submenu, text)

        def sep(into=None):
            win32gui.AppendMenu(into if into else menu,
                                win32con.MF_SEPARATOR, 0, "")

        enabled = self.cfg.get("enabled", True)
        # Ask the guard what is actually blocked right now: a rule can be
        # qualified by resolution, so a rule existing is not the same as it
        # currently applying.
        blocked_now = set(m.device for m in self.guard.blocked())

        # One tick per display. Unblocking a screen to use it was previously
        # a trip into Settings, which is a lot of clicks for something you do
        # every time you want to watch a film on the screen behind the amp.
        # _make_block_toggle parks a qualified rule rather than discarding it,
        # so unticking and reticking does not silently downgrade
        # DON0015@interlaced to a bare id.
        for mon in self.guard.monitors:
            add("Block %s" % mon.name, self._make_block_toggle(mon.hwid),
                checked=mon.device in blocked_now)
        sep()

        # The two master switches stay at the top level: they are the answer to
        # "make it stop", and burying that in a submenu would be unkind.
        add("Keep windows off blocked displays", self._toggle_enabled,
            checked=enabled)
        add("Keep pointer off blocked displays", self._toggle_cursor_block,
            checked=self.cfg.get("block_cursor", False) and enabled,
            enabled=enabled)
        sep()

        add("Rescue windows now",
            lambda: self.guard.sweep("manual", include_offscreen=True))

        # Top level rather than buried in Settings: saving and applying a
        # layout is a daily action, and it was two hops away.
        modes = self._load_modes()
        arrange = win32gui.CreatePopupMenu()
        save_menu = win32gui.CreatePopupMenu()
        add("Every display", lambda: self._save_arrangement(), into=save_menu)
        sep(into=save_menu)
        for mon in self.guard.monitors:
            add(mon.label(),
                (lambda h: lambda: self._save_arrangement(h))(mon.hwid),
                into=save_menu)
        add_sub("Save this arrangement as...", save_menu, into=arrange)
        if modes:
            apply_menu = win32gui.CreatePopupMenu()
            setup_menu = win32gui.CreatePopupMenu()
            for name in sorted(modes):
                add(name,
                    (lambda n: lambda: self._apply_arrangement(mode=n))(name),
                    into=apply_menu)
                add(name,
                    (lambda n: lambda: self._apply_arrangement(
                        mode=n, launch=True))(name),
                    into=setup_menu)
            add_sub("Arrange windows like...", apply_menu, into=arrange)
            # Separate, because this starts programs and that should never
            # happen as a side effect of tidying a screen.
            add_sub("Set up like..., opening what is missing", setup_menu,
                    into=arrange)
        sep(into=arrange)
        add("Undo that arrangement", self._undo_arrangement,
            enabled=bool(self._load_arrangement(ARRANGEMENT_UNDO_PATH)),
            into=arrange)
        add_sub("Arrangements", arrange)

        # Pinning is "this app, that screen", and both are in front of the user
        # at the moment they open this menu.
        if self.menu_target and is_manageable(self.menu_target, self.cfg):
            pinned_app = process_name(self.menu_target)
            pins = self.cfg.get("app_displays") or {}
            pin_menu = win32gui.CreatePopupMenu()
            for mon in self.guard.monitors:
                add(mon.label(), self._make_pin_action(mon.hwid),
                    checked=pins.get(pinned_app) == mon.hwid, into=pin_menu)
            if pinned_app in pins:
                sep(into=pin_menu)
                add("Do not pin it anywhere", self._make_pin_action(None),
                    into=pin_menu)
            add_sub("Always open %s on" % (pinned_app or "this app"), pin_menu)

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

        editor = self.cfg.get("editor", "")

        # Everything occasional lives behind one slide, so the top level stays
        # short enough to read at a glance. Start with Windows sits last,
        # behind a separator: it changes what happens at every logon and is not
        # something to catch with a stray click.
        more = win32gui.CreatePopupMenu()
        add("Open settings window...", self._open_settings, into=more)
        add("Diagnose my displays...", self._show_diagnostics, into=more)
        sep(into=more)
        add("Save window + icon layout now", self._snapshot_all, into=more)
        add("Restore window + icon layout", self._restore_all, into=more)
        sep(into=more)
        add("Auto-restore window positions", self._toggle_restore_windows,
            checked=self.cfg.get("restore_windows", True), into=more)
        add("Auto-restore desktop icons", self._toggle_restore_icons,
            checked=self.cfg.get("restore_icons", True), into=more)
        sep(into=more)
        add("Edit config file", lambda: open_text_file(CONFIG_PATH, editor),
            into=more)
        add("Reload settings", self._reload_config, into=more)
        add("Open config folder", lambda: os.startfile(APP_DIR), into=more)
        add("View log", lambda: open_text_file(LOG_PATH, editor), into=more)
        sep(into=more)
        add("Check for updates", self._check_updates, into=more)
        add("Project page", lambda: open_url(PROJECT_URL), into=more)
        support = (self.cfg.get("support_url") or "").strip()
        if support:
            add("Support this project", lambda: open_url(support), into=more)
        sep(into=more)
        add("Start with Windows", self._toggle_autostart,
            checked=os.path.exists(STARTUP_VBS), into=more)
        add_sub("Settings and more", more)

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

    def _make_pin_action(self, hwid):
        """Pin the app in front to a chosen display, or unpin it with None.

        Deliberately a choice of destination rather than "pin it where it is".
        The screen you want to dedicate is usually one you have already blocked,
        and blocked screens are exactly the ones you cannot drag a window onto -
        so "pin it where it is" would mean unblock, drag, pin, re-block. Pick
        the display and the window is sent there.
        """
        def action():
            hwnd = self.menu_target
            if not hwnd or not is_manageable(hwnd, self.cfg):
                return
            app = process_name(hwnd)
            if not app:
                return
            pins = dict(self.cfg.get("app_displays") or {})
            if hwid is None:
                pins.pop(app, None)
                log.info("%s is no longer pinned to a display", app)
            else:
                pins[app] = hwid
                mon = self.guard.by_hwid(hwid)
                log.info("%s pinned to %s", app, mon.label() if mon else hwid)
            self.cfg["app_displays"] = pins
            save_config(self.cfg)
            # A pinned display stops being fenced, so redo the fence before
            # moving anything - otherwise the window lands somewhere the
            # pointer still cannot follow.
            self._apply_cursor_clip()
            if hwid is not None:
                self.guard.place_assigned(hwnd)
        return action

    # -- saved arrangements
    #
    # Different from the window snapshot, which is in memory and answers "what
    # did that display change just move?". This is on disk and answers "put my
    # screen back the way I like it", which is a thing you want after a reboot,
    # when the snapshot is long gone.
    def _load_modes(self):
        """{name: [slots]}. Understands the older single unnamed arrangement."""
        try:
            with open(ARRANGEMENT_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle) or {}
        except (OSError, ValueError):
            return {}
        if isinstance(data.get("modes"), dict):
            return dict((k, v.get("slots") or []) for k, v in data["modes"].items())
        if data.get("slots"):
            return {"Saved layout": data["slots"]}   # written before modes
        return {}

    def _save_modes(self, modes):
        return self._write_json(
            ARRANGEMENT_PATH,
            {"modes": dict((k, {"slots": v}) for k, v in modes.items())})

    def _load_arrangement(self, path=None):
        """Slots from a standalone file, used for the undo."""
        try:
            with open(path or ARRANGEMENT_PATH, "r", encoding="utf-8") as handle:
                return (json.load(handle) or {}).get("slots") or []
        except (OSError, ValueError):
            return []

    def _write_arrangement(self, slots, path):
        return self._write_json(path, {"slots": slots})

    def _write_json(self, path, payload):
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            return True
        except OSError as exc:
            log.error("could not write %s: %s", os.path.basename(path), exc)
            return False

    def _save_arrangement(self, only_hwid=None, mode=None):
        """Save the current layout into a named mode.

        With no name, one is asked for in a child process - a modal dialog here
        would stall the message pump that runs everything else. The slots are
        gathered first, so what gets saved is the screen as it looked when the
        menu was clicked, not after the user has been typing for a minute.
        """
        if mode is None:
            slots = self._gather_slots(only_hwid)
            if not slots:
                log.info("nothing to save")
                return
            threading.Thread(target=self._name_then_save,
                             args=(slots, only_hwid), daemon=True).start()
            return
        slots = self._gather_slots(only_hwid)
        if not slots:
            log.info("nothing to save")
            return
        self._store_mode(mode, slots, only_hwid)

    def _name_then_save(self, slots, only_hwid):
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--ask-name", "Name this arrangement"]
            else:
                cmd = [sys.executable, os.path.join(APP_DIR, "phantommonitor.py"),
                       "--ask-name", "Name this arrangement"]
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except Exception as exc:
            log.warning("could not ask for a name: %s", exc)
            return
        name = (done.stdout or "").strip()
        if done.returncode != 0 or not name:
            log.info("naming cancelled; nothing saved")
            return
        self._store_mode(name, slots, only_hwid)

    def _store_mode(self, name, slots, only_hwid):
        modes = self._load_modes()
        if only_hwid and name in modes:
            # Saving one screen into an existing mode replaces only that
            # screen's slots, so building a mode up display by display works.
            slots = [x for x in modes[name]
                     if x.get("hwid") != only_hwid] + slots
        modes[name] = slots
        if self._save_modes(modes):
            log.info("saved %d window(s) as %r", len(slots), name)

    def _gather_slots(self, only_hwid=None):
        slots = []
        hwnds = []
        win32gui.EnumWindows(lambda h, acc: acc.append(h), hwnds)
        for hwnd in hwnds:
            if not is_manageable(hwnd, self.cfg):
                continue
            try:
                rect = win32gui.GetWindowRect(hwnd)
                mon = monitor_of_rect(rect, self.guard.monitors,
                                      require_overlap=True)
                app = process_name(hwnd)
            except Exception:
                continue
            if mon is None or not app:
                continue
            if only_hwid and mon.hwid != only_hwid:
                continue
            slots.append({"app": app, "hwid": mon.hwid,
                          "rel": list(offset_in(rect, mon)),
                          "exe": exe_path(hwnd)})
        return slots

    def _apply_arrangement(self, only_hwid=None, path=None,
                           reason="saved layout", launch=False, mode=None):
        if path:
            slots = self._load_arrangement(path)
        else:
            modes = self._load_modes()
            if mode is None:
                mode = next(iter(modes), None)
            slots = modes.get(mode) or []
            if mode:
                reason = repr(mode) + " layout"
        if not slots:
            log.info("no saved arrangement yet - use Save first")
            return
        if only_hwid:
            slots = [x for x in slots if x.get("hwid") == only_hwid]
        def open_windows():
            found, hwnds = [], []
            win32gui.EnumWindows(lambda h, acc: acc.append(h), hwnds)
            for hwnd in hwnds:
                if is_manageable(hwnd, self.cfg):
                    try:
                        found.append((hwnd, process_name(hwnd)))
                    except Exception:
                        continue
            return found

        windows = open_windows()
        if launch:
            for exe, count in missing_launches(slots, windows).items():
                for _ in range(count):
                    try:
                        subprocess.Popen([exe])
                        log.info("started %s", os.path.basename(exe))
                    except OSError as exc:
                        log.warning("could not start %s: %s", exe, exc)
                        break
                    time.sleep(0.4)
            # Give them a moment to appear, but do not hang about if they do
            # not - a program that fails to start should not block the rest.
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                time.sleep(0.5)
                windows = open_windows()
                if not missing_launches(slots, windows):
                    break
        placed = 0
        # Remember where everything was first. Arranging gathers windows in
        # from other screens, so it can move something the user wanted left
        # alone - and without this there is no way back from that.
        # The state before an arrange is itself just an arrangement, so store
        # it as one: slots per app, on disk. Keeping it as window handles in
        # memory meant a restart or a reboot silently threw the undo away, and
        # a handle means nothing after either.
        undo = []
        for hwnd, slot in fill_slots(slots, windows):
            try:
                rect = win32gui.GetWindowRect(hwnd)
                was = monitor_of_rect(rect, self.guard.monitors,
                                      require_overlap=True)
                if was is None:
                    continue
                undo.append({"app": process_name(hwnd), "hwid": was.hwid,
                             "rel": list(offset_in(rect, was))})
            except Exception:
                continue
        for hwnd, slot in fill_slots(slots, windows):
            mon = self.guard.by_hwid(slot.get("hwid"))
            if mon is None:
                continue            # that display is not here today
            try:
                x, y, width, height = offset_onto(slot["rel"], mon)
                current = win32gui.GetWindowPlacement(hwnd)
                if current[1] == win32con.SW_SHOWMINIMIZED:
                    # Leave it minimized and correct where it will reappear.
                    # Yanking a window open because you tidied the screen would
                    # be rude, and rcNormalPosition is in workspace coordinates
                    # rather than screen ones.
                    ox, oy = self.guard.workspace_offset()
                    win32gui.SetWindowPlacement(
                        hwnd, (current[0], current[1], current[2], current[3],
                               (x - ox, y - oy,
                                x - ox + width, y - oy + height)))
                else:
                    win32gui.SetWindowPos(hwnd, 0, x, y, width, height,
                                          win32con.SWP_NOZORDER
                                          | win32con.SWP_NOACTIVATE)
                placed += 1
            except Exception:
                continue
        if placed:
            self._write_arrangement(undo, ARRANGEMENT_UNDO_PATH)
        log.info("arranged %d window(s) from the %s", placed, reason)

    def _undo_arrangement(self):
        """Put the windows an arrangement moved back where they were."""
        self._apply_arrangement(path=ARRANGEMENT_UNDO_PATH, reason="undo")

    def _show_diagnostics(self):
        """Write the report, put it on the clipboard, and open it.

        The clipboard copy is the point: this exists so somebody can paste it
        into an issue without opening a terminal, which most people reporting a
        display problem should not have to do.
        """
        try:
            text = diagnostics_text(self.cfg)
        except Exception as exc:
            log.exception("could not build diagnostics")
            text = "diagnostics failed: %s" % exc
        path = os.path.join(LOG_DIR, "diagnostics.txt")
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            log.error("could not write diagnostics: %s", exc)
            return
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            log.info("diagnostics copied to the clipboard and written to %s", path)
        except Exception as exc:
            log.warning("could not copy diagnostics to the clipboard: %s", exc)
        open_text_file(path, self.cfg.get("editor", ""))

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
            if lparam == win32con.WM_LBUTTONUP:
                # Left click is the primary action, which for this app is
                # "show me the settings" - the convention almost every tray
                # app follows. Right click is where options live.
                try:
                    self._open_settings()
                except Exception:
                    log.exception("could not open settings from the tray")
            elif lparam == win32con.WM_RBUTTONUP:
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

        if msg == WM_APP_ARRANGE:
            # wparam is an index into the display list, or 0 for every display.
            # An index rather than a hardware id because a window message
            # carries integers, and the caller should not need to know EDIDs.
            mons = self.guard.monitors
            hwid = mons[wparam - 1].hwid if 1 <= wparam <= len(mons) else None
            self._apply_arrangement(hwid, launch=bool(lparam))
            return 0

        if msg == WM_APP_ARRANGE_NAMED:
            # The name arrives in a small file beside the arrangements, because
            # a window message carries integers and marshalling a string is far
            # more trouble than one line of text.
            note = ARRANGEMENT_PATH + ".apply"
            try:
                with open(note, "r", encoding="utf-8") as handle:
                    wanted = handle.read().strip()
                os.remove(note)
            except OSError:
                wanted = ""
            if wanted:
                self._apply_arrangement(mode=wanted)
            return 0

        if msg == WM_APP_UNDO_ARRANGE:
            self._undo_arrangement()
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
                        # The layout ended up where it started. That does
                        # NOT mean nothing was scrambled: a game taking
                        # exclusive full screen flips the display config and
                        # flips it straight back, and Windows sweeps every
                        # window off the other monitors onto the primary in
                        # between. By the time the settle expires the
                        # signature matches again, so judging by signature
                        # missed precisely the case people most need fixed.
                        #
                        # Judge by the windows instead. The restore only
                        # touches windows whose placement differs from the
                        # snapshot frozen when the event began, so a display
                        # event that really did move nothing costs a no-op.
                        log.info("display event with no net layout change; "
                                 "checking for displaced windows")
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


def diagnostics_text(cfg):
    """The --diag report as a string, for the tray menu and the clipboard.

    Captures print_diagnostics rather than duplicating it, so the two can never
    drift apart - the terminal and the tray show the same thing by
    construction.
    """
    import contextlib
    buffer = StringIO()
    with contextlib.redirect_stdout(buffer):
        print_diagnostics(cfg)
    return buffer.getvalue()


def ask_for_name(title, suggestion=""):
    """Show a one-field prompt and print what was typed. Run as a child.

    In its own process because a modal Tk dialog inside the tray app would
    stall its message pump, and everything - evacuation, the fence, hotkeys -
    runs on that pump.
    """
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    answer = {"text": ""}
    ttk.Label(root, text=title).grid(row=0, column=0, columnspan=2,
                                     padx=12, pady=(12, 4), sticky="w")
    var = tk.StringVar(value=suggestion)
    entry = ttk.Entry(root, textvariable=var, width=32)
    entry.grid(row=1, column=0, columnspan=2, padx=12, pady=4)
    entry.focus_set()
    entry.selection_range(0, tk.END)

    def ok(*_a):
        answer["text"] = var.get().strip()
        root.destroy()

    ttk.Button(root, text="Cancel", command=root.destroy).grid(
        row=2, column=0, padx=12, pady=12, sticky="e")
    ttk.Button(root, text="Save", command=ok).grid(
        row=2, column=1, padx=(0, 12), pady=12, sticky="w")
    root.bind("<Return>", ok)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.update_idletasks()
    root.geometry("+%d+%d" % (root.winfo_screenwidth() // 2 - 150,
                              root.winfo_screenheight() // 3))
    root.mainloop()
    if not answer["text"]:
        return 1
    sys.stdout.write(answer["text"])
    return 0


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
    declared, denied = cfg.get("av_devices"), cfg.get("not_av_devices")
    for mon in monitors:
        blocked = any(monitor_matches_block(mon, r)
                      for r in cfg.get("blocked_hwids", []))
        print("%s   (hotkey slot %d)" % (mon.name, mon.number))
        print("   hardware id : %s" % (mon.hwid or "?"))
        hint = looks_like_av_device(mon.hwid, mon.name, declared, denied)
        if hint:
            print("   looks like  : %s, not a display - a likely thing to block"
                  % hint)
            pref = edid_preferred(mon.hwid)
            if pref:
                print("   it asks for : %dx%d %s   (Windows calls this "
                      "\"Recommended\")" % (pref[0], pref[1],
                       "interlaced" if pref[2] else "progressive"))
            rule, why = suggest_rule(
                mon.hwid, mon.name, True, pref,
                mon.rect[2] - mon.rect[0], mon.rect[3] - mon.rect[1])
            if rule:
                print("   try rule    : %s" % rule)
                print("                 %s" % why)
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
    unknown = [m for m in monitors
               if not looks_like_av_device(m.hwid, m.name, declared, denied)]
    if unknown:
        print("If any display above is really an amp, switch or extractor, add its")
        print("hardware id to av_devices in config.json - and please report it, so")
        print("the built-in list catches up.")
        print()
    if any(looks_like_av_device(m.hwid, m.name, declared, denied)
           for m in monitors):
        # Both amps tested here sat on the last good EDID for roughly four
        # minutes after the screen went dark. Anyone comparing --diag output
        # straight after hitting the TV remote sees no change and concludes,
        # wrongly, that nothing can be detected.
        print("Comparing this with a screen awake behind the amp? Switch the screen")
        print("off, then WAIT FIVE MINUTES before running --diag again. Receivers")
        print("hold the last good EDID for a few minutes; both amps tested took")
        print("about four. Run it too soon and nothing will appear to change.")
        print()
    print("If a receiver reports a power state above rather than 'not supported',")
    print("please open an issue - it would mean a screen switched off behind it")
    print("can be detected without waiting out that timeout.")


def single_instance():
    handle = kernel32.CreateMutexW(None, False, "Global\\PhantomMonitor_SingleInstance")
    return handle and kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    args = set(a.lower() for a in sys.argv[1:])
    cfg = load_config()
    setup_logging(cfg.get("log_level", "INFO"),
                  os.path.join(LOG_DIR, "settings.log")
                  if "--settings" in args else None)
    awareness = set_dpi_awareness()

    if "--ask-name" in args:
        rest = [a for a in sys.argv[1:] if a != "--ask-name"]
        return ask_for_name(rest[0] if rest else "Name",
                            rest[1] if len(rest) > 1 else "")

    if "--arrange" in args:
        # Ask the running guard to do it, rather than starting a second copy
        # that would arrange windows and immediately exit. This is the hook for
        # a Stream Deck button or any other launcher.
        which = 0
        for a in sys.argv[1:]:
            if a.isdigit():
                which = int(a)
        target = win32gui.FindWindow("PhantomMonitorWnd", None)
        if not target:
            print("Phantom Monitor is not running")
            return 1
        win32gui.PostMessage(target, WM_APP_ARRANGE, which,
                             1 if "--open" in args else 0)
        print("asked Phantom Monitor to arrange %s"
              % ("display %d" % which if which else "every display"))
        return 0

    if "--diag" in args:
        print_diagnostics(cfg)
        return 0

    if "--settings" in args:
        import settings_window
        live = enum_monitors()
        monitors = [(m.name, m.hwid, m.rect[2] - m.rect[0], m.rect[3] - m.rect[1],
                     m.rect[0], m.rect[1], m.primary) for m in live]
        attached = set(m.hwid for m in live)
        absent = [(hwid, name) for hwid, name in known_displays()
                  if hwid not in attached]
        settings_window.run(CONFIG_PATH, monitors, absent)
        return 0

    if "--list" in args:
        for mon in enum_monitors():
            rules = cfg.get("blocked_hwids", [])
            blocked = (" [BLOCKED]"
                       if any(monitor_matches_block(mon, r) for r in rules) else "")
            hint = looks_like_av_device(mon.hwid, mon.name,
                                        cfg.get("av_devices"), cfg.get("not_av_devices"))
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
