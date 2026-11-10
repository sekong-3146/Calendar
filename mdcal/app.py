"""Entry point: single instance, tray menu, wiring."""
import os
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QLockFile, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart, carry, tasks
from .config import APP_DIR, DB_PATH, LOCK_PATH, load_config, save_config, update_config
from .detail import DayDetail
from .grid import CalendarWindow
from .mdsource import MdIndex
from .memofile import MemoConflict, MemoFiles
from .store import Store

LOG_PATH = APP_DIR / "error.log"


def _excepthook(etype, value, tb):
    """pythonw has no console: log and show every unhandled error."""
    text = "".join(traceback.format_exception(etype, value, tb))
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")
    except OSError as e:
        text += f"\n(로그 기록 실패: {e})"
    if QApplication.instance():
        QMessageBox.critical(None, "MdCal 오류", text[-3000:])
    else:
        sys.__excepthook__(etype, value, tb)


def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#F2F3F5"))
    p.drawRoundedRect(QRectF(4, 6, 56, 54), 10, 10)
    p.setBrush(QColor("#E5484D"))
    p.drawRoundedRect(QRectF(4, 6, 56, 18), 10, 10)
    p.drawRect(QRectF(4, 16, 56, 8))
    p.setPen(QColor("#22252B"))
    p.setFont(QFont("Malgun Gothic", 20, QFont.Bold))
    p.drawText(QRectF(4, 24, 56, 36), Qt.AlignCenter, str(date.today().day))
    p.end()
    return QIcon(pm)


