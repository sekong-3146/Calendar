"""Run from the project root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -t .

Tests never touch the real %APPDATA%\\MdCal or a real screen: APPDATA points at a temp
folder and Qt uses the offscreen platform. Both must be set before mdcal is imported,
so every test module starts with `import tests`.
"""
import os
import sys
import tempfile

if "mdcal.config" in sys.modules:
    # Too late: mdcal.config already captured the real APPDATA. Refuse to run rather than
    # let a test overwrite the user's real config.json.
    raise RuntimeError("mdcal was imported before the test sandbox; start test modules with 'import tests'")

SANDBOX = tempfile.mkdtemp(prefix="mdcal_test_appdata_")
os.environ["APPDATA"] = SANDBOX
os.environ["QT_QPA_PLATFORM"] = "offscreen"

_app = None


def qapp():
    """One QApplication for the whole test run."""
    global _app
    from PySide6.QtWidgets import QApplication
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


def pump(seconds=0.4):
    """Process Qt events for a while (the file watcher reports from another thread)."""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp().processEvents()
        time.sleep(0.01)


def assert_sandboxed():
    """Call before a test writes config: fails if mdcal would write outside the sandbox."""
    from mdcal import config
    if not str(config.CONFIG_PATH).startswith(SANDBOX):
        raise RuntimeError(f"config path is outside the test sandbox: {config.CONFIG_PATH}")
