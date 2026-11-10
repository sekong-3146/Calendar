"""Expanded day view: editable memo + read-only MD notes."""
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QGuiApplication, QKeySequence, QPainter,
                           QPalette, QPen, QShortcut, QTextCursor)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QSizeGrip, QSplitter, QTabWidget, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from . import tasks
from .memofile import MemoConflict
from .winpin import set_topmost

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
        self._ime_preedit = False
        self._base_mtime = None                   # memo file mtime when loaded/saved
        self._notes_sig = []
        self._stat_warned = False                 # reload_notes stat failure already reported
        self.topmost = False                      # follows the calendar while Win+D desktop is shown

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
        self.memo.installEventFilter(self)
        self.memo.setContextMenuPolicy(Qt.CustomContextMenu)
        self.memo.customContextMenuRequested.connect(self._memo_context_menu)
        self.memo.setPlaceholderText("메모를 입력하세요 (자동 저장)")
        self.memo.textChanged.connect(self._on_edit)
        memo_box = QWidget()
        ml = QVBoxLayout(memo_box)
        ml.setContentsMargins(0, 0, 0, 0)
        self.memo_label = QLabel("메모", objectName="section")
        self.btn_open_memo = QPushButton("메모 Obsidian에서 열기")
        self.btn_open_memo.setEnabled(self.memos.enabled)
        self.btn_open_memo.clicked.connect(self._open_memo)
        memo_head = QHBoxLayout()
        memo_head.addWidget(self.memo_label, 1)
        memo_head.addWidget(self.btn_open_memo)
        ml.addLayout(memo_head)
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
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=lambda: self._shift_day(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=lambda: self._shift_day(1))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._toggle_task_lines)
        self.resize(560, 640)

    # data
    def show_day(self, day, anchor: QRect | None = None):
        if not self._load_memo(day):
            return
        self.day = day
        self.title.setText(f"{day.year}년 {day.month}월 {day.day}일 ({WEEK_KO[day.weekday()]})")
        self._load_notes()
        if anchor is not None:
            self._place(anchor)
        self.show()
        set_topmost(int(self.winId()), self.topmost)
        self.raise_()
        self.activateWindow()
        self.memo.setFocus()
        self.memo.moveCursor(self.memo.textCursor().MoveOperation.End)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.ShortcutOverride
                and event.modifiers() == Qt.ControlModifier
                and (event.key() in (Qt.Key_Left, Qt.Key_Right)
                     or (watched is self.memo and event.key() == Qt.Key_L))):
            event.ignore()       # Let window shortcuts override editor commands.
            return True
        if watched is self.memo:
            if event.type() == QEvent.InputMethod:
                self._ime_preedit = bool(event.preeditString())
            elif (event.type() == QEvent.KeyPress
                  and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                  and event.modifiers() == Qt.NoModifier
                  and self._continue_task()):
                return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _replace_block_text(cursor, block, text):
        old = block.text()
        prefix = 0
        while prefix < min(len(old), len(text)) and old[prefix] == text[prefix]:
            prefix += 1
        suffix = 0
        while (suffix < min(len(old), len(text)) - prefix
               and old[-suffix - 1] == text[-suffix - 1]):
            suffix += 1
        old_end = len(old) - suffix
        new_end = len(text) - suffix
        # Qt positions count UTF-16 units, not Python characters.
        start = block.position() + len(old[:prefix].encode("utf-16-le")) // 2
        end = block.position() + len(old[:old_end].encode("utf-16-le")) // 2
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.insertText(text[prefix:new_end])

    def _apply_task_lines(self, transform):
        if self.memo.isReadOnly():
            return
        QGuiApplication.inputMethod().commit()
        saved = self.memo.textCursor()
        doc = self.memo.document()
        first = doc.findBlock(saved.selectionStart()).blockNumber()
        last_block = doc.findBlock(saved.selectionEnd())
        last = last_block.blockNumber()
        if saved.hasSelection() and saved.selectionEnd() == last_block.position():
            last -= 1
        lines = self.memo.toPlainText().split("\n")
        mask = tasks.editable_mask(lines)
        changes = []
        for line_no in range(first, last + 1):
            if mask[line_no]:
                text = transform(lines[line_no])
                if text != lines[line_no]:
                    changes.append((line_no, text))
        if not changes:
            return
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        try:
            for line_no, text in changes:
                self._replace_block_text(cursor, doc.findBlockByNumber(line_no), text)
        finally:
            cursor.endEditBlock()
        self.memo.setTextCursor(saved)

    def _toggle_task_lines(self):
        if self.memo.hasFocus():
            self._apply_task_lines(tasks.toggle_line)

    def _mark_task_lines(self, mark):
        def transform(line):
            task = tasks.parse_task(line)
            if task and ((mark == tasks.DONE and task.is_open)
                         or (mark == tasks.OPEN and task.is_done)):
                return tasks.set_mark(line, mark)
            return line
        self._apply_task_lines(transform)

    def _memo_context_menu(self, pos):
        menu = self.memo.createStandardContextMenu()
        menu.addSeparator()
        commands = (
            ("할 일로 만들기 (Ctrl+L)", lambda: self._apply_task_lines(tasks.make_task)),
            ("완료 표시", lambda: self._mark_task_lines(tasks.DONE)),
            ("완료 해제", lambda: self._mark_task_lines(tasks.OPEN)),
            ("체크박스 제거", lambda: self._apply_task_lines(tasks.make_plain)),
        )
        for text, handler in commands:
            action = menu.addAction(text)
            action.setEnabled(not self.memo.isReadOnly())
            action.triggered.connect(handler)
        try:
            menu.exec(self.memo.mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def _continue_task(self):
        if self.memo.isReadOnly() or self._ime_preedit:
            return False
        cursor = self.memo.textCursor()
        if cursor.hasSelection() or not cursor.atBlockEnd():
            return False
        block = cursor.block()
        mask = tasks.editable_mask(self.memo.toPlainText().split("\n"))
        if not mask[block.blockNumber()]:
            return False
        prefix = tasks.continuation(block.text())
        if prefix is None:
            return False
        cursor.beginEditBlock()
        try:
            if prefix:
                cursor.insertText("\n" + prefix)
            else:
                text = block.text()
                indent = text[:len(text) - len(text.lstrip(" \t"))]
                self._replace_block_text(cursor, block, indent)
        finally:
            cursor.endEditBlock()
        self.memo.setTextCursor(cursor)
        return True

    def toggle_task_at(self, line_no: int, expected_line: str) -> bool:
        if self.memo.isReadOnly():
            QMessageBox.warning(self, "MdCal", "읽기 전용 메모는 변경할 수 없습니다.")
            return False
        QGuiApplication.inputMethod().commit()
        block = self.memo.document().findBlockByNumber(line_no)
        text = block.text()
        # Same rule as the closed-window path (tasks.toggle_done): never edit inside a code block.
        mask = tasks.editable_mask(self.memo.toPlainText().split("\n"))
        task = tasks.parse_task(text) if block.isValid() and mask[line_no] else None
        if (not block.isValid() or text.rstrip("\r") != expected_line.rstrip("\r")
                or task is None or not (task.is_open or task.is_done)):
            QMessageBox.warning(self, "MdCal", "메모 내용이 바뀌었습니다. 다시 시도하세요.")
            return False
        saved = self.memo.textCursor()
        cursor = QTextCursor(self.memo.document())
        cursor.beginEditBlock()
        try:
            self._replace_block_text(cursor, block,
                                     tasks.set_mark(text, tasks.DONE if task.is_open else tasks.OPEN))
        finally:
            cursor.endEditBlock()
        self.memo.setTextCursor(saved)
        return True

    def append_task(self, text: str) -> bool:
        """Add '- [ ] text' at the end of the open memo (one undo step). False if read-only."""
        if self.memo.isReadOnly():
            return False
        QGuiApplication.inputMethod().commit()
        pos = self.memo.textCursor().position()
        new = tasks.append_task(self.memo.toPlainText(), text).rstrip("\n")
        cursor = QTextCursor(self.memo.document())
        cursor.beginEditBlock()
        try:
            cursor.select(QTextCursor.Document)
            cursor.insertText(new)
        finally:
            cursor.endEditBlock()
        cursor.setPosition(min(pos, self.memo.document().characterCount() - 1))
        self.memo.setTextCursor(cursor)
        return True

    def _shift_day(self, delta):
        if self.day is not None:
            self.show_day(self.day + timedelta(days=delta))

    def _load_memo(self, day=None):
        navigating = day is not None
        day = self.day if day is None else day
        try:
            doc = self.memos.read(day) if self.memos.enabled else None
        except OSError as e:
            QMessageBox.warning(self, "MdCal", f"메모 읽기 실패 (현재 내용은 유지됨)\n{e}")
            return False
        if navigating:
            was_dirty = self._dirty
            self.flush()                 # Stage the target read before changing current edits.
            if self._dirty:
                return False
            if day == self.day and was_dirty:
                return True              # Keep the result of saving or resolving this same day.
        self.memo.blockSignals(True)
        try:
            if doc is not None:
                self._base_mtime = doc.mtime
                self.memo.setReadOnly(bool(doc.error))
                self.memo.setPlainText(doc.body)
                label = f"메모 → {self.memos.display_name(day)}"
                if doc.error:
                    label += f" (읽기 전용: {doc.error})"
                self.memo_label.setText(label)
            else:
                self._base_mtime = None
                self.memo.setReadOnly(True)
                self.memo.setPlainText("")
                self.memo_label.setText("메모 (저장 폴더 없음 — 트레이 메뉴 > 메모 저장 폴더)")
            self.btn_open_memo.setEnabled(doc is not None)
            self._dirty = False
        finally:
            self.memo.blockSignals(False)
        return True

    def reload_notes(self):
        """Called when MD files change while open."""
        if not (self.isVisible() and self.day):
            return
        if self.memos.enabled and not self._dirty:
            p = self.memos.path(self.day)
            try:
                mtime = p.stat().st_mtime
            except FileNotFoundError:
                mtime = None
                self._stat_warned = False
            except OSError as e:
                mtime = self._base_mtime           # cannot tell: keep what is shown
                if not self._stat_warned:          # called on every index change: report once
                    self._stat_warned = True
                    QMessageBox.warning(self, "MdCal", f"메모 파일을 확인할 수 없습니다 (화면 내용은 유지됨)\n{p}\n{e}")
            else:
                self._stat_warned = False
            if mtime != self._base_mtime:          # edited outside (Obsidian)
                pos = self.memo.textCursor().position()
                if not self._load_memo():
                    return
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
        while self.tabs.count():
            page = self.tabs.widget(0)
            self.tabs.removeTab(0)
            page.deleteLater()
        for n in self.notes:
            view = QTextBrowser()
            view.installEventFilter(self)
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

    def set_topmost(self, on: bool):
        self.topmost = on
        if self.testAttribute(Qt.WA_WState_Created):
            set_topmost(int(self.winId()), on)

    @property
    def has_unsaved(self):
        return self._dirty

    def _on_edit(self):
        self._dirty = True
        self._save_timer.start()

    def flush(self):
        """Write memo md. Never overwrites a file changed outside without asking."""
        self._save_timer.stop()
        if not (self._dirty and self.day is not None and self.memos.enabled) or self.memo.isReadOnly():
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
        reload = box.addButton("파일 내용 불러오기 (내 변경 버림)", QMessageBox.DestructiveRole)
        cancel = box.addButton("취소", QMessageBox.RejectRole)
        box.setEscapeButton(cancel)
        box.setDefaultButton(cancel)
        self._save_timer.stop()
        try:
            box.exec()
        finally:
            self._save_timer.stop()       # Retry only after another edit or explicit flush.
        if box.clickedButton() is keep:
            try:
                doc = self.memos.read(self.day)
                mtime = self.memos.write(self.day, self.memo.toPlainText(), doc.mtime)
            except (OSError, MemoConflict) as e:
                QMessageBox.warning(self, "MdCal", f"메모 저장 실패 (편집 내용은 창에 남아 있음)\n{e}")
                return
            self._base_mtime = mtime
            self._dirty = False
            self.saved.emit(self.day)
        elif box.clickedButton() is reload and self._load_memo():
            self.saved.emit(self.day)

    def _open_current(self):
        i = self.tabs.currentIndex()
        if not 0 <= i < len(self.notes):
            return
        self._open_path(self.notes[i].path)

    def _open_memo(self):
        if self.day is None or not self.memos.enabled:
            return
        self.flush()
        if self._dirty:
            return
        path = self.memos.path(self.day)
        try:
            path.stat()
        except FileNotFoundError:
            QMessageBox.information(self, "MdCal", "저장된 메모 파일이 없습니다. 메모를 입력하면 저장됩니다.")
            return
        except OSError as e:
            QMessageBox.warning(self, "MdCal", f"메모 파일을 확인할 수 없습니다.\n{path}\n{e}")
            return
        self._open_path(path)

    def _open_path(self, path):
        try:
            if find_vault(path):
                url = QUrl.fromEncoded(("obsidian://open?path=" + quote(str(path), safe="")).encode("ascii"))
                if QDesktopServices.openUrl(url):
                    return
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
