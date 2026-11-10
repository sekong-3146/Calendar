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
_FENCE_RE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})(?P<rest>.*)$")
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
    fenced, _ = _scan_fences(lines)
    return [not f and bool(ln.strip()) and not _HEAD_RE.match(ln) for ln, f in zip(lines, fenced)]


def _scan_fences(lines):
    """(per-line True if fence delimiter or inside a fence, the fence still open at the end or None).
    A fence closes only with the same character, at least as long, nothing after it,
    and at most 3 spaces deeper than its opener."""
    flags, open_fence, open_indent, list_indent = [], None, 0, None
    for ln in lines:
        s = ln.rstrip("\r")
        m = _FENCE_RE.match(s)
        indent = len(s.expandtabs(4)) - len(s.expandtabs(4).lstrip(" "))
        if open_fence is None:
            # 4+ spaces is indented code, not a fence, unless it sits inside a list item.
            in_reach = indent <= 3 or (list_indent is not None and indent > list_indent)
            if m and in_reach and not (m["fence"][0] == "`" and "`" in m["rest"]):
                open_fence, open_indent = m["fence"], indent
            elif _LIST_RE.match(s):
                list_indent = indent
            elif s.strip() and indent == 0:
                list_indent = None                   # a top-level paragraph ends the list
            flags.append(open_fence is not None)
        else:
            flags.append(True)
            if (m and m["fence"][0] == open_fence[0] and len(m["fence"]) >= len(open_fence)
                    and not m["rest"].strip() and indent <= open_indent + 3):
                open_fence = None
    return flags, open_fence


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
    return _append_lines(body, [f"- [ ] {strip_carry_suffix(t).strip()}" for t in texts])


def append_task(body: str, text: str) -> str:
    """Append '- [ ] text' exactly as typed (quick add): no carry-suffix cleanup."""
    text = text.strip()
    return _append_lines(body, [f"- [ ] {text}"]) if text else body


def _append_lines(body: str, new_lines) -> str:
    new = "\n".join(new_lines)
    base = body.rstrip("\n")
    open_fence = _scan_fences(split_lines(base))[1]
    if open_fence:
        base += "\n" + open_fence    # close it with the same delimiter so the new lines stay tasks
    return (base + "\n" + new + "\n") if base.strip() else (new + "\n")
