"""Calendar memo = one md file per day (<memo_folder>\\YYMMDD.md). The md file is the source of truth."""
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .mdsource import decode_bytes, split_frontmatter


def replace_retry(src, dst, tries=3, wait=0.05):
    """os.replace, retried briefly when the target is locked for a moment
    (virus scanner, indexer, sync tool). The last failure is raised."""
    for left in range(tries - 1, -1, -1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not left:
                raise
            time.sleep(wait)


class MemoConflict(Exception):
    """File changed on disk (e.g. edited in Obsidian) since it was read."""


@dataclass
class MemoDoc:
    body: str
    frontmatter: str      # raw text between --- lines, None if absent
    mtime: float          # None if the file does not exist
    error: str | None = None


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
        text, error = decode_bytes(p.read_bytes())
        fm, body = split_frontmatter(text)
        return MemoDoc(body, fm, mtime, error)

    def write(self, day: date, body: str, expected_mtime):
        """Write body (keeps existing frontmatter). Empty body moves the file to vault .trash.
        Raises MemoConflict if the file changed since expected_mtime. Returns new mtime or None."""
        p = self.path(day)
        cur = self._mtime(p)
        if cur != expected_mtime:
            raise MemoConflict(str(p))
        fm = None
        if cur is not None:
            text, error = decode_bytes(p.read_bytes())
            if error:
                raise OSError(f"메모 인코딩 오류로 저장할 수 없습니다: {p}\n{error}")
            fm, _ = split_frontmatter(text)
        if not body.strip():
            if cur is not None:
                self._to_trash(p)
            return None
        if fm is None:
            fm = f"date: {day.isoformat()}"
        if not body.endswith("\n"):
            body += "\n"
        tmp = p.with_name(p.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"---\n{fm}\n---\n{body}")
            replace_retry(tmp, p)
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)          # never leave a half-saved copy in the vault
            except OSError as cleanup:           # say so in the message callers show to the user
                raise OSError(f"{e}\n임시 파일도 지우지 못했습니다: {tmp} ({cleanup})") from e
            raise
        return self._mtime(p)

    def _to_trash(self, p: Path):
        """Obsidian-style trash (<vault>\\.trash) instead of permanent delete."""
        trash = self.folder / ".trash"
        trash.mkdir(exist_ok=True)
        dst = trash / p.name
        if dst.exists():
            dst = trash / f"{p.stem} {datetime.now():%Y%m%d%H%M%S}{p.suffix}"
        os.replace(p, dst)
