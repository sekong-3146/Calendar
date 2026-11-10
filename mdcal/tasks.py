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
    """(per-line True if fence delimiter or inside a fence,
        the top-level fence still open at the end or None).
    Follows CommonMark for fences and list items: indentation counts from the list item
    the line sits in (4+ there is indented code, not a fence); a fence closes with the same
    character, at least as long, nothing after it; a fence inside a list item also ends
    when that item ends, so it never needs a closing line from us.
    Checked against Qt's parser (md4c) on random documents; Obsidian itself was not checked."""
    flags, items = [], []            # items: content columns of the open list items, outermost first
    fence, base = None, 0            # open delimiter; content column of the item holding it (0: none)
    text_before = False              # previous line was paragraph text (a plain line may continue it)
    for ln in lines:
        s = ln.rstrip("\r").expandtabs(4)
        blank = not s.strip()
        indent = len(s) - len(s.lstrip(" "))
        m = _FENCE_RE.match(s)
        if fence is not None:
            closes = (m and m["fence"][0] == fence[0] and len(m["fence"]) >= len(fence)
                      and not m["rest"].strip())
            left_item = bool(base) and not blank and indent < base
            if left_item:                            # the list item ended, and its code block with it
                while items and indent < items[-1]:
                    items.pop()
                fence, text_before = None, False
                if closes and indent <= 3:           # ...and a closing line there is still its closer
                    flags.append(True)
                    continue
            else:
                flags.append(True)
                if closes and indent - base <= 3:
                    fence = None
                text_before = False
                continue
        if blank:
            flags.append(False)
            text_before = False
            continue
        lazy = (text_before and items and indent < items[-1] and not m
                and not _LIST_RE.match(s) and not _HEAD_RE.match(s))
        if not lazy:                                 # a lazy line continues the item's paragraph
            while items and indent < items[-1]:
                items.pop()
        base = items[-1] if items else 0
        m = None
        if indent - base <= 3:
            item = _LIST_RE.match(s)
            if item:                                 # '- ```' : the fence may start on the bullet line
                gap = len(s) - indent - len(item["bullet"]) - len(item["text"])
                base = indent + len(item["bullet"]) + (gap if gap <= 4 else 1)
                items.append(base)
                m = _FENCE_RE.match(item["text"])
            else:
                m = _FENCE_RE.match(s)
        if m and not (m["fence"][0] == "`" and "`" in m["rest"]):
            fence = m["fence"]
        flags.append(fence is not None)
        text_before = fence is None
    return flags, (fence if base == 0 else None)


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
