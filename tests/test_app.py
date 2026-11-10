"""App handlers, called with a stand-in for the App object (no tray, registry or lock file)."""
import tests  # noqa: F401  (sandbox first: must precede every mdcal import)
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QMessageBox

from mdcal import app as app_module
from mdcal.app import App
from mdcal.memofile import MemoFiles
from tests import qapp

D10 = date(2026, 11, 10)


class QuickAddTest(unittest.TestCase):
    def setUp(self):
        qapp()
        self.dir = Path(tempfile.mkdtemp(prefix="mdcal_app_"))
        self.warnings = []
        patcher = mock.patch.object(QMessageBox, "warning", lambda *a, **k: self.warnings.append(a[2]))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.detail = SimpleNamespace(isVisible=lambda: False, day=None, appended=[])
        self.detail.append_task = lambda text: self.detail.appended.append(text) or True
        self.refreshed = []
        self.app = SimpleNamespace(memos=MemoFiles(str(self.dir)), detail=self.detail, win=None,
                                   index=SimpleNamespace(refresh_memos=lambda: self.refreshed.append(1)))

    def add(self, text):
        App._on_task_added(self.app, D10, text)

    def test_creates_and_appends_to_the_memo_file(self):
        self.add("우유 사기")
        self.add("메일 답장")
        self.assertEqual((self.dir / "261110.md").read_text("utf-8"),
                         "---\ndate: 2026-11-10\n---\n- [ ] 우유 사기\n- [ ] 메일 답장\n")
        self.assertEqual((self.warnings, len(self.refreshed)), ([], 2))

    def test_goes_to_the_open_day_window_instead_of_the_file(self):
        self.detail.isVisible, self.detail.day = (lambda: True), D10
        self.add("a")
        self.assertEqual(self.detail.appended, ["a"])
        self.assertFalse((self.dir / "261110.md").exists())

    def test_failure_shows_the_typed_text(self):
        (self.dir / "261110.md").write_bytes(b"\xff\xfe\xfd broken \x81")
        self.add("잃으면 안 되는 내용")
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("잃으면 안 되는 내용", self.warnings[0])
        self.assertEqual((self.dir / "261110.md").read_bytes(), b"\xff\xfe\xfd broken \x81")

    def test_no_memo_folder(self):
        self.app.memos = MemoFiles(None)
        self.add("a")
        self.assertIn("a", self.warnings[0])


class ExceptHookTest(unittest.TestCase):
    def test_errors_raised_while_the_box_is_open_are_only_logged(self):
        tests.assert_sandboxed()
        qapp()
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        app_module.LOG_PATH.unlink(missing_ok=True)
        app_module._error_counts.clear()
        shown = []

        def critical(parent, title, text):
            shown.append(text)
            if len(shown) < 5:                       # a timer firing while the box is open
                self.raise_into_hook("second")
        with mock.patch.object(QMessageBox, "critical", critical):
            self.raise_into_hook("first")
            self.raise_into_hook("third")            # the box was closed: shown again
        self.assertEqual(len(shown), 2)
        self.assertIn("first", shown[0])
        log = app_module.LOG_PATH.read_text("utf-8")
        self.assertEqual(self.entries(), 4)          # all four errors are logged

    def test_a_repeating_error_is_shown_once_and_logged_a_few_times(self):
        tests.assert_sandboxed()
        qapp()
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        app_module.LOG_PATH.unlink(missing_ok=True)
        app_module._error_counts.clear()
        shown = []
        with mock.patch.object(QMessageBox, "critical", lambda parent, title, text: shown.append(text)):
            for _ in range(50):                      # e.g. a paint or 300 ms timer error
                self.raise_into_hook("every tick")
            self.raise_into_hook("something else")
        self.assertEqual(len(shown), 2)
        self.assertEqual(self.entries(), app_module.ERROR_LOG_REPEATS + 1)

    @staticmethod
    def entries():
        log = app_module.LOG_PATH.read_text("utf-8")
        return log.count("\n--- ") + log.startswith("--- ")

    @staticmethod
    def raise_into_hook(message):
        try:
            raise RuntimeError(message)
        except RuntimeError as e:
            app_module._excepthook(type(e), e, e.__traceback__)


if __name__ == "__main__":
    unittest.main()
