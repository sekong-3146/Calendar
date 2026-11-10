"""Keep a window at the bottom of the z-order (desktop-gadget style). Win32 via ctypes.

Win+D (Show desktop) lifts the desktop window above us. While the desktop is shown we go
TOPMOST, and drop back to the bottom once another app is really back above the desktop.
"""
import ctypes
import os
import time
from ctypes import wintypes
from datetime import datetime

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_WINDOWPOSCHANGING = 0x0046
HWND_TOP, HWND_BOTTOM, HWND_TOPMOST, HWND_NOTOPMOST = 0, 1, -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
GW_HWNDPREV = 3
GA_ROOT, GA_ROOTOWNER = 2, 3
LOG_MAX = 256 * 1024             # pin.log rotates to pin.log.1 above this size
PEEK_GRACE = 1.0                 # seconds a peek survives while the previous app still has focus

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
WM_HOTKEY = 0x0312
_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
_MOD_NOREPEAT = 0x4000
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL


def parse_hotkey(text: str):
    """'ctrl+alt+c' -> (modifier flags, virtual key). Raises ValueError if not understood."""
    parts = [p.strip().lower() for p in text.split("+")]
    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif key is None and p:
            key = p
        else:
            raise ValueError(f"단축키를 해석할 수 없습니다: {text}")
    if not mods or key is None:
        raise ValueError(f"단축키에는 조합 키(ctrl/alt/shift/win)와 키 하나가 필요합니다: {text}")
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        return mods, ord(key.upper())
    if key == "space":
        return mods, 0x20
    if key[0] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return mods, 0x70 + int(key[1:]) - 1
    raise ValueError(f"지원하지 않는 키입니다: {key} (A-Z, 0-9, F1-F24, space)")


def register_hotkey(hwnd: int, hotkey_id: int, text: str):
    """System-wide hotkey delivered to hwnd as WM_HOTKEY. Raises ValueError / OSError."""
    mods, vk = parse_hotkey(text)
    if not user32.RegisterHotKey(hwnd, hotkey_id, mods | _MOD_NOREPEAT, vk):
        raise ctypes.WinError(ctypes.get_last_error())


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

    def __init__(self, hwnd: int, on_raised_changed=None, log_path=None, on_log_error=None,
                 verbose=False):
        self.hwnd = hwnd
        self.enabled = True
        self.raised = False              # True while lifted (Win+D desktop shown, or peek)
        self.on_raised_changed = on_raised_changed
        self.log_path = log_path         # raise/lower actions and failures
        self.on_log_error = on_log_error
        self.verbose = verbose           # also log every foreground/desktop state change
        self._last_state = None
        self._retries = 0                # TOPMOST re-applied this many times since the last raise
        self._raise_failed = False       # gave up; no new raise until the desktop is no longer above
        self._peek_fg = None             # foreground window when peek started (no lower while it stays)
        self._peek_until = 0.0           # ...but only this long: activation may fail or be missed
        self._dismissed = False          # user lowered us by hotkey; no auto raise until desktop is gone

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
        if not above:
            self._raise_failed = self._dismissed = False
        if self._peek_fg is not None and (user32.GetForegroundWindow() != self._peek_fg
                                          or time.monotonic() > self._peek_until):
            self._peek_fg = None         # focus moved (or never came to us): normal rules again
        action = ""
        if not self.raised:
            # Raise unless a real app is visible in front (focus may stay on taskbar or a covered app).
            if above and not (app_fg and not hidden) and not self._raise_failed and not self._dismissed:
                action = "RAISE"
        elif app_fg and not hidden and self._peek_fg is None:
            action = "LOWER"
        self._log(cls, ours, above, hidden, action)
        if action:
            self._set_raised(action == "RAISE")
        elif self.raised and not is_topmost(self.hwnd):
            self._retry_raise()

    def peek(self, on: bool):
        """Lift above all windows on request (hotkey). Stays up until focus moves to another app."""
        if on == self.raised:
            return
        self._peek_fg = user32.GetForegroundWindow() if on else None
        self._peek_until = time.monotonic() + PEEK_GRACE
        self._dismissed = not on         # lowered on request: stay down even if the desktop is shown
        self._write(self._stamp() + (" PEEK\n" if on else " PEEK END\n"))
        self._set_raised(on)

    def activated(self):
        """Our window became active (called from the window's activation event).
        From now on focus moving to any app lowers us, even the app that was in front before."""
        self._peek_fg = None

    def _retry_raise(self):
        """We believe we are raised but the window is not topmost: re-apply, at most 3 times."""
        self._retries += 1
        if self._retries > 3:
            self._write(self._stamp() + " RAISE FAILED after 3 retries: giving up until the desktop is hidden\n")
            self._raise_failed = True
            self._peek_fg = None
            self._set_raised(False)
            return
        ok = set_topmost(self.hwnd, True)
        self._write(self._stamp() + f" RETRY {self._retries} ok={ok} topmost_after={int(is_topmost(self.hwnd))}\n")

    @staticmethod
    def _stamp():
        return f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3]

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
        if not self.log_path or not (action or self.verbose):
            return
        # Window-list details are costly, so only collect them in verbose mode.
        diag = self.diag() if self.verbose else None
        state = (cls, ours, above, hidden, diag, self.raised)
        if state == self._last_state and not action:
            return
        after = (action == "RAISE") if action else self.raised
        self._last_state = (cls, ours, above, hidden, diag, after)   # state after this poll's action
        line = (self._stamp()
                + f" fg={cls} ours={ours} desk_above={above} fg_hidden={hidden} raised={self.raised}"
                + (f" above={diag[0]} workerw={diag[1]} defview={diag[2]} topmost={diag[3]}" if diag else "")
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
        self._retries = 0
        ctypes.set_last_error(0)
        ok = set_topmost(self.hwnd, on)
        after = is_topmost(self.hwnd)
        if self.verbose or not ok or after != on:      # normally silent; always report a mismatch
            self._write(self._stamp() + f" SetWindowPos({'TOPMOST' if on else 'NOTOPMOST'}) ok={ok}"
                        + f" err={ctypes.get_last_error()} topmost_after={int(after)}\n")
        if not on:
            self.send_to_bottom()
        if self.on_raised_changed:
            self.on_raised_changed(on)
