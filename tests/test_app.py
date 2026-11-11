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


class ExportTest(unittest.TestCase):
    START, END = date(2026, 11, 3), D10

    def setUp(self):
        qapp()
        self.dir = Path(tempfile.mkdtemp(prefix="mdcal_export_"))
        self.shown = {"warning": [], "information": [], "question": []}
        self.answer = QMessageBox.Yes
        for kind in self.shown:
            result = (lambda *a, **k: self.answer) if kind == "question" else (lambda *a, **k: None)
            patcher = mock.patch.object(
                QMessageBox, kind,
                lambda *a, _kind=kind, _result=result, **k: self.shown[_kind].append(a[2]) or _result())
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = self.dir / "업무정리 261103-261110.md"
        self.asked = []

        def save_name(parent, title, suggested, filt):
            self.asked.append(suggested)
            return (str(self.target) if self.target else ""), filt
        patcher = mock.patch.object(app_module.QFileDialog, "getSaveFileName", save_name)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(app_module, "update_config",
                                    lambda cfg, key, value: cfg.__setitem__(key, value))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.memo = "- [x] 보고서"
        self.cleared, self.tray = [], []
        self.scans = []
        index = SimpleNamespace(
            rescan=lambda: self.scans.append(1), notes=lambda d: [], missing=[], scan_errors=[],
            memo_note=lambda d: SimpleNamespace(body=self.memo, error=None) if d == D10 else None)
        self.detail = SimpleNamespace(isVisible=lambda: False, has_unsaved=False, flush=lambda: None)
        self.app = SimpleNamespace(
            cfg={"export_folder": None}, index=index, detail=self.detail,
            win=SimpleNamespace(grid=SimpleNamespace(clear_range=lambda: self.cleared.append(1))),
            tray=SimpleNamespace(showMessage=lambda title, text: self.tray.append(text)))
        self.app._set_cfg = lambda *a: App._set_cfg(self.app, *a)
        self.app._export_report = lambda a, b: App._export_report(self.app, a, b)

    def export(self):
        App._export_report(self.app, self.START, self.END)

    def test_writes_the_file_and_remembers_the_folder(self):
        self.export()
        self.assertIn("  - 보고서", self.target.read_text("utf-8"))
        self.assertEqual(Path(self.asked[0]).name, "업무정리 261103-261110.md")
        self.assertEqual(self.app.cfg["export_folder"], str(self.dir))
        self.assertEqual((len(self.cleared), len(self.tray)), (1, 1))
        self.export()                                # the next dialog starts in that folder
        self.assertEqual(Path(self.asked[1]).parent, self.dir)

    def test_nothing_to_export_asks_for_no_file(self):
        self.memo = "그냥 메모"
        self.export()
        self.assertEqual((self.asked, len(self.shown["information"])), ([], 1))

    def test_rescans_first_and_reports_what_could_not_be_checked(self):
        self.app.index.scan_errors.append(r"C:\vault\잠김: denied")
        self.export()
        self.assertEqual(len(self.scans), 1)
        self.assertIn("## 확인하지 못한 곳\n- C:\\vault\\잠김: denied", self.target.read_text("utf-8"))
        self.assertIn("읽지 못한 곳 1건", self.tray[0])
        self.memo = "그냥 메모"                       # nothing found AND a folder unread: a warning, not "none"
        self.export()
        self.assertEqual((len(self.shown["warning"]), self.shown["information"]), (1, []))
        self.assertIn("잠김", self.shown["warning"][0])

    def test_cancelled_dialog_keeps_the_selection(self):
        self.target = None
        self.export()
        self.assertEqual((self.cleared, self.tray, list(self.dir.iterdir())), ([], [], []))

    def test_unsaved_memo_stops_the_export(self):
        self.detail.isVisible, self.detail.has_unsaved = (lambda: True), True
        self.export()
        self.assertEqual((self.asked, len(self.shown["warning"])), ([], 1))

    def test_a_date_led_name_needs_confirmation(self):
        self.target = self.dir / "261110 정리.md"
        self.answer = QMessageBox.No
        self.export()
        self.assertFalse(self.target.exists())
        self.answer = QMessageBox.Yes
        self.export()
        self.assertTrue(self.target.exists())
        self.assertEqual(len(self.shown["question"]), 2)

    def test_save_failure_is_reported_and_keeps_the_selection(self):
        with mock.patch.object(app_module.report, "replace_retry", side_effect=PermissionError("locked")):
            self.export()
        self.assertEqual((len(self.shown["warning"]), self.cleared, self.tray), (1, [], []))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_month_ranges(self):
        got = []
        self.app._export_report = lambda a, b: got.append((a, b))
        with mock.patch.object(app_module, "date", wraps=date) as fake:
            fake.today.return_value = date(2027, 1, 15)
            App._export_month(self.app, 0)
            App._export_month(self.app, 1)
            App._export_month(self.app, 2)
        self.assertEqual(got, [(date(2027, 1, 1), date(2027, 1, 15)),
                               (date(2026, 12, 1), date(2026, 12, 31)),
                               (date(2026, 11, 1), date(2026, 11, 30))])


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
