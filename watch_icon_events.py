"""Report accessibility events from the desktop ListView, to find a save trigger."""
import ctypes, ctypes.wintypes as wt, time
import desktop_icons as di

lv = di.desktop_listview()
u = ctypes.windll.user32
EV = {0x8000: "CREATE", 0x8001: "DESTROY", 0x800B: "LOCATIONCHANGE",
      0x8004: "NAMECHANGE", 0x800F: "VALUECHANGE", 0x8006: "FOCUS",
      0x8007: "SELECTION", 0x800E: "REORDER", 0x8002: "SHOW", 0x8003: "HIDE"}

Proc = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG,
                          wt.DWORD, wt.DWORD)
u.SetWinEventHook.restype = wt.HANDLE
u.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, Proc,
                              wt.DWORD, wt.DWORD, wt.DWORD]
seen = {}

def on_event(_h, ev, hwnd, obj, child, _t, _ts):
    if hwnd != lv:
        return
    key = (EV.get(ev, hex(ev)), obj, child)
    if key not in seen:
        seen[key] = 0
        print("  %-16s idObject=%-5d idChild=%d" % key, flush=True)
    seen[key] += 1

proc = Proc(on_event)
hook = u.SetWinEventHook(0x8000, 0x8010, 0, proc, 0, 0, 0)
print("watching desktop listview %d for 60s - drag an icon now\n" % lv, flush=True)

msg = wt.MSG()
end = time.time() + 60
while time.time() < end:
    while u.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
        u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
    time.sleep(0.02)

print("\ntotal distinct event kinds: %d" % len(seen), flush=True)
for k, n in sorted(seen.items(), key=lambda kv: -kv[1]):
    print("  %-16s idObject=%-5d idChild=%-4d  x%d" % (k[0], k[1], k[2], n), flush=True)
