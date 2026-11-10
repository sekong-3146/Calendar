import tests  # noqa: F401  (sandbox first: must precede every mdcal import)
import os
import tempfile
import unittest
from unittest import mock
from datetime import date
from pathlib import Path

from mdcal import carry
from mdcal.memofile import MemoConflict, MemoFiles

D9, D10 = date(2026, 11, 9), date(2026, 11, 10)


class MemoFilesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="mdcal_memo_"))
        self.memos = MemoFiles(str(self.dir))

    def test_write_then_read(self):
        mtime = self.memos.write(D10, "hello", None)
        self.assertEqual((self.dir / "261110.md").read_text("utf-8"), "---\ndate: 2026-11-10\n---\nhello\n")
        doc = self.memos.read(D10)
        self.assertEqual((doc.body, doc.mtime, doc.error), ("hello\n", mtime, None))

    def test_existing_frontmatter_is_kept(self):
        (self.dir / "261110.md").write_text("---\ntags: x\n---\nold\n", "utf-8")
        doc = self.memos.read(D10)
        self.memos.write(D10, "new", doc.mtime)
        self.assertEqual((self.dir / "261110.md").read_text("utf-8"), "---\ntags: x\n---\nnew\n")

    def test_conflict_when_changed_outside(self):
        mtime = self.memos.write(D10, "a", None)
        p = self.dir / "261110.md"
        os.utime(p, (mtime + 5, mtime + 5))
        with self.assertRaises(MemoConflict):
            self.memos.write(D10, "b", mtime)
        with self.assertRaises(MemoConflict):      # file appeared since we saw "no file"
            self.memos.write(D10, "b", None)
        self.assertIn("a", p.read_text("utf-8"))

    def test_empty_body_moves_file_to_trash(self):
        mtime = self.memos.write(D10, "a", None)
        self.assertIsNone(self.memos.write(D10, "  \n", mtime))
        self.assertFalse((self.dir / "261110.md").exists())
        self.assertTrue((self.dir / ".trash" / "261110.md").exists())

    def test_undecodable_file_is_not_overwritten(self):
        p = self.dir / "261110.md"
        raw = b"\xff\xfe\xfd broken \x81"
        p.write_bytes(raw)
        doc = self.memos.read(D10)
        self.assertIsNotNone(doc.error)
        with self.assertRaises(OSError):
            self.memos.write(D10, "new", doc.mtime)
        self.assertEqual(p.read_bytes(), raw)

    def test_failed_save_leaves_no_tmp_file_and_keeps_the_old_text(self):
        mtime = self.memos.write(D10, "old", None)
        with mock.patch("mdcal.memofile.os.replace", side_effect=PermissionError(5, "locked")) as rep:
            with self.assertRaises(PermissionError):
                self.memos.write(D10, "new", mtime)
        self.assertEqual(rep.call_count, 3)                              # bounded retry, then give up
        self.assertEqual([f.name for f in self.dir.iterdir()], ["261110.md"])
        self.assertEqual(self.memos.read(D10).body, "old\n")

    def test_save_survives_a_momentary_lock(self):
        mtime = self.memos.write(D10, "old", None)
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(5, "locked")
            real(src, dst)
        with mock.patch("mdcal.memofile.os.replace", flaky):
            self.memos.write(D10, "new", mtime)
        self.assertEqual((self.memos.read(D10).body, len(calls)), ("new\n", 2))

    def test_cp949_file_is_read(self):
        (self.dir / "261110.md").write_bytes("한글 메모\n".encode("cp949"))
        doc = self.memos.read(D10)
        self.assertEqual((doc.body, doc.error), ("한글 메모\n", None))


class CarryTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="mdcal_carry_"))
        self.memos = MemoFiles(str(self.dir))

    def body(self, day):
        return self.memos.read(day).body

    def test_carry_moves_open_tasks_and_marks_source(self):
        self.memos.write(D9, "- [ ] a\n- [x] done\n- [ ] b", None)
        self.memos.write(D10, "today", None)
        result = carry.carry(self.memos, [D9], D10)
        self.assertEqual((result.moved, result.marked, result.errors), (2, 2, []))
        self.assertEqual(self.body(D10), "today\n- [ ] a\n- [ ] b\n")
        self.assertEqual(self.body(D9), "- [>] a (→ 11/10)\n- [x] done\n- [>] b (→ 11/10)\n")

    def test_retry_does_not_duplicate(self):
        self.memos.write(D9, "- [ ] a", None)
        self.memos.write(D10, "- [ ] a", None)       # as if a previous run saved the target only
        result = carry.carry(self.memos, [D9], D10)
        self.assertEqual((result.moved, result.marked), (0, 1))
        self.assertEqual(self.body(D10), "- [ ] a\n")
        self.assertEqual(carry.carry(self.memos, [D9], D10).marked, 0)

    def test_pending_days(self):
        self.memos.write(D9, "- [ ] a\n- [ ] b", None)
        self.assertEqual(carry.pending_days(self.memos, D10, 14), {D9: 2})
        self.assertEqual(carry.pending_days(self.memos, D10, 0), {})

    def test_future_source_is_skipped(self):
        self.memos.write(D10, "- [ ] a", None)
        self.assertEqual(carry.carry(self.memos, [D10], D9).marked, 0)
        self.assertEqual(self.body(D10), "- [ ] a\n")


if __name__ == "__main__":
    unittest.main()
