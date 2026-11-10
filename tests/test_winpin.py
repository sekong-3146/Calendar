import tests  # noqa: F401  (sandbox first: must precede every mdcal import)
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mdcal import winpin


class HotkeyParseTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(winpin.parse_hotkey("ctrl+alt+c"), (0x3, ord("C")))
        self.assertEqual(winpin.parse_hotkey(" Win + Shift + F2 "), (0xC, 0x71))
        self.assertEqual(winpin.parse_hotkey("alt+space"), (0x1, 0x20))

    def test_invalid(self):
        for text in ("c", "ctrl+", "ctrl+alt", "ctrl+a+b", "ctrl+home", "ctrl+f99", ""):
            with self.assertRaises(ValueError, msg=text):
                winpin.parse_hotkey(text)


class FakePin(winpin.BottomPin):
    """BottomPin with the Win32 queries replaced by settable values."""

    def __init__(self, log_path):
        super().__init__(1234, log_path=log_path)
        self.fg = ("Progman", False)     # (class, ours)
        self.above = False
        self.hidden = False

    def foreground(self):
        return self.fg

    def desktop_above(self):
        return self.above

    def foreground_hidden(self):
        return self.hidden

    def send_to_bottom(self):
        pass

    def diag(self):
        return "Above", 0, "Progman", 0


class PollTest(unittest.TestCase):
    def setUp(self):
        self.log = Path(tempfile.mkdtemp(prefix="mdcal_pin_")) / "pin.log"
        self.topmost = False             # what the "window" really is
        self.sticks = True               # whether SetWindowPos has any effect
        self.fg_hwnd = 100

        def set_topmost(hwnd, on):
            if self.sticks:
                self.topmost = on
            return True

        user32 = mock.Mock()
        user32.GetForegroundWindow.side_effect = lambda: self.fg_hwnd
        self.now = 0.0                   # fake clock for the peek grace period
        clock = SimpleNamespace(monotonic=lambda: self.now)
        for name, value in (("set_topmost", set_topmost), ("is_topmost", lambda hwnd: self.topmost),
                            ("user32", user32), ("time", clock)):
            patcher = mock.patch.object(winpin, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pin = FakePin(str(self.log))

    def log_text(self):
        return self.log.read_text("utf-8") if self.log.exists() else ""

    def test_raise_and_lower(self):
        self.pin.above = True
        self.pin.poll()
        self.assertTrue(self.pin.raised and self.topmost)
        self.pin.above = False           # we are above the desktop now
        self.pin.poll()
        self.assertTrue(self.pin.raised)
        self.pin.fg = ("Chrome_WidgetWin_1", False)
        self.pin.poll()
        self.assertFalse(self.pin.raised or self.topmost)
        self.assertEqual(self.log_text().count("-> RAISE"), 1)
        self.assertEqual(self.log_text().count("-> LOWER"), 1)

    def test_no_raise_while_an_app_is_visible_in_front(self):
        self.pin.above = True
        self.pin.fg = ("Chrome_WidgetWin_1", False)
        self.pin.poll()
        self.assertFalse(self.pin.raised)
        self.pin.hidden = True           # that app is covered by the shown desktop
        self.pin.poll()
        self.assertTrue(self.pin.raised)

    def test_lost_topmost_is_reapplied(self):
        self.pin.above = True
        self.pin.poll()
        self.topmost = False             # something took it away
        self.pin.poll()
        self.assertTrue(self.pin.raised and self.topmost)
        self.assertIn("RETRY 1", self.log_text())

    def test_gives_up_after_three_retries_and_does_not_loop(self):
        self.sticks = False
        self.pin.above = True
        self.pin.poll()                  # RAISE (no effect)
        for _ in range(4):
            self.pin.poll()              # RETRY 1..3, then give up
        self.assertFalse(self.pin.raised)
        self.assertIn("RAISE FAILED", self.log_text())
        size = len(self.log_text())
        for _ in range(10):
            self.pin.poll()              # desktop still above: must stay quiet
        self.assertFalse(self.pin.raised)
        self.assertEqual(len(self.log_text()), size)
        self.pin.above = False           # desktop went away: allowed to try again next time
        self.pin.poll()
        self.sticks, self.pin.above = True, True
        self.pin.poll()
        self.assertTrue(self.pin.raised and self.topmost)

    def test_peek_stays_until_focus_moves_to_another_app(self):
        self.pin.fg = ("Chrome_WidgetWin_1", False)
        self.pin.peek(True)
        self.assertTrue(self.pin.raised and self.topmost)
        self.pin.poll()                  # same foreground window as when peek started
        self.assertTrue(self.pin.raised)
        self.fg_hwnd, self.pin.fg = 200, ("Qt", True)      # our window got focus
        self.pin.poll()
        self.assertTrue(self.pin.raised)
        self.fg_hwnd, self.pin.fg = 300, ("XLMAIN", False)  # user clicked another app
        self.pin.poll()
        self.assertFalse(self.pin.raised or self.topmost)

    def test_peek_toggle_off(self):
        self.pin.peek(True)
        self.pin.peek(False)
        self.assertFalse(self.pin.raised or self.topmost)
        self.assertIn("PEEK END", self.log_text())

    def test_peek_lowers_when_user_returns_to_the_same_app_between_polls(self):
        self.pin.fg = ("Chrome_WidgetWin_1", False)       # app A, hwnd 100
        self.pin.peek(True)
        self.pin.activated()             # our window got focus (activation event) ...
        self.pin.poll()                  # ... but by the next poll A is in front again
        self.assertFalse(self.pin.raised or self.topmost)

    def test_peek_does_not_stay_up_forever_if_activation_never_happens(self):
        self.pin.fg = ("Chrome_WidgetWin_1", False)
        self.pin.peek(True)
        self.now = winpin.PEEK_GRACE - 0.1
        self.pin.poll()
        self.assertTrue(self.pin.raised)                  # still inside the grace period
        self.now = winpin.PEEK_GRACE + 0.1
        self.pin.poll()
        self.assertFalse(self.pin.raised or self.topmost)

    def test_hotkey_dismiss_during_show_desktop_stays_down(self):
        self.pin.above = True
        self.pin.poll()                  # Win+D raised us
        self.assertTrue(self.pin.raised)
        self.pin.peek(False)             # user presses the hotkey to put it away
        for _ in range(5):
            self.pin.poll()              # desktop still shown above us
        self.assertFalse(self.pin.raised or self.topmost)
        self.pin.above = False           # that Show Desktop session ended
        self.pin.poll()
        self.pin.above = True            # next Win+D raises again
        self.pin.poll()
        self.assertTrue(self.pin.raised)

    def test_explicit_peek_overrides_a_dismissal(self):
        self.pin.above = True
        self.pin.poll()
        self.pin.peek(False)
        self.pin.peek(True)
        self.assertTrue(self.pin.raised and self.topmost)

    def test_log_is_quiet_by_default(self):
        for cls in ("A", "B", "C"):
            self.pin.fg = (cls, False)
            self.pin.poll()
        self.assertEqual(self.log_text(), "")

    def test_verbose_log_records_state_changes_once(self):
        self.pin.verbose = True
        for cls in ("A", "A", "B"):
            self.pin.fg = (cls, False)
            self.pin.poll()
        lines = self.log_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("fg=A", lines[0])
        self.assertIn("above=Above workerw=0 defview=Progman", lines[1])


if __name__ == "__main__":
    unittest.main()
