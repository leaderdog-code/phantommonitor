"""Read and restore desktop icon positions.

Windows recalculates the desktop icon grid whenever the display layout changes
and never puts the icons back. This reads their positions before, and restores
them after.

The icons live in a SysListView32 owned by Explorer, so positions have to be
read across a process boundary: the ListView messages want a pointer to a buffer
in the *receiving* process, which means allocating inside Explorer and reading
the result back out. Writing positions is simpler - the coordinates are packed
into lParam directly, with no buffer involved.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

import win32con
import win32gui
import win32process

log = logging.getLogger("PhantomMonitor")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

LVM_FIRST = 0x1000
LVM_GETITEMCOUNT = LVM_FIRST + 4
LVM_SETITEMPOSITION = LVM_FIRST + 15
LVM_GETITEMPOSITION = LVM_FIRST + 16
LVM_GETITEMTEXTW = LVM_FIRST + 115
LVS_AUTOARRANGE = 0x0100
LVIF_TEXT = 0x0001

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
SMTO_ABORTIFHUNG = 0x0002

LRESULT = ctypes.c_ssize_t

kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                    wt.DWORD, wt.DWORD]
kernel32.VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = LRESULT
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
                                       wt.UINT, wt.UINT, ctypes.POINTER(LRESULT)]


class LVITEMW(ctypes.Structure):
    _fields_ = [("mask", wt.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
                ("state", wt.UINT), ("stateMask", wt.UINT),
                ("pszText", ctypes.c_void_p), ("cchTextMax", ctypes.c_int),
                ("iImage", ctypes.c_int), ("lParam", ctypes.c_ssize_t),
                ("iIndent", ctypes.c_int), ("iGroupId", ctypes.c_int),
                ("cColumns", wt.UINT), ("puColumns", ctypes.c_void_p),
                ("piColFmt", ctypes.c_void_p), ("iGroup", ctypes.c_int)]


def send(hwnd, msg, wparam, lparam, timeout_ms=3000):
    """SendMessage that cannot wedge us if Explorer is busy."""
    result = LRESULT(0)
    ok = user32.SendMessageTimeoutW(hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG,
                                    timeout_ms, ctypes.byref(result))
    return result.value if ok else None


def desktop_listview():
    """The SysListView32 holding the desktop icons, or 0 if it cannot be found."""
    progman = win32gui.FindWindow("Progman", None)
    if progman:
        defview = win32gui.FindWindowEx(progman, 0, "SHELLDLL_DefView", None)
        if defview:
            lv = win32gui.FindWindowEx(defview, 0, "SysListView32", None)
            if lv:
                return lv
    # With a wallpaper slideshow the desktop is re-parented under a WorkerW.
    found = []

    def visit(hwnd, _):
        if win32gui.GetClassName(hwnd) != "WorkerW":
            return
        defview = win32gui.FindWindowEx(hwnd, 0, "SHELLDLL_DefView", None)
        if defview:
            lv = win32gui.FindWindowEx(defview, 0, "SysListView32", None)
            if lv:
                found.append(lv)

    win32gui.EnumWindows(visit, None)
    return found[0] if found else 0


def auto_arrange_on(lv):
    """Auto-arrange overrides any position we set, so callers should warn about it."""
    try:
        return bool(win32gui.GetWindowLong(lv, win32con.GWL_STYLE) & LVS_AUTOARRANGE)
    except Exception:
        return False


class _Remote:
    """A scratch buffer inside Explorer, for ListView messages that want a pointer."""

    def __init__(self, hwnd, size):
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        self.proc = kernel32.OpenProcess(
            PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE
            | PROCESS_QUERY_INFORMATION, False, pid)
        self.addr = None
        self.size = size
        if self.proc:
            self.addr = kernel32.VirtualAllocEx(self.proc, None, size,
                                                MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        if self.proc:
            if self.addr:
                kernel32.VirtualFreeEx(self.proc, self.addr, 0, MEM_RELEASE)
            kernel32.CloseHandle(self.proc)
        return False

    def ok(self):
        return bool(self.proc and self.addr)

    def write(self, data, offset=0):
        written = ctypes.c_size_t(0)
        return bool(kernel32.WriteProcessMemory(
            self.proc, ctypes.c_void_p(self.addr + offset), ctypes.byref(data),
            ctypes.sizeof(data), ctypes.byref(written)))

    def read(self, into, offset=0):
        got = ctypes.c_size_t(0)
        return bool(kernel32.ReadProcessMemory(
            self.proc, ctypes.c_void_p(self.addr + offset), ctypes.byref(into),
            ctypes.sizeof(into), ctypes.byref(got)))


def get_icons(lv=None):
    """{icon name: (x, y)} for every desktop icon, or {} if it cannot be read."""
    lv = lv or desktop_listview()
    if not lv:
        return {}
    count = send(lv, LVM_GETITEMCOUNT, 0, 0) or 0
    if count <= 0:
        return {}

    text_bytes = 520          # 260 wide chars
    item_offset = text_bytes  # keep the LVITEMW after the text buffer
    icons = {}
    with _Remote(lv, text_bytes + ctypes.sizeof(LVITEMW) + 32) as mem:
        if not mem.ok():
            log.warning("cannot read desktop icons: no access to Explorer's memory")
            return {}
        for index in range(count):
            point = wt.POINT()
            if send(lv, LVM_GETITEMPOSITION, index, mem.addr) is None:
                continue
            if not mem.read(point):
                continue

            item = LVITEMW()
            item.mask = LVIF_TEXT
            item.iItem = index
            item.iSubItem = 0
            item.pszText = mem.addr
            item.cchTextMax = 260
            if not mem.write(item, item_offset):
                continue
            if not send(lv, LVM_GETITEMTEXTW, index, mem.addr + item_offset):
                continue
            buf = (ctypes.c_wchar * 260)()
            if not mem.read(buf):
                continue
            name = buf.value
            if name:
                icons[name] = (point.x, point.y)
    return icons


def set_icons(layout, lv=None):
    """Put icons back where `layout` says. Returns how many were moved."""
    lv = lv or desktop_listview()
    if not lv or not layout:
        return 0
    current = get_icons(lv)
    index_of = {}
    count = send(lv, LVM_GETITEMCOUNT, 0, 0) or 0

    # Names alone are not enough to address an item, so pair them back up by
    # walking the list once more in the same order get_icons used.
    with _Remote(lv, 520 + ctypes.sizeof(LVITEMW) + 32) as mem:
        if not mem.ok():
            return 0
        for index in range(count):
            item = LVITEMW()
            item.mask = LVIF_TEXT
            item.iItem = index
            item.iSubItem = 0
            item.pszText = mem.addr
            item.cchTextMax = 260
            if not mem.write(item, 520):
                continue
            if not send(lv, LVM_GETITEMTEXTW, index, mem.addr + 520):
                continue
            buf = (ctypes.c_wchar * 260)()
            if mem.read(buf) and buf.value:
                index_of[buf.value] = index

    moved = 0
    for name, (x, y) in layout.items():
        index = index_of.get(name)
        if index is None:
            continue
        if current.get(name) == (x, y):
            continue
        lparam = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
        if send(lv, LVM_SETITEMPOSITION, index, lparam) is not None:
            moved += 1
    return moved


if __name__ == "__main__":
    lv = desktop_listview()
    print("desktop listview:", lv, "| auto-arrange:", auto_arrange_on(lv))
    found = get_icons(lv)
    print("read %d icons" % len(found))
    for name, pos in sorted(found.items(), key=lambda kv: kv[1])[:12]:
        print("   %-42s %s" % (name[:42], pos))
