"""Period summary: what was done between two dates, as one Markdown text.

Titles only (finished task lines, note titles and H2 headings); note bodies are not copied.
Unfinished tasks ('- [ ]', '- [>]') are left out: this is a record of what was done.
Reads through the index, so it sees exactly what the calendar shows.
"""
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import tasks
from .mdsource import WATCH_FAILED
from .memofile import replace_retry

WEEKDAYS = "월화수목금토일"
# Text that would start a Markdown block (fence, heading, quote, rule, list, checkbox) when it
# opens a list item, although it was plain text behind '- [x] ' in the memo.
_BLOCK_RE = re.compile(r"^(?:`{3,}|~{3,}|#{1,6}(?:\s|$)|>|[-+*](?:\s|$)|\[.\](?:\s|$)"
                       r"|(?:[-*_]\s*){3,}$|=+\s*$|(?P<num>\d+)[.)](?:\s|$))")


def _item(text: str) -> str:
    """List item text that renders as the same characters it had in the memo."""
    m = _BLOCK_RE.match(text)
    if not m:
        return text
    at = len(m["num"]) if m["num"] else 0        # '1. a' -> '1\. a'
    return text[:at] + "\\" + text[at:]


@dataclass
class Report:
    text: str
    done: int = 0
    notes: int = 0
    errors: int = 0          # memos/notes that could not be read (listed under their day)
    unread: list = field(default_factory=list)   # folders/files the last scan could not check

    @property
    def empty(self):
        return not (self.done or self.notes or self.errors)


def default_name(start: date, end: date) -> str:
    """Never starts with a date: such a file in an MD folder would show up as that day's note."""
    if start == end:
        return f"업무정리 {start:%y%m%d}.md"
    return f"업무정리 {start:%y%m%d}-{end:%y%m%d}.md"


def build_report(index, start: date, end: date) -> Report:
    if start > end:
        start, end = end, start
    sections = []
    done = notes = errors = 0
    d = start
    while d <= end:
        lines = []
        memo = index.memo_note(d)
        if memo is not None:
            if memo.error:   # never skip silently: the summary would look complete
                errors += 1
                lines.append(f"- 메모 읽기 실패: {memo.error}")
            # A carried line ('- [>]') is not done; the task counts on the day it is checked.
            finished = [t.text.strip() for t in tasks.tasks(memo.body) if t.is_done and t.text.strip()]
            if finished:
                done += len(finished)
                lines.append(f"- 완료 {len(finished)}건")
                lines += [f"  - {_item(t)}" for t in finished]
        day_notes = []
        for n in index.notes(d):
            summary = n.summary_lines()
            line = summary[0]
            if n.error:
                errors += 1
                if n.text:                           # unreadable notes already say so in their title
                    line += f" ({n.error})"
            else:
                notes += 1
            day_notes.append([f"  - {_item(line)}"] + [f"    - {_item(h)}" for h in summary[1:]])
        if day_notes:
            lines.append(f"- 노트 {len(day_notes)}개")
            lines += [ln for note_lines in day_notes for ln in note_lines]
        if lines:
            sections.append(f"## {d:%Y-%m-%d} ({WEEKDAYS[d.weekday()]})\n" + "\n".join(lines))
        d += timedelta(days=1)

    period = f"{start:%Y-%m-%d}" if start == end else f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
    head = [f"# 업무 정리 {period}", "", "## 요약",
            f"- 완료 {done}건 / 노트 {notes}개"]
    if errors:
        head.append(f"- 읽기 실패 {errors}건 (해당 날짜에 표시, 이 정리에서 빠진 내용이 있을 수 있음)")
    # A folder that could not be scanned has no note to carry the error: name it, or the summary
    # would pass off old (or missing) contents as complete.
    unread = [f"{p}: 폴더 없음" for p in index.missing]
    unread += [e for e in index.scan_errors if not e.startswith(WATCH_FAILED)]
    unread = list(dict.fromkeys(unread))
    tail = []
    if unread:
        head.append(f"- 확인하지 못한 폴더·파일 {len(unread)}곳 (맨 아래 목록, 이 정리에서 빠진 내용이 있을 수 있음)")
        tail.append("## 확인하지 못한 곳\n" + "\n".join(f"- {_item(e)}" for e in unread))
    return Report("\n\n".join(["\n".join(head)] + sections + tail) + "\n", done, notes, errors, unread)


def save_report(path, text: str):
    """Atomic write (tmp then replace), UTF-8. Raises OSError; no half-written file is left."""
    path = Path(path)
    # A fresh name: a fixed '<name>.tmp' could be somebody else's file in a folder the user picked.
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        replace_retry(tmp, path)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup:
            raise OSError(f"{e}\n임시 파일도 지우지 못했습니다: {tmp} ({cleanup})") from e
        raise
