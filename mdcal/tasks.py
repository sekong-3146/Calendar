"""Obsidian-style task lines ('- [ ] text'). Pure text functions, no I/O.

Line numbers are 0-based indexes into body.split("\\n") where body is the memo text
after frontmatter (memofile.MemoDoc.body / MdNote.body). Same indexing everywhere.
"""
import re
from dataclasses import dataclass
from datetime import date

OPEN, DONE, CARRIED = " ", "x", ">"

_TASK_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<bullet>[-*+]|\d+[.)])[ \t]+\[(?P<mark>[^\]])\](?:[ \t]+|$)(?P<text>.*)$")
_LIST_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<bullet>[-*+]|\d+[.)])[ \t]+(?P<text>.*)$")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
_HEAD_RE = re.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]|$)")
_CARRY_SUFFIX_RE = re.compile(r"[ \t]*\(→ \d{1,2}/\d{1,2}\)[ \t]*$")


@dataclass(frozen=True)
class Task:
    line_no: int
    indent: str
    bullet: str
    mark: str
    text: str

    @property
    def is_open(self):
        return self.mark == OPEN

    @property
    def is_done(self):
        return self.mark in ("x", "X")

    @property
    def is_carried(self):
        return self.mark == CARRIED


def split_lines(body: str) -> list:
    return body.split("\n")


def join_lines(lines) -> str:
    return "\n".join(lines)


def parse_task(line: str, line_no: int = 0):
    """Task or None. A trailing '\\r' is ignored."""
    m = _TASK_RE.match(line.rstrip("\r"))
    if not m:
        return None
    return Task(line_no, m["indent"], m["bullet"], m["mark"], m["text"])


def editable_mask(lines) -> list:
    """True where a line may be converted: not blank, not a heading, not inside/at a code fence."""
    mask, in_fence = [], False
    for ln in lines:
        if _FENCE_RE.match(ln):
            in_fence = not in_fence
            mask.append(False)
            continue
        mask.append(not in_fence and bool(ln.strip()) and not _HEAD_RE.match(ln))
    return mask


def tasks(body: str) -> list:
    """All task lines outside code fences, in order."""
    lines = split_lines(body)
    out = []
    for i, (ln, ok) in enumerate(zip(lines, editable_mask(lines))):
        if ok:
            t = parse_task(ln, i)
            if t:
                out.append(t)
    return out


def open_tasks(body: str) -> list:
    return [t for t in tasks(body) if t.is_open]


def _cr(line):
    return "\r" if line.endswith("\r") else ""


def make_task(line: str) -> str:
    """plain -> '- [ ] plain'; '- a' / '1. a' -> '- [ ] a' / '1. [ ] a'; tasks unchanged."""
    if parse_task(line) or not line.strip():
        return line
    cr, s = _cr(line), line.rstrip("\r")
    m = _LIST_RE.match(s)
    if m:
        return f"{m['indent']}{m['bullet']} [ ] {m['text']}{cr}"
    indent = s[:len(s) - len(s.lstrip(" \t"))]
    return f"{indent}- [ ] {s.lstrip(' \t')}{cr}"


def make_plain(line: str) -> str:
    """Remove the checkbox, keep the bullet: '- [ ] a' -> '- a'. Non-tasks unchanged."""
    t = parse_task(line)
    if not t:
        return line
    return f"{t.indent}{t.bullet} {t.text}{_cr(line)}"


def set_mark(line: str, mark: str) -> str:
    t = parse_task(line)
    if not t:
        return line
    return f"{t.indent}{t.bullet} [{mark}] {t.text}{_cr(line)}"


def toggle_line(line: str) -> str:
    """Ctrl+L cycle: plain/list -> '[ ]' -> '[x]' -> '[ ]'. Carried/other marks unchanged."""
    t = parse_task(line)
    if t is None:
        return make_task(line)
    if t.is_open:
        return set_mark(line, DONE)
    if t.is_done:
        return set_mark(line, OPEN)
    return line


def apply_to_lines(body: str, first: int, last: int, fn) -> str:
    """Apply fn to lines first..last (inclusive) that editable_mask allows."""
    lines = split_lines(body)
    mask = editable_mask(lines)
    for i in range(max(0, first), min(last, len(lines) - 1) + 1):
        if mask[i]:
            lines[i] = fn(lines[i])
    return join_lines(lines)


def toggle_done(body: str, line_no: int, expected_line: str) -> str:
    """Flip '[ ]' <-> '[x]' on one line (cell click).
    Raises ValueError if the line no longer matches expected_line or is not open/done."""
    lines = split_lines(body)
    if not 0 <= line_no < len(lines) or lines[line_no] != expected_line:
        raise ValueError("메모 내용이 바뀌었습니다. 다시 시도하세요.")
    t = parse_task(lines[line_no], line_no) if editable_mask(lines)[line_no] else None
    if t is None or not (t.is_open or t.is_done):
        raise ValueError("체크할 수 있는 할 일이 아닙니다.")
    lines[line_no] = set_mark(lines[line_no], DONE if t.is_open else OPEN)
    return join_lines(lines)


def continuation(line: str):
    """Enter handling for task lines.
    None: not a task (default behavior). "": empty task item (caller removes its prefix).
    Otherwise the prefix for the new line, e.g. '  - [ ] ' or '3. [ ] '."""
    t = parse_task(line)
    if t is None:
        return None
    if not t.text.strip():
        return ""
    bullet = t.bullet
    if bullet[0].isdigit():
        bullet = f"{int(bullet[:-1]) + 1}{bullet[-1]}"
    return f"{t.indent}{bullet} [ ] "


def strip_carry_suffix(text: str) -> str:
    return _CARRY_SUFFIX_RE.sub("", text)


def carried_line(line: str, target: date) -> str:
    """'- [ ] a' -> '- [>] a (→ 11/10)'."""
    t = parse_task(line)
    if not t:
        return line
    return f"{t.indent}{t.bullet} [{CARRIED}] {strip_carry_suffix(t.text)} (→ {target.month}/{target.day}){_cr(line)}"


def has_task_text(body: str, text: str) -> bool:
    """Any task (any mark) with the same text, ignoring carry suffix and surrounding spaces."""
    key = strip_carry_suffix(text).strip()
    return any(strip_carry_suffix(t.text).strip() == key for t in tasks(body))


def append_open_tasks(body: str, texts) -> str:
    """Append '- [ ] text' lines at the end of body."""
    if not texts:
        return body
    new = "\n".join(f"- [ ] {strip_carry_suffix(t).strip()}" for t in texts)
    base = body.rstrip("\n")
    if sum(1 for ln in split_lines(base) if _FENCE_RE.match(ln)) % 2:
        base += "\n```"      # close an unclosed fence so appended tasks stay tasks (renders the same)
    return (base + "\n" + new + "\n") if base.strip() else (new + "\n")
