"""Entry point: single instance, tray menu, wiring."""
import os
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QLockFile, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart
from .config import APP_DIR, DB_PATH, LOCK_PATH, load_config, save_config
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
        self.win.raisedChanged.connect(self.detail.set_topmost)

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
        self._warn_missing()
        try:
            autostart.refresh()          # project moved -> fix the Run path
        except OSError as e:
            QMessageBox.warning(None, "MdCal", f"자동 실행 경로 갱신 실패\n{e}")

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
        self.cfg["week_starts_sunday"] = not monday
        save_config(self.cfg)
        self.win.refresh()

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(None, "MD 폴더 선택")
        if not d:
            return
        d = os.path.normpath(d)
        if d not in self.cfg["md_folders"]:
            self.cfg["md_folders"].append(d)
            save_config(self.cfg)
            self.index.set_folders(self.cfg["md_folders"])

    def _change_memo_folder(self):
        self.detail.flush()
        if self.detail.has_unsaved:
            return
        d = QFileDialog.getExistingDirectory(None, "메모 저장 폴더 선택", self.cfg["memo_folder"] or "")
        if not d:
            return
        d = os.path.normpath(d)
        self.cfg["memo_folder"] = d
        save_config(self.cfg)
        self.memos.folder = Path(d)
        self.detail.close()
        self._migrate_db_memos()
        self.index.set_memo_folder(d)

    def _remove_folder(self, f):
        self.cfg["md_folders"].remove(f)
        save_config(self.cfg)
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
