"""App handlers, called with a stand-in for the App object (no tray, registry or lock file)."""
import tests  # noqa: F401  (sandbox first: must precede every mdcal import)
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QMessageBox

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


if __name__ == "__main__":
    unittest.main()
