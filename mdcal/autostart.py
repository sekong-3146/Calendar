"""Start with Windows via HKCU Run key (per-user, no admin)."""
import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MdCal"


def command() -> str:
    """pythonw (no console) + MdCal.pyw of this checkout."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    script = Path(__file__).resolve().parent.parent / "MdCal.pyw"
    return f'"{pyw if pyw.exists() else exe}" "{script}"'


def registered(key=RUN_KEY):
    """Current Run value or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, VALUE_NAME)[0]
    except FileNotFoundError:
        return None


def set_enabled(on: bool, key=RUN_KEY):
    """Return False if already off, True on success; raise OSError on failure."""
    if not on and registered(key) is None:
        return False
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, VALUE_NAME)
            except FileNotFoundError:
                return False     # Removed by another process after the check.
    return True


def refresh(key=RUN_KEY):
    """If enabled but pointing at an old path (project moved), rewrite it. Returns True if changed."""
    cur = registered(key)
    if cur is not None and cur != command():
        return set_enabled(True, key)
    return False
