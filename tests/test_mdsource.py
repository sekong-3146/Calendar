import tests  # noqa: F401  (sandbox first: must precede every mdcal import)
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from mdcal.mdsource import MdIndex, date_from_name, norm
from mdcal.memofile import replace_retry
from tests import qapp, wait_until

D10, D11 = date(2026, 11, 10), date(2026, 11, 11)


class DateFromNameTest(unittest.TestCase):
    def test_patterns(self):
        self.assertEqual(date_from_name("261110 회의"), (D10, "회의"))
        self.assertEqual(date_from_name("2026-11-10_plan"), (D10, "plan"))
        self.assertEqual(date_from_name("20261110"), (D10, ""))
        self.assertEqual(date_from_name("REVIEW_1"), (None, None))
        self.assertEqual(date_from_name("269999"), (None, None))


class IndexTest(unittest.TestCase):
    def setUp(self):
        qapp()
        self.root = Path(tempfile.mkdtemp(prefix="mdcal_idx_")) / "vault"
        (self.root / "sub").mkdir(parents=True)
        (self.root / ".obsidian").mkdir()
        (self.root / "261110.md").write_text("memo", "utf-8")
        (self.root / "sub" / "261110 회의.md").write_text("## 안건", "utf-8")
        (self.root / ".obsidian" / "261110.md").write_text("hidden", "utf-8")
        (self.root / "notes.md").write_text("no date", "utf-8")
        self.index = MdIndex([str(self.root)], str(self.root))
        self.emits = 0
        self.index.changed.connect(self._count)
        self.index.rescan()

    def _count(self):
        self.emits += 1

    def tearDown(self):
        self.index.deleteLater()
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def titles(self, day):
        return [n.title for n in self.index.notes(day)]

    def test_full_scan(self):
        self.assertEqual(self.titles(D10), ["vault/sub/회의"])       # memo file and dot folder excluded
        self.assertEqual(self.index.memo_note(D10).body, "memo")
        self.assertEqual(self.index.notes(D10)[0].summary_lines(), ["vault/sub/회의", "안건"])
        self.assertIn(norm(self.root / "261110.md"), self.index.watched())
        self.assertNotIn(norm(self.root / ".obsidian"), self.index.watched())

    def test_partial_scan_picks_up_new_changed_and_deleted_files(self):
        sub_note = self.index.notes(D10)[0]
        (self.root / "261111.md").write_text("tomorrow", "utf-8")
        p = self.root / "261110.md"
        p.write_text("memo v2", "utf-8")
        os.utime(p, (p.stat().st_mtime + 5,) * 2)
        self.index.rescan_dirs([str(self.root)])
        self.assertEqual(self.index.memo_note(D11).body, "tomorrow")
        self.assertEqual(self.index.memo_note(D10).body, "memo v2")
        self.assertIs(self.index.notes(D10)[0], sub_note)            # untouched folder is not re-read
        (self.root / "261111.md").unlink()
        self.index.rescan_dirs([str(self.root)])
        self.assertIsNone(self.index.memo_note(D11))
        # the watcher drops deleted paths by itself, later
        self.assertTrue(wait_until(lambda: norm(self.root / "261111.md") not in self.index.watched()))

    def test_partial_scan_follows_new_and_removed_subfolders(self):
        (self.root / "new" / "deep").mkdir(parents=True)
        (self.root / "new" / "deep" / "261111 a.md").write_text("x", "utf-8")
        self.index.rescan_dirs([str(self.root)])
        self.assertEqual(self.titles(D11), ["vault/new/deep/a"])
        shutil.rmtree(self.root / "new")
        shutil.rmtree(self.root / "sub")
        self.index.rescan_dirs([str(self.root)])
        self.assertEqual((self.titles(D11), self.titles(D10)), ([], []))
        want = {norm(self.root), norm(self.root / "261110.md")}
        wait_until(lambda: self.index.watched() == want)
        self.assertEqual(self.index.watched(), want)

    def test_partial_scan_matches_full_scan(self):
        (self.root / "sub" / "261111 b.md").write_text("x", "utf-8")
        (self.root / "sub" / "261110 회의.md").unlink()
        self.index.rescan_dirs([str(self.root / "sub")])
        partial = {d: [n.path for n in ns] for d, ns in self.index.by_day.items()}
        self.index.rescan()
        self.assertEqual(partial, {d: [n.path for n in ns] for d, ns in self.index.by_day.items()})

    def test_unknown_and_missing_paths_are_ignored(self):
        before = self.emits
        self.index.rescan_dirs([tempfile.gettempdir(), str(self.root / "nope")])
        self.assertEqual(self.titles(D10), ["vault/sub/회의"])
        self.assertEqual(self.emits, before + 1)

    def test_recreated_subfolder_is_rescanned_and_watched_again(self):
        sub = self.root / "sub"
        shutil.rmtree(sub)
        sub.mkdir()                                  # recreated before the watcher's event is handled
        (sub / "261111 new.md").write_text("x", "utf-8")
        # real flow: change signal -> debounce -> rescan (-> one retry for the watch)
        wait_until(lambda: self.titles(D11) == ["vault/sub/new"]
                   and {norm(sub), norm(sub / "261111 new.md")} <= self.index.watched())
        self.assertEqual((self.titles(D10), self.titles(D11)), ([], ["vault/sub/new"]))
        self.assertIn(norm(sub), self.index.watched())
        self.assertIn(norm(sub / "261111 new.md"), self.index.watched())
        (sub / "261111 second.md").write_text("x", "utf-8")              # the new watch really works
        wait_until(lambda: len(self.titles(D11)) == 2)
        self.assertEqual(self.titles(D11), ["vault/sub/new", "vault/sub/second"])
        self.assertEqual(self.index.scan_errors, [])

    def test_watcher_signal_updates_index_without_manual_rescan(self):
        (self.root / "sub" / "261111 auto.md").write_text("x", "utf-8")
        p = self.root / "261110.md"
        for body in ("v1", "v2", "v3"):              # tmp -> rename, as memofile and Obsidian save
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(body, "utf-8")
            replace_retry(tmp, p, tries=20)          # a scanner may hold the file for a moment
            wait_until(lambda: self.index.memo_note(D10).body == body)
            self.assertEqual(self.index.memo_note(D10).body, body)
        wait_until(lambda: self.titles(D11) == ["vault/sub/auto"])
        self.assertEqual(self.titles(D11), ["vault/sub/auto"])

    def failing_scandir(self, bad):
        real = os.scandir

        def scandir(path):
            if norm(path) == norm(bad):
                raise PermissionError(13, "access denied", str(path))
            return real(path)
        return mock.patch.object(os, "scandir", scandir)

    def test_unreadable_folder_keeps_notes_and_watches_and_reports_once(self):
        errors = []
        self.index.scanError.connect(errors.append)
        watched = self.index.watched()
        with self.failing_scandir(self.root / "sub"):
            self.index.rescan_dirs([str(self.root / "sub")])
            self.index.rescan_dirs([str(self.root / "sub")])
        self.assertEqual(self.titles(D10), ["vault/sub/회의"])
        self.assertEqual(self.index.watched(), watched)
        self.assertEqual(len(errors), 1)
        self.assertIn("sub", errors[0])
        (self.root / "sub" / "261111 later.md").write_text("x", "utf-8")
        self.index.rescan_dirs([str(self.root / "sub")])                 # readable again
        self.assertEqual(self.titles(D11), ["vault/sub/later"])
        with self.failing_scandir(self.root / "sub"):
            self.index.rescan_dirs([str(self.root / "sub")])
        self.assertEqual(len(errors), 2)                                 # a new failure is reported again

    def test_lost_watch_plus_unreadable_keeps_notes_and_reports_once(self):
        errors = []
        self.index.scanError.connect(errors.append)
        sub = self.root / "sub"
        self.index._watcher.removePath(str(sub))     # as if the watch had died
        self.assertNotIn(norm(sub), self.index.watched())
        with self.failing_scandir(sub):
            self.index.rescan_dirs([str(self.root)])     # parent event -> child is re-walked
            self.index.rescan_dirs([str(self.root)])
        self.assertEqual(self.titles(D10), ["vault/sub/회의"])            # not dropped
        self.assertEqual(len(errors), 1)
        (sub / "261111 later.md").write_text("x", "utf-8")
        self.index.rescan_dirs([str(self.root)])                         # readable again: re-walked
        self.assertEqual(self.titles(D11), ["vault/sub/later"])
        self.assertIn(norm(sub), self.index.watched())

    def test_watch_registration_failure_is_retried_once_then_reported_once(self):
        errors, calls = [], []
        self.index.scanError.connect(errors.append)
        new = self.root / "sub" / "261111 x.md"
        new.write_text("x", "utf-8")
        real = self.index._watcher.addPaths

        def reject_new(paths):
            calls.append(list(paths))
            rest = [p for p in paths if norm(p) != norm(new)]
            return [p for p in paths if norm(p) == norm(new)] + (real(rest) if rest else [])
        with mock.patch.object(self.index._watcher, "addPaths", reject_new):
            self.index.rescan_dirs([str(self.root / "sub")])
            self.assertEqual(errors, [])                                 # first failure: retry pending
            wait_until(lambda: errors)                                   # debounce -> retry -> give up
            self.assertEqual(len(errors), 1)
            self.assertIn("261111 x.md", errors[0])
            attempts = len(calls)
            self.index.rescan_dirs([str(self.root / "sub")])
            wait_until(lambda: len(calls) >= attempts + 2, timeout=1.6)  # room for the retry cycle
            self.assertEqual(len(errors), 1)                             # same path is not announced again
            self.assertLessEqual(len(calls), attempts + 2)               # and no endless retry
        self.assertEqual(self.titles(D11), ["vault/sub/x"])              # the note itself is still shown

    def test_full_rescan_keeps_last_contents_of_an_unreadable_folder(self):
        errors = []
        self.index.scanError.connect(errors.append)
        with self.failing_scandir(self.root / "sub"):
            self.index.rescan()
        self.assertEqual(self.titles(D10), ["vault/sub/회의"])
        self.assertEqual(len(self.index.scan_errors), 1)                 # shown by the caller, no signal
        self.assertEqual(errors, [])

    def test_drive_root_contains_its_folders(self):
        index = MdIndex(["D:\\"])
        self.assertEqual(index._root_of(norm("D:\\notes\\a")), "D:\\")
        self.assertIsNone(index._root_of(norm("E:\\notes")))
        self.assertTrue(MdIndex._under(norm("C:\\vault\\sub"), norm("C:\\vault")))
        self.assertFalse(MdIndex._under(norm("C:\\vault2"), norm("C:\\vault")))
        index.deleteLater()

    def test_junction_back_to_parent_is_not_followed(self):
        loop = self.root / "sub" / "loop"
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(loop), str(self.root)], capture_output=True)
        if done.returncode != 0:
            self.skipTest("cannot create a junction here")
        try:
            self.index.rescan()
            self.assertEqual(self.titles(D10), ["vault/sub/회의"])        # once, not once per loop level
            self.assertFalse(any("loop" in k for k in self.index._dirs))
        finally:
            os.rmdir(loop)                           # remove the junction itself, not its target

    def test_missing_root_is_reported(self):
        self.index.set_folders([str(self.root), str(self.root.parent / "gone")])
        self.assertEqual(self.index.missing, [str(self.root.parent / "gone")])

    def test_root_missing_at_start_is_read_when_it_appears(self):
        late = self.root.parent / "late"
        self.index.set_folders([str(self.root), str(late)])
        self.assertTrue(self.index._revive.isActive())
        self.index._check_missing()                                      # still not there
        self.assertEqual(self.index.missing, [str(late)])
        late.mkdir()
        (late / "261111 a.md").write_text("x", "utf-8")
        self.index._check_missing()
        self.assertEqual((self.index.missing, self.titles(D11)), ([], ["late/a"]))
        self.assertIn(norm(late), self.index.watched())
        self.assertFalse(self.index._revive.isActive())

    def test_deleted_root_is_reported_once_and_recovers(self):
        errors = []
        self.index.scanError.connect(errors.append)
        other = self.root.parent / "other"
        other.mkdir()
        (other / "261110 o.md").write_text("x", "utf-8")
        self.index.set_folders([str(self.root), str(other)])
        shutil.rmtree(other)
        self.index.rescan_dirs([str(other)])
        self.index.rescan_dirs([str(other)])
        self.assertEqual((self.index.missing, len(errors)), ([str(other)], 1))
        self.assertEqual(self.titles(D10), ["vault/sub/회의"])            # the other root is untouched
        self.assertTrue(self.index._revive.isActive())
        other.mkdir()
        (other / "261111 back.md").write_text("x", "utf-8")
        with self.failing_scandir(other):                                # back but not readable yet
            self.index._check_missing()
            self.index._check_missing()
        self.assertEqual(self.index.missing, [str(other)])
        self.assertLessEqual(len(self.index.scan_errors), 2)             # polling must not pile up errors
        self.index._check_missing()
        self.assertEqual((self.index.missing, self.titles(D11)), ([], ["other/back"]))
        self.assertIn(norm(other / "261111 back.md"), self.index.watched())
        (other / "261111 more.md").write_text("x", "utf-8")              # and it is really watched again
        self.assertTrue(wait_until(lambda: len(self.titles(D11)) == 2))

    def test_file_that_cannot_be_checked_keeps_its_note(self):
        locked = self.root / "sub" / "261110 회의.md"
        self.index.notes(D10)                                            # loaded once
        real = Path.stat

        def stat(path, **kw):
            if norm(path) == norm(locked):
                raise PermissionError(13, "locked", str(path))
            return real(path, **kw)
        with mock.patch.object(Path, "stat", stat):
            self.index.rescan_dirs([str(self.root / "sub")])
        self.assertEqual(self.titles(D10), ["vault/sub/회의"])
        self.assertEqual(len(self.index.scan_errors), 1)


if __name__ == "__main__":
    unittest.main()
