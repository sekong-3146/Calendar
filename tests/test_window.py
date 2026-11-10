"""Calendar window, day window and config behavior (offscreen, temp APPDATA)."""
import tests  # noqa: F401  (sandbox first: must precede every mdcal import)
import ctypes
import unittest
from ctypes import wintypes
from datetime import date
from unittest import mock

from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QMessageBox

from mdcal import config, detail, grid, winpin
from tests import qapp

D10 = date(2026, 11, 10)


class FakeStore:
    def colors(self, first, last):
        return {}


class FakeIndex:
    def memo_note(self, day):
        return None

    def notes(self, day):
        return []


class Base(unittest.TestCase):
    def setUp(self):
        tests.assert_sandboxed()
        qapp()
        self.warnings = []
        patcher = mock.patch.object(QMessageBox, "warning", lambda *a, **k: self.warnings.append(a[2]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def break_saving(self):
        patcher = mock.patch.object(config, "save_config", side_effect=PermissionError("disk says no"))
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher


class ConfigTest(Base):
    def test_update_config_saves(self):
        cfg = config._defaults()
        config.update_config(cfg, "opacity", 0.6)
        self.assertEqual(config.load_config()[0]["opacity"], 0.6)

    def test_update_config_rolls_back_on_failure(self):
        cfg = config._defaults()
        self.break_saving()
        with self.assertRaises(OSError):
            config.update_config(cfg, "opacity", 0.4)
        self.assertEqual(cfg["opacity"], 0.80)
        with self.assertRaises(OSError):
            config.update_config(cfg, "brand_new", 1)
        self.assertNotIn("brand_new", cfg)

    def test_wrong_types_in_the_file_fall_back_to_defaults_with_a_warning(self):
        import json
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        config.CONFIG_PATH.write_text(json.dumps({
            "opacity": "abc", "geometry": ["a", 1, 2, 3], "md_folders": None, "locked": 1,
            "carry_mode": "sometimes", "carry_lookback_days": True, "peek_hotkey": 5,
            "week_starts_sunday": False, "last_carry": "2026-11-10", "unknown": 1}), "utf-8")
        cfg, warn = config.load_config()
        defaults = config._defaults()
        for key in ("opacity", "geometry", "md_folders", "locked", "carry_mode",
                    "carry_lookback_days", "peek_hotkey"):
            self.assertEqual(cfg[key], defaults[key], key)
            self.assertIn(key, warn)
        self.assertEqual((cfg["week_starts_sunday"], cfg["last_carry"]), (False, "2026-11-10"))
        self.assertNotIn("week_starts_sunday", warn)
        self.assertNotIn("unknown", cfg)

    def test_a_saved_config_loads_back_without_warning(self):
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        cfg = config._defaults()
        cfg.update(geometry=[10, 20, 760, 560], opacity=1, memo_folder="C:\\x", md_folders=["C:\\y"])
        config.save_config(cfg)
        self.assertEqual(config.load_config(), (cfg, None))


class WindowTest(Base):
    def setUp(self):
        super().setUp()
        self.cfg = config._defaults()
        self.cfg["geometry"] = None
        self.win = grid.CalendarWindow(self.cfg, FakeStore(), FakeIndex())
        self.win.year, self.win.month = 2026, 11
        self.win.refresh()
        self.addCleanup(self.win.deleteLater)

    def move(self, x):
        self.win.setGeometry(x, 110, 500, 400)
        self.win._save_geo.stop()

    def native(self, message):
        msg = wintypes.MSG()
        msg.message = message
        self.win.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))

    def test_timer_save_during_hold_is_retried_not_dropped(self):
        self.move(100)
        self.win._hold_save.start()
        self.win._store_geometry()
        self.assertIsNone(self.cfg["geometry"])
        self.assertTrue(self.win._save_geo.isActive())

    def test_user_drag_end_saves_at_once_even_during_hold(self):
        self.move(120)
        self.win._hold_save.start()
        self.native(winpin.WM_EXITSIZEMOVE)
        self.assertEqual(self.cfg["geometry"], [120, 110, 500, 400])
        self.move(130)
        self.native(0x0003)              # WM_MOVE must not save
        self.assertEqual(self.cfg["geometry"], [120, 110, 500, 400])

    def test_geometry_save_failure_warns_once_and_keeps_old_value(self):
        self.move(120)
        self.win._store_geometry()
        self.break_saving()
        for x in (140, 150, 160):
            self.move(x)
            self.win._store_geometry()
        self.assertEqual(self.cfg["geometry"], [120, 110, 500, 400])
        self.assertEqual(len(self.warnings), 1)

    def test_minimized_geometry_is_not_saved(self):
        self.move(120)
        with mock.patch.object(self.win, "isMinimized", return_value=True):
            self.win._store_geometry(by_user=True)
        self.assertIsNone(self.cfg["geometry"])

    def test_lock_and_opacity_failure_changes_nothing(self):
        self.break_saving()
        self.win.set_locked(True)
        self.win.set_opacity(0.4)
        self.assertEqual((self.cfg["locked"], self.cfg["opacity"]), (False, 0.80))
        self.assertFalse(self.win.grip.isHidden())
        self.assertEqual(len(self.warnings), 2)

    def test_screen_signal_is_connected_once(self):
        calls = []

        class Counting(grid.CalendarWindow):         # other test windows watch the same screen
            def _on_screens_changed(self, *args):
                calls.append(args)

        win = Counting(self.cfg, FakeStore(), FakeIndex())
        screen = QGuiApplication.primaryScreen()
        for _ in range(3):
            win._watch_screen(screen)
        screen.geometryChanged.emit(QRect(0, 0, 10, 10))
        win.deleteLater()
        self.assertEqual(len(calls), 1)

    # quick add
    def click(self, day):
        g = self.win.grid
        pos = g.cell_rect(g.cells.index(day)).center()
        g.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pos, g.mapToGlobal(pos),
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    def test_quick_add_emits_task_and_stays_open(self):
        g = self.win.grid
        g.resize(700, 600)
        added = []
        g.taskAdded.connect(lambda d, text: added.append((d, text)))
        self.click(D10)
        self.assertEqual(g.selected, D10)
        self.assertFalse(g._quick.isHidden())
        cell = g.cell_rect(g.cells.index(D10)).toRect()
        self.assertTrue(cell.contains(g._quick.geometry()))
        g._quick.setText("  우유 사기 ")
        g._quick.returnPressed.emit()
        g._quick.returnPressed.emit()                # empty box: nothing added
        self.assertEqual(added, [(D10, "우유 사기")])
        self.assertEqual((g._quick.text(), g.selected), ("", D10))

    def test_quick_add_escape_and_month_change_close_it(self):
        g = self.win.grid
        self.click(D10)
        g.eventFilter(g._quick, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertTrue(g._quick.isHidden() and g.selected is None)
        self.click(D10)
        self.win.shift_month(3)                      # that day is no longer shown
        self.assertTrue(g._quick.isHidden() and g.selected is None)

    def test_unsent_draft_does_not_follow_to_another_day(self):
        g = self.win.grid
        added = []
        g.taskAdded.connect(lambda d, text: added.append((d, text)))
        self.click(D10)
        g._quick.setText("draft for the 10th")
        self.click(date(2026, 11, 11))               # no Enter in between
        self.assertEqual((g.selected, g._quick.text()), (date(2026, 11, 11), ""))
        g._quick.returnPressed.emit()
        self.assertEqual(added, [])
        g._quick.setText("same day")                 # clicking the same cell again keeps the draft
        self.click(date(2026, 11, 11))
        self.assertEqual(g._quick.text(), "same day")

    def test_quick_add_can_be_disabled(self):
        self.win.grid.quick_add = False
        self.click(D10)
        self.assertIsNone(self.win.grid.selected)


class BadPath:
    error = PermissionError("locked")

    def stat(self):
        raise BadPath.error

    def __str__(self):
        return r"X:\memo\261110.md"


class FakeDoc:
    body, mtime, error = "hello", 111.0, None


class FakeMemos:
    enabled = True

    def __init__(self):
        self.reads = 0

    def read(self, day):
        self.reads += 1
        return FakeDoc()

    def path(self, day):
        return BadPath()

    def display_name(self, day):
        return "memo/261110.md"


class DetailTest(Base):
    def setUp(self):
        super().setUp()
        self.memos = FakeMemos()
        self.detail = detail.DayDetail(self.memos, FakeIndex())
        self.detail.show_day(D10)
        self.addCleanup(self.close)

    def close(self):
        self.detail._dirty = False
        self.detail._save_timer.stop()
        self.detail.close()
        self.detail.deleteLater()

    def test_reload_notes_reports_stat_error_once_and_keeps_text(self):
        BadPath.error = PermissionError("locked")
        reads = self.memos.reads
        self.detail.reload_notes()
        self.detail.reload_notes()
        self.assertEqual((len(self.warnings), self.memos.reads), (1, reads))
        self.assertEqual(self.detail.memo.toPlainText(), "hello")

    def test_reload_notes_treats_missing_file_as_changed(self):
        BadPath.error = FileNotFoundError("gone")
        reads = self.memos.reads
        self.detail.reload_notes()
        self.assertEqual((self.memos.reads, self.warnings), (reads + 1, []))

    def test_cell_checkbox_does_not_edit_inside_a_code_block(self):
        self.detail.memo.setPlainText("```\n- [ ] code\n```\n- [ ] real")
        self.assertFalse(self.detail.toggle_task_at(1, "- [ ] code"))
        self.assertEqual(len(self.warnings), 1)
        self.assertTrue(self.detail.toggle_task_at(3, "- [ ] real"))
        self.assertEqual(self.detail.memo.toPlainText(), "```\n- [ ] code\n```\n- [x] real")
        self.assertFalse(self.detail.toggle_task_at(9, "- [ ] nowhere"))          # past the end

    def test_append_task_is_one_undo_step(self):
        self.assertTrue(self.detail.append_task("우유 사기"))
        self.assertEqual(self.detail.memo.toPlainText(), "hello\n- [ ] 우유 사기")
        self.assertTrue(self.detail.has_unsaved)
        self.detail.memo.undo()
        self.assertEqual(self.detail.memo.toPlainText(), "hello")

    def test_append_task_keeps_text_as_typed(self):
        self.detail.append_task("보고서 (→ 11/12)")
        self.assertEqual(self.detail.memo.toPlainText(), "hello\n- [ ] 보고서 (→ 11/12)")

    def test_append_task_refused_when_read_only(self):
        self.detail.memo.setReadOnly(True)
        self.assertFalse(self.detail.append_task("x"))
        self.assertEqual(self.detail.memo.toPlainText(), "hello")


if __name__ == "__main__":
    unittest.main()
