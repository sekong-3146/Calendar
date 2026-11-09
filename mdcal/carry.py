"""Carry open tasks forward, saving the target before marking each source."""
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import tasks
from .memofile import MemoConflict


@dataclass
class CarryResult:
    moved: int = 0
    marked: int = 0
    days: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def pending_days(memos, target: date, lookback: int) -> dict:
    """Count open tasks in existing memos before the target day."""
    pending = {}
    if lookback <= 0:
        return pending
    for offset in range(lookback, 0, -1):
        day = target - timedelta(days=offset)
        if not memos.path(day).exists():
            continue
        doc = memos.read(day)
        if doc.error:
            raise OSError(f"{memos.display_name(day)}: 메모 인코딩 오류로 읽을 수 없습니다.\n{doc.error}")
        count = len(tasks.open_tasks(doc.body))
        if count:
            pending[day] = count
    return pending


def carry(memos, sources: list, target: date) -> CarryResult:
    """Stop on the first failure; a retry deduplicates any saved target tasks."""
    result = CarryResult()
    if not memos.enabled:
        result.errors.append("메모 저장 폴더가 없습니다.")
        return result

    for source in sorted(sources):
        if source >= target:
            continue
        day, action = source, "원본 읽기"
        try:
            source_doc = memos.read(source)
            if source_doc.error:
                raise OSError(f"메모 인코딩 오류: {source_doc.error}")
            opens = tasks.open_tasks(source_doc.body)
            if not opens:
                continue

            day, action = target, "대상 읽기"
            target_doc = memos.read(target)
            if target_doc.error:
                raise OSError(f"메모 인코딩 오류: {target_doc.error}")
            new, seen = [], set()
            for task in opens:
                key = tasks.strip_carry_suffix(task.text).strip()
                if key not in seen and not tasks.has_task_text(target_doc.body, task.text):
                    new.append(task.text)
                    seen.add(key)

            if new:
                day, action = target, "대상 저장"
                body = tasks.append_open_tasks(target_doc.body, new)
                if not body.strip():
                    raise OSError("빈 본문 저장을 거부했습니다.")
                memos.write(target, body, target_doc.mtime)
                result.moved += len(new)

            day, action = source, "원본 저장"
            lines = tasks.split_lines(source_doc.body)
            for task in opens:
                lines[task.line_no] = tasks.carried_line(lines[task.line_no], target)
            body = tasks.join_lines(lines)
            if not body.strip():
                raise OSError("빈 본문 저장을 거부했습니다.")
            memos.write(source, body, source_doc.mtime)
            result.marked += len(opens)
            result.days.append(source)
        except MemoConflict:
            result.errors.append(
                f"{memos.display_name(day)}: {action} 중 다른 곳에서 변경됨. 이월을 중단했습니다."
            )
            break
        except OSError as error:
            result.errors.append(f"{memos.display_name(day)}: {action} 실패. 이월을 중단했습니다.\n{error}")
            break
    return result
