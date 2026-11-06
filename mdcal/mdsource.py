"""Scan MD folders, map files to dates by filename, watch for changes. Read-only."""
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

# Order matters: longer forms first so 20261105 is not read as 201105.
_PATTERNS = [
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"^(\d{4})(\d{2})(\d{2})(?!\d)"),
    re.compile(r"^(\d{2})(\d{2})(\d{2})(?!\d)"),
]
_H_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def date_from_name(stem: str):
    """Return (date, rest_of_name) or (None, None)."""
    for pat in _PATTERNS:
        m = pat.match(stem)
        if not m:
            continue
        y, mo, d = (int(g) for g in m.groups())
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d), stem[m.end():].strip(" _-.")
        except ValueError:      # e.g. 123456 is not a valid date
            return None, None
    return None, None


def _clean(s: str) -> str:
    return s.replace("**", "").replace("`", "").strip()


def decode_bytes(raw: bytes):
    """Return (text, error). UTF-8 first, then CP949."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "인코딩 판별 실패 (UTF-8로 일부 대체)"


def split_frontmatter(text: str):
    """Return (frontmatter_raw or None, body)."""
    lines = text.split("\n")
    if lines and lines[0].rstrip("\r") == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r") == "---":
                return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None, text


def norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


@dataclass
class MdNote:
    path: Path
    day: date
    label: str                     # filename tail, may be ""
    folder: str = ""               # display prefix, e.g. "업무"
    mtime: float = 0.0
    text: str = None               # None until loaded
    body: str = ""                 # text without frontmatter
    error: str = None
    headings: list = field(default_factory=list)   # (level, text)

    @property
    def title(self):
        return f"{self.folder}/{self.label or self.path.stem}"   # '/' since '\' renders as ₩ in Korean fonts

    def load(self):
        try:
            raw = self.path.read_bytes()
        except OSError as e:
            self.text, self.body, self.error = "", "", f"읽기 실패: {e}"
            return
        self.text, self.error = decode_bytes(raw)
        self.body = split_frontmatter(self.text)[1]
        self.headings = []
        in_fence = False
        for line in self.body.splitlines():
            if _FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = _H_RE.match(line)
            if m:
                self.headings.append((len(m.group(1)), _clean(m.group(2))))

    def summary_lines(self):
        """Short lines for the day cell."""
        if self.error and not self.text:
            return [f"{self.title} (읽기 실패)"]
        h2 = [t for lv, t in self.headings if lv == 2]
        if self.label:
            first, rest = self.label, h2
        elif h2:
            first, rest = h2[0], h2[1:]
        else:
            h1 = [t for lv, t in self.headings if lv == 1]
            first, rest = (h1[0] if h1 else self.path.stem), []
        return [f"{self.folder}/{first}"] + rest


class MdIndex(QObject):
    """Keeps {date: [MdNote]} for configured folders and emits changed on edits."""
    changed = Signal()

    def __init__(self, folders, memo_folder=None, parent=None):
        super().__init__(parent)
        self.folders = list(folders)
        self.memo_folder = memo_folder   # always scanned; its YYMMDD.md files are calendar memos
        self.by_day = {}
        self.missing = []            # configured folders that do not exist
        self._cache = {}             # path -> MdNote (reuse parsed content if mtime same)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._schedule)
        self._watcher.fileChanged.connect(self._schedule)
        self._debounce = QTimer(self, singleShot=True, interval=500)
        self._debounce.timeout.connect(self.rescan)

    def set_folders(self, folders):
        self.folders = list(folders)
        self.rescan()

    def set_memo_folder(self, folder):
        self.memo_folder = folder
        self.rescan()

    def _roots(self):
        roots, seen = [], set()
        for f in self.folders + ([self.memo_folder] if self.memo_folder else []):
            if norm(f) not in seen:
                seen.add(norm(f))
                roots.append(f)
        return roots

    def _schedule(self, *_):
        self._debounce.start()

    def memo_path(self, day: date):
        return Path(self.memo_folder) / f"{day:%y%m%d}.md" if self.memo_folder else None

    def rescan(self):
        by_day, dirs, files, cache = {}, [], [], {}
        roots = self._roots()
        self.missing = [f for f in roots if not os.path.isdir(f)]
        for root in roots:
            if not os.path.isdir(root):
                continue
            base = Path(root).name
            for cur, subdirs, names in os.walk(root):
                subdirs[:] = [d for d in subdirs if not d.startswith(".")]   # .obsidian, .trash, .git
                dirs.append(cur)
                rel = os.path.relpath(cur, root)
                folder = base if rel == "." else f"{base}/{rel.replace(os.sep, '/')}"
                for n in names:
                    if not n.lower().endswith(".md"):
                        continue
                    p = Path(cur) / n
                    day, label = date_from_name(p.stem)
                    if day is None:
                        continue
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        continue         # removed between listdir and stat; next scan settles it
                    old = self._cache.get(p)
                    note = old if old and old.mtime == mtime else MdNote(p, day, label, folder, mtime)
                    cache[p] = note
                    by_day.setdefault(day, []).append(note)
                    files.append(str(p))
        for notes in by_day.values():
            notes.sort(key=lambda x: (x.label != "", x.path.name))   # plain daily note first
        self._cache, self.by_day = cache, by_day
        self._rewatch(dirs + files)
        self.changed.emit()

    def _rewatch(self, paths):
        old = self._watcher.directories() + self._watcher.files()
        if old:
            self._watcher.removePaths(old)
        if paths:
            self._watcher.addPaths(paths)

    def _all(self, day):
        notes = self.by_day.get(day, [])
        for n in notes:
            if n.text is None:
                n.load()
        return notes

    def notes(self, day: date):
        """Notes for a day except the calendar memo file, content loaded on demand."""
        mp = self.memo_path(day)
        key = norm(mp) if mp else None
        return [n for n in self._all(day) if norm(n.path) != key]

    def memo_note(self, day: date):
        mp = self.memo_path(day)
        if not mp:
            return None
        key = norm(mp)
        return next((n for n in self._all(day) if norm(n.path) == key), None)
