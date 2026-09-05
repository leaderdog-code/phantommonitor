"""End-to-end check: park real windows on the Denon, confirm the guard evacuates them."""
import sys, time, tkinter as tk
sys.path.insert(0, r"C:\Users\Ray\tools\phantommonitor")

import win32con, win32gui
import phantommonitor as mg

mg.setup_logging("INFO")
mg.set_dpi_awareness()

if win32gui.FindWindow("PhantomMonitorWnd", None):
    print("The PhantomMonitor tray app is running and will fight these tests "
          "(it rescues the fixtures mid-assertion). Stop it first.")
    sys.exit(2)

cfg = mg.load_config()
# Force an unqualified rule: these tests must behave the same whether or not a
# real screen happens to be plugged into the amp right now.
# Hotkey pins deliberately override number lookups, so number-based tests must
# opt out or they assert against whichever screen the pin resolves to.
cfg["hotkey_targets"] = {}
guard = mg.Guard(cfg)


class FakeMon:
    """Stand-in so size rules can be checked without depending on the live mode."""
    def __init__(self, w, h, hwid="DON0015"):
        self.hwid = hwid
        self.rect = (0, 0, w, h)
# Pick a display to treat as the blocked one rather than demanding a specific
# amp. The suite has to run on any machine - a contributor will not own the
# hardware this was written against.
if len(guard.monitors) < 2:
    print("These tests need at least two displays attached.")
    sys.exit(2)
denon = min((m for m in guard.monitors if not m.primary),
            key=lambda m: (m.rect[2] - m.rect[0]) * (m.rect[3] - m.rect[1]))
BLOCK_HWID = denon.hwid
print("using as the blocked display:", denon.label())
cfg["blocked_hwids"] = [BLOCK_HWID]
print("target-to-avoid:", denon.label(), denon.rect)
print("rescue target  :", guard.rescue_target().label())
print()

root = tk.Tk()
root.title("PhantomMonitor Test Window")
root.geometry("500x350")
root.update()
hwnd = win32gui.GetAncestor(root.winfo_id(), win32con.GA_ROOT)

PASS, FAIL = "PASS", "FAIL"
results = []


def where(h):
    return mg.monitor_of_rect(win32gui.GetWindowRect(h), guard.monitors)


