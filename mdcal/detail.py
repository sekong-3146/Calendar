"""Expanded day view: editable memo + read-only MD notes."""
import os
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QGuiApplication, QKeySequence, QPainter,
                           QPalette, QPen, QShortcut)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QSizeGrip, QSplitter, QTabWidget, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from .memofile import MemoConflict

WEEK_KO = "월화수목금토일"

CSS = """
QWidget{color:#E8EAED;font:10pt 'Malgun Gothic'}
QLabel#title{font:bold 14pt 'Malgun Gothic';color:#F2F3F5}
QLabel#section{color:#9AA0AA;font:9pt 'Malgun Gothic';padding-top:4px}
QPlainTextEdit,QTextBrowser{background:#1B1E25;border:1px solid #2E333D;border-radius:6px;padding:6px;
  selection-background-color:#3B5B8C}
QTabWidget::pane{border:none}
QTabBar::tab{background:#232731;color:#AEB4BE;padding:4px 10px;border-top-left-radius:5px;
  border-top-right-radius:5px;margin-right:2px}
QTabBar::tab:selected{background:#2F3542;color:#7FD1B9}
QPushButton{background:#2B303B;border:1px solid #3A404C;border-radius:5px;padding:3px 10px}
QPushButton:hover{background:#353B48}
QToolButton{background:transparent;border:none;font:12pt;padding:2px 8px;border-radius:4px}
QToolButton:hover{background:rgba(255,255,255,30)}
QSplitter::handle{background:transparent;height:6px}
"""


def find_vault(path: Path):
    for parent in path.parents:
        if (parent / ".obsidian").is_dir():
            return parent
    return None


class _Header(QWidget):
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.window().windowHandle().startSystemMove()


