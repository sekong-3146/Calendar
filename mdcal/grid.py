"""Month calendar window pinned to the desktop bottom."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QMessageBox, QSizeGrip, QToolButton, QToolTip,
                               QVBoxLayout, QWidget)

from . import tasks
from .config import APP_DIR, update_config
from .winpin import WM_EXITSIZEMOVE, BottomPin, message_id, restore_no_activate

FONT = "Malgun Gothic"
C_BG = QColor(22, 24, 30)
C_TEXT = QColor(232, 234, 237)
C_DIM = QColor(150, 155, 165)
C_SUN = QColor(255, 123, 123)
C_SAT = QColor(123, 182, 255)
C_TODAY = QColor(255, 200, 87)
C_LINE = QColor(255, 255, 255, 26)
C_MD = QColor(127, 209, 185)
C_HOVER = QColor(255, 255, 255, 18)
HEAD_H = 22          # weekday row height

DAY_COLORS = [("빨강", "#E5484D"), ("주황", "#F76B15"), ("노랑", "#FFC53D"),
              ("초록", "#30A46C"), ("파랑", "#0090FF"), ("보라", "#8E4EC6")]


@dataclass
class CellData:
    memo: str = ""
    color: str = None
    md: list = field(default_factory=list)     # one list of summary lines per note


def month_cells(year, month, sunday_first):
    first = date(year, month, 1)
    offset = (first.weekday() + 1) % 7 if sunday_first else first.weekday()
    start = first - timedelta(days=offset)
    return [start + timedelta(days=i) for i in range(42)]


def _with_alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(a)
    return c


class GridWidget(QWidget):
    dayActivated = Signal(object)            # date
    colorRequested = Signal(object, object)  # date, "#rrggbb" or None
    taskToggled = Signal(object, int, str)    # date, body line number, raw line
    carryRequested = Signal(object)          # source date

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.month = 1
        self.sunday_first = True
        self.cells = []
        self.data = {}
        self.hover = None
        self._truncated = set()
        self._task_rects = []                # QRectF, date, body line number, raw line
        self._pressed_task = None            # Preserve the first click across a rescan.
        self.carry_menu = True               # False when carry_mode is "off" (set by app)
        self.f_head = QFont(FONT, 9)
        self.f_day = QFont(FONT, 10, QFont.Bold)
        self.f_text = QFont(FONT, 8)

    def set_view(self, month, cells, data, sunday_first):
        self.month, self.cells, self.data, self.sunday_first = month, cells, data, sunday_first
        self._truncated.clear()
        self._task_rects.clear()
        self.unsetCursor()
        QToolTip.hideText()
        self.update()

    # geometry
    def _cell_size(self):
        return self.width() / 7, (self.height() - HEAD_H) / 6

    def cell_rect(self, idx) -> QRectF:
        w, h = self._cell_size()
        return QRectF((idx % 7) * w, HEAD_H + (idx // 7) * h, w, h)

    def cell_global_rect(self, day) -> QRect:
        if day not in self.cells:
            return QRect(self.mapToGlobal(self.rect().center()), self.rect().size() / 4)
        r = self.cell_rect(self.cells.index(day)).toRect()
        return QRect(self.mapToGlobal(r.topLeft()), r.size())

    def _hit(self, pos):
        if not self.cells or pos.y() < HEAD_H:
            return None
        w, h = self._cell_size()
        col = min(6, max(0, int(pos.x() / w)))
        row = min(5, max(0, int((pos.y() - HEAD_H) / h)))
        return row * 7 + col

    # painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self._cell_size()
        names = "일월화수목금토" if self.sunday_first else "월화수목금토일"
        p.setFont(self.f_head)
        for i, n in enumerate(names):
            p.setPen(C_SUN if n == "일" else C_SAT if n == "토" else C_DIM)
            p.drawText(QRectF(i * w, 0, w, HEAD_H), Qt.AlignCenter, n)

        p.setPen(QPen(C_LINE, 1))
        for r in range(7):
            y = HEAD_H + r * h
            p.drawLine(0, int(y), self.width(), int(y))
        for c in range(1, 7):
            p.drawLine(int(c * w), HEAD_H, int(c * w), self.height())

        today = date.today()
        self._truncated.clear()
        self._task_rects.clear()
        for idx, d in enumerate(self.cells):
            self._paint_cell(p, idx, d, today)

    def _paint_cell(self, p, idx, d, today):
        r = self.cell_rect(idx)
        inner = r.adjusted(1, 1, -1, -1)
        cd = self.data.get(d)
        in_month = d.month == self.month
        fade = 255 if in_month else 95

        if cd and cd.color:
            p.fillRect(inner, _with_alpha(QColor(cd.color), 90 if in_month else 45))
        if idx == self.hover:
            p.fillRect(inner, C_HOVER)
        if d == today:
            p.setPen(QPen(C_TODAY, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 4, 4)

        wd = d.weekday()
        base = C_SUN if wd == 6 else C_SAT if wd == 5 else C_TEXT
        p.setFont(self.f_day)
        p.setPen(_with_alpha(base, fade))
        p.drawText(r.adjusted(6, 3, -4, 0), Qt.AlignLeft | Qt.AlignTop, str(d.day))
        if not cd:
            return

        lines = []
        memo_lines = cd.memo.split("\n")
        for line_no, (raw, editable) in enumerate(zip(memo_lines, tasks.editable_mask(memo_lines))):
            if not raw.strip():
                continue
            task = tasks.parse_task(raw, line_no) if editable else None
            text, color, glyph = raw, C_TEXT, ""
            if task and (task.is_open or task.is_done or task.is_carried):
                prefix = "☐ " if task.is_open else "☑ " if task.is_done else "→ "
                text = prefix + task.text
                if not task.is_open:
                    color = C_DIM
                if task.is_open or task.is_done:
                    glyph = prefix
            lines.append((text, _with_alpha(color, fade), line_no, raw, glyph))
        for note_lines in cd.md:
            for i, ln in enumerate(note_lines):
                text = "▸ " + ln if i == 0 else "  · " + ln
                color = _with_alpha(C_MD, fade if i == 0 else int(fade * 0.7))
                lines.append((text, color, None, None, ""))

        fm = QFontMetrics(self.f_text)
        lh = fm.height()
        x, tw = r.left() + 5, int(r.width() - 10)
        y, bottom = r.top() + 22, r.bottom() - 2
        p.setFont(self.f_text)
        cut = False
        for text, color, line_no, raw, glyph in lines:
            if y + lh > bottom:
                cut = True
                break
            el = fm.elidedText(text, Qt.ElideRight, tw)
            cut |= el != text
            p.setPen(color)
            p.drawText(QRectF(x, y, tw, lh), Qt.AlignLeft | Qt.AlignVCenter, el)
            if glyph and el.startswith(glyph.rstrip()):
                rect = QRectF(x, y, min(tw, fm.horizontalAdvance(glyph)), lh)
                self._task_rects.append((rect, d, line_no, raw))
            y += lh
        if cut:      # hint: double-click to see everything
            self._truncated.add(idx)
            p.setPen(_with_alpha(C_TODAY, fade))
            p.drawText(r.adjusted(0, 0, -5, -1), Qt.AlignRight | Qt.AlignBottom, "⋯")

    # input
    def event(self, e):
        if e.type() == QEvent.ToolTip:
            idx = self._hit(e.pos())
            if idx in self._truncated:
                cd = self.data[self.cells[idx]]
                parts = ([cd.memo] if cd.memo else []) + ["\n".join(lines) for lines in cd.md]
                text = '<p style="white-space:pre-wrap">' + escape("\n\n".join(parts)).replace("\n", "<br>") + "</p>"
                QToolTip.showText(e.globalPos(), text, self, self.cell_rect(idx).toRect())
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)

    def _task_hit(self, pos):
        return next((entry for entry in self._task_rects if entry[0].contains(pos)), None)

    def mousePressEvent(self, e):
        hit = self._task_hit(e.position())
        self._pressed_task = hit if e.button() == Qt.LeftButton else None
        if e.button() == Qt.LeftButton and hit is not None:
            self.taskToggled.emit(*hit[1:])
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self._task_hit(e.position()) else Qt.ArrowCursor)
        idx = self._hit(e.position())
        if idx != self.hover:
            self.hover = idx
            self.update()

    def leaveEvent(self, _):
        self.hover = None
        self.unsetCursor()
        self.update()

    def mouseDoubleClickEvent(self, e):
        idx = self._hit(e.position())
        hit = self._task_hit(e.position()) or self._pressed_task
        if (e.button() == Qt.LeftButton and hit is not None and idx is not None
                and hit[0].contains(e.position()) and self.cells[idx] == hit[1]):
            e.accept()
            return
        if idx is not None and e.button() == Qt.LeftButton:
            self.dayActivated.emit(self.cells[idx])

    def contextMenuEvent(self, e):
        idx = self._hit(e.pos())
        if idx is None:
            return
        d = self.cells[idx]
        m = QMenu(self)
        m.addAction("열기 / 메모 쓰기", lambda: self.dayActivated.emit(d))
        cd = self.data.get(d)
        count = len(tasks.open_tasks(cd.memo)) if self.carry_menu and cd and d < date.today() else 0
        if count:
            m.addAction(f"미완료 할 일 오늘로 보내기 ({count}개)", lambda: self.carryRequested.emit(d))
        m.addSeparator()
        for name, hexv in DAY_COLORS:
            m.addAction(name, lambda h=hexv: self.colorRequested.emit(d, h))
        m.addAction("색 지우기", lambda: self.colorRequested.emit(d, None))
        m.exec(e.globalPos())


class HeaderBar(QWidget):
    """Title row; dragging it moves the window unless locked."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.owner.cfg["locked"]:
            self.window().windowHandle().startSystemMove()


