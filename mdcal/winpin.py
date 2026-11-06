"""Keep a window at the bottom of the z-order (desktop-gadget style). Win32 via ctypes."""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_WINDOWPOSCHANGING = 0x0046
HWND_BOTTOM = 1
HWND_TOP = 0
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
GA_ROOT = 2
_DESKTOP_CLASSES = {"Progman", "WorkerW"}


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


class BottomPin:
    """Call handle(msg_ptr) from QWidget.nativeEvent; call check_covered() periodically."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.enabled = True
        self._raising = False

    def handle(self, msg_ptr: int):
        if not self.enabled or self._raising:
            return
        msg = wintypes.MSG.from_address(msg_ptr)
        if msg.message == WM_WINDOWPOSCHANGING and msg.lParam:
            pos = WINDOWPOS.from_address(msg.lParam)
            if not pos.flags & SWP_NOZORDER:
                pos.hwndInsertAfter = HWND_BOTTOM

    def send_to_bottom(self):
        user32.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def check_covered(self):
        """After Win+D the desktop window covers us; raise just above it (no activation)."""
        if not self.enabled:
            return False
        rc = wintypes.RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(rc)):
            return False
        # physical pixels, sample the center
        hit = user32.WindowFromPoint(wintypes.POINT((rc.left + rc.right) // 2, (rc.top + rc.bottom) // 2))
        root = user32.GetAncestor(hit, GA_ROOT) if hit else None
        if not root or root == self.hwnd:
            return False
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(root, buf, 64)
        if buf.value not in _DESKTOP_CLASSES:
            return False
        self._raising = True
        try:
            user32.SetWindowPos(self.hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        finally:
            self._raising = False
        return True
