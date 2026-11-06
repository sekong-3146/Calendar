"""Calendar memo = one md file per day (<memo_folder>\\YYMMDD.md). The md file is the source of truth."""
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .mdsource import decode_bytes, split_frontmatter


class MemoConflict(Exception):
    """File changed on disk (e.g. edited in Obsidian) since it was read."""


@dataclass
class MemoDoc:
    body: str
    frontmatter: str      # raw text between --- lines, None if absent
    mtime: float          # None if the file does not exist


class MemoFiles:
    def __init__(self, folder):
        self.folder = Path(folder) if folder else None

    @property
    def enabled(self):
        return self.folder is not None and self.folder.is_dir()

    def path(self, day: date) -> Path:
        return self.folder / f"{day:%y%m%d}.md"

    def display_name(self, day: date) -> str:
        return f"{self.folder.name}/{day:%y%m%d}.md"

    def _mtime(self, p):
        try:
            return p.stat().st_mtime
        except FileNotFoundError:
            return None

    def read(self, day: date) -> MemoDoc:
        p = self.path(day)
        mtime = self._mtime(p)
        if mtime is None:
            return MemoDoc("", None, None)
        text, _err = decode_bytes(p.read_bytes())
        fm, body = split_frontmatter(text)
        return MemoDoc(body, fm, mtime)

    def write(self, day: date, body: str, expected_mtime):
        """Write body (keeps existing frontmatter). Empty body moves the file to vault .trash.
        Raises MemoConflict if the file changed since expected_mtime. Returns new mtime or None."""
        p = self.path(day)
        cur = self._mtime(p)
        if cur != expected_mtime:
            raise MemoConflict(str(p))
        if not body.strip():
            if cur is not None:
                self._to_trash(p)
            return None
        fm = None
        if cur is not None:
            fm, _ = split_frontmatter(decode_bytes(p.read_bytes())[0])
        if fm is None:
            fm = f"date: {day.isoformat()}"
        if not body.endswith("\n"):
            body += "\n"
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"---\n{fm}\n---\n{body}")
        os.replace(tmp, p)
        return self._mtime(p)

    def _to_trash(self, p: Path):
        """Obsidian-style trash (<vault>\\.trash) instead of permanent delete."""
        trash = self.folder / ".trash"
        trash.mkdir(exist_ok=True)
        dst = trash / p.name
        if dst.exists():
            dst = trash / f"{p.stem} {datetime.now():%Y%m%d%H%M%S}{p.suffix}"
        os.replace(p, dst)
