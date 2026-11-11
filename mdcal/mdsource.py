"""Scan MD folders, map files to dates by filename, watch for changes. Read-only."""
import os
import re
import stat
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
REVIVE_MS = 5000                 # how often a missing configured folder is checked for its return
WATCH_FAILED = "변경 감시 등록 실패"   # scan_errors prefix: contents were read, only the watch is missing


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


@dataclass
class _Dir:
    path: str                      # as scanned (original case)
    notes: list


class MdIndex(QObject):
    """Keeps {date: [MdNote]} for configured folders and emits changed on edits."""
    changed = Signal()
    scanError = Signal(str)          # a folder became unreadable during a background rescan

    def __init__(self, folders, memo_folder=None, parent=None):
        super().__init__(parent)
        self.folders = list(folders)
        self.memo_folder = memo_folder   # always scanned; its YYMMDD.md files are calendar memos
        self.by_day = {}
        self.missing = []            # configured folders that do not exist
        self.scan_errors = []       # surfaced on startup and explicit refresh only
        self._cache = {}             # path -> MdNote (reuse parsed content if mtime same)
        self._dirs = {}              # norm(dir) -> _Dir; lets one folder be rescanned alone
        self._previous = {}          # _dirs of the last scan, only while a full rescan runs
        self._unreadable = set()     # folders that exist but could not be read (reported once)
        self._quiet = False          # True during a full rescan: no scanError signals
        self._watch_retry = set()    # paths whose watch registration failed on the last rebuild
        self._watch_reported = set() # ...and whose final failure the user was already told about
        self._pending = set()        # changed folders waiting for the debounce
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._schedule)
        self._watcher.fileChanged.connect(lambda p: self._schedule(os.path.dirname(os.path.normpath(p))))
        self._debounce = QTimer(self, singleShot=True, interval=500)
        self._debounce.timeout.connect(self._flush_pending)
        # A missing root has nothing to watch, so it is polled; runs only while self.missing is not empty.
        self._revive = QTimer(self, interval=REVIVE_MS)
        self._revive.timeout.connect(self._check_missing)

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

    def _schedule(self, path):
        self._pending.add(os.path.normpath(path))
        self._debounce.start()

    def _flush_pending(self):
        paths, self._pending = self._pending, set()
        self.rescan_dirs(paths)

    def memo_path(self, day: date):
        return Path(self.memo_folder) / f"{day:%y%m%d}.md" if self.memo_folder else None

    def refresh_memos(self):
        """Pick up a memo file we just wrote without waiting for the watcher."""
        if self.memo_folder:
            self.rescan_dirs([self.memo_folder])

    def rescan(self):
        """Full scan of every configured folder (startup, manual refresh, folder list change)."""
        self._debounce.stop()
        self._pending.clear()
        self.missing = []
        self.scan_errors = []
        self._previous, self._dirs = self._dirs, {}
        self._quiet = True               # the caller shows scan_errors itself after a full scan
        try:
            for root in self._roots():
                try:
                    is_dir = stat.S_ISDIR(os.stat(root).st_mode)
                except FileNotFoundError:
                    self.missing.append(root)
                    continue
                except OSError as e:
                    self._scan_failed(root, e)
                    continue
                if not is_dir:
                    self.missing.append(root)
                    continue
                self._walk(root, root)
        finally:
            self._previous, self._quiet = {}, False
        self._sync_revive()
        self._rebuild(rewatch=set(self._dirs))

    def _sync_revive(self):
        if self.missing and not self._revive.isActive():
            self._revive.start()
        elif not self.missing:
            self._revive.stop()

    def _check_missing(self):
        """A configured folder that was missing (never there, deleted, drive unplugged) is back."""
        touched = set()
        for root in [r for r in self.missing if os.path.isdir(r)]:
            touched |= self._rewalk(root, root)
            if norm(root) in self._dirs:             # still unreadable: stays missing, tried again
                self.missing.remove(root)
        self._sync_revive()
        self.scan_errors = list(dict.fromkeys(self.scan_errors))[-50:]   # polled: must not grow
        if touched:
            self._rebuild(rewatch=touched)

    def rescan_dirs(self, paths):
        """Rescan only these folders (not their unchanged subfolders). Unknown paths are ignored."""
        touched = set()
        live = {norm(p) for p in self._watcher.directories()}
        for path in paths:
            key = norm(path)
            root = self._root_of(key)
            if root is None:
                continue
            known = {k for k, d in self._dirs.items() if k != key and norm(os.path.dirname(d.path)) == key}
            try:
                subdirs = self._scan_dir(root, os.path.normpath(str(path)))
            except FileNotFoundError:
                self._drop(key)                      # folder is gone
                self._roots_lost(key)
                continue
            except OSError as e:
                self._scan_failed(path, e)           # unreadable right now: keep contents and watches
                continue
            touched.add(key)
            found = {norm(s) for s in subdirs}
            for k in known - found:
                self._drop(k)
            for s in subdirs:
                # New subfolder, one deleted and recreated (its watch died with the old folder),
                # or one whose last read failed (its kept contents may be stale).
                if norm(s) not in self._dirs or norm(s) not in live or norm(s) in self._unreadable:
                    touched.update(self._rewalk(root, s))
        self.scan_errors = list(dict.fromkeys(self.scan_errors))[-50:]
        self._rebuild(rewatch=touched)

    @staticmethod
    def _under(key, parent):
        """key is parent or below it. rstrip: a drive root ('d:\\') already ends with a separator."""
        return key == parent or key.startswith(parent.rstrip(os.sep) + os.sep)

    def _root_of(self, key):
        for root in self._roots():
            if self._under(key, norm(root)):
                return root
        return None

    def _drop(self, key):
        for k in [k for k in self._dirs if self._under(k, key)]:
            del self._dirs[k]
        self._unreadable = {k for k in self._unreadable if not self._under(k, key)}

    def _roots_lost(self, key):
        """A deleted folder was (or contained) a configured root: nothing is left to watch for
        its return, so remember it as missing (polled by _check_missing) and tell the user once."""
        lost = [r for r in self._roots()
                if self._under(norm(r), key) and r not in self.missing and not os.path.isdir(r)]
        if lost:
            self.missing.extend(lost)
            self._sync_revive()
            self.scanError.emit("MD 폴더가 사라졌습니다 (다시 생기면 자동으로 읽음)\n" + "\n".join(lost))

    def _rewalk(self, root, start):
        """Replace the subtree at start with a fresh walk. Parts that cannot be read keep their
        old contents (via _previous), and their 'already reported' mark, instead of vanishing."""
        key = norm(start)
        self._previous = {k: d for k, d in self._dirs.items() if self._under(k, key)}
        for k in self._previous:
            del self._dirs[k]
        try:
            return self._walk(root, start)
        finally:
            self._previous = {}

    def _scan_failed(self, path, error):
        """A folder exists but cannot be read. Not treated as deleted; reported once per folder."""
        key = norm(path)
        self.scan_errors.append(f"{path}: {error}")
        for k, d in self._previous.items():          # full scan: keep what we knew below it
            if self._under(k, key):
                self._dirs.setdefault(k, d)
        if key not in self._unreadable:
            self._unreadable.add(key)
            if not self._quiet:
                self.scanError.emit(f"폴더를 읽을 수 없습니다 (마지막으로 읽은 내용 유지)\n{path}\n{error}")

    def _walk(self, root, start):
        """Scan start and everything below it. Returns the keys scanned."""
        keys, stack = set(), [start]
        while stack:
            cur = stack.pop()
            try:
                subdirs = self._scan_dir(root, cur)
            except FileNotFoundError:
                continue                             # Removed while walking.
            except OSError as e:
                self._scan_failed(cur, e)
                continue
            keys.add(norm(cur))
            stack.extend(subdirs)
        return keys

    def _scan_dir(self, root, cur):
        """Read one folder into self._dirs and return its subfolder paths. Raises OSError if unreadable."""
        with os.scandir(cur) as it:
            entries = list(it)
        base = Path(root).name or str(root)          # drive root has no name
        rel = os.path.relpath(cur, root)
        folder = base if rel == "." else f"{base}/{rel.replace(os.sep, '/')}"
        notes, subdirs = [], []
        for entry in entries:
            name = entry.name
            try:
                if entry.is_junction():
                    continue                         # like os.walk: never descend through links (cycles)
                if entry.is_dir(follow_symlinks=False):
                    if not name.startswith("."):     # .obsidian, .trash, .git
                        subdirs.append(entry.path)
                    continue
                if not name.lower().endswith(".md") or not entry.is_file():
                    continue
                p = Path(entry.path)
                day, label = date_from_name(p.stem)
                if day is None:
                    continue
                mtime = p.stat().st_mtime
            except FileNotFoundError:
                continue                             # Removed between listing and stat.
            except OSError as e:
                self.scan_errors.append(f"{entry.path}: {e}")
                kept = self._cache.get(Path(entry.path))
                if kept is not None:                 # locked for a moment: keep showing the note
                    notes.append(kept)
                continue
            old = self._cache.get(p)
            notes.append(old if old and old.mtime == mtime else MdNote(p, day, label, folder, mtime))
        self._dirs[norm(cur)] = _Dir(cur, notes)
        self._unreadable.discard(norm(cur))
        return subdirs

    def watched(self):
        """Paths the OS watcher really holds, normalized (not what we asked for)."""
        return {norm(p) for p in self._watcher.directories() + self._watcher.files()}

    def _rebuild(self, rewatch):
        """Recompute by_day from self._dirs and sync the watcher. rewatch: folder keys just scanned."""
        by_day, cache, wanted = {}, {}, {}
        for d in self._dirs.values():
            wanted[norm(d.path)] = d.path
            for note in d.notes:
                cache[note.path] = note
                by_day.setdefault(note.day, []).append(note)
                wanted[norm(note.path)] = str(note.path)
        for notes in by_day.values():
            notes.sort(key=lambda x: (x.label != "", x.path.name))   # plain daily note first
        self._cache, self.by_day = cache, by_day
        # Compare with what the watcher really holds: it drops deleted folders/files by itself.
        live = {norm(p): p for p in self._watcher.directories() + self._watcher.files()}
        # A replaced file (tmp -> rename, as we and Obsidian save) can lose its watch while still
        # listed, so files of the folders just scanned are always re-added.
        again = {norm(n.path) for k in rewatch if k in self._dirs for n in self._dirs[k].notes}
        remove = [p for k, p in live.items() if k not in wanted or k in again]
        if remove:
            self._watcher.removePaths(remove)
        add = [p for k, p in wanted.items() if k not in live or k in again]
        failed = {norm(p): p for p in (self._watcher.addPaths(add) if add else [])}
        # The watcher forgets a deleted path only after its own event is processed; until then
        # it refuses to add the recreated path. Try once more after the debounce, then report.
        gave_up = []
        for k, p in failed.items():
            if not os.path.exists(p):
                continue                             # vanished meanwhile: nothing to watch
            if k not in self._watch_retry:
                self._schedule(p if os.path.isdir(p) else os.path.dirname(p))
                continue
            self.scan_errors.append(f"{WATCH_FAILED}: {p}")
            if k not in self._watch_reported:
                gave_up.append(p)
        self._watch_retry = set(failed)
        self._watch_reported = (self._watch_reported & set(failed)) | {norm(p) for p in gave_up}
        if gave_up:                                  # one message, each path reported once
            more = f"\n외 {len(gave_up) - 3}개" if len(gave_up) > 3 else ""
            self.scanError.emit("변경 감시를 등록하지 못했습니다 (새로고침해야 반영됨)\n"
                                + "\n".join(gave_up[:3]) + more)
        self.changed.emit()

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