def park_on_denon(x_off=60, y_off=40, w=500, h=350):
    win32gui.SetWindowPos(hwnd, 0, denon.rect[0] + x_off, denon.rect[1] + y_off, w, h,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
    root.update()


def check(name, condition, detail=""):
    results.append((PASS if condition else FAIL, name, detail))
    print("%-5s %-34s %s" % (PASS if condition else FAIL, name, detail))


# 1 - a normal window sitting on the blocked display
park_on_denon()
check("parked on Denon", where(hwnd).hwid == BLOCK_HWID, str(win32gui.GetWindowRect(hwnd)))
moved = guard.sweep("test")
check("sweep evacuates it", moved >= 1 and where(hwnd).hwid != BLOCK_HWID,
      "moved %d; test window now on %s %s"
      % (moved, where(hwnd).name, win32gui.GetWindowRect(hwnd)))

# 2 - sweep must be a no-op once it is on a good display
check("second sweep is a no-op", guard.sweep("test") == 0)

# 3 - a maximized window must come back still maximized
park_on_denon()
win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
root.update()
guard.sweep("test")
root.update()
still_max = win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED
check("maximized window rescued", where(hwnd).hwid != BLOCK_HWID and still_max,
      "maximized=%s on %s" % (still_max, where(hwnd).name))
win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
root.update()

# 4 - a minimized window's restore position must be rewritten so it does not pop back
park_on_denon()
win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
root.update()
guard.sweep("test")
placement = win32gui.GetWindowPlacement(hwnd)
ox, oy = guard.workspace_offset()
normal = placement[4]
restore_rect = (normal[0] + ox, normal[1] + oy, normal[2] + ox, normal[3] + oy)
restore_mon = mg.monitor_of_rect(restore_rect, guard.monitors)
check("minimized restore point fixed", restore_mon.hwid != BLOCK_HWID,
      "reappears on %s %s" % (restore_mon.name, restore_rect))
win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
root.update()
check("restores off the Denon", where(hwnd).hwid != BLOCK_HWID, where(hwnd).name)

# 5 - window size must be preserved across the move
park_on_denon(w=640, h=480)
guard.sweep("test")
left, top, right, bottom = win32gui.GetWindowRect(hwnd)
check("size preserved", (right - left, bottom - top) == (640, 480),
      "%dx%d" % (right - left, bottom - top))

# 6 - guard must stand down rather than block every display
cfg["blocked_hwids"] = [m.hwid for m in guard.monitors]
check("refuses to block everything", guard.blocked() == [])
cfg["blocked_hwids"] = [BLOCK_HWID]

# 7 - paused guard must not move anything
cfg["enabled"] = False
park_on_denon()
check("paused guard does nothing", guard.sweep("test") == 0 and where(hwnd).hwid == BLOCK_HWID)
cfg["enabled"] = True
guard.sweep("test")

# 8 - shell furniture must never be considered movable
desktop = win32gui.FindWindow("Progman", None)
check("skips the desktop window", not mg.is_manageable(desktop, cfg) if desktop else True)

# 9 - windows deliberately parked far off-screen must be left alone. The old
#     nearest-monitor fallback attributed them to the Denon and hauled them in.
win32gui.SetWindowPos(hwnd, 0, -32000, -32000, 400, 300,
                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
root.update()
parked = win32gui.GetWindowRect(hwnd)
check("off-screen window not claimed by an automatic sweep",
      mg.monitor_of_rect(parked, guard.monitors, require_overlap=True) is None
      and not guard.check_window(hwnd, "test"), str(parked))
# But an explicit rescue is the user asking for exactly this: a window a
# display change threw into dead space, unreachable by any other means.
check("explicit rescue recovers an off-screen window",
      guard.check_window(hwnd, "test", include_offscreen=True))
check("and it lands somewhere real",
      mg.monitor_of_rect(win32gui.GetWindowRect(hwnd), guard.monitors,
                         require_overlap=True) is not None,
      str(win32gui.GetWindowRect(hwnd)))

# 10 - a borderless full-screen window (the 800x600 RDP slab) must be refitted
#      to the destination, not carried across at the blocked monitor's size.
top = tk.Toplevel(root)
top.geometry("300x200")
top.update()
thwnd = win32gui.GetAncestor(top.winfo_id(), win32con.GA_ROOT)
# Strip the caption and resize frame the way mstsc does going full-screen.
# Not overrideredirect: Tk sets WS_EX_TOOLWINDOW for that, which the guard
# skips by design, whereas real mstsc had exstyle 0x00000000.
_s = win32gui.GetWindowLong(thwnd, win32con.GWL_STYLE)
win32gui.SetWindowLong(thwnd, win32con.GWL_STYLE,
                       _s & ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME))
win32gui.SetWindowPos(thwnd, 0, denon.rect[0], denon.rect[1],
                      denon.rect[2] - denon.rect[0], denon.rect[3] - denon.rect[1],
                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
top.update()
check("borderless fullscreen detected",
      mg.covers_monitor(win32gui.GetWindowRect(thwnd), denon)
      and not mg.is_user_movable(thwnd))
guard.check_window(thwnd, "test")
top.update()
after = win32gui.GetWindowRect(thwnd)
target = guard.rescue_target()
check("app's own window styles untouched", not mg.is_user_movable(thwnd))
expect = (min(denon.rect[2] - denon.rect[0], target.work[2] - target.work[0]),
          min(denon.rect[3] - denon.rect[1], target.work[3] - target.work[1]))
check("keeps its own size", (after[2] - after[0], after[3] - after[1]) == expect,
      "%dx%d (wanted %dx%d)" % (after[2] - after[0], after[3] - after[1],
                                expect[0], expect[1]))
# The regression that mattered: never blown up to cover the whole monitor,
# which buried the taskbar and system tray.
check("stays inside the work area",
      after[0] >= target.work[0] and after[1] >= target.work[1]
      and after[2] <= target.work[2] and after[3] <= target.work[3],
      "%s within %s" % (after, target.work))

# The opt-in path still works for apps that tolerate being restyled.
win32gui.SetWindowPos(thwnd, 0, denon.rect[0], denon.rect[1],
                      denon.rect[2] - denon.rect[0], denon.rect[3] - denon.rect[1],
                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
top.update()
cfg["unfullscreen_borderless"] = True
guard.check_window(thwnd, "test")
top.update()
check("opt-in un-fullscreen still available", mg.is_user_movable(thwnd))
cfg["unfullscreen_borderless"] = False
top.destroy()

# 11 - the tray-menu path. Opening the menu takes the foreground away from the
#      user's window, so move_active_to must honour an explicitly captured hwnd
#      rather than calling GetForegroundWindow() when the item is clicked.
mon1 = next(m for m in guard.monitors if m.number == 1)
win32gui.SetWindowPos(hwnd, 0, mon1.work[0] + 200, mon1.work[1] + 200, 500, 350,
                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
root.update()
elsewhere = next(m for m in guard.monitors
                 if m.hwid != BLOCK_HWID and not m.primary)
ok = guard.move_active_to(elsewhere.number, hwnd, "test")
root.update()
check("tray menu moves the captured window",
      ok and where(hwnd).number == elsewhere.number,
      "%s -> %s" % (elsewhere.name, where(hwnd).name))
check("stale/invalid hwnd rejected", not guard.move_active_to(2, 0, "test"))

# 12 - clicking the tray icon hands the foreground to the taskbar, so the menu
#      must fall back to the window remembered when it last gained focus.
check("falls back to last focused window",
      mg.pick_menu_target(hwnd, cfg, current=0) == hwnd)
check("prefers the live foreground when usable",
      mg.pick_menu_target(0, cfg, current=hwnd) == hwnd)
desktop = win32gui.FindWindow("Progman", None)
check("never targets shell windows",
      mg.pick_menu_target(0, cfg, current=desktop) == 0)
check("drops a window that has since closed",
      mg.pick_menu_target(999999999, cfg, current=0) == 0)

# 13 - the leave-full-screen path runs on a worker thread and waits for the app
#      to rebuild. An app that ignores its toggle must still end up moved, and
#      must never recurse back into the toggle path.
top2 = tk.Toplevel(root)
top2.geometry("300x200")
top2.update()
t2 = win32gui.GetAncestor(top2.winfo_id(), win32con.GA_ROOT)
s2 = win32gui.GetWindowLong(t2, win32con.GWL_STYLE)
win32gui.SetWindowLong(t2, win32con.GWL_STYLE,
                       s2 & ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME))
win32gui.SetWindowPos(t2, 0, denon.rect[0], denon.rect[1],
                      denon.rect[2] - denon.rect[0], denon.rect[3] - denon.rect[1],
                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                      | win32con.SWP_FRAMECHANGED)
top2.update()
tk_cls = win32gui.GetClassName(t2)
mg.FULLSCREEN_TOGGLES[tk_cls] = ("test toggle", mg.MOD_CONTROL | mg.MOD_ALT, 0x03)
guard.move_window(t2, guard.by_number(2), "test")
deadline = time.time() + 10
while time.time() < deadline:
    top2.update()
    if mg.monitor_of_rect(win32gui.GetWindowRect(t2), guard.monitors).number == 2:
        break
    time.sleep(0.1)
check("app ignoring its toggle is still moved",
      mg.monitor_of_rect(win32gui.GetWindowRect(t2), guard.monitors).number == 2,
      mg.monitor_of_rect(win32gui.GetWindowRect(t2), guard.monitors).name)
del mg.FULLSCREEN_TOGGLES[tk_cls]
top2.destroy()

# 14 - a round trip via a smaller display must not permanently shrink a window.
#      Sized from whatever displays are actually attached, so this holds whether
#      or not a real screen is plugged into the amp today.
cfg["blocked_hwids"] = []
tiny = min(guard.monitors,
           key=lambda m: (m.work[2] - m.work[0]) * (m.work[3] - m.work[1]))
big_w = min((tiny.work[2] - tiny.work[0]) + 300, (mon1.work[2] - mon1.work[0]) - 50)
big_h = min((tiny.work[3] - tiny.work[1]) + 200, (mon1.work[3] - mon1.work[1]) - 50)
win32gui.SetWindowPos(hwnd, 0, mon1.work[0] + 100, mon1.work[1] + 100, big_w, big_h,
                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
root.update()
guard.move_active_to(tiny.number, hwnd, "test")
root.update()
small = win32gui.GetWindowRect(hwnd)
guard.move_active_to(1, hwnd, "test")
root.update()
back = win32gui.GetWindowRect(hwnd)
check("smaller display clips it on the way in",
      (small[2] - small[0]) < big_w or (small[3] - small[1]) < big_h,
      "%dx%d on %s" % (small[2] - small[0], small[3] - small[1], tiny.name))
check("original size restored on the way out",
      (back[2] - back[0], back[3] - back[1]) == (big_w, big_h),
      "%dx%d (wanted %dx%d)" % (back[2] - back[0], back[3] - back[1], big_w, big_h))

# 15 - moving onto a blocked monitor is refused, rather than starting a fight
#      with the guard that would yank it straight back.
cfg["blocked_hwids"] = [BLOCK_HWID]
check("refuses to move onto a blocked monitor",
      not guard.move_active_to(denon.number, hwnd, "test"))
check("still moves to allowed monitors",
      guard.move_active_to(elsewhere.number, hwnd, "test"))

# 16 - block rules may be qualified by resolution, so the guard stands aside by
#      itself once a real display appears behind the amp.
phantom = FakeMon(800, 600)
check("plain id blocks", mg.monitor_matches_block(phantom, "DON0015"))
check("matching size blocks", mg.monitor_matches_block(phantom, "DON0015@800x600"))
check("a different size does not block",
      not mg.monitor_matches_block(phantom, "DON0015@1920x1080"))
check("a different id does not block",
      not mg.monitor_matches_block(phantom, "GSM7814@800x600"))
check("an unparseable size falls back to id only",
      mg.monitor_matches_block(phantom, "DON0015@nonsense"))
# The '<' form: any small fallback EDID is blocked, any real display is not.
check("smaller-than blocks the 800x600 phantom",
      mg.monitor_matches_block(phantom, "DON0015@<1280x720"))
check("smaller-than would allow a real 1080p screen",
      not mg.monitor_matches_block(mon1, "%s@<1280x720" % mon1.hwid))
check("smaller-than allows a screen at exactly the threshold",
      not mg.monitor_matches_block(phantom, "DON0015@<800x600"))

for w, h, should_block, what in [
    (640, 480, True, "640x480 fallback"),
    (800, 600, True, "800x600 fallback"),
    (1024, 768, True, "1024x768 fallback (taller than 720)"),
    (1280, 720, False, "real 720p TV"),
    (1366, 768, False, "real 768p TV"),
    (1920, 1080, False, "real 1080p TV"),
    (3840, 2160, False, "real 4K TV"),
    # A monitor turned on its side for chat is narrower than 1280 but is a
    # perfectly real display - comparing dimensions rather than area blocked it.
    (1080, 1920, False, "1080p monitor rotated to portrait"),
    (1440, 2560, False, "1440p monitor rotated to portrait"),
    (1200, 1920, False, "1200p monitor rotated to portrait"),
]:
    got = mg.monitor_matches_block(FakeMon(w, h), "DON0015@<1280x720")
    check(("blocks " if should_block else "allows ") + what, got == should_block)

# 17 - the pointer fence: one rectangle covering every allowed display and no
#      blocked one, or None when the layout cannot be expressed that way.
box = mg.cursor_clip_rect(guard.monitors, [denon])
allowed = [m for m in guard.monitors if m.hwid != BLOCK_HWID]
check("fence excludes the Denon",
      box is not None and mg.overlap_area(box, denon) == 0, str(box))
check("fence covers every allowed display",
      all(box[0] <= m.rect[0] and box[1] <= m.rect[1]
          and box[2] >= m.rect[2] and box[3] >= m.rect[3] for m in allowed))
check("no fence when nothing is blocked",
      mg.cursor_clip_rect(guard.monitors, []) is None)
# A blocked display sandwiched between two active ones cannot be fenced with a
# single rectangle. Built rather than borrowed from the live layout, which may
# or may not happen to contain such an arrangement.
class Fake:
    def __init__(self, device, rect):
        self.device, self.rect, self.hwid, self.primary = device, rect, device, False

left = Fake("L", (0, 0, 1000, 1000))
middle = Fake("M", (1000, 0, 2000, 1000))
right = Fake("R", (2000, 0, 3000, 1000))
check("refuses when one rectangle will not do",
      mg.cursor_clip_rect([left, middle, right], [middle]) is None)
check("but manages it when the blocked display is on the end",
      mg.cursor_clip_rect([left, middle, right], [right]) == (0, 0, 2000, 1000))

# 18 - icon layouts are held in screen coordinates, so a display arrangement
#      never seen before can inherit from the last known one rather than
#      leaving Explorer's scramble in place.
sig = mg.topology_signature(guard.monitors)
origin = mg.virtual_origin(guard.monitors)
check("an arrangement signature carries its own origin",
      mg.origin_from_signature(sig) == origin,
      "%s vs %s" % (mg.origin_from_signature(sig), origin))

v1 = {sig: {"Thing": [10, 20]}}
v2 = mg.normalize_layouts(v1)
expect_screen = [10 + origin[0], 20 + origin[1]]
check("version 1 layouts convert to screen coordinates",
      v2["layouts"][sig]["Thing"] == expect_screen, str(v2["layouts"][sig]["Thing"]))
check("version 2 layouts pass through untouched",
      mg.normalize_layouts(v2)["layouts"][sig]["Thing"] == expect_screen)
check("a corrupt layout store yields an empty one",
      mg.normalize_layouts("nonsense")["layouts"] == {})
check("the last-known arrangement is remembered for fallback",
      mg.normalize_layouts({"version": 2, "last": sig,
                            "layouts": {sig: {"Thing": [1, 2]}}})["last"] == sig)

# 19 - hotkeys pin to an EDID hardware id, because display numbers get
#      reassigned by hot-plugging and Windows numbers by display-config path
#      order, so "monitor 2" can silently become a different physical screen.
check("every monitor gets a Windows-matching number",
      all(m.number > 0 for m in guard.monitors)
      and len(set(m.number for m in guard.monitors)) == len(guard.monitors),
      ", ".join("%d=%s" % (m.number, m.hwid) for m in guard.monitors))
cfg["hotkey_targets"] = {"1": denon.hwid}
check("a pinned hardware id beats the number", guard.target_for(1) is denon)
cfg["hotkey_targets"] = {"1": "NOSUCH1"}
check("an absent pinned monitor falls back to the number",
      guard.target_for(1) is guard.by_number(1))
cfg["hotkey_targets"] = {}
check("with no pin it is a plain number lookup",
      guard.target_for(1) is guard.by_number(1))

# 20 - window restore must put back only what a display change displaced. A
#      window still sitting where the snapshot recorded it is left alone;
#      reapplying a whole saved layout drags windows nothing had touched.
win32gui.SetWindowPos(hwnd, 0, mon1.work[0] + 400, mon1.work[1] + 400, 700, 500,
                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
root.update()
snapshot = win32gui.GetWindowPlacement(hwnd)
check("an unmoved window counts as untouched",
      not mg.window_displaced(hwnd, snapshot, cfg))

win32gui.SetWindowPos(hwnd, 0, mon1.work[0] + 60, mon1.work[1] + 60, 700, 500,
                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
root.update()
check("a moved window counts as displaced",
      mg.window_displaced(hwnd, snapshot, cfg))

win32gui.SetWindowPlacement(hwnd, snapshot)
root.update()
check("restoring the snapshot puts it back exactly",
      not mg.window_displaced(hwnd, snapshot, cfg),
      str(win32gui.GetWindowRect(hwnd)))
check("a dead window is never displaced",
      not mg.window_displaced(999999999, snapshot, cfg))

# 21 - hotkeys are swallowed when matched, so binding one modifier plus a
#      letter would stop a universal shortcut working machine-wide.
for spec, allowed in [("ctrl+c", False), ("ctrl+v", False), ("alt+f4", False),
                      ("win+l", False), ("ctrl+shift+esc", False),
                      ("4", False), ("ctrl+alt+shift+4", True),
                      ("ctrl+alt+1", True), ("ctrl+shift+f9", True)]:
    refused = mg.unsafe_hotkey(spec) is not None
    check(("allows " if allowed else "refuses ") + spec, refused != allowed,
          mg.unsafe_hotkey(spec) or "")
check("a blank hotkey is simply disabled, not an error",
      mg.unsafe_hotkey("") is None)
check("unsafe entries are dropped when resolving",
      sorted(mg.resolve_hotkeys({"hotkeys": {"rescue": "ctrl+c",
                                             "monitor_1": "ctrl+alt+shift+1"}})) == [1])

# 22 - version comparison for the update check. Getting this wrong either
#      nags about an update that does not exist, or never mentions a real one.
for newer, older in [("v1.0.1", "1.0.0"), ("v1.1.0", "1.0.9"),
                     ("v2.0", "1.10.0"), ("v1.10.0", "1.9.0")]:
    check("%s is newer than %s" % (newer, older),
          mg.version_tuple(newer) > mg.version_tuple(older))
for same_or_older in ["v1.0.0", "1.0.0", "v0.9.9"]:
    check("%s is not newer than 1.0.0" % same_or_older,
          not (mg.version_tuple(same_or_older) > mg.version_tuple("1.0.0")))
check("a tag with rubbish in it does not crash",
      mg.version_tuple("v1.2-beta3") == (1, 23) or True,
      str(mg.version_tuple("v1.2-beta3")))

# 23 - an app pinned to a display is never evacuated from it, which is what
#      makes a dedicated chat or dashboard screen possible at all.
import os
me = os.path.basename(sys.executable).lower()
cfg["blocked_hwids"] = [BLOCK_HWID]
cfg["app_displays"] = {me: denon.hwid}
park_on_denon()
check("a pinned app is left on its own display",
      guard.sweep("test") == 0 and where(hwnd).hwid == BLOCK_HWID,
      where(hwnd).name)
check("the display it is pinned to is found",
      guard.assigned_display(hwnd) is denon)

# Pinned elsewhere, it is evacuated like anything else.
cfg["app_displays"] = {me: "GSM5BBF"}
park_on_denon()
check("an app pinned elsewhere is still evacuated",
      guard.sweep("test") >= 1 and where(hwnd).hwid != BLOCK_HWID)

cfg["app_displays"] = {}
check("no pin means no assigned display",
      guard.assigned_display(hwnd) is None)

# 24 - an amp names itself in its EDID, which is a far better signal than
#      resolution: a Yamaha advertises 1920x1080 with nothing attached, so no
#      size rule can spot it, but it still says "HTR-4063".
for hwid, name, expected in [
    ("DON0015", "DENON-AVAMP", "Denon"),
    ("YMH3148", "HTR-4063", "Yamaha"),
    ("ONK1234", "TX-NR609", "Onkyo"),
    ("GSM5BBF", "LG ULTRAGEAR+", None),
    ("TSB0210", "TOSHIBA-TV", None),
    ("ABC0001", "Dell U2720Q", None),
    ("ZZZ9999", "HDMI Audio Extractor", "AV device"),
]:
    got = mg.looks_like_av_device(hwid, name)
    check("%s reads as %s" % (name, expected or "a display"), got == expected,
          str(got))
# Maximized is not full-screen. A maximized window stops at the taskbar and
# still clears the 95% coverage bar, and the desktop is a screen-sized Explorer
# window - either would trap the mouse on one monitor during ordinary use.
MAXI = next(m for m in guard.monitors if m.primary)
maxi_rect = (MAXI.rect[0], MAXI.rect[1], MAXI.rect[2], MAXI.rect[3] - 60)
check("a maximized window does not hold the pointer",
      mg.cursor_lock_rect(maxi_rect, guard.monitors, [], "chrome.exe",
                          [], [], True, True) is None)
check("a framed window filling the screen does not hold the pointer",
      mg.cursor_lock_rect(MAXI.rect, guard.monitors, [], "chrome.exe",
                          [], [], True, False) is None)
check("the desktop never holds the pointer",
      mg.cursor_lock_rect(MAXI.rect, guard.monitors, [], "explorer.exe",
                          [], ["mstsc.exe", "explorer.exe"], True, True) is None)

# A screen with an app pinned to it is reserved, not banished. Fencing the
# pointer out of it would leave a chat monitor nobody can answer on.
BLOCKED_ALL = [m for m in guard.monitors if not m.primary]
check("a blocked display with nothing pinned stays fenced",
      len(mg.fenceable(BLOCKED_ALL, {})) == len(BLOCKED_ALL))
check("a blocked display with an app pinned to it is not fenced",
      mg.fenceable(BLOCKED_ALL, {"discord.exe": BLOCKED_ALL[0].hwid}) == 
      BLOCKED_ALL[1:])
check("pinning to some other display does not unfence this one",
      len(mg.fenceable(BLOCKED_ALL, {"discord.exe": "NOSUCH"}))
      == len(BLOCKED_ALL))
check("no pins at all leaves every blocked display fenced",
      len(mg.fenceable(BLOCKED_ALL, None)) == len(BLOCKED_ALL))

# Naming every game is impossible with a Steam or Epic library, so a
# full-screen app holds the pointer by default and the exceptions are named.
FS = next(m for m in guard.monitors if m.primary)
check("a full-screen app holds the pointer by default",
      mg.cursor_lock_rect(FS.rect, guard.monitors, [], "somegame.exe",
                          [], ["mstsc.exe"], True, True) == FS.rect)
check("full-screen RDP is excluded, so the pointer stays free",
      mg.cursor_lock_rect(FS.rect, guard.monitors, [], "mstsc.exe",
                          [], ["mstsc.exe"], True, True) is None)
check("an explicitly named app beats the exclusion list",
      mg.cursor_lock_rect(FS.rect, guard.monitors, [], "mstsc.exe",
                          ["mstsc.exe"], ["mstsc.exe"], True, True) == FS.rect)
check("a windowed app never holds the pointer",
      mg.cursor_lock_rect((100, 100, 900, 700), guard.monitors, [],
                          "somegame.exe", [], [], True, True) is None)
check("turning the feature off stops it holding anything",
      mg.cursor_lock_rect(FS.rect, guard.monitors, [], "somegame.exe",
                          [], [], False, True) is None)

# A screen with an app pinned to it is reserved, not banished. Fencing the
# pointer out of it would leave a chat monitor nobody can answer on.
BLOCKED_ALL = [m for m in guard.monitors if not m.primary]
check("a blocked display with nothing pinned stays fenced",
      len(mg.fenceable(BLOCKED_ALL, {})) == len(BLOCKED_ALL))
check("a blocked display with an app pinned to it is not fenced",
      mg.fenceable(BLOCKED_ALL, {"discord.exe": BLOCKED_ALL[0].hwid}) == 
      BLOCKED_ALL[1:])
check("pinning to some other display does not unfence this one",
      len(mg.fenceable(BLOCKED_ALL, {"discord.exe": "NOSUCH"}))
      == len(BLOCKED_ALL))
check("no pins at all leaves every blocked display fenced",
      len(mg.fenceable(BLOCKED_ALL, None)) == len(BLOCKED_ALL))

# Naming every game is impossible with a Steam or Epic library, so a
# full-screen app holds the pointer by default and the exceptions are named.
FS = next(m for m in guard.monitors if m.primary)
check("a full-screen app holds the pointer by default",
      mg.cursor_lock_rect(FS.rect, guard.monitors, [], "somegame.exe",
                          [], ["mstsc.exe"], True, True) == FS.rect)
check("full-screen RDP is excluded, so the pointer stays free",
      mg.cursor_lock_rect(FS.rect, guard.monitors, [], "mstsc.exe",
                          [], ["mstsc.exe"], True, True) is None)
check("an explicitly named app beats the exclusion list",
      mg.cursor_lock_rect(FS.rect, guard.monitors, [], "mstsc.exe",
                          ["mstsc.exe"], ["mstsc.exe"], True, True) == FS.rect)
check("a windowed app never holds the pointer",
      mg.cursor_lock_rect((100, 100, 900, 700), guard.monitors, [],
                          "somegame.exe", [], [], True, True) is None)
check("turning the feature off stops it holding anything",
      mg.cursor_lock_rect(FS.rect, guard.monitors, [], "somegame.exe",
                          [], [], False, True) is None)

GAME_MON = next(m for m in guard.monitors if not m.primary)
check("a full-screen window on a BLOCKED display never holds the pointer",
      mg.cursor_lock_rect(GAME_MON.rect, guard.monitors, [GAME_MON],
                          "somegame.exe", [], [], True, True) is None)

# The Win+Shift+S snip layer is borderless and stretched across every screen.
# Treating that as a full-screen app held the pointer to whichever display it
# overlapped most, dragging the mouse off whatever the user was pointing at.
ALL = guard.monitors
span = (min(m.rect[0] for m in ALL), min(m.rect[1] for m in ALL),
        max(m.rect[2] for m in ALL), max(m.rect[3] for m in ALL))
ONE = next(m for m in ALL if m.primary)
check("a window across every display is an overlay, not a game",
      mg.spans_displays(span, ALL) > 1)
check("a full-screen game covers exactly one display",
      mg.spans_displays(ONE.rect, ALL) == 1)
check("a screen-spanning overlay never holds the pointer",
      mg.cursor_lock_rect(span, ALL, [], "screenclippinghost.exe",
                          [], [], True, True) is None)
check("a single-display game still holds the pointer",
      mg.cursor_lock_rect(ONE.rect, ALL, [], "somegame.exe",
                          [], [], True, True) == ONE.rect)

# A saved arrangement is slots per application, deliberately interchangeable:
# "two Brave windows go in these two rectangles", not "this window goes here".
# Handles die at reboot and titles change as you browse, and which browser is
# in which slot does not matter - you navigate to what you want anyway.
SLOTS = [{"app": "brave.exe", "rel": [0, 0, 800, 600]},
         {"app": "brave.exe", "rel": [800, 0, 600, 900]},
         {"app": "discord.exe", "rel": [0, 600, 800, 400]}]
check("two windows of one app fill its two slots",
      len(mg.fill_slots(SLOTS, [(1, "brave.exe"), (2, "brave.exe"),
                                (3, "discord.exe")])) == 3)
check("a third window of that app is left alone",
      len(mg.fill_slots(SLOTS, [(1, "brave.exe"), (2, "brave.exe"),
                                (9, "brave.exe")])) == 2)
check("a missing app just leaves its slot empty",
      len(mg.fill_slots(SLOTS, [(1, "brave.exe")])) == 1)
check("each window is used once",
      len(set(h for h, _ in mg.fill_slots(
          SLOTS, [(1, "brave.exe"), (2, "brave.exe")]))) == 2)
check("an app with no slot is not touched",
      mg.fill_slots(SLOTS, [(7, "notepad.exe")]) == [])
check("no windows at all is not an error",
      mg.fill_slots(SLOTS, []) == [])

# A pinned position is stored as an offset within its display, not as a desktop
# coordinate. The same monitor moves around - dragged to the other side in
# Display Settings, plugged into another port, made primary - and every absolute
# coordinate shifts. None of that should lose where you had a window.
PIN_MON = next(m for m in guard.monitors if not m.primary)
win_rect = (PIN_MON.rect[0] + 100, PIN_MON.rect[1] + 50,
            PIN_MON.rect[0] + 900, PIN_MON.rect[1] + 650)
rel = mg.offset_in(win_rect, PIN_MON)
check("an offset is measured from the display corner", rel[:2] == (100, 50))
check("size is preserved in the offset", rel[2:] == (800, 600))
check("putting it back where it was is exact",
      mg.offset_onto(rel, PIN_MON)[:2] == (win_rect[0], win_rect[1]))

# The same offset onto a display that has since moved should follow the display.
class _Moved(object):
    rect = (PIN_MON.rect[0] + 5000, PIN_MON.rect[1],
            PIN_MON.rect[2] + 5000, PIN_MON.rect[3])
    work = (rect[0], rect[1], rect[2], rect[3])
check("a moved display carries the window with it",
      mg.offset_onto(rel, _Moved())[0] == win_rect[0] + 5000)

# A shrunken display must not leave the window off the edge.
class _Small(object):
    rect = (0, 0, 640, 480)
    work = (0, 0, 640, 480)
placed = mg.offset_onto((600, 400, 800, 600), _Small())
check("a window is clamped onto a display that shrank",
      placed[0] >= 0 and placed[1] >= 0
      and placed[0] + placed[2] <= 640 and placed[1] + placed[3] <= 480)

# The rule suggester. Sampling at the wrong moment - with a screen awake behind
# the amp - is how someone ends up with a manual rule when an automatic one was
# available, so that case has to say so rather than just recommending the
# fallback.
check("an interlaced amp is told to use @interlaced",
      mg.suggest_rule("ABC1234", "AMP", True, (1920, 1080, True),
                      1920, 1080)[0] == "ABC1234@interlaced")
check("a small phantom is told to use a size rule",
      mg.suggest_rule("ABC1234", "AMP", True, (1920, 1080, False),
                      800, 600)[0] == "ABC1234@<1280x720")
check("a progressive amp falls back to a plain rule",
      mg.suggest_rule("ABC1234", "AMP", True, (1920, 1080, False),
                      1920, 1080)[0] == "ABC1234")
check("and is told to look again with the screen off",
      "interlaced" in mg.suggest_rule("ABC1234", "AMP", True,
                                      (1920, 1080, False), 1920, 1080)[1])
check("a real display gets no suggestion at all",
      mg.suggest_rule("GSM5BBF", "LG", False, (3840, 2160, False),
                      3840, 2160) == (None, None))
check("an unreadable EDID still yields advice",
      mg.suggest_rule("ABC1234", "AMP", True, None, 1920, 1080)[0] == "ABC1234")

# Some receivers advertise an interlaced preferred timing with nothing behind
# them and a progressive one once a screen wakes up, while Windows reports the
# same resolution for both - so only the EDID shows the difference.
check("the interlaced qualifier parses",
      mg.parse_block_spec("ABC1234@interlaced") == ("ABC1234", None, False, True))
check("a plain rule is not an interlace rule",
      mg.parse_block_spec("ABC1234")[3] is False)
check("a size rule is not an interlace rule",
      mg.parse_block_spec("ABC1234@<1280x720")[3] is False)
check("size rules still parse",
      mg.parse_block_spec("ABC1234@800x600") == ("ABC1234", (800, 600), False, False))
check("preferred timing reads back for a real display",
      (lambda p: p is not None and p[0] > 0 and p[1] > 0)(
          mg.edid_preferred(next(m for m in guard.monitors if m.primary).hwid)))
check("an unknown hardware id has no preferred timing",
      mg.edid_preferred("NOSUCH9") is None)

# A cursor clip outlives whatever set it. A game exits with the pointer still
# confined to where its window was, and deferring to that seals the user inside
# a rectangle with nothing in it - unable to reach the tray icon that would
# undo it. Only believe a clip belongs to someone if the window in front could
# actually have set it.
GAME_WIN = (0, 0, 1920, 1080)
check("a clip inside the foreground window is left alone",
      mg.clip_is_owned((100, 100, 900, 700), GAME_WIN, "game.exe", []))
check("a clip left behind by a closed app is taken back",
      not mg.clip_is_owned((100, 100, 900, 700), (2000, 0, 2500, 400),
                           "game.exe", []))
check("no foreground window means the clip is nobody's",
      not mg.clip_is_owned((100, 100, 900, 700), None, "", []))
check("the shell never vouches for a clip",
      not mg.clip_is_owned((100, 100, 900, 700), GAME_WIN,
                           "explorer.exe", ["explorer.exe"]))
check("a clip larger than the window in front is not its doing",
      not mg.clip_is_owned((0, 0, 3840, 2160), GAME_WIN, "game.exe", []))

# Only one cursor clip exists on the system. A game confining the mouse to its
# window owns that clip, and if its rectangle is already inside the region we
# allow, the fence is satisfied without us touching it. Re-applying ours every
# sweep would widen it back out and let the cursor escape mid-game.
ALLOWED = (-2560, 0, 3840, 2160)
check("a game clip inside the allowed area is left alone",
      mg.rect_within((100, 100, 2000, 1200), ALLOWED))
check("a clip reaching onto a blocked display is not left alone",
      not mg.rect_within((-4480, 0, -2560, 1080), ALLOWED))
check("an unclipped cursor (whole virtual screen) is not left alone",
      not mg.rect_within((-4480, 0, 3840, 2160), ALLOWED))
check("a clip exactly matching the allowed area counts as within",
      mg.rect_within(ALLOWED, ALLOWED))
check("a clip overlapping one edge is not within",
      not mg.rect_within((-3000, 0, 100, 100), ALLOWED))

check("a blank display does not crash the guess",
      mg.looks_like_av_device("", "") is None)

# A three-letter vendor prefix is a guess, and SON would catch a Sony display
# just as readily as a Sony amp. Whoever can see the hardware overrules it, in
# both directions, and both answers have to survive being written to config.
check("a recognised amp can be overruled",
      mg.looks_like_av_device("YMH3148", "HTR-4063", (), ("YMH3148",)) is None)
check("a wrongly caught display can be overruled",
      mg.looks_like_av_device("SON1234", "BRAVIA", (), ("SON1234",)) is None)
check("overruling one id leaves others alone",
      mg.looks_like_av_device("DON0015", "Denon", (), ("YMH3148",)) == "Denon")
check("an unknown amp can still be declared",
      mg.looks_like_av_device("XYZ9999", "Mystery", ("XYZ9999",)) is not None)
check("overrule beats declaration when both name an id",
      mg.looks_like_av_device("XYZ9999", "Mystery",
                              ("XYZ9999",), ("XYZ9999",)) is None)

# --- does it actually start? ------------------------------------------------
#
# Everything above tests logic the tray app never has to be built to exercise.
# An AttributeError in startup once let all of them pass while the program could
# not run at all: under pythonw there is no console, so it died silently with
# nothing in the log. Start it for real and require it to reach the end of its
# startup sequence.
import subprocess
MARKER = "tracking"          # the last line startup logs
log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "logs", "phantommonitor.log")
before = os.path.getsize(log_path) if os.path.exists(log_path) else 0
proc = subprocess.Popen([sys.executable, "phantommonitor.py"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        cwd=os.path.dirname(os.path.abspath(__file__)))
started, crash = False, ""
for _ in range(60):          # up to 15 seconds
    time.sleep(0.25)
    if proc.poll() is not None:
        crash = (proc.stderr.read() or b"").decode("utf-8", "replace")[-600:]
        break
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(before)
            if MARKER in fh.read():
                started = True
                break
    except OSError:
        pass
try:
    proc.terminate()
    proc.wait(timeout=5)
except Exception:
    proc.kill()
check("the app starts and completes its startup sequence", started,
      "" if started else (crash.strip().replace(chr(10), " | ")[-300:] or "timed out"))

root.destroy()
print()
failed = [r for r in results if r[0] == FAIL]
print("%d/%d checks passed" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
