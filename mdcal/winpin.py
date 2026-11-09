"""Keep a window at the bottom of the z-order (desktop-gadget style). Win32 via ctypes.

Win+D (Show desktop) lifts the desktop window above us. While the desktop is shown we go
TOPMOST, and drop back to the bottom once another app takes the foreground.
"""
import ctypes
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_WINDOWPOSCHANGING = 0x0046
HWND_TOP, HWND_BOTTOM, HWND_TOPMOST, HWND_NOTOPMOST = 0, 1, -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
GW_HWNDPREV = 3
GA_ROOTOWNER = 3

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

_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
_PID = os.getpid()


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def window_pid(hwnd) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def set_topmost(hwnd: int, on: bool):
    user32.SetWindowPos(hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0, _FLAGS)


class BottomPin:
    """handle(msg_ptr) from QWidget.nativeEvent; poll() every ~300 ms."""

    def __init__(self, hwnd: int, on_raised_changed=None):
        self.hwnd = hwnd
        self.enabled = True
        self.raised = False              # True while Win+D desktop is shown
        self.on_raised_changed = on_raised_changed

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

    def desktop_above(self) -> bool:
        """A visible Progman/WorkerW sits above us in z-order (Show desktop active)."""
        w = self.hwnd
        while True:
            w = user32.GetWindow(w, GW_HWNDPREV)
            if not w:
                return False
            if user32.IsWindowVisible(w) and class_name(w) in DESKTOP_CLASSES:
                return True

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
        if not self.raised:
            if (ours or cls in DESKTOP_CLASSES) and self.desktop_above():
                self._set_raised(True)
        elif cls is not None and not ours and cls not in SHELL_CLASSES:
            self._set_raised(False)

    def _set_raised(self, on: bool):
        self.raised = on
        set_topmost(self.hwnd, on)
        if not on:
            self.send_to_bottom()
        if self.on_raised_changed:
            self.on_raised_changed(on)