class CalendarWindow(QWidget):
    raisedChanged = Signal(bool)     # True while lifted above the Win+D desktop
    dateChanged = Signal(object)     # new local date, after refreshing the calendar

    def __init__(self, cfg, store, index):
        # No WindowStaysOnBottomHint: Qt would block the TOPMOST lift used for Win+D.
        # Bottom pinning is done by BottomPin via nativeEvent.
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("MdCal")
        self.cfg, self.store, self.index = cfg, store, index
        self.pin = None
        today = date.today()
        self.year, self.month = today.year, today.month
        self._shown_today = today
        self.detail = None              # set by app

        self.header = HeaderBar(self)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(10, 6, 10, 2)
        btn_css = ("QToolButton{color:#E8EAED;background:transparent;border:none;padding:2px 8px;"
                   "font:10pt 'Malgun Gothic';border-radius:4px}"
                   "QToolButton:hover{background:rgba(255,255,255,30)}"
                   "QToolButton::menu-indicator{image:none;width:0}")
        self.btn_prev, self.btn_next, self.btn_today = QToolButton(), QToolButton(), QToolButton()
        self.btn_menu = QToolButton()       # menu attached by app (same as tray menu)
        self.btn_menu.setPopupMode(QToolButton.InstantPopup)
        self.btn_menu.setToolTip("메뉴")
        for b, t in ((self.btn_prev, "◀"), (self.btn_next, "▶"), (self.btn_today, "오늘"),
                     (self.btn_menu, "☰")):
            b.setText(t)
            b.setStyleSheet(btn_css)
            b.setCursor(Qt.PointingHandCursor)
        self.title = QLabel()
        self.title.setStyleSheet("color:#F2F3F5;font:bold 13pt 'Malgun Gothic';padding:0 6px")
        hl.addWidget(self.btn_prev)
        hl.addWidget(self.title)
        hl.addWidget(self.btn_next)
        hl.addStretch(1)
        hl.addWidget(self.btn_today)
        hl.addWidget(self.btn_menu)
        self.btn_prev.clicked.connect(lambda: self.shift_month(-1))
        self.btn_next.clicked.connect(lambda: self.shift_month(1))
        self.btn_today.clicked.connect(self.go_today)

        self.grid = GridWidget(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 6)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(self.grid, 1)
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(14, 14)
        self.grip.setVisible(not cfg["locked"])
        self.setMinimumSize(420, 320)

        self.grid.colorRequested.connect(self._set_color)

        self._save_geo = QTimer(self, singleShot=True, interval=600)
        self._save_geo.timeout.connect(self._store_geometry)
        self._tick = QTimer(self, interval=1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()
        self._pin_timer = QTimer(self, interval=300)     # Win+D detection
        self._pin_timer.timeout.connect(self._on_pin_tick)
        self._pin_timer.start()

        # Monitor add/remove: recheck placement; never save positions caused by it.
        self._fallback = None            # QRect we moved to because the saved spot is off-screen
        self._screen_timer = QTimer(self, singleShot=True, interval=500)
        self._screen_timer.timeout.connect(self._ensure_on_screen)
        self._hold_save = QTimer(self, singleShot=True, interval=1500)
        self._geo_save_warned = False
        app = QGuiApplication.instance()
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)
        app.primaryScreenChanged.connect(self._on_screens_changed)
        for s in QGuiApplication.screens():
            self._watch_screen(s)
        self._apply_geometry()

    # month navigation / data
    def shift_month(self, delta):
        m = self.year * 12 + (self.month - 1) + delta
        self.year, self.month = divmod(m, 12)
        self.month += 1
        self.refresh()

    def go_today(self):
        t = date.today()
        self.year, self.month = t.year, t.month
        self.refresh()

    def refresh(self):
        cells = month_cells(self.year, self.month, self.cfg["week_starts_sunday"])
        colors = self.store.colors(cells[0], cells[-1])
        data = {}
        for d in cells:
            mn = self.index.memo_note(d)
            memo = mn.body if mn else ""
            md = [n.summary_lines() for n in self.index.notes(d)]
            if memo.strip() or d in colors or md:
                data[d] = CellData(memo, colors.get(d), md)
        self.title.setText(f"{self.year}년 {self.month}월")
        self.grid.set_view(self.month, cells, data, self.cfg["week_starts_sunday"])

    def _set_color(self, day, color):
        self.store.set_color(day, color)
        self.refresh()

    def set_locked(self, locked):
        try:
            update_config(self.cfg, "locked", locked)
        except OSError as e:
            QMessageBox.warning(self, "MdCal", f"위치 잠금 설정 저장 실패\n{e}")
            return
        self.grip.setVisible(not locked)

    def set_opacity(self, value):
        try:
            update_config(self.cfg, "opacity", value)
        except OSError as e:
            QMessageBox.warning(self, "MdCal", f"투명도 설정 저장 실패\n{e}")
            return
        self.update()

    # window placement
    def _saved_rect(self):
        g = self.cfg.get("geometry")
        return QRect(*g) if g and len(g) == 4 else None

    @staticmethod
    def _visible(r: QRect) -> bool:
        """Header strip (drag handle) is reachable on some monitor."""
        head = QRect(r.x(), r.y(), r.width(), 40)
        need_w = min(120, head.width())
        for s in QGuiApplication.screens():
            part = s.availableGeometry().intersected(head)
            if part.width() >= need_w and part.height() >= 20:
                return True
        return False

    def _default_rect(self):
        avail = QGuiApplication.primaryScreen().availableGeometry()
        saved = self._saved_rect()
        w = min(saved.width() if saved else 760, avail.width() - 48)
        h = min(saved.height() if saved else 560, avail.height() - 48)
        return QRect(avail.right() - w - 24, avail.top() + 24, w, h)

    def _apply_geometry(self):
        saved = self._saved_rect()
        if saved and self._visible(saved):
            self._fallback = None
            self.setGeometry(saved)
        elif saved:                      # its monitor is missing: park on primary, keep saved
            self._fallback = self._default_rect()
            self.setGeometry(self._fallback)
        else:
            self._fallback = None
            self.setGeometry(self._default_rect())

    def _watch_screen(self, s):
        # UniqueConnection: safe to call again for a screen that is already watched.
        s.geometryChanged.connect(self._on_screens_changed, Qt.UniqueConnection)
        s.availableGeometryChanged.connect(self._on_screens_changed, Qt.UniqueConnection)

    def _on_screens_changed(self, *args):
        for s in QGuiApplication.screens():
            self._watch_screen(s)
        self._save_geo.stop()            # Windows may move us; don't overwrite the saved spot
        self._hold_save.start()
        self._screen_timer.start()

    def _ensure_on_screen(self):
        saved = self._saved_rect()
        if saved and self._visible(saved):
            if self.geometry() != saved or self._fallback is not None:
                self._apply_geometry()   # monitor is back: return to the saved spot
        elif self._fallback is None or not self._visible(self.geometry()):
            self._apply_geometry()

    def _store_geometry(self, by_user=False):
        """by_user: the user just finished a drag/resize, so save even during the hold."""
        if self.isMinimized():
            return                       # minimized geometry is not a real position
        if self._hold_save.isActive() and not by_user:
            self._save_geo.start()       # retry after the hold instead of dropping the change
            return
        if self._fallback is not None:
            if self.geometry() == self._fallback:
                return                   # parked by us, not moved by the user
            self._fallback = None        # user moved it: this becomes the new saved spot
        g = self.geometry()
        new = [g.x(), g.y(), g.width(), g.height()]
        if new == self.cfg.get("geometry"):
            return
        try:
            update_config(self.cfg, "geometry", new)
        except OSError as e:
            if not self._geo_save_warned:        # timer-driven: report once, not on every move
                self._geo_save_warned = True
                QMessageBox.warning(self, "MdCal", f"창 위치 저장 실패 (이후 실패는 다시 알리지 않음)\n{e}")
            return
        self._geo_save_warned = False

    def moveEvent(self, e):
        self._save_geo.start()
        super().moveEvent(e)

    def resizeEvent(self, e):
        self.grip.move(self.width() - self.grip.width() - 2, self.height() - self.grip.height() - 2)
        self._save_geo.start()
        super().resizeEvent(e)

    # desktop pinning
    def showEvent(self, e):
        super().showEvent(e)
        if self.pin is None:
            self.pin = BottomPin(int(self.winId()), self.raisedChanged.emit,
                                 log_path=str(APP_DIR / "pin.log"),
                                 on_log_error=lambda e: QMessageBox.warning(
                                     self, "MdCal", f"pin.log 기록 실패 (기록 중지)\n{e}"))
        if not self.pin.raised:
            self.pin.send_to_bottom()

    def nativeEvent(self, event_type, message):
        if self.pin is not None:
            self.pin.handle(int(message))
        if message_id(int(message)) == WM_EXITSIZEMOVE:
            # Save now: a monitor-change signal right after must not snap back to the old spot.
            self._save_geo.stop()
            self._store_geometry(by_user=True)
        return False, 0

    def changeEvent(self, e):
        # No taskbar button, so a minimized calendar (Win+M, "minimize all") could not be brought back.
        if e.type() == QEvent.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.unminimize)
        super().changeEvent(e)

    def unminimize(self):
        if not self.isMinimized():
            return
        restore_no_activate(int(self.winId()))
        if self.pin is not None and not self.pin.raised:
            self.pin.send_to_bottom()

    def _on_pin_tick(self):
        if self.pin is not None and (self.isVisible() or self.pin.raised):
            self.pin.poll()

    def _on_tick(self):
        t = date.today()
        if t != self._shown_today:      # midnight: move highlight / month
            if (self.year, self.month) == (self._shown_today.year, self._shown_today.month):
                self.year, self.month = t.year, t.month
            self._shown_today = t
            self.refresh()
            self.dateChanged.emit(t)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.setBrush(_with_alpha(C_BG, int(255 * self.cfg["opacity"])))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
