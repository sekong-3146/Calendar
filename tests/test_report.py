"""Period summary text and its file (no Qt widgets)."""
import tests  # noqa: F401  (sandbox first: must precede every mdcal import)
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mdcal import report
from mdcal.mdsource import MdNote, date_from_name

D3, D4, D5, D7 = date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 7)


def note(day, label, headings=(), folder="업무", error=None, text="x"):
    return MdNote(Path(f"{day:%y%m%d} {label}.md"), day, label, folder, text=text, error=error,
                  headings=list(headings))


class FakeIndex:
    def __init__(self, memos=None, notes=None):
        self._memos, self._notes = memos or {}, notes or {}
        self.missing, self.scan_errors = [], []

    def memo_note(self, day):
        m = self._memos.get(day)
        if m is None:
            return None
        return m if not isinstance(m, str) else SimpleNamespace(body=m, error=None)

    def notes(self, day):
        return self._notes.get(day, [])


class BuildTest(unittest.TestCase):
    def test_done_tasks_and_notes(self):
        index = FakeIndex(
            {D3: "- [x] 보고서 초안\n- [ ] 견적서 회신\n메모 줄\n```\n- [x] 코드 안\n```",
             D5: "- [x] 메일 답장\n  - [X] 하위 항목"},
            {D5: [note(D5, "회의", [(1, "제목"), (2, "안건"), (2, "결정사항")])]})
        r = report.build_report(index, D3, D7)
        self.assertEqual((r.done, r.notes, r.errors), (3, 1, 0))
        self.assertEqual(r.text, (
            "# 업무 정리 2026-11-03 ~ 2026-11-07\n\n"
            "## 요약\n- 완료 3건 / 노트 1개\n\n"
            "## 2026-11-03 (화)\n- 완료 1건\n  - 보고서 초안\n\n"
            "## 2026-11-05 (목)\n- 완료 2건\n  - 메일 답장\n  - 하위 항목\n"
            "- 노트 1개\n  - 업무/회의\n    - 안건\n    - 결정사항\n"))

    def test_a_carried_task_is_counted_once_on_the_day_it_is_done(self):
        index = FakeIndex({D3: "- [>] 견적서 (→ 11/4)", D4: "- [>] 견적서 (→ 11/5)", D5: "- [x] 견적서"})
        r = report.build_report(index, D3, D5)
        self.assertEqual(r.done, 1)
        self.assertEqual(r.text.count("견적서"), 1)
        self.assertNotIn("2026-11-03", r.text.split("## 요약")[1])

    def test_unfinished_tasks_are_left_out(self):
        index = FakeIndex({D3: "- [ ] 견적서 회신\n- [>] 넘긴 일 (→ 11/9)", D5: "- [x] 운동"})
        r = report.build_report(index, D3, D5)
        self.assertEqual(r.done, 1)
        for word in ("견적서", "넘긴 일", "미완료"):
            self.assertNotIn(word, r.text)
        self.assertTrue(report.build_report(index, D3, D4).empty)        # only open tasks: nothing done

    def test_empty_period(self):
        r = report.build_report(FakeIndex({D3: "그냥 메모\n- [-] 취소한 일\n- [ ] 아직"}), D3, D7)
        self.assertTrue(r.empty)
        self.assertNotIn("## 2026", r.text)

    def test_reversed_and_single_day_range(self):
        index = FakeIndex({D3: "- [x] a"})
        self.assertEqual(report.build_report(index, D5, D3).text, report.build_report(index, D3, D5).text)
        self.assertTrue(report.build_report(index, D3, D3).text.startswith("# 업무 정리 2026-11-03\n"))
        self.assertTrue(report.build_report(index, D4, D4).empty)      # days outside are not read

    def test_unreadable_days_are_listed_not_dropped(self):
        index = FakeIndex(
            {D3: SimpleNamespace(body="", error="읽기 실패: locked"),
             D4: SimpleNamespace(body="- [x] 살아남은 줄", error="인코딩 판별 실패")},
            {D5: [note(D5, "회의", error="읽기 실패: gone", text=""),
                  note(D5, "깨진", error="인코딩 판별 실패", text="abc")]})
        r = report.build_report(index, D3, D5)
        self.assertEqual((r.done, r.notes, r.errors), (1, 0, 4))
        self.assertFalse(r.empty)
        self.assertIn("- 읽기 실패 4건", r.text)
        self.assertIn("## 2026-11-03 (화)\n- 메모 읽기 실패: 읽기 실패: locked", r.text)
        self.assertIn("  - 살아남은 줄", r.text)
        self.assertIn("  - 업무/회의 (읽기 실패)\n  - 업무/깨진 (인코딩 판별 실패)", r.text)


    def test_folders_the_scan_could_not_check_are_named(self):
        index = FakeIndex({D3: "- [x] 예전에 읽어 둔 일"})
        index.missing = [r"X:\없는폴더"]
        index.scan_errors = [r"C:\vault\잠김: [WinError 5] denied", r"C:\vault\잠김: [WinError 5] denied",
                             r"변경 감시 등록 실패: C:\vault\a"]          # read fine, only not watched
        r = report.build_report(index, D3, D5)
        self.assertEqual((r.done, r.errors, len(r.unread)), (1, 0, 2))
        self.assertIn("- 확인하지 못한 폴더·파일 2곳", r.text)
        self.assertTrue(r.text.endswith(
            "## 확인하지 못한 곳\n- X:\\없는폴더: 폴더 없음\n- C:\\vault\\잠김: [WinError 5] denied\n"))
        nothing = report.build_report(index, D4, D5)                     # nothing found, but not "all clear"
        self.assertTrue(nothing.empty and nothing.unread)
        self.assertNotIn("확인하지 못한", report.build_report(FakeIndex({D3: "- [x] a"}), D3, D3).text)

    def test_titles_that_look_like_markdown_blocks_still_show_as_text(self):
        tests.qapp()
        from PySide6.QtGui import QTextDocument
        titles = ["```deploy", "~~~", "---", "* * *", "# 해시", "> 인용", "- 대시", "+ 플러스",
                  "1. 번호", "12) 번호", "[ ] 상자", "===", "*강조* 는 그대로", "#태그", "-5도"]
        index = FakeIndex({D3: "\n".join(f"- [x] {t}" for t in titles) + "\n- [x] 마지막"},
                          {D3: [note(D3, "회의", [(2, "```코드"), (2, "- 목록")])]})
        r = report.build_report(index, D3, D3)
        self.assertEqual(r.done, len(titles) + 1)
        doc = QTextDocument()
        doc.setMarkdown(r.text)
        shown = [ln.strip() for ln in doc.toPlainText().split("\n")]
        for t in titles + ["마지막", "```코드", "- 목록"]:
            self.assertIn(t.replace("*강조*", "강조"), shown, t)
        self.assertIn("  - *강조* 는 그대로", r.text)                    # inline text is not escaped
        self.assertIn("  - #태그\n  - -5도", r.text)


class FileTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="mdcal_report_"))

    def test_save_never_touches_another_tmp_file(self):
        p = self.dir / "정리.md"
        other = self.dir / "정리.md.tmp"
        other.write_text("someone else's draft", "utf-8")
        report.save_report(p, "new")
        with mock.patch.object(report, "replace_retry", side_effect=PermissionError("locked")):
            with self.assertRaises(OSError):
                report.save_report(p, "newer")
        self.assertEqual(other.read_text("utf-8"), "someone else's draft")
        self.assertEqual(sorted(x.name for x in self.dir.iterdir()), ["정리.md", "정리.md.tmp"])

    def test_default_name_is_not_read_back_as_a_dated_note(self):
        for name in (report.default_name(D3, D7), report.default_name(D3, D3)):
            self.assertEqual(date_from_name(Path(name).stem), (None, None), name)
        self.assertEqual(report.default_name(D3, D7), "업무정리 261103-261107.md")

    def test_save_writes_utf8_and_replaces(self):
        p = self.dir / "정리.md"
        p.write_text("old", "utf-8")
        report.save_report(p, "# 한글\n- a\n")
        self.assertEqual(p.read_bytes(), "# 한글\n- a\n".encode("utf-8"))
        self.assertEqual([x.name for x in self.dir.iterdir()], ["정리.md"])

    def test_failed_save_keeps_the_old_file_and_leaves_no_tmp(self):
        p = self.dir / "정리.md"
        p.write_text("old", "utf-8")
        with mock.patch.object(report, "replace_retry", side_effect=PermissionError("locked")):
            with self.assertRaises(OSError):
                report.save_report(p, "new")
        self.assertEqual(p.read_text("utf-8"), "old")
        self.assertEqual([x.name for x in self.dir.iterdir()], ["정리.md"])


if __name__ == "__main__":
    unittest.main()