class App:
    def __init__(self, qapp):
        self.qapp = qapp
        self.cfg, warn = load_config()
        self.store = Store(DB_PATH)
        self.memos = MemoFiles(self.cfg["memo_folder"])
        self.index = MdIndex(self.cfg["md_folders"], self.cfg["memo_folder"])
        self.win = CalendarWindow(self.cfg, self.store, self.index)
        self.detail = DayDetail(self.memos, self.index)

        self.index.changed.connect(self.win.refresh)
        self.index.changed.connect(self.detail.reload_notes)
        self.detail.saved.connect(lambda _d: self.index.rescan())   # pick up the new file mtime now
        self.win.grid.dayActivated.connect(
            lambda d: self.detail.show_day(d, self.win.grid.cell_global_rect(d)))
        self.win.grid.taskToggled.connect(self._on_task_toggled)
        self.win.grid.carryRequested.connect(lambda d: self._run_carry([d], date.today(), manual=True))
        self.win.dateChanged.connect(lambda _d: self._auto_carry())
        self.win.dateChanged.connect(lambda _d: self._refresh_icon())
        self.win.raisedChanged.connect(self.detail.set_topmost)
        self.win.grid.carry_menu = self.cfg.get("carry_mode", "manual") != "off"

        icon = make_icon()
        qapp.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("MdCal")
        self.tray.activated.connect(self._on_tray)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._build_menu)
        self.tray.setContextMenu(self.menu)
        self.win.btn_menu.setMenu(self.menu)     # header ☰ shows the same menu

        if warn:
            QMessageBox.warning(None, "MdCal", warn)
        self._migrate_db_memos()
        self.index.rescan()
        self.win.show()
        self.tray.show()
        self._auto_carry()
        self._warn_missing()
        try:
            autostart.refresh()          # project moved -> fix the Run path
        except OSError as e:
            QMessageBox.warning(None, "MdCal", f"자동 실행 경로 갱신 실패\n{e}")

    def _refresh_icon(self):
        """The icon shows today's day number: redraw it at midnight."""
        icon = make_icon()
        self.qapp.setWindowIcon(icon)
        self.tray.setIcon(icon)

    def _set_cfg(self, key, value, what) -> bool:
        """Save one setting. On failure the old value stays and the user is told."""
        try:
            update_config(self.cfg, key, value)
        except OSError as e:
            QMessageBox.warning(None, "MdCal", f"{what} 저장 실패 (변경 취소됨)\n{e}")
            return False
        return True

    def _on_task_toggled(self, d, line_no, raw):
        if not self.memos.enabled:
            return
        if self.detail.isVisible() and self.detail.day == d:
            self.detail.toggle_task_at(line_no, raw)
            return
        try:
            doc = self.memos.read(d)
            if doc.error:
                QMessageBox.warning(self.win, "MdCal", f"메모를 수정할 수 없습니다.\n{doc.error}")
                return
            new = tasks.toggle_done(doc.body, line_no, raw)
            self.memos.write(d, new, doc.mtime)
        except ValueError as e:
            QMessageBox.warning(self.win, "MdCal", str(e))
            self.index.rescan()
            return
        except MemoConflict:
            QMessageBox.warning(self.win, "MdCal", "메모 파일이 다른 곳에서 변경되었습니다. 다시 시도하세요.")
            self.index.rescan()
            return
        except OSError as e:
            QMessageBox.warning(self.win, "MdCal", f"할 일 변경 실패\n{e}")
            return
        self.index.rescan()

    def _run_carry(self, sources, target, manual: bool):
        if not self.memos.enabled:
            if manual:
                QMessageBox.warning(self.win, "MdCal", "메모 저장 폴더를 사용할 수 없습니다.")
            return False
        if self.detail.isVisible():
            self.detail.flush()
            if self.detail.has_unsaved:
                QMessageBox.warning(self.win, "MdCal", "저장되지 않은 메모가 있어 넘기기를 중단했습니다")
                return False
        result = carry.carry(self.memos, sources, target)
        self.index.rescan()
        self.detail.reload_notes()
        if result.errors:
            QMessageBox.warning(self.win, "MdCal",
                                f"{result.moved}개를 옮긴 뒤 넘기기를 중단했습니다.\n" + "\n".join(result.errors))
            return False
        if manual:
            # marked counts lines already present today too (moved = newly appended only)
            QMessageBox.information(self.win, "MdCal", f"{result.marked}개를 오늘로 넘겼습니다")
        elif result.moved:
            self.tray.showMessage("MdCal", f"미완료 할 일 {result.moved}개를 오늘로 옮겼습니다")
        return True

    def _auto_carry(self):
        today = date.today()
        if (self.cfg.get("carry_mode", "manual") != "auto"
                or self.cfg.get("last_carry") == today.isoformat() or not self.memos.enabled):
            return
        try:
            days = carry.pending_days(self.memos, today, self.cfg.get("carry_lookback_days", 14))
        except OSError as e:
            QMessageBox.warning(self.win, "MdCal", f"미완료 할 일 확인 실패\n{e}")
            return
        if days and not self._run_carry(sorted(days), today, manual=False):
            return
        previous = self.cfg.get("last_carry")
        self.cfg["last_carry"] = today.isoformat()
        try:
            save_config(self.cfg)
        except OSError as e:
            self.cfg["last_carry"] = previous
            QMessageBox.warning(self.win, "MdCal", f"자동 넘기기 날짜 저장 실패\n{e}")

    def _set_carry_mode(self, mode):
        previous = self.cfg.get("carry_mode", "manual")
        self.cfg["carry_mode"] = mode
        try:
            save_config(self.cfg)
        except OSError as e:
            self.cfg["carry_mode"] = previous
            QMessageBox.warning(self.win, "MdCal", f"할 일 넘기기 설정 저장 실패\n{e}")
            return
        self.win.grid.carry_menu = mode != "off"
        if mode == "auto":
            self._auto_carry()

    def _migrate_db_memos(self):
        """One-time: move memos from the old DB table into <memo_folder>\\YYMMDD.md."""
        legacy = self.store.legacy_memos()
        if not legacy or not self.memos.enabled:
            return
        failed = []
        for day, text in legacy:
            try:
                doc = self.memos.read(day)
                body = (doc.body.rstrip("\n") + "\n\n" + text) if doc.body.strip() else text
                self.memos.write(day, body, doc.mtime)
            except (OSError, MemoConflict) as e:
                failed.append(f"{day}: {e}")
                continue                     # keep this one in the DB
            self.store.delete_legacy_memo(day)
        if failed:
            QMessageBox.warning(None, "MdCal", "DB 메모 이전 실패 (DB에 남겨둠):\n" + "\n".join(failed))

    def _warn_missing(self):
        warnings = []
        if self.index.missing:
            warnings.append("다음 MD 폴더를 찾을 수 없습니다:\n" + "\n".join(self.index.missing))
        if self.index.scan_errors:
            warnings.append("다음 MD 파일/폴더를 확인할 수 없습니다:\n" + "\n".join(self.index.scan_errors))
        if warnings:
            QMessageBox.warning(None, "MdCal", "\n\n".join(warnings))

    def _refresh(self):
        self.index.rescan()
        self._warn_missing()

    def _on_tray(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.win.show()
            self.win.unminimize()
            self.win.go_today()

    def _build_menu(self):
        m = self.menu
        m.clear()
        m.addAction("보이기" if not self.win.isVisible() else "숨기기", self._toggle_visible)
        m.addAction("오늘로", self.win.go_today)
        m.addAction("새로고침", self._refresh)
        m.addSeparator()

        fm = m.addMenu("MD 폴더")
        fm.addAction("폴더 추가…", self._add_folder)
        if self.cfg["md_folders"]:
            fm.addSeparator()
        for f in self.cfg["md_folders"]:
            sub = fm.addMenu(f)
            sub.addAction("탐색기에서 열기", lambda f=f: os.startfile(f) if os.path.isdir(f) else None)
            sub.addAction("목록에서 제거", lambda f=f: self._remove_folder(f))

        mf = self.cfg["memo_folder"]
        mm = m.addMenu("메모 저장 폴더")
        mm.addAction(mf or "(설정 안 됨)").setEnabled(False)
        mm.addAction("변경…", self._change_memo_folder)
        if mf and os.path.isdir(mf):
            mm.addAction("탐색기에서 열기", lambda: os.startfile(mf))

        cm = m.addMenu("미완료 할 일 넘기기")
        carry_group = QActionGroup(cm)
        carry_group.setExclusive(True)
        for label, mode in (("자동 (시작·자정에 오늘로)", "auto"),
                            ("수동 (날짜 우클릭)", "manual"), ("끄기", "off")):
            a = QAction(label, cm, checkable=True,
                        checked=self.cfg.get("carry_mode", "manual") == mode)
            a.triggered.connect(lambda _=False, mode=mode: self._set_carry_mode(mode))
            carry_group.addAction(a)
            cm.addAction(a)

        om = m.addMenu("배경 투명도")
        grp = QActionGroup(om)
        for label, v in (("진하게 (95%)", 0.95), ("보통 (80%)", 0.80), ("연하게 (60%)", 0.60), ("아주 연하게 (40%)", 0.40)):
            a = QAction(label, om, checkable=True, checked=abs(self.cfg["opacity"] - v) < 0.01)
            a.triggered.connect(lambda _=False, v=v: self.win.set_opacity(v))
            grp.addAction(a)
            om.addAction(a)

        a = QAction("월요일 시작", m, checkable=True, checked=not self.cfg["week_starts_sunday"])
        a.triggered.connect(self._toggle_week_start)
        m.addAction(a)
        a = QAction("위치 잠금", m, checkable=True, checked=self.cfg["locked"])
        a.triggered.connect(self.win.set_locked)
        m.addAction(a)
        try:
            enabled = autostart.registered() is not None
        except OSError as e:
            a = QAction("Windows 시작 시 자동 실행 (확인 실패)", m)
            a.setEnabled(False)
            a.setToolTip(str(e))
        else:
            a = QAction("Windows 시작 시 자동 실행", m, checkable=True, checked=enabled)
            a.triggered.connect(self._toggle_autostart)
        m.setToolTipsVisible(True)
        m.addAction(a)
        m.addSeparator()
        m.addAction("데이터 폴더 열기", lambda: os.startfile(str(APP_DIR)))
        m.addAction("종료", self.quit)

    def _toggle_autostart(self, on):
        try:
            autostart.set_enabled(on)     # False means already off; no warning needed.
        except OSError as e:
            QMessageBox.warning(None, "MdCal", f"자동 실행 설정 실패\n{e}")

    def _toggle_visible(self):
        self.win.setVisible(not self.win.isVisible())

    def _toggle_week_start(self, monday):
        if self._set_cfg("week_starts_sunday", not monday, "주 시작 요일 설정"):
            self.win.refresh()

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(None, "MD 폴더 선택")
        if not d:
            return
        d = os.path.normpath(d)
        folders = self.cfg["md_folders"]
        if d not in folders and self._set_cfg("md_folders", folders + [d], "MD 폴더 목록"):
            self.index.set_folders(self.cfg["md_folders"])

    def _change_memo_folder(self):
        self.detail.flush()
        if self.detail.has_unsaved:
            return
        d = QFileDialog.getExistingDirectory(None, "메모 저장 폴더 선택", self.cfg["memo_folder"] or "")
        if not d:
            return
        d = os.path.normpath(d)
        if not self._set_cfg("memo_folder", d, "메모 저장 폴더"):
            return
        self.memos.folder = Path(d)
        self.detail.close()
        self._migrate_db_memos()
        self.index.set_memo_folder(d)

    def _remove_folder(self, f):
        folders = [x for x in self.cfg["md_folders"] if x != f]
        if self._set_cfg("md_folders", folders, "MD 폴더 목록"):
            self.index.set_folders(self.cfg["md_folders"])

    def quit(self):
        self.detail.flush()
        if self.detail.has_unsaved:      # save failed; do not drop edits
            return
        self.tray.hide()
        self.qapp.quit()


def main():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    sys.excepthook = _excepthook
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(LOCK_PATH))
    if not lock.tryLock(100):
        QMessageBox.information(None, "MdCal", "MdCal이 이미 실행 중입니다. (트레이 아이콘 확인)")
        return 1
    app = App(qapp)
    rc = qapp.exec()
    app.detail.flush()
    app.store.close()
    lock.unlock()
    return rc