class DayDetail(QWidget):
    saved = Signal(object)      # date whose memo was written

    def __init__(self, memos, index):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(CSS)
        self.memos, self.index = memos, index     # memos: MemoFiles
        self.day = None
        self.notes = []
        self._dirty = False
        self._base_mtime = None                   # memo file mtime when loaded/saved
        self._notes_sig = []

        head = _Header(self)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(objectName="title")
        close = QToolButton(text="✕")
        close.clicked.connect(self.close)
        hl.addWidget(self.title)
        hl.addStretch(1)
        hl.addWidget(close)

        self.memo = QPlainTextEdit()
        self.memo.setPlaceholderText("메모를 입력하세요 (자동 저장)")
        self.memo.textChanged.connect(self._on_edit)
        memo_box = QWidget()
        ml = QVBoxLayout(memo_box)
        ml.setContentsMargins(0, 0, 0, 0)
        self.memo_label = QLabel("메모", objectName="section")
        ml.addWidget(self.memo_label)
        ml.addWidget(self.memo)

        self.tabs = QTabWidget()
        self.btn_open = QPushButton("Obsidian에서 열기")
        self.btn_open.clicked.connect(self._open_current)
        self.tabs.setCornerWidget(self.btn_open, Qt.TopRightCorner)
        self.notes_box = QWidget()
        nl = QVBoxLayout(self.notes_box)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.addWidget(QLabel("노트 (읽기 전용)", objectName="section"))
        nl.addWidget(self.tabs)

        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(memo_box)
        self.split.addWidget(self.notes_box)
        self.split.setChildrenCollapsible(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.addWidget(head)
        lay.addWidget(self.split, 1)
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(14, 14)

        self._save_timer = QTimer(self, singleShot=True, interval=700)
        self._save_timer.timeout.connect(self.flush)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close)
        self.resize(560, 640)

    # data
    def show_day(self, day, anchor: QRect):
        self.flush()
        if self._dirty:          # save failed: stay on the current day
            return
        self.day = day
        self.title.setText(f"{day.year}년 {day.month}월 {day.day}일 ({WEEK_KO[day.weekday()]})")
        self._load_memo()
        self._load_notes()
        self._place(anchor)
        self.show()
        self.raise_()
        self.activateWindow()
        self.memo.setFocus()
        self.memo.moveCursor(self.memo.textCursor().MoveOperation.End)

    def _load_memo(self):
        self._dirty = False
        self.memo.blockSignals(True)
        if self.memos.enabled:
            doc = self.memos.read(self.day)
            self._base_mtime = doc.mtime
            self.memo.setReadOnly(False)
            self.memo.setPlainText(doc.body)
            self.memo_label.setText(f"메모 → {self.memos.display_name(self.day)}")
        else:
            self._base_mtime = None
            self.memo.setReadOnly(True)
            self.memo.setPlainText("")
            self.memo_label.setText("메모 (저장 폴더 없음 — 트레이 메뉴 > 메모 저장 폴더)")
        self.memo.blockSignals(False)

    def reload_notes(self):
        """Called when MD files change while open."""
        if not (self.isVisible() and self.day):
            return
        if self.memos.enabled and not self._dirty:
            p = self.memos.path(self.day)
            mtime = p.stat().st_mtime if p.exists() else None
            if mtime != self._base_mtime:          # edited outside (Obsidian)
                pos = self.memo.textCursor().position()
                self._load_memo()
                c = self.memo.textCursor()
                c.setPosition(min(pos, len(self.memo.toPlainText())))
                self.memo.setTextCursor(c)
        sig = [(n.path, n.mtime) for n in self.index.notes(self.day)]
        if sig != self._notes_sig:       # rebuild only when notes changed (keeps scroll)
            cur = self.tabs.currentIndex()
            self._load_notes()
            if 0 <= cur < self.tabs.count():
                self.tabs.setCurrentIndex(cur)

    def _load_notes(self):
        self.notes = self.index.notes(self.day)
        self._notes_sig = [(n.path, n.mtime) for n in self.notes]
        self.tabs.clear()
        for n in self.notes:
            view = QTextBrowser()
            view.setOpenExternalLinks(True)
            pal = view.palette()
            pal.setColor(QPalette.Link, QColor("#7BB6FF"))
            view.setPalette(pal)
            body = n.body or ""
            if n.error:
                body = f"> ⚠ {n.error}\n\n" + body
            view.setMarkdown(body)
            self.tabs.addTab(view, n.title)
        self.notes_box.setVisible(bool(self.notes))
        if self.notes:
            self.split.setSizes([160, 480])

    @property
    def has_unsaved(self):
        return self._dirty

    def _on_edit(self):
        self._dirty = True
        self._save_timer.start()

    def flush(self):
        """Write memo md. Never overwrites a file changed outside without asking."""
        self._save_timer.stop()
        if not (self._dirty and self.day is not None and self.memos.enabled):
            return
        try:
            self._base_mtime = self.memos.write(self.day, self.memo.toPlainText(), self._base_mtime)
        except MemoConflict:
            self._resolve_conflict()
            return
        except OSError as e:
            QMessageBox.warning(self, "MdCal", f"메모 저장 실패 (편집 내용은 창에 남아 있음)\n{e}")
            return
        self._dirty = False
        self.saved.emit(self.day)

    def _resolve_conflict(self):
        box = QMessageBox(QMessageBox.Warning, "MdCal",
                          f"{self.memos.display_name(self.day)} 파일이 다른 곳(Obsidian 등)에서 변경되었습니다.",
                          parent=self)
        keep = box.addButton("내 메모로 덮어쓰기", QMessageBox.AcceptRole)
        box.addButton("파일 내용 불러오기 (내 변경 버림)", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is keep:
            p = self.memos.path(self.day)
            self._base_mtime = p.stat().st_mtime if p.exists() else None
            self.flush()
        else:
            self._load_memo()
            self.saved.emit(self.day)

    def _open_current(self):
        i = self.tabs.currentIndex()
        if not 0 <= i < len(self.notes):
            return
        path = self.notes[i].path
        if find_vault(path):
            url = QUrl.fromEncoded(("obsidian://open?path=" + quote(str(path), safe="")).encode("ascii"))
            if QDesktopServices.openUrl(url):
                return
        try:
            os.startfile(str(path))       # default .md app when Obsidian URI is unavailable
        except OSError as e:
            QMessageBox.warning(self, "MdCal", f"파일을 열 수 없습니다.\n{path}\n{e}")

    # window
    def _place(self, anchor: QRect):
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = min(self.width(), avail.width()), min(self.height(), avail.height())
        x = anchor.right() + 8
        if x + w > avail.right():
            x = anchor.left() - w - 8
        x = max(avail.left(), min(x, avail.right() - w))
        y = max(avail.top(), min(anchor.top() - 40, avail.bottom() - h))
        self.setGeometry(x, y, w, h)

    def resizeEvent(self, e):
        self.grip.move(self.width() - self.grip.width() - 3, self.height() - self.grip.height() - 3)
        super().resizeEvent(e)

    def closeEvent(self, e):
        self.flush()
        if self._dirty:          # save failed: keep the window so edits are not lost
            e.ignore()
            return
        super().closeEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(24, 27, 33, 248))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
