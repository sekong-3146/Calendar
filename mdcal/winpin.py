"""Keep a window at the bottom of the z-order (desktop-gadget style). Win32 via ctypes.

Win+D (Show desktop) lifts the desktop window above us. While the desktop is shown we go
TOPMOST, and drop back to the bottom once another app is really back above the desktop.
"""
import ctypes
import os
from ctypes import wintypes
from datetime import datetime

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_WINDOWPOSCHANGING = 0x0046
HWND_TOP, HWND_BOTTOM, HWND_TOPMOST, HWND_NOTOPMOST = 0, 1, -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
GW_HWNDPREV = 3
GA_ROOT, GA_ROOTOWNER = 2, 3
LOG_MAX = 256 * 1024             # pin.log rotates to pin.log.1 above this size

DESKTOP_CLASSES = {"Progman", "WorkerW"}
# Foreground windows that do not end the "desktop shown" state (taskbar, Start, lock screen ...)
SHELL_CLASSES = DESKTOP_CLASSES | {
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland", "Windows.UI.Core.CoreWindow",
    "XamlExplorerHostIslandWindow", "TaskListThumbnailWnd", "#32768"}


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x0008

# NOOWNERZORDER is required: once the window has had focus it owns a hidden "IME" window,
# and without this flag HWND_TOPMOST/HWND_TOP return success but do nothing.
SWP_NOOWNERZORDER = 0x0200
_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
_PID = os.getpid()


WM_EXITSIZEMOVE = 0x0232         # user finished dragging or resizing the window


def message_id(msg_ptr: int) -> int:
    """Message number of the MSG passed to QWidget.nativeEvent."""
    return wintypes.MSG.from_address(msg_ptr).message


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def window_pid(hwnd) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def set_topmost(hwnd: int, on: bool) -> bool:
    return bool(user32.SetWindowPos(hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0, _FLAGS))


def restore_no_activate(hwnd: int):
    """Un-minimize without taking focus (SW_SHOWNOACTIVATE)."""
    user32.ShowWindow(hwnd, 4)


def is_topmost(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


class BottomPin:
    """handle(msg_ptr) from QWidget.nativeEvent; poll() every ~300 ms."""

    def __init__(self, hwnd: int, on_raised_changed=None, log_path=None, on_log_error=None):
        self.hwnd = hwnd
        self.enabled = True
        self.raised = False              # True while Win+D desktop is shown
        self.on_raised_changed = on_raised_changed
        self.log_path = log_path         # state transitions for diagnosing Win+D misses
        self.on_log_error = on_log_error
        self._last_state = None

    def handle(self, msg_ptr: int):
        if not self.enabled or self.raised:
            return
        msg = wintypes.MSG.from_address(msg_ptr)
        if msg.message == WM_WINDOWPOSCHANGING and msg.lParam:
            pos = WINDOWPOS.from_address(msg.lParam)
            if not pos.flags & SWP_NOZORDER:
                pos.hwndInsertAfter = HWND_BOTTOM

    def send_to_bottom(self):
        user32.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0, _FLAGS)

    @staticmethod
    def _under_desktop(hwnd) -> bool:
        """A visible Progman/WorkerW sits above hwnd in z-order."""
        w = hwnd
        while True:
            w = user32.GetWindow(w, GW_HWNDPREV)
            if not w:
                return False
            if user32.IsWindowVisible(w) and class_name(w) in DESKTOP_CLASSES:
                return True

    def desktop_above(self) -> bool:
        """Show desktop is active over us."""
        return self._under_desktop(self.hwnd)

    def foreground_hidden(self) -> bool:
        """Foreground app is covered by the shown desktop (Win+D left focus on it)."""
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        return self._under_desktop(user32.GetAncestor(fg, GA_ROOT) or fg)

    def foreground(self):
        """(class of root owner, is_our_process) of the foreground window, or (None, False)."""
        fg = user32.GetForegroundWindow()
        if not fg:
            return None, False
        root = user32.GetAncestor(fg, GA_ROOTOWNER) or fg
        return class_name(root), window_pid(root) == _PID

    def poll(self):
        if not self.enabled:
            return
        cls, ours = self.foreground()
        above = self.desktop_above()
        hidden = self.foreground_hidden() if cls is not None and not ours else False
        app_fg = cls is not None and not ours and cls not in SHELL_CLASSES
        action = ""
        if not self.raised:
            # Raise unless a real app is visible in front (focus may stay on taskbar or a covered app).
            if above and not (app_fg and not hidden):
                action = "RAISE"
        elif app_fg and not hidden:
            action = "LOWER"
        self._log(cls, ours, above, hidden, action)
        if action:
            self._set_raised(action == "RAISE")

    def diag(self):
        """Log-only: (class of nearest visible window above us, visible WorkerW count,
        class hosting the icon view). Tells Win+D apart from a plain desktop click."""
        w, above = self.hwnd, None
        while above is None:
            w = user32.GetWindow(w, GW_HWNDPREV)
            if not w:
                break
            if user32.IsWindowVisible(w):
                above = class_name(w)
        workers, host = 0, None
        for cls in ("Progman", "WorkerW"):
            w = None
            while True:
                w = user32.FindWindowExW(None, w, cls, None)
                if not w:
                    break
                if cls == "WorkerW" and user32.IsWindowVisible(w):
                    workers += 1
                if host is None and user32.FindWindowExW(w, None, "SHELLDLL_DefView", None):
                    host = cls
        return above, workers, host, int(is_topmost(self.hwnd))

    def _log(self, cls, ours, above, hidden, action):
        if not self.log_path:
            return
        diag = self.diag()
        state = (cls, ours, above, hidden, diag, self.raised)
        if state == self._last_state and not action:
            return
        after = (action == "RAISE") if action else self.raised
        self._last_state = (cls, ours, above, hidden, diag, after)   # state after this poll's action
        line = (f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3]
                + f" fg={cls} ours={ours} desk_above={above} fg_hidden={hidden} raised={self.raised}"
                + f" above={diag[0]} workerw={diag[1]} defview={diag[2]} topmost={diag[3]}"
                + (f" -> {action}" if action else "") + "\n")
        self._write(line)

    def _write(self, line):
        if not self.log_path:
            return
        try:
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > LOG_MAX:
                os.replace(self.log_path, str(self.log_path) + ".1")
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            self.log_path = None         # diagnostics only: stop logging, report once
            if self.on_log_error:
                self.on_log_error(e)

    def _set_raised(self, on: bool):
        self.raised = on
        ctypes.set_last_error(0)
        ok = set_topmost(self.hwnd, on)
        self._write(f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3]
                    + f" SetWindowPos({'TOPMOST' if on else 'NOTOPMOST'}) ok={ok}"
                    + f" err={ctypes.get_last_error()} topmost_after={int(is_topmost(self.hwnd))}\n")
        if not on:
            self.send_to_bottom()
        if self.on_raised_changed:
            self.on_raised_changed(on)
